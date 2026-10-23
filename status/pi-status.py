#!/usr/bin/env python3
"""My Apps status helper for the Raspberry Pi.

A tiny background program (Python standard library only) that feeds the
server lights at the top of "My Apps Menu.html", the same way Command Center
does on Windows:

  * this Pi: name, IP, temperature, CPU, memory, SD card, uptime, power warnings
  * SSH, VNC, The Shelf, PiMesh, PodDeck, Blueprint Wiki, NetPulse, ... (green/red)
  * any other web server running on this Pi (found by itself)
  * other computers you add with "+ Watch" (your PC's Shelf, another Pi, ...)

It only listens on 127.0.0.1 (this Pi), and the start/watch buttons need a
key that only the menu page on this Pi can read (status/token.js).
"""
import json, os, re, secrets, shutil, signal, socket, subprocess, sys, threading, time
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("MYAPPS_STATUS_PORT", "8399"))
HERE = os.path.dirname(os.path.abspath(__file__))
CONF_DIR = os.path.join(os.path.expanduser("~"), ".config", "myapps-status")
WATCH_FILE = os.path.join(CONF_DIR, "watch.json")
TOKEN_FILE = os.path.join(HERE, "token.js")

# id, name, note, how to find it, ports to try, show even when off, desktop launcher (to start it)
KNOWN = [
    dict(id="ssh", name="SSH", kind="ssh", ports=[22], always=True,
         note="Log in to this Pi from another computer."),
    dict(id="vnc", name="VNC", kind="vnc", ports=[5900, 5901], always=True,
         note="See this Pi's screen from another computer (RealVNC or TigerVNC viewer)."),
    dict(id="shelf", name="The Shelf", title=r"shelf", ports=[8080, 8081, 8000], always=True,
         note="Your apps, files and AI tab."),
    dict(id="pimesh", name="PiMesh", title=r"pimesh", ports=[8470], always=True,
         note="All your Pis and their stats."),
    dict(id="poddeck", name="PodDeck", title=r"poddeck", ports=[8070], always=True, launch="poddeck",
         note="Podman containers."),
    dict(id="wiki", name="Blueprint Wiki", title=r"blueprint", ports=[8080, 8081, 8082, 8083], always=True,
         launch="blueprint-wiki", note="Projects, notes and diagrams."),
    dict(id="netpulse", name="NetPulse", title=r"netpulse", ports=[8790, 8791, 8792, 8793, 8794, 8795, 8765, 8766],
         always=True, launch="netpulse", note="Network monitor."),
    dict(id="halostudio", name="Halo Studio", title=r"halo studio", ports=[8395], launch="halo-studio",
         note="AI image editor."),
    dict(id="pidirstat", name="PiDirStat", title=r"dirstat", ports=[8088, 8089, 8090, 9000], launch="pidirstat",
         note="What's filling the SD card."),
    dict(id="ponlab", name="PON Lab", title=r"pon lab", ports=[8080, 8081], launch="ponlab",
         note="Meter outage tester."),
    dict(id="ponfleet", name="PON Fleet", title=r"pon fleet", ports=[8090, 8091], launch="ponfleet",
         note="Controls many PON Lab Pis."),
    dict(id="rpi400perf", name="RPi400 Performance", title=r"rpi400 performance", ports=[8420],
         launch="rpi400-performance", note="Make the Pi run as fast as it can."),
    dict(id="rdp", name="Remote Desktop", kind="tcp", ports=[3389], note="xrdp: connect from Windows Remote Desktop."),
    dict(id="samba", name="File sharing", kind="tcp", ports=[445], note="Samba: this Pi's shared folders."),
]
SKIP_PORTS = {PORT, 8399, 53, 111, 631, 4713}   # this helper, DNS, rpcbind, printer queue, sound server

state = {"servers": [], "host": {}, "watch": [], "at": None, "scans": 0}
lock = threading.Lock()
_cpu_prev = None
_titles = {}          # port -> (checked_at, status, title)


# ------------------------------------------------------------------ helpers
def run(cmd, timeout=3):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def tcp_open(host, port, timeout=0.6):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_probe(host, port, timeout=1.2):
    """Return (status, title) if something on host:port answers like a web server, else None."""
    try:
        c = http.client.HTTPConnection(host, port, timeout=timeout)
        c.request("GET", "/", headers={"User-Agent": "MyApps-status", "Accept": "text/html,*/*"})
        r = c.getresponse()
        body = r.read(65536).decode("utf-8", "replace")
        c.close()
        m = re.search(r"<title[^>]*>([^<]{1,120})</title>", body, re.I)
        return r.status, (m.group(1).strip() if m else "")
    except Exception:
        return None


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def listening():
    """{port: process name} for every TCP port listening on this Pi."""
    out = run(["ss", "-ltnpH"]) or run(["ss", "-ltnp"])
    ports = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[0].startswith("LISTEN"):
            continue
        local = parts[3]
        try:
            port = int(local.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            continue
        m = re.search(r'\(\("([^"]+)"', line)
        name = m.group(1) if m else ports.get(port, "")
        if port not in ports or name:
            ports[port] = name
        ports.setdefault(port, "")
    if not ports:   # no "ss": read the kernel's socket table directly (no program names)
        for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                for line in open(fn).read().splitlines()[1:]:
                    f = line.split()
                    if len(f) > 3 and f[3] == "0A":          # 0A = LISTEN
                        ports.setdefault(int(f[1].rsplit(":", 1)[1], 16), "")
            except OSError:
                pass
    return ports


def host_stats():
    global _cpu_prev
    h = {"name": socket.gethostname(), "ip": lan_ip(), "user": os.environ.get("USER") or os.path.basename(os.path.expanduser("~"))}
    try:
        h["temp"] = round(int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000, 1)
    except Exception:
        pass
    try:
        f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        idle, total = f[3] + (f[4] if len(f) > 4 else 0), sum(f)
        if _cpu_prev:
            dt = total - _cpu_prev[1]
            h["cpu"] = round(100 * (1 - (idle - _cpu_prev[0]) / dt)) if dt else 0
        _cpu_prev = (idle, total)
    except Exception:
        pass
    try:
        mi = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        h["memTotal"] = mi["MemTotal"] * 1024
        h["memUsed"] = (mi["MemTotal"] - mi.get("MemAvailable", mi["MemFree"])) * 1024
    except Exception:
        pass
    try:
        du = shutil.disk_usage("/")
        h["diskTotal"], h["diskUsed"] = du.total, du.used
    except Exception:
        pass
    try:
        h["uptime"] = int(float(open("/proc/uptime").read().split()[0]))
    except Exception:
        pass
    try:
        h["model"] = open("/proc/device-tree/model").read().strip("\x00 \n")
    except Exception:
        pass
    t = run(["vcgencmd", "get_throttled"])
    m = re.search(r"0x([0-9a-fA-F]+)", t)
    if m:
        v = int(m.group(1), 16)
        h["powerNow"] = bool(v & 0x1)        # under-voltage right now
        h["hotNow"] = bool(v & 0x4)          # throttled right now
        h["powerEver"] = bool(v & 0x10000)   # under-voltage since boot
    return h


def svc_active(names):
    for n in names:
        if run(["systemctl", "is-active", n]).strip() == "active":
            return True
    return False


def load_watch():
    try:
        w = json.load(open(WATCH_FILE))
        return [x for x in w if isinstance(x, dict) and x.get("host") and int(x.get("port", 0)) > 0][:30]
    except Exception:
        return []


def save_watch(w):
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = WATCH_FILE + ".tmp"
    json.dump(w, open(tmp, "w"), indent=1)
    os.replace(tmp, WATCH_FILE)


# ------------------------------------------------------------------ scanning
def scan():
    now = time.time()
    ip = lan_ip()
    ports = listening()
    # web page titles (re-checked every 30 s per port)
    web = {}
    for p in sorted(ports):
        if p in SKIP_PORTS or p in (22, 445, 139, 3389) or 5900 <= p <= 5910:
            continue
        t = _titles.get(p)
        if not t or now - t[0] > 30:
            r = http_probe("127.0.0.1", p)
            _titles[p] = t = (now, r[0] if r else 0, r[1] if r else None)
        if t[2] is not None:
            web[p] = t
    for p in list(_titles):
        if p not in ports:
            del _titles[p]

    servers, used = [], set()
    # 1) known web apps, matched by page title
    for k in KNOWN:
        if "title" in k:
            hit = next((p for p in sorted(web, key=lambda p: (p not in k["ports"], p))
                        if p not in used and web[p][2] and re.search(k["title"], web[p][2], re.I)), None)
            k["_port"] = hit
            if hit:
                used.add(hit)
    # 2) known apps whose title we couldn't read: fall back to their usual port
    for k in KNOWN:
        if "title" in k and not k["_port"]:
            hit = next((p for p in k["ports"] if p in ports and p not in used and not (web.get(p) and web[p][2])), None)
            k["_port"] = hit
            if hit:
                used.add(hit)
    for k in KNOWN:
        kind = k.get("kind", "web")
        if kind == "web":
            p = k["_port"]
            up = p is not None
            s = dict(id=k["id"], name=k["name"], note=k["note"], kind="web", up=up,
                     port=p or k["ports"][0], status=web[p][1] if up and p in web else 0,
                     title=web[p][2] if up and p in web else "", launch=k.get("launch"))
            if up:
                s["stoppable"] = True
                s["url"] = "http://localhost:%d/" % p
                s["lanUrl"] = "http://%s:%d/" % (ip, p)
        else:
            p = next((q for q in k["ports"] if q in ports), None)
            if p is None and k["id"] == "ssh":
                p = 22 if tcp_open("127.0.0.1", 22) else None
            up = p is not None
            s = dict(id=k["id"], name=k["name"], note=k["note"], kind=kind, up=up, port=p or k["ports"][0])
            if k["id"] == "vnc" and up:
                s["detail"] = ports.get(p) or ("wayvnc" if svc_active(["wayvnc"]) else "")
            if k["id"] in ("ssh", "vnc"):
                s["config"] = True
            if up:
                used.add(p)
        if s["up"] or k.get("always"):
            servers.append(s)
    # 3) anything else on this Pi that answers like a web page
    for p in sorted(web):
        if p in used:
            continue
        status, title = web[p][1], web[p][2]
        proc = ports.get(p, "")
        if not title and status >= 400 and not proc:
            continue   # an unnamed helper port that isn't a real page
        nm = title if title and len(title) <= 40 else (proc or "Port %d" % p)
        servers.append(dict(id="port%d" % p, name=nm, note=("Program: " + proc) if proc else "", kind="web",
                            up=True, port=p, status=status, title=title, other=True, stoppable=True,
                            url="http://localhost:%d/" % p, lanUrl="http://%s:%d/" % (ip, p)))

    # other computers
    watch = []
    for w in load_watch():
        host, port = w["host"], int(w["port"])
        up = tcp_open(host, port, 0.8)
        item = dict(name=w.get("name") or host, host=host, port=port, up=up)
        if up and port not in (22, 445, 3389) and not 5900 <= port <= 5910:
            r = http_probe(host, port, 1.0)
            if r:
                item["url"] = "http://%s:%d/" % (host, port)
                item["status"] = r[0]
        watch.append(item)

    hs = host_stats()
    with lock:
        state.update(servers=servers, host=hs, watch=watch, at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                     scans=state["scans"] + 1)


def scanner():
    while True:
        try:
            scan()
        except Exception as e:   # keep the lights on no matter what
            print("scan error:", e, file=sys.stderr)
        time.sleep(4)


# ------------------------------------------------------------------ actions
def launch(desktop_id):
    for d in (os.path.expanduser("~/.local/share/applications"), "/usr/share/applications"):
        if os.path.exists(os.path.join(d, desktop_id + ".desktop")):
            break
    else:
        return False, "Run Set Up My Apps.sh first, so this app has its launcher."
    for cmd in (["gtk-launch", desktop_id], ["gio", "launch", os.path.join(d, desktop_id + ".desktop")]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True, "Starting..."
    return False, "Couldn't find gtk-launch. Start it from its Desktop icon instead."


def open_config():
    # use the menu's own launcher so it asks for the password the normal way
    if os.path.exists("/usr/share/applications/rc_gui.desktop") and shutil.which("gtk-launch"):
        subprocess.Popen(["gtk-launch", "rc_gui"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True, "Opening Raspberry Pi Configuration. Turn it on in the Interfaces tab."
    term = shutil.which("lxterminal") or shutil.which("x-terminal-emulator")
    if term:
        subprocess.Popen([term, "-e", "sudo raspi-config"], start_new_session=True)
        return True, "Opening raspi-config: Interface Options."
    return False, "Open the Raspberry Pi menu > Preferences > Raspberry Pi Configuration > Interfaces."


# ------------------------------------------------------------------ stopping a web server
CONTAINER_PROCS = {"rootlessport", "rootlessport-child", "conmon", "podman", "docker-proxy", "containerd-shim", "slirp4netns", "pasta"}


def port_pids(port):
    """[(pid, program)] listening on this TCP port. Asks sudo (no password) if the owner is hidden."""
    def parse(out):
        found = []
        for line in out.splitlines():
            if not line.startswith("LISTEN"):
                continue
            try:
                if int(line.split()[3].rsplit(":", 1)[1]) != port:
                    continue
            except (ValueError, IndexError):
                continue
            for name, pid in re.findall(r'\("([^"]+)",pid=(\d+)', line):
                if (int(pid), name) not in found:
                    found.append((int(pid), name))
        return found
    got = parse(run(["ss", "-ltnpH"]))
    if not got and shutil.which("sudo"):
        got = parse(run(["sudo", "-n", "ss", "-ltnpH"]))
    return got


def unit_of(pid):
    """('system'|'user', 'name.service') if systemd runs this program as a service, else (None, None)."""
    try:
        cg = open("/proc/%d/cgroup" % pid).read()
    except OSError:
        return None, None
    m = re.search(r"/user@\d+\.service/.*?([^/]+\.service)\s*$", cg, re.M)
    if m:
        return "user", m.group(1)
    m = re.search(r"/system\.slice/(?:.*/)?([^/]+\.service)\s*$", cg, re.M)
    if m:
        return "system", m.group(1)
    return None, None


def as_root(cmd, timeout=120):
    """Run a command as root: sudo with no password first, then the desktop's password box."""
    if shutil.which("sudo"):
        r = subprocess.run(["sudo", "-n"] + cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True
    if shutil.which("pkexec"):
        try:
            return subprocess.run(["pkexec"] + cmd, capture_output=True, timeout=timeout).returncode == 0
        except subprocess.TimeoutExpired:
            return False
    return False


def still_listening(port, wait=5.0):
    end = time.time() + wait
    while time.time() < end:
        if port not in listening():
            return False
        time.sleep(0.3)
    return True


def stop_server(port, name):
    if port == PORT or port in (22, 445, 139, 3389) or 5900 <= port <= 5910:
        return False, "That one can't be stopped from here."
    pids = port_pids(port)
    if not pids:
        return False, "Couldn't tell which program runs %s. Close its window, or restart the Pi." % name
    progs = {p for _, p in pids}
    if progs & CONTAINER_PROCS:
        return False, "%s runs in a container. Stop it in PodDeck." % name
    notes, stopped_units = [], set()
    for pid, prog in pids:
        scope, unit = unit_of(pid)
        if unit == "myapps-status.service":
            return False, "That's the server lights helper itself."
        if unit and unit not in stopped_units:
            stopped_units.add(unit)
            if scope == "user":
                run(["systemctl", "--user", "stop", unit], timeout=20)
                if run(["systemctl", "--user", "is-enabled", unit]).strip() == "enabled":
                    notes.append("boot")
            else:
                if not as_root(["systemctl", "stop", unit]):
                    return False, "Didn't get permission to stop %s." % name
                if run(["systemctl", "is-enabled", unit]).strip() == "enabled":
                    notes.append("boot")
            continue
        if unit:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except PermissionError:
            if not as_root(["kill", "-TERM", str(pid)]):
                return False, "Didn't get permission to stop %s." % name
        except ProcessLookupError:
            pass
    if still_listening(port):
        for pid, _ in pids:            # it didn't close nicely: make it
            try:
                os.kill(pid, signal.SIGKILL)
            except PermissionError:
                as_root(["kill", "-KILL", str(pid)])
            except ProcessLookupError:
                pass
        if still_listening(port, 3):
            return False, "%s is still running. Try again, or restart the Pi." % name
    _titles.pop(port, None)
    msg = "Stopped %s." % name
    if "boot" in notes:
        msg += " It starts again by itself when the Pi restarts."
    return True, msg


# ------------------------------------------------------------------ web server
def make_token():
    tok = secrets.token_hex(16)
    tmp = TOKEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write("window.MYAPPS_STATUS={port:%d,token:%s};\n" % (PORT, json.dumps(tok)))
    os.replace(tmp, TOKEN_FILE)
    return tok


TOKEN = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MyApps-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/status"):
            with lock:
                self._send(200, dict(state, ok=True, helper=PORT))
        else:
            self._send(404, {"ok": False})

    def do_POST(self):
        if self.headers.get("X-MyApps-Key") != TOKEN:
            return self._send(403, {"ok": False, "msg": "Wrong key. Reload the menu."})
        u = urlparse(self.path)
        n = min(int(self.headers.get("Content-Length") or 0), 10000)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        if u.path == "/start":
            k = next((k for k in KNOWN if k["id"] == body.get("id") and k.get("launch")), None)
            if not k:
                return self._send(400, {"ok": False, "msg": "That one can't be started from here."})
            ok, msg = launch(k["launch"])
            return self._send(200, {"ok": ok, "msg": msg})
        if u.path == "/stop":
            try:
                port = int(body.get("port"))
            except Exception:
                port = 0
            with lock:
                s = next((x for x in state["servers"] if x.get("kind") == "web" and x.get("up") and x.get("port") == port), None)
            if not s:
                return self._send(400, {"ok": False, "msg": "That server isn't running."})
            try:
                ok, msg = stop_server(port, s["name"])
            except Exception as e:  # noqa
                ok, msg = False, "Couldn't stop it: %s" % e
            threading.Thread(target=scan, daemon=True).start()
            return self._send(200, {"ok": ok, "msg": msg})
        if u.path == "/config":
            ok, msg = open_config()
            return self._send(200, {"ok": ok, "msg": msg})
        if u.path == "/watch":
            w = load_watch()
            if body.get("remove") is not None:
                w = [x for x in w if not (x["host"] == body.get("host") and int(x["port"]) == int(body.get("port", 0)))]
            else:
                host = str(body.get("host", "")).strip()
                try:
                    port = int(body.get("port"))
                except Exception:
                    port = 0
                if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,100}", host) or not 0 < port < 65536:
                    return self._send(400, {"ok": False, "msg": "Type an address like 192.168.1.20 and a port like 8080."})
                w = [x for x in w if not (x["host"] == host and int(x["port"]) == port)]
                w.append(dict(name=str(body.get("name") or host)[:40], host=host, port=port))
            save_watch(w[:30])
            threading.Thread(target=scan, daemon=True).start()
            return self._send(200, {"ok": True})
        self._send(404, {"ok": False})


def main():
    global TOKEN
    TOKEN = make_token()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        sys.exit("My Apps status helper: port %d is busy (%s). Is it already running?" % (PORT, e))
    threading.Thread(target=scanner, daemon=True).start()
    print("My Apps status helper on http://127.0.0.1:%d/status" % PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
