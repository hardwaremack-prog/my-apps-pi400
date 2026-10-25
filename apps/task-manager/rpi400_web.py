#!/usr/bin/env python3
"""
RPi400 Task Manager, web page for the desktop editions
Lets phones and PCs on the same network use the Task Manager in a browser. It runs as a small web server,
so you can watch and control the Pi from any phone or laptop on the same network.

  http://<pi address>:8090      (on its own hotspot: http://192.168.1.250:8090)

Anyone on the network can look at the stats. Anything that changes the Pi
(hotspot, reboot, overclock, ending programs, Bluetooth...) needs the control password.
Privileged actions go through /usr/local/bin/rpi400-helper, exactly like the desktop editions.

No Tk, no desktop needed: Python standard library + psutil (+ qrcode, optional).
Set the password with the Web page switch in the Task Manager, or:   rpi400-web --set-password
"""
import os
import re
import sys
import csv
import json
import glob
import time
import hmac
import select
import shutil
import socket
import hashlib
import secrets
import threading
import subprocess
from collections import deque
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    import psutil
except ImportError:
    sys.stderr.write("psutil is missing. Install it with:  sudo apt install python3-psutil\n")
    sys.exit(1)
try:
    import qrcode
except Exception:
    qrcode = None

APP_NAME = "RPi400 Task Manager"
VERSION = "1.0"
HELPER = "/usr/local/bin/rpi400-helper"
PORT = int(os.environ.get("RZ_PORT", "8090"))
AP_CON = "RPI400-OPEN"            # NetworkManager's name for the hotspot profile (never changes)
AP_SSID_DEFAULT = "RPI400-OPEN"   # Wi-Fi name and password; can be changed on the Hotspot tab
AP_PASS_DEFAULT = "fifty-seven57"
AP_SSID, AP_PASS = AP_SSID_DEFAULT, AP_PASS_DEFAULT
AP_IP = "192.168.1.250"
SHELF_URL = f"http://{AP_IP}:8080/"   # The Shelf's usual address on the hotspot (see shelf_url)
CONF = "/etc/rpi400-taskmgr.conf"
UPDATE_LOG = "/var/log/rpi400-update.log"      # written by "rpi400-helper update", readable by everyone
UPDATE_STATE = "/run/rpi400-update.state"      # running|done|failed  start  end  reboot-needed
CFG_DIR = os.path.expanduser(os.environ.get("RZ_CFG_DIR", "~/.config/rpi400-taskmgr"))
AUTH_FILE = os.path.join(CFG_DIR, "web-password.json")
STABILITY_MARK = os.path.join(CFG_DIR, "stability-running.json")
LOG_DIR = os.path.expanduser(os.environ.get("RZ_LOG_DIR", "~/Desktop"))
VISITOR_LOG = "RPI400-OPEN visitor log.csv"
LOG_WRITE = os.environ.get("RZ_LOG_WRITE", "0") != "0"   # the desktop app writes the log   # off when a desktop app on the same Pi writes the log
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
CFG_PATH = "/boot/firmware/config.txt" if os.path.exists("/boot/firmware/config.txt") else "/boot/config.txt"
OC_BEGIN = "# >>> rpi400-taskmgr overclock >>>"
OC_END = "# <<< rpi400-taskmgr overclock <<<"
def _is_pi5():
    try:
        with open("/proc/device-tree/compatible", "rb") as f:
            return b"bcm2712" in f.read()
    except OSError:
        return False


OC_PRESETS = ([("stock", "Stock", "stock 2.4 GHz"), ("2600", "2.6", "2.6 GHz"), ("2800", "2.8", "2.8 GHz"),
               ("3000", "3.0", "3.0 GHz")]
              if _is_pi5() else
              [("stock", "Stock", "stock speed"), ("2000", "2.0 GHz", "2.0 GHz"), ("2147", "2.15", "2.15 GHz")])
SESSION_HOURS = 12


# ---------------------------------------------------------------- small helpers
def run(cmd, timeout=8):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out"
    except Exception as e:  # noqa
        return 1, str(e)


def run_helper(*args, timeout=120):
    if not os.path.exists(HELPER):
        return 1, "The root helper isn't installed yet. Run:  sudo bash install.sh"
    return run(["sudo", "-n", HELPER, *args], timeout)


HAS_VCGEN = shutil.which("vcgencmd") is not None


def vcgen(*args):
    if not HAS_VCGEN:
        return ""
    rc, out = run(["vcgencmd", *args], 3)
    return out if rc == 0 else ""


def read_file(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def fmt_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def short_pair(used, total):
    """'151 / 427 MB' style text that fits under a gauge."""
    if total >= 2 * 1024 ** 3:
        return f"{used / 1024 ** 3:.1f} / {total / 1024 ** 3:.1f} GB"
    return f"{used / 1024 ** 2:.0f} / {total / 1024 ** 2:.0f} MB"


def cpu_temp():
    try:
        temps = getattr(psutil, "sensors_temperatures", lambda: {})()
        for key in ("cpu_thermal", "cpu-thermal", "coretemp"):
            if temps.get(key):
                return temps[key][0].current
    except Exception:
        pass
    raw = read_file("/sys/class/thermal/thermal_zone0/temp")
    return int(raw) / 1000 if raw.isdigit() else None


def read_oc():
    txt = read_file(CFG_PATH)
    m = re.search(re.escape(OC_BEGIN) + r"(.*?)" + re.escape(OC_END), txt, re.S)
    if m:
        f = re.search(r"arm_freq=(\d+)", m.group(1))
        return f"{f.group(1)} MHz (overclocked)" if f else "Overclocked"
    if re.search(r"^\s*arm_freq\s*=", txt, re.M):
        return "Custom (set by hand in config.txt)"
    return "Stock"


def listening_ports():
    try:
        return {c.laddr.port for c in psutil.net_connections("inet") if c.status == psutil.CONN_LISTEN}
    except Exception:
        return set()


def load_ap_settings():
    """Pick up a changed hotspot name/password (the helper keeps them in /etc/rpi400-taskmgr.conf)."""
    global AP_SSID, AP_PASS
    conf = read_file(CONF)
    vals = {k: (re.findall(rf"^{k}=(.*)$", conf, re.M) or [""])[-1] for k in ("AP_SSID", "AP_PASS")}
    AP_SSID = vals["AP_SSID"] or AP_SSID_DEFAULT
    AP_PASS = vals["AP_PASS"] or AP_PASS_DEFAULT


def ap_keeps_mobile_data():
    return not re.search(r"^AP_SHARE=1", read_file(CONF), re.M)


def wifi_qr_text(ssid, password):
    esc = lambda v: re.sub(r'([\\;,:"])', r"\\\1", v)
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"


def ap_text_problem(ssid, password):
    """Same rules as the helper. Returns a message, or '' when both are fine."""
    if not isinstance(ssid, str) or not isinstance(password, str):
        return "Type a network name and a password."
    if not (1 <= len(ssid) <= 32) or ssid != ssid.strip() or not re.fullmatch(r"[ -~]+", ssid):
        return "The network name must be 1 to 32 letters, numbers or symbols, with no spaces at the start or end."
    if not (8 <= len(password) <= 63) or not re.fullmatch(r"[ -~]+", password):
        return "The password must be 8 to 63 letters, numbers or symbols."
    return ""


def shelf_url(ports=None):
    """The Shelf listens on 8080 unless it was set up on port 80."""
    if ports is None:
        ports = listening_ports()
    return f"http://{AP_IP}/" if 80 in ports and 8080 not in ports else f"http://{AP_IP}:8080/"


def current_user():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, ImportError):
        return os.environ.get("USER") or "?"


def update_state():
    parts = read_file(UPDATE_STATE).split()
    st = parts[0] if parts else "idle"
    nums = [int(x) if x.isdigit() else 0 for x in (parts[1:4] + ["0", "0", "0"])[:3]]
    if st == "running" and nums[0] and time.time() - nums[0] > 3 * 3600:
        st = "failed"   # the Pi was restarted or the update was stopped part-way
    log = read_file(UPDATE_LOG).splitlines()
    return {"state": st, "start": nums[0], "end": nums[1], "reboot": nums[2] == 1, "log": "\n".join(log[-200:])}


def led_blink_enabled():
    return not re.search(r"^LED_BLINK=0", read_file(CONF), re.M)


# ---------------------------------------------------------------- shared with the desktop editions
# (copied unchanged from rpi400_taskmgr.py by make-zero.py)
def fmt_dur(sec):
    sec = int(max(0, sec))
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s" if m else f"{s}s"


def signal_words(dbm):
    if dbm is None:
        return ""
    return "excellent" if dbm >= -55 else "good" if dbm >= -67 else "fair" if dbm >= -75 else "weak"


def strip_ansi(s):
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]|[\x01\x02]", "", s)


def hotspot_clients(dev="wlan0"):
    """Devices joined to the hotspot, as dicts: mac, ip, name, signal, rx, tx, secs.
    Uses the Wi-Fi chip's own station list (exact join/leave), plus DHCP leases for names."""
    leases = {}
    for path in glob.glob("/var/lib/NetworkManager/dnsmasq-*.leases"):
        for line in read_file(path).splitlines():
            parts = line.split()
            if len(parts) >= 4:
                leases[parts[1].lower()] = (parts[2], "" if parts[3] == "*" else parts[3])
    neigh = {}
    _, out = run(["ip", "neigh", "show", "dev", dev], 3)
    for line in out.splitlines():
        parts = line.split()
        if parts and ":" not in parts[0] and "lladdr" in parts and parts[-1] not in ("FAILED", "INCOMPLETE"):
            neigh[parts[parts.index("lladdr") + 1].lower()] = parts[0]

    clients = []
    rc, out = run(["iw", "dev", dev, "station", "dump"], 3)
    if rc == 0:
        for block in re.split(r"^Station ", out, flags=re.M)[1:]:
            mac = block.split()[0].lower()

            def num(key, block=block):
                m = re.search(rf"^\s*{key}:\s*(-?\d+)", block, re.M)
                return int(m.group(1)) if m else None

            ip, name = leases.get(mac, ("", ""))
            clients.append(dict(mac=mac, ip=neigh.get(mac) or ip, name=name, signal=num("signal"),
                                rx=num("rx bytes") or 0, tx=num("tx bytes") or 0, secs=num("connected time")))
    else:  # no iw: fall back to the neighbour table
        for mac, ip in neigh.items():
            clients.append(dict(mac=mac, ip=ip, name=leases.get(mac, ("", ""))[1],
                                signal=None, rx=0, tx=0, secs=None))
    return clients


def bt_pair(mac, notify=lambda kind, text: None):
    """Pair, trust and connect a device with bluetoothctl, answering its prompts as they come.
    notify(kind, text) reports progress: kind is "passkey" (type this on the new keyboard),
    "confirm" (auto-confirmed code), or "info"."""
    try:
        p = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
    except FileNotFoundError:
        return 1, "bluetoothctl not found"
    fd = p.stdout.fileno()
    st = {"paired": False, "connected": False, "failed": "", "pending": ""}
    seen = []

    def send(cmd):
        try:
            p.stdin.write((cmd + "\n").encode())
            p.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def handle(line):
        seen.append(line.strip())
        if re.search(r"Confirm passkey (\d+)", line):
            notify("confirm", re.search(r"Confirm passkey (\d+)", line).group(1))
            send("yes")
        elif re.search(r"Request confirmation|Authorize service|Request authorization|\(yes/no\)", line):
            send("yes")
        elif re.search(r"Passkey:\s*(\d{4,6})", line):
            notify("passkey", re.search(r"Passkey:\s*(\d{4,6})", line).group(1))
        elif "Enter PIN code" in line:
            notify("info", "The device asked for a PIN, so 0000 was sent.")
            send("0000")
        if "Pairing successful" in line or "AlreadyExists" in line:
            st["paired"] = True
        if "Connection successful" in line:
            st["connected"] = True
        m = re.search(r"Failed to (pair|connect)[^:]*:?\s*(.*)", line)
        if m:
            st["failed"] = f"Failed to {m.group(1)}: {m.group(2).strip() or 'no details'}"

    def run_until(done, secs):
        end = time.time() + secs
        while time.time() < end and not done():
            if p.poll() is not None:
                break
            r, _, _ = select.select([fd], [], [], 0.25)
            if not r:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            st["pending"] += strip_ansi(chunk.decode(errors="replace")).replace("\r", "\n")
            lines = st["pending"].split("\n")
            st["pending"] = lines.pop()
            for ln in lines:
                handle(ln)
            if re.search(r"(\(yes/no\)|PIN code|passkey[^:]*)\s*:\s*$", st["pending"], re.I):
                handle(st["pending"])
                st["pending"] = ""

    try:
        for cmd in ("agent KeyboardDisplay", "default-agent", "power on", "pairable on"):
            send(cmd)
        run_until(lambda: False, 1.5)
        send(f"pair {mac}")
        run_until(lambda: st["paired"] or st["failed"], 45)
        if st["paired"]:
            st["failed"] = ""
            send(f"trust {mac}")
            run_until(lambda: False, 1.5)
            send(f"connect {mac}")
            run_until(lambda: st["connected"] or st["failed"], 20)
        send("quit")
        run_until(lambda: False, 1)
    finally:
        try:
            p.kill()
        except Exception:
            pass
    if st["paired"] and st["connected"]:
        return 0, "Paired and connected."
    if st["paired"]:
        return 0, "Paired. It should connect as soon as you use it (press a key or click)."
    return 1, st["failed"] or "The device didn't respond. Make sure it's still in pairing mode and try again."


STRESS_WORKER = r"""
import hashlib, sys, time
end = time.time() + float(sys.argv[1])
data = bytes(range(256)) * 4096
ref = hashlib.sha256(data).hexdigest()
ref_x = None
rounds = 0
while time.time() < end:
    for _ in range(8):
        if hashlib.sha256(data).hexdigest() != ref:
            print("MISMATCH", flush=True); sys.exit(3)
    x = 1
    for i in range(30000):
        x = (x * 1103515245 + 12345 + i) & 0x7FFFFFFF
    if ref_x is None:
        ref_x = x
    elif x != ref_x:
        print("MISMATCH", flush=True); sys.exit(3)
    rounds += 1
print(rounds, flush=True)
"""


def vpn_status():
    """Tailscale, for the Remote access controls: installed, state, address, name, sign-in link, devices online."""
    if not shutil.which("tailscale"):
        return {"installed": False, "state": "", "ip": "", "name": "", "auth_url": "", "peers": 0, "tailnet": ""}
    rc, out = run(["tailscale", "status", "--json"], 4)
    try:
        d = json.loads(out[out.index("{"):]) if "{" in out else {}
    except ValueError:
        d = {}
    me = d.get("Self") or {}
    ips = [ip for ip in (me.get("TailscaleIPs") or []) if "." in ip]
    peers = d.get("Peer") or {}
    return {"installed": True, "state": d.get("BackendState") or "Stopped",
            "ip": ips[0] if ips else "", "name": (me.get("DNSName") or "").rstrip("."),
            "auth_url": d.get("AuthURL") or "", "peers": sum(1 for p in peers.values() if p.get("Online")),
            "tailnet": (d.get("CurrentTailnet") or {}).get("Name", "")}


# ---------------------------------------------------------------- password + sessions
def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 120_000).hex()


def set_password(pw):
    os.makedirs(CFG_DIR, exist_ok=True)
    salt = secrets.token_bytes(16)
    tmp = AUTH_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"salt": salt.hex(), "hash": _hash(pw, salt)}, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTH_FILE)


def check_password(pw):
    try:
        with open(AUTH_FILE) as f:
            d = json.load(f)
        return hmac.compare_digest(_hash(pw, bytes.fromhex(d["salt"])), d["hash"])
    except (OSError, ValueError, KeyError):
        return False


class Sessions:
    def __init__(self):
        self.tokens = {}
        self.fails = {}
        self.lock = threading.Lock()

    def new(self):
        t = secrets.token_urlsafe(32)
        with self.lock:
            self.tokens[t] = time.time() + SESSION_HOURS * 3600
        return t

    def valid(self, t):
        with self.lock:
            exp = self.tokens.get(t or "")
            if exp and exp > time.time():
                return True
            self.tokens.pop(t or "", None)
            return False

    def drop(self, t):
        with self.lock:
            self.tokens.pop(t or "", None)

    def locked_out(self, ip):
        with self.lock:
            recent = [x for x in self.fails.get(ip, []) if time.time() - x < 300]
            self.fails[ip] = recent
            return len(recent) >= 5

    def failed(self, ip):
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())


SESSIONS = Sessions()


# ---------------------------------------------------------------- visitor log (no desktop: kept in ~/visitor-logs)
class VisitorLog:
    GRACE = 12
    FIELDS = ["Date", "Time", "Event", "Device name", "IP address", "MAC address", "Stayed", "Data used"]

    def __init__(self):
        self.active, self.history, self.ap_on, self.error = {}, [], None, ""
        self.path = os.path.join(LOG_DIR, VISITOR_LOG)
        self._mtime = None
        self.refresh(force=True)

    def refresh(self, force=False):
        """Read the log file again. When another program writes it, this keeps the page up to date."""
        if LOG_WRITE and not force:
            return
        try:
            mt = os.path.getmtime(self.path)
            if mt == self._mtime and not force:
                return
            with open(self.path, newline="") as f:
                self.history = [r for r in csv.DictReader(f) if r.get("Event")][-300:]
            self._mtime = mt
        except (OSError, csv.Error):
            pass

    def update(self, clients, ap_on, now):
        pending = []
        if ap_on != self.ap_on:
            if self.ap_on is None:
                if ap_on:
                    pending.append((now, "Logging started", {}))
            else:
                pending.append((now, "Hotspot started" if ap_on else "Hotspot stopped", {}))
            self.ap_on = ap_on
        seen = set()
        for c in clients:
            mac = c["mac"]
            seen.add(mac)
            a = self.active.get(mac)
            if a is None:
                first = now - c["secs"] if c.get("secs") is not None else now
                a = self.active[mac] = dict(c, first=first, last=now)
                pending.append((first, "Joined", a))
            else:
                for k in ("ip", "name", "signal", "rx", "tx", "secs"):
                    if c.get(k) not in (None, ""):
                        a[k] = c[k]
                a["last"] = now
        for mac, a in list(self.active.items()):
            if mac not in seen and now - a["last"] > self.GRACE:
                del self.active[mac]
                pending.append((a["last"], "Left", a))
        for when, event, a in sorted(pending, key=lambda p: p[0]):
            self._record(event, a, when)

    def _record(self, event, a, when):
        if not LOG_WRITE:
            return   # the desktop app on this Pi writes the log; refresh() reads it
        t = time.localtime(when)
        row = {"Date": time.strftime("%Y-%m-%d", t), "Time": time.strftime("%H:%M:%S", t), "Event": event,
               "Device name": a.get("name") or ("" if not a else "(no name)"),
               "IP address": a.get("ip", ""), "MAC address": a.get("mac", ""),
               "Stayed": fmt_dur(a["last"] - a["first"]) if event == "Left" else "",
               "Data used": fmt_bytes(a.get("rx", 0) + a.get("tx", 0)) if event == "Left" else ""}
        self.history.append(row)
        del self.history[:-300]
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            new = not os.path.exists(self.path)
            with open(self.path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self.FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
            self.error = ""
        except OSError as e:
            self.error = f"Couldn't write the visitor log: {e}"

    def active_list(self, now):
        return [dict(name=a.get("name") or "(no name)", ip=a.get("ip") or "", mac=a["mac"], signal=a.get("signal"),
                     quality=signal_words(a.get("signal")),
                     joined=time.strftime("%I:%M %p", time.localtime(a["first"])).lstrip("0"),
                     duration=fmt_dur(now - a["first"]), data=fmt_bytes(a.get("rx", 0) + a.get("tx", 0)))
                for a in sorted(self.active.values(), key=lambda x: x["first"])]

    def report(self):
        now = time.time()
        lines = [f"{AP_SSID} hotspot — visitor report", time.strftime("Saved %A %B %d, %Y at %I:%M %p"), "",
                 f"CONNECTED RIGHT NOW ({len(self.active)})"]
        if not self.active:
            lines.append("  nobody")
        for a in self.active_list(now):
            lines.append(f"  {a['name']:<24} {a['ip'] or '—':<16} {a['mac']}   since {a['joined']} "
                         f"({a['duration']}, {a['data']})")
        lines += ["", "ACTIVITY (most recent last)"]
        for r in self.history:
            extra = f"  stayed {r['Stayed']}, used {r['Data used']}" if r.get("Stayed") else ""
            who = f"{r.get('Device name') or ''} {r.get('IP address') or ''} {r.get('MAC address') or ''}".strip()
            lines.append(f"  {r['Date']} {r['Time']}  {r['Event']:<8} {who}{extra}")
        joins = [r for r in self.history if r["Event"] == "Joined"]
        lines += ["", f"Total joins in this log: {len(joins)} · different devices: "
                      f"{len({r['MAC address'] for r in joins})}"]
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- background jobs (actions that take a while)
class Jobs:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, label, fn):
        jid = secrets.token_hex(6)
        job = {"id": jid, "label": label, "done": False, "rc": None, "out": "", "events": [], "started": time.time()}
        with self.lock:
            self.jobs[jid] = job
            for k in [k for k, j in self.jobs.items() if j["done"] and time.time() - j["started"] > 900]:
                del self.jobs[k]

        def work():
            try:
                rc, out = fn(job)
            except Exception as e:  # noqa
                rc, out = 1, str(e)
            job.update(rc=rc, out=out, done=True)
            LOG.add(("✔ " if rc == 0 else "✖ ") + label + (f": {out.splitlines()[0]}" if out else ""), rc == 0)

        threading.Thread(target=work, daemon=True).start()
        return jid

    def get(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
            return dict(j, events=list(j["events"])) if j else None


class ActivityLog:
    def __init__(self):
        self.items = deque(maxlen=60)

    def add(self, text, ok=None):
        self.items.append({"t": time.strftime("%H:%M:%S"), "text": text, "ok": ok})


JOBS, LOG = Jobs(), ActivityLog()


# ---------------------------------------------------------------- stability test (runs on the Pi, watched from the page)
class Stability:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False}
        self.procs = []
        self.warning = self._check_leftover()

    def _check_leftover(self):
        if not os.path.exists(STABILITY_MARK):
            return ""
        try:
            with open(STABILITY_MARK) as f:
                info = json.load(f)
        except (OSError, ValueError):
            info = {}
        if abs(info.get("boot", 0) - psutil.boot_time()) < 5 and psutil.pid_exists(info.get("pid", -1)):
            return ""
        try:
            os.remove(STABILITY_MARK)
        except OSError:
            pass
        msg = (f"The last stability test, at {info.get('speed', 'the overclock')}, never finished. The Pi probably "
               "froze or restarted under load, so that speed isn't stable on your board. Choose a lower speed.")
        LOG.add("✖ " + msg, False)
        return msg

    def start(self, minutes):
        with self.lock:
            if self.state.get("running"):
                return False
            speed = read_oc().replace(" (overclocked)", "")
            secs = max(6, min(int(minutes * 60), 1800))
            self.state = {"running": True, "speed": speed, "secs": secs, "t0": time.time(), "temps": [],
                          "max": 0.0, "seen": 0, "errors": False, "verdict": "", "level": "", "clock": None}
            try:
                os.makedirs(CFG_DIR, exist_ok=True)
                with open(STABILITY_MARK, "w") as f:
                    json.dump({"speed": speed, "started": time.time(), "pid": os.getpid(),
                               "boot": psutil.boot_time()}, f)
            except OSError:
                pass
            n = psutil.cpu_count() or 4
            self.procs = [subprocess.Popen([sys.executable, "-c", STRESS_WORKER, str(secs)],
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                          for _ in range(n)]
        LOG.add(f"▶ Stability test started ({secs // 60 or 1} min at {speed})")
        threading.Thread(target=self._watch, daemon=True).start()
        return True

    def _watch(self):
        while True:
            time.sleep(1)
            with self.lock:
                st = self.state
                if not st.get("running"):
                    return
                t = cpu_temp() or 0.0
                st["temps"].append(round(t, 1))
                st["temps"] = st["temps"][-180:]
                st["max"] = max(st["max"], t)
                fr = psutil.cpu_freq()
                st["clock"] = round(fr.current) if fr else None
                m = re.search(r"0x([0-9a-fA-F]+)", vcgen("get_throttled"))
                st["seen"] |= (int(m.group(1), 16) & 0xF) if m else 0
                done = [p for p in self.procs if p.poll() is not None]
                if any(p.returncode != 0 for p in done):
                    st["errors"] = True
                if st["errors"] or len(done) == len(self.procs):
                    self._finish(False)
                    return

    def stop(self):
        with self.lock:
            if self.state.get("running"):
                self._finish(True)

    def _finish(self, stopped):   # call with lock held
        st = self.state
        st["running"] = False
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        self.procs = []
        try:
            os.remove(STABILITY_MARK)
        except OSError:
            pass
        hot = f"Hottest {st['max']:.0f}°C."
        if stopped:
            st["verdict"], st["level"] = "Stopped. No verdict.", "dim"
        elif st["errors"]:
            st["verdict"], st["level"] = (f"✖ FAILED: the CPU got wrong answers at {st['speed']}. This speed isn't "
                                          "stable on your board. Choose a lower speed and reboot."), "bad"
        elif st["seen"] & 0x1:
            st["verdict"], st["level"] = (f"⚠ POWER PROBLEM: the supply dipped under load. {hot} Use a stronger "
                                          "5 V 2.5 A supply or battery bank before trusting this speed."), "warn"
        elif st["seen"] & 0xE:
            st["verdict"], st["level"] = (f"⚠ STABLE, BUT HOT: no errors, but it got hot enough to slow itself "
                                          f"down. {hot} Better cooling helps."), "warn"
        else:
            st["verdict"], st["level"] = f"✔ PASSED at {st['speed']}. No errors, no throttling. {hot}", "ok"
        if not stopped:
            LOG.add("Stability test: " + st["verdict"], st["level"] == "ok")

    def snapshot(self):
        with self.lock:
            st = dict(self.state)
            if st.get("running"):
                st["left"] = max(0, int(st["secs"] - (time.time() - st["t0"])))
                st["progress"] = min(100, (time.time() - st["t0"]) * 100 / st["secs"])
            st["temps"] = list(st.get("temps", []))
            st["warning"] = self.warning
            return st


# ---------------------------------------------------------------- the poller: gathers everything every 2 seconds
class Poller(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.data = {}
        self.cpu_hist = deque([0.0] * 60, maxlen=60)
        self.net_hist = deque([(0.0, 0.0)] * 60, maxlen=60)
        self.visitors = VisitorLog()
        self.prev_net = psutil.net_io_counters(pernic=True)
        self.prev_disk = psutil.disk_io_counters()
        self.prev_t = time.time()
        self.n = 0
        self.vpn_now = True
        psutil.cpu_percent(percpu=True)

    def get(self):
        with self.lock:
            return dict(self.data)

    def run(self):
        while True:
            try:
                d = self.collect()
                with self.lock:
                    self.data.update(d)
            except Exception as e:  # noqa
                LOG.add(f"poller: {e}", False)
            self.n += 1
            time.sleep(2)

    def collect(self):
        now = time.time()
        dt = max(0.5, now - self.prev_t)
        d = {}
        per = psutil.cpu_percent(percpu=True)
        cpu = sum(per) / len(per) if per else 0.0
        self.cpu_hist.append(cpu)
        d["cpu"], d["cores"], d["cpu_hist"] = round(cpu, 1), [round(x) for x in per], list(self.cpu_hist)
        fr = psutil.cpu_freq()
        d["clock"], d["clock_max"] = (round(fr.current), round(fr.max)) if fr else (None, None)
        d["temp"] = cpu_temp()
        vm, sw, du = psutil.virtual_memory(), psutil.swap_memory(), psutil.disk_usage("/")
        d["mem"] = {"pct": vm.percent, "short": short_pair(vm.total - vm.available, vm.total)}
        d["swap"] = {"pct": sw.percent, "short": short_pair(sw.used, sw.total)} if sw.total else None
        d["disk"] = {"pct": du.percent, "free": fmt_bytes(du.free)}
        dio = psutil.disk_io_counters()
        if dio and self.prev_disk:
            d["disk_read"] = fmt_bytes(max(0, dio.read_bytes - self.prev_disk.read_bytes) / dt) + "/s"
            d["disk_write"] = fmt_bytes(max(0, dio.write_bytes - self.prev_disk.write_bytes) / dt) + "/s"
        self.prev_disk = dio
        d["uptime"] = fmt_dur(now - psutil.boot_time())
        d["load"] = [round(x, 2) for x in os.getloadavg()]
        d["nprocs"] = len(psutil.pids())

        m = re.search(r"([\d.]+)V", vcgen("measure_volts", "core"))
        d["vcore"] = float(m.group(1)) if m else None
        m = re.search(r"0x([0-9a-fA-F]+)", vcgen("get_throttled"))
        d["throttled"] = int(m.group(1), 16) if m else None
        d["governor"] = read_file("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        d["oc"] = read_oc()

        cur = psutil.net_io_counters(pernic=True)
        stats, addrs = psutil.net_if_stats(), psutil.net_if_addrs()
        nics, trx, ttx = [], 0.0, 0.0
        for n in sorted(cur):
            if n == "lo" or n.startswith(("docker", "veth", "br-", "virbr", "ifb", "dummy", "sit", "p2p-dev")):
                continue
            c, p = cur[n], self.prev_net.get(n, cur[n])
            rx, tx = max(0, c.bytes_recv - p.bytes_recv) / dt, max(0, c.bytes_sent - p.bytes_sent) / dt
            trx, ttx = trx + rx, ttx + tx
            ip = next((a.address for a in addrs.get(n, []) if a.family == socket.AF_INET), "")
            nics.append({"name": n, "kind": "Wi-Fi" if n.startswith("wl") else "USB" if n.startswith("usb") else
                         "Ethernet" if n.startswith(("eth", "en")) else "Network",
                         "up": read_file(f"/sys/class/net/{n}/carrier") == "1", "ip": ip,
                         "rx": rx, "tx": tx, "rx_rate": fmt_bytes(rx) + "/s", "tx_rate": fmt_bytes(tx) + "/s",
                         "rx_total": fmt_bytes(c.bytes_recv), "tx_total": fmt_bytes(c.bytes_sent)})
        self.prev_net, self.prev_t = cur, now
        self.net_hist.append((trx, ttx))
        d["nics"], d["net_hist"] = nics, list(self.net_hist)

        _, out = run(["iw", "dev", "wlan0", "link"], 3)
        m = re.search(r"SSID: (.+)", out)
        d["ssid"] = m.group(1).strip() if m else ""
        m = re.search(r"signal: (-?\d+) dBm", out)
        d["signal"] = int(m.group(1)) if m else None
        rc, out = run(["nmcli", "-t", "-f", "NAME", "con", "show", "--active"], 4)
        d["nm"] = rc == 0
        d["ap_on"] = AP_CON in out.splitlines() if rc == 0 else False
        load_ap_settings()
        d["ap_ssid"] = AP_SSID
        d["shelf_url"] = shelf_url()
        d["ap_keep_data"] = ap_keeps_mobile_data()
        d["ap_band"] = "a" if re.search(r"^AP_BAND=a", read_file(CONF), re.M) else "bg"
        clients = hotspot_clients() if d["ap_on"] else []
        self.visitors.update(clients, d["ap_on"], now)
        d["people"] = len(self.visitors.active)

        ports = listening_ports()
        d["ssh"] = 22 in ports
        rc, out = run(["bluetoothctl", "show"], 3)
        d["bt_present"] = rc == 0 and "Controller" in out
        d["bt_powered"] = "Powered: yes" in out
        _, out = run(["bluetoothctl", "devices", "Connected"], 3)
        d["bt_connected"] = [l.split(" ", 2)[2] for l in out.splitlines()
                             if l.startswith("Device ") and len(l.split(" ", 2)) == 3]
        if self.n % 5 == 0:
            _, out = run(["systemctl", "is-enabled", "rpi400-fallback-ap"], 3)
            d["fallback"] = out.strip() == "enabled"
        d["led"] = led_blink_enabled()
        if self.n % 3 == 0 or self.vpn_now:
            self.vpn_now = False
            d["vpn"] = vpn_status()
        d["ips"] = [x["ip"] for x in nics if x["ip"]]
        return d


# ---------------------------------------------------------------- static facts
def board_info():
    load_ap_settings()
    model = read_file("/proc/device-tree/model").replace("\x00", "") or os.uname().machine
    osname = "Linux"
    for line in read_file("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            osname = line.split("=", 1)[1].strip('"')
    return {"model": model, "os": osname, "host": socket.gethostname(), "ncpu": psutil.cpu_count() or 1,
            "app": APP_NAME, "version": VERSION, "ssid": AP_SSID, "pass": AP_PASS, "ap_ip": AP_IP,
            "shelf": SHELF_URL, "port": PORT, "oc_presets": OC_PRESETS, "user": current_user()}


def qr_svg(text):
    if not qrcode:
        return None
    q = qrcode.QRCode(border=2)
    q.add_data(text)
    q.make(fit=True)
    m = q.get_matrix()
    n = len(m)
    rects = "".join(f'<rect x="{x}" y="{y}" width="1" height="1"/>' for y, row in enumerate(m)
                    for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><g fill="#000">{rects}</g></svg>')


def processes(sort="cpu", limit=150):
    ncpu = psutil.cpu_count() or 1
    rows = []
    for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent", "memory_info",
                                  "num_threads", "status", "cmdline"]):
        i = p.info
        name = i.get("name") or "?"
        cl = i.get("cmdline") or []
        if name.startswith(("python", "node", "bash", "sh", "perl")) and len(cl) > 1:
            arg = next((a for a in cl[1:] if not a.startswith("-")), None)
            if arg:
                name = f"{name} ({os.path.basename(arg)})"
        rss = i["memory_info"].rss if i.get("memory_info") else 0
        rows.append({"pid": i["pid"], "name": name, "user": i.get("username") or "",
                     "cpu": round((i.get("cpu_percent") or 0) / ncpu, 1), "mem": round(i.get("memory_percent") or 0, 1),
                     "rss": rss, "rss_h": fmt_bytes(rss), "threads": i.get("num_threads") or 0,
                     "status": i.get("status") or ""})
    key = {"pid": "pid", "name": "name", "user": "user", "cpu": "cpu", "mem": "mem", "rss": "rss",
           "threads": "threads"}.get(sort, "cpu")
    rev = key in ("cpu", "mem", "rss", "threads")
    rows.sort(key=lambda r: r[key].lower() if isinstance(r[key], str) else r[key], reverse=rev)
    return rows[:limit], len(rows)


# ---------------------------------------------------------------- actions (all need the password)
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def bt_scan(job):
    run_helper("bt-on", timeout=30)
    run(["bluetoothctl", "power", "on"], 5)
    job["events"].append({"kind": "info", "text": "Scanning for 10 seconds…"})
    run(["bluetoothctl", "--timeout", "10", "scan", "on"], 20)
    _, out = run(["bluetoothctl", "devices"], 5)
    _, pout = run(["bluetoothctl", "devices", "Paired"], 5)
    paired = {ln.split()[1].upper() for ln in pout.splitlines() if ln.startswith("Device ")}
    devs = []
    for ln in out.splitlines():
        parts = ln.split(" ", 2)
        if len(parts) == 3 and parts[0] == "Device":
            mac, name = parts[1].upper(), parts[2]
            devs.append({"mac": mac, "name": name, "paired": mac in paired,
                         "named": name.replace("-", ":").upper() != mac})
    devs.sort(key=lambda x: (x["paired"], not x["named"], x["name"].lower()))
    job["devices"] = devs
    return 0, f"Found {len(devs)} device{'s' if len(devs) != 1 else ''}"


def do_action(name, a):
    """Returns (job_id or None, error or None)."""
    simple = {"dropcaches": ("Clean & optimize memory", ["dropcaches"]),
              "bt-rescue": ("Bluetooth rescue", ["bt-rescue"]),
              "ap-up": ("Start hotspot", ["ap-up"]), "ap-down": ("Stop hotspot", ["ap-down"]),
              "led-on": ("Flash light while hotspot is on: ON", ["led", "on"]),
              "led-off": ("Flash light while hotspot is on: OFF", ["led", "off"]),
              "led-test": ("Light test (6 seconds)", ["led", "test", "6"]),
              "reboot": ("Reboot", ["reboot"]), "poweroff": ("Shut down", ["poweroff"]),
              "update": ("Update everything", ["update", "start"])}
    if name in simple:
        label, args = simple[name]
        return JOBS.start(label, lambda job: run_helper(*args)), None
    if name == "ap-set":
        if a.get("default"):
            ssid, pw = AP_SSID_DEFAULT, AP_PASS_DEFAULT
        else:
            ssid, pw = a.get("ssid"), a.get("password")
        problem = ap_text_problem(ssid, pw)
        if problem:
            return None, problem
        return JOBS.start(f"Hotspot name → {ssid}", lambda job: run_helper("ap-set", ssid, pw, timeout=60)), None
    if name == "ap-data":
        mode = a.get("mode")
        if mode not in ("keep", "share"):
            return None, "bad mode"
        return JOBS.start("Phones keep their mobile data: " + ("ON" if mode == "keep" else "OFF"),
                          lambda job: run_helper("ap-data", mode, timeout=60)), None
    if name == "gov":
        g = a.get("mode")
        if g not in ("powersave", "ondemand", "performance"):
            return None, "bad mode"
        return JOBS.start(f"CPU mode → {g}", lambda job: run_helper("gov", g)), None
    if name == "oc":
        prof = a.get("profile")
        if prof not in [p for p, _, _ in OC_PRESETS]:
            return None, "bad profile"
        return JOBS.start(f"Overclock → {dict((p, f) for p, _, f in OC_PRESETS)[prof]}",
                          lambda job: run_helper("oc", prof)), None
    if name == "kill":
        try:
            pid = int(a.get("pid"))
        except (TypeError, ValueError):
            return None, "bad pid"
        sig = "KILL" if a.get("force") else "TERM"
        if pid <= 1:
            return None, "bad pid"
        return JOBS.start(f"{'Force kill' if sig == 'KILL' else 'End'} PID {pid}",
                          lambda job: run_helper("kill", str(pid), sig)), None
    if name == "kick":
        mac = a.get("mac", "")
        if not MAC_RE.match(mac):
            return None, "bad MAC"
        return JOBS.start(f"Disconnect {mac}", lambda job: run_helper("kick", mac)), None
    if name in ("vpn-on", "vpn-down", "vpn-logout", "vpn-key"):
        def vpn_job(job):
            if name == "vpn-on":
                steps = [("vpn", "up")] if shutil.which("tailscale") else [("vpn", "install"), ("vpn", "up")]
            elif name == "vpn-key":
                steps = [("vpn", "install"), ("vpn", "key", str(a.get("key", "")).strip())]
            else:
                steps = [("vpn", name.split("-")[1])]
            res = (0, "")
            for st in steps:
                res = run_helper(*st, timeout=300)
                if res[0] != 0:
                    break
            POLLER.vpn_now = True
            out = res[1].strip().splitlines()[-1] if res[1].strip() else ""
            if out.startswith("LOGIN "):
                return 0, "Sign in once: scan the code on the page with your phone."
            return res
        if name == "vpn-key" and not re.match(r"^tskey-[A-Za-z0-9_-]+$", str(a.get("key", "")).strip()):
            return None, "That doesn't look like a Tailscale auth key (they start with tskey-)."
        label = {"vpn-on": "Tailscale on", "vpn-down": "Tailscale off", "vpn-logout": "Sign out of Tailscale",
                 "vpn-key": "Join Tailscale with an auth key"}[name]
        return JOBS.start(label, vpn_job), None
    if name == "bt-scan":
        return JOBS.start("Bluetooth scan", bt_scan), None
    if name == "bt-pair":
        mac = a.get("mac", "")
        if not MAC_RE.match(mac):
            return None, "bad MAC"
        return JOBS.start(f"Pair {mac}", lambda job: bt_pair(
            mac, notify=lambda kind, text: job["events"].append({"kind": kind, "text": text}))), None
    if name == "stability-start":
        try:
            minutes = float(a.get("minutes", 3))
        except (TypeError, ValueError):
            minutes = 3
        return None, (None if STABILITY.start(minutes) else "A test is already running")
    if name == "stability-stop":
        STABILITY.stop()
        return None, None
    if name == "dismiss-warning":
        STABILITY.warning = ""
        return None, None
    return None, "unknown action"


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "RPi400TaskManagerWeb/" + VERSION

    def log_message(self, *a):
        pass

    # -- helpers
    def _token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "rztm":
                return v
        return None

    def authed(self):
        return SESSIONS.valid(self._token())

    def send(self, code, body, ctype="application/json", extra=None):
        if not isinstance(body, (bytes, bytearray)):
            body = (json.dumps(body) if ctype == "application/json" else str(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json", "image/svg")) else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 10000:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    # -- GET
    def do_GET(self):
        url = urlparse(self.path)
        p, q = url.path, parse_qs(url.query)
        if p in ("/", "/index.html"):
            try:
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html", {"Content-Security-Policy":
                                     "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                     "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'"})
            except OSError:
                return self.send(500, "web/index.html is missing", "text/plain")
        if p == "/api/info":
            return self.send(200, dict(board_info(), authed=self.authed(), has_password=os.path.exists(AUTH_FILE)))
        if p == "/api/stats":
            d = POLLER.get()
            d["authed"] = self.authed()
            d["stability"] = STABILITY.snapshot()
            d["log"] = list(LOG.items)[-25:] if d["authed"] else []
            if d.get("vpn") and not d["authed"]:
                d["vpn"] = dict(d["vpn"], auth_url="", ip="", name="", tailnet="")
            return self.send(200, d)
        if p == "/api/procs":
            rows, total = processes(q.get("sort", ["cpu"])[0])
            return self.send(200, {"rows": rows, "total": total})
        if p == "/api/qr.svg":
            load_ap_settings()
            svg = qr_svg(wifi_qr_text(AP_SSID, AP_PASS))
            return self.send(200, svg, "image/svg+xml") if svg else self.send(404, "qrcode not installed", "text/plain")
        # -- password needed from here on
        if not self.authed():
            return self.send(403, {"error": "Log in first"})
        if p == "/api/update":
            return self.send(200, update_state())
        if p == "/api/visitors":
            v = POLLER.visitors
            v.refresh()
            return self.send(200, {"active": v.active_list(time.time()), "history": list(reversed(v.history))[:150],
                                   "error": v.error, "path": v.path})
        if p == "/api/visitors.csv":
            try:
                with open(POLLER.visitors.path, "rb") as f:
                    data = f.read()
            except OSError:
                data = (",".join(VisitorLog.FIELDS) + "\n").encode()
            return self.send(200, data, "text/csv", {"Content-Disposition": f'attachment; filename="{VISITOR_LOG}"'})
        if p == "/api/vpn-qr.svg":
            url = (POLLER.get().get("vpn") or {}).get("auth_url", "")
            svg = qr_svg(url) if url else None
            return self.send(200, svg, "image/svg+xml") if svg else self.send(404, "no sign-in pending", "text/plain")
        if p == "/api/report.txt":
            name = time.strftime("Hotspot visitors %Y-%m-%d %H%M.txt")
            POLLER.visitors.refresh()
            return self.send(200, POLLER.visitors.report(), "text/plain",
                             {"Content-Disposition": f'attachment; filename="{name}"'})
        if p.startswith("/api/job/"):
            j = JOBS.get(p.rsplit("/", 1)[1])
            return self.send(200, j) if j else self.send(404, {"error": "no such job"})
        return self.send(404, {"error": "not found"})

    # -- POST
    def do_POST(self):
        p = urlparse(self.path).path
        if self.headers.get("X-RZ") != "1":          # blocks cross-site form posts
            return self.send(403, {"error": "missing header"})
        a = self.body()
        if p == "/api/login":
            ip = self.client_address[0]
            if SESSIONS.locked_out(ip):
                return self.send(429, {"error": "Too many wrong tries. Wait a few minutes."})
            if not os.path.exists(AUTH_FILE):
                return self.send(400, {"error": "No password set yet. Switch the Web page on in the Task Manager on the Pi, or run: rpi400-web --set-password"})
            if check_password(str(a.get("password", ""))):
                t = SESSIONS.new()
                LOG.add(f"Logged in from {ip}", True)
                return self.send(200, {"ok": True}, extra={"Set-Cookie":
                                 f"rztm={t}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_HOURS * 3600}"})
            SESSIONS.failed(ip)
            time.sleep(1)
            return self.send(401, {"error": "Wrong password"})
        if p == "/api/logout":
            SESSIONS.drop(self._token())
            return self.send(200, {"ok": True}, extra={"Set-Cookie": "rztm=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        if not self.authed():
            return self.send(403, {"error": "Log in first"})
        if p == "/api/action":
            jid, err = do_action(str(a.get("action", "")), a)
            if err:
                return self.send(400, {"error": err})
            return self.send(200, {"job": jid})
        return self.send(404, {"error": "not found"})


def main():
    if "--set-password" in sys.argv:
        import getpass
        if sys.stdin.isatty():
            pw = getpass.getpass("New control password: ")
            if pw != getpass.getpass("Type it again: "):
                print("Those didn't match. Nothing changed.")
                sys.exit(1)
        else:
            pw = sys.stdin.readline().rstrip("\n")
        if len(pw) < 6:
            print("Please use at least 6 characters. Nothing changed.")
            sys.exit(1)
        set_password(pw)
        print("Control password saved.")
        return
    if "--version" in sys.argv:
        print(f"{APP_NAME} {VERSION}")
        return
    global POLLER, STABILITY
    STABILITY = Stability()
    POLLER = Poller()
    POLLER.start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print(f"{APP_NAME} running on http://0.0.0.0:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
