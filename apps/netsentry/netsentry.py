#!/usr/bin/env python3
"""
NetSentry - real-time home network watchdog.

Sweeps your local network, keeps a live list of every device, and raises
plain-English alerts for things that look wrong:
  * a new / unknown device joining
  * someone impersonating your router (ARP spoofing / man-in-the-middle)
  * your router's hardware address suddenly changing
  * devices with dangerous or backdoor-style ports open
  * this computer talking to (or listening on) known malware/botnet ports

Pure Python standard library - nothing to install. Works on Windows,
Raspberry Pi OS / Linux and macOS. Open http://127.0.0.1:8775 to view.
"""
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "NetSentry"
VERSION = "1.0"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(APP_DIR, "netsentry_data.json")
OUI_FILE = os.path.join(APP_DIR, "oui_cache.txt")
UI_FILE = os.path.join(APP_DIR, "index.html")
HOST, PORT = "127.0.0.1", 8775
SCAN_INTERVAL = 30          # seconds between network sweeps
PORT_RESCAN = 300           # seconds before re-probing a device's ports
CONN_INTERVAL = 5           # seconds between checks of this PC's connections
OFFLINE_AFTER = 3           # missed sweeps before a device is marked offline
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
NO_WINDOW = 0x08000000 if IS_WIN else 0

# ---------------------------------------------------------------- port rules
# port: (label, level, plain-English explanation)
PORTS = {
    # dangerous / backdoor style
    23:    ("Telnet", "high", "Old, unencrypted remote login. This is the #1 way botnets like Mirai break into cameras, DVRs and routers."),
    2323:  ("Telnet (alt)", "high", "Alternate Telnet port used by IoT botnets to take over smart gadgets."),
    4444:  ("Port 4444", "high", "Default port for Metasploit and many remote-access trojans. Almost never legitimate on a home network."),
    1337:  ("Port 1337", "high", "'Leet' port favoured by hacking tools and backdoors."),
    31337: ("Port 31337", "high", "Classic backdoor port (Back Orifice and friends)."),
    12345: ("Port 12345", "high", "Historic trojan port (NetBus) still used by some malware."),
    6667:  ("IRC", "high", "IRC chat port - commonly used by botnets to receive commands."),
    5555:  ("Android ADB", "high", "Android debug access left open. Infected TV boxes and phones are hijacked through this."),
    2375:  ("Docker API", "high", "Unprotected Docker control port - anyone on the network can run programs on this machine."),
    # worth a look
    21:    ("FTP", "medium", "Unencrypted file transfer. Passwords travel in plain text."),
    3389:  ("Remote Desktop", "medium", "Windows Remote Desktop is on. Fine if you use it, risky if you don't."),
    5900:  ("VNC", "medium", "Screen-sharing (VNC) is on. Fine if you set it up, otherwise someone could view this screen."),
    1080:  ("SOCKS proxy", "medium", "This device is acting as a proxy - malware sometimes turns devices into relays for other people's traffic."),
    3128:  ("Web proxy", "medium", "A web proxy is running. Unexpected proxies can be used to relay or snoop on traffic."),
    9050:  ("Tor", "medium", "Tor is running. Could be intentional, but malware also uses Tor to hide."),
    7547:  ("TR-069", "medium", "ISP remote-management port. It has been mass-exploited in the past; keep router firmware updated."),
    8291:  ("MikroTik Winbox", "medium", "Router admin port that has been targeted by large botnets."),
    # normal services - shown as info only
    22:    ("SSH", "info", "Secure remote login."),
    53:    ("DNS", "info", "Name lookups (normal for routers)."),
    80:    ("Web", "info", "Web page / admin panel."),
    443:   ("Web (secure)", "info", "Secure web page / admin panel."),
    445:   ("File sharing", "info", "Windows file sharing (SMB)."),
    548:   ("Mac sharing", "info", "Apple file sharing."),
    554:   ("Camera stream", "info", "Video stream (RTSP) - usually a camera."),
    631:   ("Printer", "info", "Printing (IPP)."),
    9100:  ("Printer", "info", "Raw printing."),
    1883:  ("MQTT", "info", "Smart-home messaging."),
    8008:  ("Cast", "info", "Chromecast / Google Cast."),
    8009:  ("Cast", "info", "Chromecast / Google Cast."),
    8080:  ("Web (alt)", "info", "Alternate web page / admin panel."),
    62078: ("Apple sync", "info", "iPhone / iPad sync service."),
}
# remote ports that this computer should not normally be talking to
SUSPICIOUS_REMOTE = {
    4444: "Metasploit / remote-access trojan port",
    1337: "hacker tool / backdoor port",
    31337: "classic backdoor port",
    12345: "trojan port",
    6666: "IRC botnet control port",
    6667: "IRC botnet control port",
    3333: "crypto-mining pool port",
    14444: "crypto-mining pool port",
    9001: "Tor relay port",
    9030: "Tor directory port",
    23: "Telnet (unencrypted)",
}
BACKDOOR_LISTEN = {4444, 1337, 31337, 12345, 6666, 6667, 2323}

BUILTIN_OUI = {
    "b827eb": "Raspberry Pi Foundation", "dca632": "Raspberry Pi Trading",
    "e45f01": "Raspberry Pi Trading", "d83add": "Raspberry Pi Trading",
    "2ccf67": "Raspberry Pi Trading", "28cdc1": "Raspberry Pi Trading",
    "240ac4": "Espressif", "30aea4": "Espressif", "84f3eb": "Espressif",
    "a4cf12": "Espressif", "ecfabc": "Espressif",
}

SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "info": 3}

# ---------------------------------------------------------------- helpers
def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=NO_WINDOW, errors="ignore")
        return r.returncode, r.stdout or ""
    except Exception:
        return 1, ""


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def gateway_ip(lip):
    if IS_WIN:
        _, out = run(["route", "print", "-4", "0.0.0.0"])
        best = None
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 5 and p[0] == "0.0.0.0" and p[1] == "0.0.0.0":
                if p[3] == lip:
                    return p[2]
                best = best or p[2]
        return best
    if IS_MAC:
        _, out = run(["route", "-n", "get", "default"])
        m = re.search(r"gateway:\s*(\S+)", out)
        return m.group(1) if m else None
    _, out = run(["ip", "route"])
    m = re.search(r"default via (\S+)", out)
    if m:
        return m.group(1)
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                p = line.split()
                if p[1] == "00000000":
                    return socket.inet_ntoa(bytes.fromhex(p[2])[::-1])
    except Exception:
        pass
    return None


def own_mac():
    n = uuid.getnode()
    return ":".join(f"{(n >> s) & 0xff:02x}" for s in range(40, -1, -8))


MAC_RE = re.compile(r"((?:[0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2})")
IP_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def norm_mac(m):
    return ":".join(x.zfill(2) for x in re.split("[:-]", m.lower()))


def read_arp():
    outs = []
    if not IS_WIN and not IS_MAC:
        outs.append(run(["ip", "neigh"])[1])
        try:
            with open("/proc/net/arp") as f:
                outs.append(f.read())
        except Exception:
            pass
    outs.append(run(["arp", "-a"])[1])
    table = {}
    for out in outs:
        for line in out.splitlines():
            u = line.upper()
            if "FAILED" in u or "INCOMPLETE" in u:
                continue
            ipm, mm = IP_RE.search(line), MAC_RE.search(line)
            if not (ipm and mm):
                continue
            mac = norm_mac(mm.group(1))
            if mac in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff") or mac.startswith(("01:00:5e", "33:33")):
                continue
            table[ipm.group(1)] = mac
    return table


def ping(ip):
    if IS_WIN:
        cmd = ["ping", "-n", "1", "-w", "700", ip]
    elif IS_MAC:
        cmd = ["ping", "-c", "1", "-W", "1000", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    code, out = run(cmd, timeout=3)
    return code == 0 and "ttl" in out.lower()


def port_open(ip, port, timeout=0.5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def rdns(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def ip_key(ip):
    try:
        return int(ipaddress.ip_address(ip))
    except ValueError:
        return 0


def is_random_mac(mac):
    try:
        return bool(int(mac[1], 16) & 2)
    except ValueError:
        return False


def load_oui():
    table = dict(BUILTIN_OUI)
    try:
        with open(OUI_FILE, encoding="utf-8") as f:
            for line in f:
                k, _, v = line.rstrip("\n").partition("\t")
                if len(k) == 6:
                    table[k] = v
    except Exception:
        pass
    return table


def download_oui():
    """Fetch the official IEEE vendor list once and cache it compactly."""
    req = urllib.request.Request("https://standards-oui.ieee.org/oui/oui.txt",
                                 headers={"User-Agent": "Mozilla/5.0 NetSentry"})
    with urllib.request.urlopen(req, timeout=40) as r:
        text = r.read().decode("utf-8", "ignore")
    rx = re.compile(r"^([0-9A-F]{2})-([0-9A-F]{2})-([0-9A-F]{2})\s+\(hex\)\s+(.+?)\s*$", re.M)
    rows = [(a + b + c).lower() + "\t" + v for a, b, c, v in rx.findall(text)]
    if len(rows) > 1000:
        with open(OUI_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(rows))
        return True
    return False


def split_addr(tok):
    m = re.match(r"^(.+)[:.](\d+|\*)$", tok)
    if not m or not re.search(r"[\d:]", m.group(1)):
        return None
    host = m.group(1).strip("[]")
    if "%" in host:
        host = host.split("%")[0]
    return host, m.group(2)


def read_connections():
    """Return (established, listening) TCP sockets on this computer."""
    if IS_WIN:
        out = run(["netstat", "-an", "-p", "TCP"])[1]
    else:
        out = run(["netstat", "-tan"])[1]
        if "tcp" not in out.lower():
            out = run(["ss", "-tan"])[1]
    est, listen = [], []
    for line in out.splitlines():
        u = line.upper()
        state = "EST" if "ESTAB" in u else ("LISTEN" if "LISTEN" in u else None)
        if not state:
            continue
        addrs = [a for a in (split_addr(t) for t in line.split()) if a]
        if len(addrs) < 2:
            continue
        (lh, lp), (rh, rp) = addrs[0], addrs[1]
        if state == "LISTEN":
            if lp.isdigit():
                listen.append((lh, int(lp)))
        elif rp.isdigit() and not rh.startswith(("127.", "::1", "::ffff:127.")):
            est.append((lh, int(lp) if lp.isdigit() else 0, rh, int(rp)))
    return est, listen


# ---------------------------------------------------------------- engine
class Sentry:
    def __init__(self):
        self.lock = threading.RLock()
        self.data = self.load()
        self.oui = load_oui()
        self.scan_alerts, self.conn_alerts = {}, {}
        self.events = []
        self.conn_summary = {"total": 0, "remotes": [], "flagged": [], "listening": []}
        self.scanning, self.progress = False, 0
        self.phase = "Starting up"
        self.last_scan = None
        self.next_scan = time.time()
        self.lip = self.gw = self.net = None
        self.scan_count = 0
        self.wake = threading.Event()
        self.log("info", f"{APP_NAME} started - watching your network.")

    # ---- persistence
    def load(self):
        base = {"devices": {}, "gateway_macs": {}, "dismissed": [], "new_devices": [],
                "first_scan_done": False, "subnet": ""}
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                base.update(json.load(f))
        except Exception:
            pass
        for d in base["devices"].values():
            d["online"] = False
            d["missed"] = OFFLINE_AFTER
        return base

    def save(self):
        tmp = DATA_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, DATA_FILE)
        except Exception as e:
            print("save failed:", e)

    def log(self, level, text):
        with self.lock:
            self.events.append({"t": time.time(), "level": level, "text": text})
            del self.events[:-300]

    def vendor(self, mac):
        if mac.startswith("nomac") or is_random_mac(mac):
            return ""
        return self.oui.get(mac.replace(":", "")[:6], "")

    def label(self, d):
        return d.get("name") or d.get("hostname") or d.get("vendor") or d.get("ip") or d["mac"]

    def guess_type(self, d):
        p, v = set(d.get("ports", [])), (d.get("vendor") or "").lower()
        if d.get("is_gateway"): return "Router"
        if d.get("is_self"): return "This computer"
        if 62078 in p: return "iPhone / iPad"
        if 9100 in p or 631 in p: return "Printer"
        if 554 in p: return "Camera"
        if 8008 in p or 8009 in p: return "Cast device / TV"
        if "raspberry" in v: return "Raspberry Pi"
        if "espressif" in v: return "Smart gadget (IoT)"
        if 3389 in p: return "Windows PC"
        if 548 in p: return "Mac"
        if 445 in p: return "Computer"
        if d.get("random_mac"): return "Phone / tablet"
        if 22 in p: return "Linux device"
        return "Device"

    # ---- main sweep
    def scan(self):
        with self.lock:
            self.scanning, self.progress, self.phase = True, 0, "Finding devices"
        lip = local_ip()
        gw = gateway_ip(lip)
        try:
            net = ipaddress.ip_network(self.data.get("subnet") or f"{lip}/24", strict=False)
        except ValueError:
            net = ipaddress.ip_network(f"{lip}/24", strict=False)
        hosts = [str(h) for h in net.hosts()][:1024]
        self.lip, self.gw, self.net = lip, gw, str(net)

        pinged = set()
        with ThreadPoolExecutor(max_workers=64) as ex:
            futs = {ex.submit(ping, h): h for h in hosts}
            for i, f in enumerate(as_completed(futs)):
                if f.result():
                    pinged.add(futs[f])
                if i % 8 == 0:
                    self.progress = int(60 * i / max(1, len(hosts)))

        arp = {}
        for ip, mac in read_arp().items():
            try:
                if ipaddress.ip_address(ip) in net and ip != str(net.broadcast_address):
                    arp[ip] = mac
            except ValueError:
                pass
        alive = pinged | set(arp) | {lip}
        selfmac = own_mac()
        mac_ips = {}
        for ip in alive:
            mac = selfmac if ip == lip else arp.get(ip) or ("nomac-" + ip)
            mac_ips.setdefault(mac, []).append(ip)

        now = time.time()
        with self.lock:
            devs = self.data["devices"]
            baseline = self.data["first_scan_done"]
            for mac, ips in mac_ips.items():
                ips.sort(key=ip_key)
                d = devs.get(mac)
                fresh = d is None
                if fresh:
                    d = devs[mac] = {"mac": mac, "name": "", "trusted": False, "first_seen": now,
                                     "ports": [], "ports_checked": 0, "hostname": "",
                                     "host_checked": 0, "online": False, "missed": 0}
                d.update(ip=ips[0], ips=ips, last_seen=now, missed=0,
                         is_gateway=bool(gw and gw in ips), is_self=lip in ips,
                         random_mac=is_random_mac(mac), vendor=self.vendor(mac))
                if d["is_self"] and fresh:
                    d["trusted"], d["name"] = True, "This computer"
                if fresh and baseline and not d["trusted"]:
                    if mac not in self.data["new_devices"]:
                        self.data["new_devices"].append(mac)
                    self.log("high", f"New device joined: {d['ip']} ({d['vendor'] or ('private address' if d['random_mac'] else 'unknown maker')})")
                elif not fresh and not d["online"] and baseline:
                    self.log("info", f"{self.label(d)} came online at {d['ip']}")
                d["online"] = True
            for mac, d in devs.items():
                if mac not in mac_ips:
                    d["missed"] = d.get("missed", 0) + 1
                    if d.get("online") and d["missed"] >= OFFLINE_AFTER:
                        d["online"] = False
                        self.log("info", f"{self.label(d)} went offline")
            need_ports = [(m, d["ip"]) for m, d in devs.items()
                          if d["online"] and now - d.get("ports_checked", 0) > PORT_RESCAN]
            need_host = [(m, d["ip"]) for m, d in devs.items()
                         if d["online"] and not d.get("hostname") and now - d.get("host_checked", 0) > 600]
            self.phase = "Checking open ports"

        # ports
        found = {m: [] for m, _ in need_ports}
        tasks = [(m, ip, p) for m, ip in need_ports for p in PORTS]
        if tasks:
            with ThreadPoolExecutor(max_workers=128) as ex:
                futs = {ex.submit(port_open, ip, p): (m, p) for m, ip, p in tasks}
                for i, f in enumerate(as_completed(futs)):
                    if f.result():
                        m, p = futs[f]
                        found[m].append(p)
                    if i % 20 == 0:
                        self.progress = 60 + int(30 * i / len(tasks))
        # hostnames (bounded wait - reverse DNS can hang)
        names = {}
        if need_host:
            self.phase = "Looking up names"
            ex = ThreadPoolExecutor(max_workers=32)
            futs = {ex.submit(rdns, ip): m for m, ip in need_host}
            try:
                for f in as_completed(futs, timeout=6):
                    names[futs[f]] = f.result()
            except Exception:
                pass
            ex.shutdown(wait=False)

        with self.lock:
            devs = self.data["devices"]
            for m, ports in found.items():
                d = devs.get(m)
                if not d:
                    continue
                old = set(d.get("ports", []))
                if d.get("ports_checked") and baseline:
                    for p in sorted(set(ports) - old):
                        if p in PORTS and PORTS[p][1] in ("high", "medium"):
                            self.log(PORTS[p][1], f"{self.label(d)} just opened {PORTS[p][0]} (port {p})")
                d["ports"] = sorted(ports)
                d["ports_checked"] = now
            for m, _ in need_host:
                if m in devs:
                    devs[m]["host_checked"] = now
                    if names.get(m) and names[m] != devs[m]["ip"]:
                        devs[m]["hostname"] = names[m]
            for d in devs.values():
                d["type"] = self.guess_type(d)
            self.build_scan_alerts(gw, arp.get(gw) if gw else None, mac_ips)
            if not self.data["first_scan_done"]:
                self.data["first_scan_done"] = True
                n = sum(1 for d in devs.values() if d["online"])
                self.log("info", f"Learned your network: {n} devices. Mark the ones you recognise as Trusted.")
            self.scan_count += 1
            self.last_scan = time.time()
            self.scanning, self.progress, self.phase = False, 100, "Watching"
            self.save()

    def _add(self, store, key, level, title, what, todo, mac=None, action=None, extra=None):
        store[key] = {"key": key, "level": level, "title": title, "what": what, "todo": todo,
                      "mac": mac, "action": action, "extra": extra or {}}

    def build_scan_alerts(self, gw, gw_mac, mac_ips):
        A, devs = {}, self.data["devices"]
        # router impersonation / change
        if gw and gw_mac:
            base = self.data["gateway_macs"].get(gw)
            if base is None:
                self.data["gateway_macs"][gw] = gw_mac
            elif base != gw_mac:
                self._add(A, f"gwchange:{gw}:{gw_mac}", "critical",
                          "Your router's hardware address changed",
                          f"Your router ({gw}) used to be {base} but is now answering as {gw_mac}. "
                          "This is exactly what an ARP-spoofing attack looks like - another device pretending to be your router so it can read your traffic.",
                          "If you just replaced or reset your router, click 'That's my new router'. Otherwise, find the device with that address in the list below and unplug it / remove it from Wi-Fi, then restart your router.",
                          action="accept_gateway", extra={"gw": gw, "mac": gw_mac})
            others = [ip for ip in mac_ips.get(gw_mac, []) if ip != gw]
            if others:
                self._add(A, f"mitm:{gw_mac}:{','.join(others)}", "critical",
                          "A device is pretending to be your router",
                          f"The router's hardware address ({gw_mac}) is also claiming {', '.join(others)}. "
                          "That usually means someone is intercepting traffic (man-in-the-middle).",
                          "Look up those addresses in the device list, disconnect anything you don't recognise, and change your Wi-Fi password.")
        # one MAC answering for several IPs
        for mac, ips in mac_ips.items():
            if mac != gw_mac and len(ips) > 1 and not mac.startswith("nomac"):
                d = devs.get(mac, {})
                self._add(A, f"multi:{mac}", "medium", "One device is answering for several addresses",
                          f"{self.label(d)} ({mac}) is claiming {', '.join(ips)}.",
                          "Wi-Fi extenders and mesh points do this normally. If you don't have one, this can be a sign of ARP spoofing - investigate this device.",
                          mac=mac)
        # dangerous ports
        for mac, d in devs.items():
            if not d.get("online"):
                continue
            for p in d.get("ports", []):
                if p not in PORTS:
                    continue
                name, lvl, why = PORTS[p]
                if lvl in ("high", "medium"):
                    who = self.label(d)
                    self._add(A, f"port:{mac}:{p}", lvl, f"{name} open on {who}",
                              f"{who} ({d['ip']}) has port {p} open. {why}",
                              "If you didn't set this up, turn the feature off in the device's settings, update its firmware, or unplug it. Dismiss if it's expected." if lvl == "medium"
                              else "Unless you know exactly why this is open, disconnect this device from the network and factory-reset / update it.",
                              mac=mac)
        # new devices waiting for review
        for mac in list(self.data["new_devices"]):
            d = devs.get(mac)
            if not d or d.get("trusted"):
                self.data["new_devices"].remove(mac)
                continue
            hint = " It uses a private (randomised) address, which phones and tablets do - it's probably someone's phone." if d.get("random_mac") else ""
            self._add(A, f"new:{mac}", "high", f"New device joined: {self.label(d)}",
                      f"A device you haven't seen before connected at {d['ip']} ({d.get('vendor') or 'unknown maker'}, looks like: {d.get('type','Device')}).{hint}",
                      "If you recognise it, click 'Trust device'. If not, change your Wi-Fi password and check your router's connected-devices page.",
                      mac=mac, action="trust")
        self._announce(self.scan_alerts, A)
        self.scan_alerts = A

    def _announce(self, old, new):
        for k, a in new.items():
            if k not in old and k not in self.data["dismissed"] and not k.startswith("new:"):
                self.log(a["level"], a["title"])

    def watch_connections(self):
        est, listen = read_connections()
        A, flagged, counts = {}, [], {}
        for lh, lp, rh, rp in est:
            c = counts.setdefault(rh, {"ip": rh, "count": 0, "ports": set()})
            c["count"] += 1
            c["ports"].add(rp)
            if rp in SUSPICIOUS_REMOTE:
                flagged.append({"ip": rh, "port": rp, "why": SUSPICIOUS_REMOTE[rp]})
                self._add(A, f"conn:{rh}:{rp}", "high",
                          f"This computer is talking to {rh} on port {rp}",
                          f"Port {rp} is a {SUSPICIOUS_REMOTE[rp]}. Legit apps rarely use it.",
                          "Check which program is responsible (Task Manager / 'netstat -ano' on Windows, 'ss -tnp' on Linux) and run a malware scan.")
        listening = sorted({p for _, p in listen})
        for p in listening:
            if p in BACKDOOR_LISTEN:
                self._add(A, f"listen:{p}", "high", f"This computer is listening on backdoor port {p}",
                          f"Something on this computer is waiting for connections on port {p}, a port commonly used by backdoors and trojans.",
                          "Find the program using it ('netstat -ano' on Windows, 'sudo ss -tlnp' on Linux) and run a malware scan.")
        remotes = sorted(counts.values(), key=lambda c: -c["count"])[:15]
        for r in remotes:
            r["ports"] = sorted(r["ports"])
        with self.lock:
            self._announce(self.conn_alerts, A)
            self.conn_alerts = A
            self.conn_summary = {"total": len(est), "unique": len(counts), "remotes": remotes,
                                 "flagged": flagged, "listening": listening}

    # ---- loops
    def scan_loop(self):
        while True:
            try:
                self.scan()
            except Exception as e:
                self.scanning = False
                self.log("medium", f"Scan problem: {e}")
            self.next_scan = time.time() + SCAN_INTERVAL
            self.wake.wait(SCAN_INTERVAL)
            self.wake.clear()

    def conn_loop(self):
        while True:
            try:
                self.watch_connections()
            except Exception as e:
                print("connection watch error:", e)
            time.sleep(CONN_INTERVAL)

    def oui_loop(self):
        if os.path.exists(OUI_FILE):
            return
        try:
            if download_oui():
                self.oui = load_oui()
                with self.lock:
                    for m, d in self.data["devices"].items():
                        d["vendor"] = self.vendor(m)
                        d["type"] = self.guess_type(d)
                self.log("info", "Downloaded the full device-maker list - names should be more detailed now.")
        except Exception:
            pass

    # ---- API
    def state(self):
        with self.lock:
            dismissed = set(self.data["dismissed"])
            alerts = [a for a in list(self.scan_alerts.values()) + list(self.conn_alerts.values())
                      if a["key"] not in dismissed]
            alerts.sort(key=lambda a: SEV_RANK[a["level"]])
            devs = []
            for d in self.data["devices"].values():
                x = dict(d)
                x["port_info"] = [{"port": p, "name": PORTS[p][0], "level": PORTS[p][1], "why": PORTS[p][2]}
                                  for p in d.get("ports", []) if p in PORTS]
                devs.append(x)
            devs.sort(key=lambda d: (not d.get("online"), not d.get("is_gateway"),
                                     not d.get("is_self"), ip_key(d.get("ip", "0.0.0.0"))))
            return {
                "app": APP_NAME, "version": VERSION, "now": time.time(),
                "lip": self.lip, "gw": self.gw, "net": self.net,
                "scanning": self.scanning, "progress": self.progress, "phase": self.phase,
                "last_scan": self.last_scan, "next_scan": self.next_scan, "interval": SCAN_INTERVAL,
                "first_scan_done": self.data["first_scan_done"], "scan_count": self.scan_count,
                "alerts": alerts, "devices": devs, "events": self.events[-120:][::-1],
                "connections": self.conn_summary, "dismissed_count": len(dismissed),
            }

    def action(self, a):
        t = a.get("type")
        with self.lock:
            devs = self.data["devices"]
            if t == "scan":
                self.wake.set()
            elif t == "trust" and a.get("mac") in devs:
                d = devs[a["mac"]]
                d["trusted"] = bool(a.get("value", True))
                if d["trusted"] and a["mac"] in self.data["new_devices"]:
                    self.data["new_devices"].remove(a["mac"])
                self.scan_alerts.pop(f"new:{a['mac']}", None)
                self.log("info", f"{self.label(d)} marked {'trusted' if d['trusted'] else 'not trusted'}")
            elif t == "trust_all":
                for m, d in devs.items():
                    if d.get("online"):
                        d["trusted"] = True
                        self.scan_alerts.pop(f"new:{m}", None)
                self.data["new_devices"] = []
                self.log("info", "All online devices marked trusted")
            elif t == "rename" and a.get("mac") in devs:
                devs[a["mac"]]["name"] = str(a.get("name", ""))[:60]
            elif t == "forget" and a.get("mac") in devs:
                self.log("info", f"Forgot {self.label(devs[a['mac']])}")
                del devs[a["mac"]]
                if a["mac"] in self.data["new_devices"]:
                    self.data["new_devices"].remove(a["mac"])
            elif t == "dismiss" and a.get("key"):
                k = a["key"]
                if k.startswith("new:"):
                    m = k[4:]
                    if m in self.data["new_devices"]:
                        self.data["new_devices"].remove(m)
                    self.scan_alerts.pop(k, None)
                elif k not in self.data["dismissed"]:
                    self.data["dismissed"].append(k)
            elif t == "undismiss_all":
                self.data["dismissed"] = []
            elif t == "accept_gateway":
                self.data["gateway_macs"][a["gw"]] = a["mac"]
                self.scan_alerts = {k: v for k, v in self.scan_alerts.items() if not k.startswith("gwchange:")}
                self.log("info", f"Accepted {a['mac']} as your router")
            elif t == "subnet":
                s = str(a.get("value", "")).strip()
                if s:
                    ipaddress.ip_network(s, strict=False)
                self.data["subnet"] = s
                self.wake.set()
            elif t == "quit":
                self.save()
                threading.Timer(0.3, lambda: os._exit(0)).start()
            self.save()
        return {"ok": True}


SENTRY = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(UI_FILE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception:
                self._send(500, "index.html missing", "text/plain")
        elif self.path.startswith("/ns.js"):
            # lets "Open NetSentry.html" on the My Apps menu find us
            self._send(200, f"(window.__netsentry=window.__netsentry||[]).push({PORT});", "application/javascript")
        elif self.path.startswith("/api/state"):
            self._send(200, json.dumps(SENTRY.state()))
        else:
            self._send(404, "{}")

    def do_POST(self):
        if self.path != "/api/action":
            return self._send(404, "{}")
        # only accept requests from our own page
        origin = self.headers.get("Origin", "")
        if origin and not origin.startswith((f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")):
            return self._send(403, '{"error":"forbidden"}')
        try:
            n = int(self.headers.get("Content-Length", 0))
            a = json.loads(self.rfile.read(n) or b"{}")
            self._send(200, json.dumps(SENTRY.action(a)))
        except Exception as e:
            self._send(400, json.dumps({"error": str(e)}))


def main():
    global SENTRY
    url = f"http://{HOST}:{PORT}"
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print(f"{APP_NAME} already seems to be running - opening {url}")
        webbrowser.open(url)
        return
    SENTRY = Sentry()
    for target in (SENTRY.scan_loop, SENTRY.conn_loop, SENTRY.oui_loop):
        threading.Thread(target=target, daemon=True).start()
    print(f"{APP_NAME} {VERSION} running at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        SENTRY.save()


if __name__ == "__main__":
    main()
