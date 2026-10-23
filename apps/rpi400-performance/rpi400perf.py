#!/usr/bin/env python3
"""
RPi400 Performance - make a Raspberry Pi 400 (or Pi 4) run as fast as it can.

A small local web app (Python standard library only). Open it from the
RPi400 Performance icon or My Apps; it shows in the browser at
http://127.0.0.1:8420/ and only answers this Pi.

System changes are made by perfhelper.py through sudo (Raspberry Pi OS lets
the main user do that without a password; otherwise a password box pops up).
"""
import json, os, re, secrets, shutil, signal, socket, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing import Pool, cpu_count
from urllib.parse import urlparse

APP = "RPi400 Performance"
VERSION = "1.0.0"
PORT = int(os.environ.get("RPI400PERF_PORT", "8420"))
HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "perfhelper.py")
CONF = os.path.join(os.path.expanduser("~"), ".config", "rpi400-performance")
BENCH_FILE = os.path.join(CONF, "speed-tests.json")
KEY = secrets.token_hex(16)
LOCK = threading.Lock()
BENCH = {"running": False, "step": "", "last": None}
SERVICES = [  # name, label, why it's safe to switch off, on by default in "Make it fast"
    ("cups", "Printing", "Only needed if you print from the Pi.", True),
    ("cups-browsed", "Printer finder", "Looks for network printers all the time.", True),
    ("ModemManager", "Cell modems", "Only for USB 4G/LTE modems.", True),
    ("triggerhappy", "Hotkey helper", "Old hotkey service most desktops don't use.", True),
    ("colord", "Color profiles", "Only for calibrated printers and screens.", True),
    ("bluetooth", "Bluetooth", "Leave on if you use a Bluetooth keyboard, mouse or headset.", False),
]


def run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def read(path, default=""):
    try:
        return open(path).read().strip("\x00 \n")
    except OSError:
        return default


def as_root(args, timeout=900):
    """Run perfhelper.py as root; sudo without password first, then the desktop password box."""
    cmd = [sys.executable or "/usr/bin/python3", HELPER] + args
    tries = []
    if shutil.which("sudo"):
        tries.append(["sudo", "-n"] + cmd)
    if shutil.which("pkexec"):
        tries.append(["pkexec"] + cmd)
    if os.geteuid() == 0:
        tries = [cmd]
    for c in tries:
        try:
            r = subprocess.run(c, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": "That took too long and was stopped."}
        line = (r.stdout.strip().splitlines() or [""])[-1]
        try:
            return json.loads(line)
        except ValueError:
            if c[0] == "sudo":
                continue   # needs a password: try the password box
            if r.returncode in (126, 127):
                return {"ok": False, "msg": "No permission was given, so nothing changed."}
            return {"ok": False, "msg": (r.stderr or r.stdout or "Something went wrong.").strip()[:300]}
    return {"ok": False, "msg": "Couldn't get permission to change system settings (no sudo or pkexec)."}


# ------------------------------------------------------------------ what the Pi is doing
_cpu_prev = None


def cpu_busy():
    global _cpu_prev
    try:
        f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        idle, total = f[3] + f[4], sum(f)
        val = None
        if _cpu_prev and total > _cpu_prev[1]:
            val = round(100 * (1 - (idle - _cpu_prev[0]) / (total - _cpu_prev[1])))
        _cpu_prev = (idle, total)
        return val
    except Exception:
        return None


def meminfo():
    try:
        return {l.split(":")[0]: int(l.split()[1]) * 1024 for l in open("/proc/meminfo")}
    except Exception:
        return {}


def config_txt():
    for p in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if os.path.isfile(p):
            return p
    return None


def overclock_now():
    cfg = config_txt()
    text = read(cfg) if cfg else ""
    m = re.search(r"# >>> RPi400 Performance overclock.*?arm_freq=(\d+)", text, re.S)
    if m:
        return m.group(1)
    other = re.findall(r"^\s*arm_freq\s*=\s*(\d+)", text, re.M)
    return ("other:" + other[-1]) if other else "stock"


def svc(name):
    en = run(["systemctl", "is-enabled", name + ".service"]).strip()
    act = run(["systemctl", "is-active", name + ".service"]).strip()
    return {"installed": en not in ("", "not-found") and "No such" not in en, "enabled": en == "enabled",
            "active": act == "active"}


def sd_info():
    d = {}
    dev = "/sys/block/mmcblk0"
    if os.path.isdir(dev):
        d["name"] = read(dev + "/device/name")
        d["size"] = int(read(dev + "/size", "0") or 0) * 512
        manf = {"0x000003": "SanDisk", "0x00001b": "Samsung", "0x000027": "Phison", "0x000028": "Lexar",
                "0x000074": "Transcend", "0x00009f": "Kingston", "0x000041": "Kingston", "0x000002": "Kingston/Toshiba",
                "0x000013": "Micron", "0x0000ad": "Longsys"}
        d["maker"] = manf.get(read(dev + "/device/manfid").lower(), "")
        d["date"] = read(dev + "/device/date")
    root_src = run(["findmnt", "-no", "SOURCE", "/"]).strip()
    d["root"] = root_src
    d["onSd"] = "mmcblk" in root_src
    du = shutil.disk_usage("/")
    d["used"], d["total"] = du.used, du.total
    d["mountOpts"] = run(["findmnt", "-no", "OPTIONS", "/"]).strip()
    return d


def tuneups():
    swaps = read("/proc/swaps")
    st = {}
    try:
        st = json.load(open("/etc/rpi400-performance/state.json"))
    except Exception:
        pass
    gov = read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    return {
        "governor": {"on": gov == "performance", "value": gov},
        "zram": {"on": "zram" in swaps, "ours": bool(st.get("zram"))},
        "sysctl": {"on": os.path.exists("/etc/sysctl.d/90-rpi400-performance.conf"),
                   "swappiness": read("/proc/sys/vm/swappiness")},
        "tmpram": {"on": run(["findmnt", "-no", "FSTYPE", "/tmp"]).strip() == "tmpfs" or bool(st.get("tmpram")),
                   "pending": bool(st.get("tmpram")) and run(["findmnt", "-no", "FSTYPE", "/tmp"]).strip() != "tmpfs"},
        "journal": {"on": os.path.exists("/etc/systemd/journald.conf.d/90-rpi400-performance.conf")},
        "trim": {"on": run(["systemctl", "is-enabled", "fstrim.timer"]).strip() == "enabled"},
        "noatime": {"on": "noatime" in run(["findmnt", "-no", "OPTIONS", "/"])},
    }


def status():
    mi = meminfo()
    t = run(["vcgencmd", "get_throttled"])
    m = re.search(r"0x([0-9a-fA-F]+)", t)
    thr = int(m.group(1), 16) if m else None
    clock = run(["vcgencmd", "measure_clock", "arm"])
    mhz = None
    m = re.search(r"=(\d+)", clock)
    if m:
        mhz = round(int(m.group(1)) / 1e6)
    else:
        f = read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
        mhz = round(int(f) / 1000) if f.isdigit() else None
    mx = read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
    temp = read("/sys/class/thermal/thermal_zone0/temp")
    try:
        bench = json.load(open(BENCH_FILE))
    except Exception:
        bench = []
    return {
        "app": APP, "version": VERSION, "model": read("/proc/device-tree/model") or socket.gethostname(),
        "host": socket.gethostname(), "cores": cpu_count(),
        "mhz": mhz, "maxMhz": round(int(mx) / 1000) if mx.isdigit() else None,
        "temp": round(int(temp) / 1000, 1) if temp.isdigit() else None,
        "cpu": cpu_busy(), "throttled": thr,
        "mem": {"total": mi.get("MemTotal", 0), "avail": mi.get("MemAvailable", 0),
                "swapTotal": mi.get("SwapTotal", 0), "swapFree": mi.get("SwapFree", 0)},
        "swaps": [l.split()[0] for l in read("/proc/swaps").splitlines()[1:] if l.strip()],
        "sd": sd_info(), "overclock": overclock_now(), "tune": tuneups(),
        "services": [dict(name=n, label=l, why=w, recommend=r, **svc(n)) for n, l, w, r in SERVICES],
        "bench": bench[-12:], "benchRunning": BENCH["running"], "benchStep": BENCH["step"],
        "uptime": int(float(read("/proc/uptime", "0 0").split()[0] or 0)),
    }


def hogs():
    """Biggest memory users, grouped by program (all Chromium tabs together, etc.)."""
    me = os.getuid()
    groups = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/status") as f:
                st = dict(l.split(":", 1) for l in f if ":" in l)
            rss = int(st.get("VmRSS", "0 kB").split()[0]) * 1024
            if rss < 5 * 1024 * 1024:
                continue
            name = st.get("Name", "?").strip()
            cmd = open(f"/proc/{pid}/cmdline").read().replace("\x00", " ").strip()
            uid = int(st.get("Uid", "0").split()[0])
        except Exception:
            continue
        key = re.sub(r"^(chromium|chrome)[-_ ].*", r"\1", name)
        if key.startswith("python") and cmd:
            script = next((os.path.basename(a) for a in cmd.split()[1:] if a.endswith(".py")), "")
            key = script or key
        g = groups.setdefault(key, {"name": key, "rss": 0, "pids": [], "mine": True, "cmd": cmd[:160]})
        g["rss"] += rss
        g["pids"].append(int(pid))
        g["mine"] = g["mine"] and uid == me
    protect = {"rpi400perf.py", "pi-status.py", "Xwayland", "wayfire", "labwc", "lxpanel", "pcmanfm", "wf-panel-pi",
               "systemd", "Xorg", "lxsession", "openbox", "pipewire", "wireplumber", "pipewire-pulse", "dbus-daemon"}
    out = sorted(groups.values(), key=lambda g: -g["rss"])[:12]
    for g in out:
        g["canClose"] = g["mine"] and g["name"] not in protect
        g["count"] = len(g["pids"])
    return out


def close_group(name):
    for g in hogs():
        if g["name"] == name and g["canClose"]:
            for pid in g["pids"]:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            return {"ok": True, "msg": f"Closed {name}. Freed about {g['rss'] / 1048576:.0f} MB."}
    return {"ok": False, "msg": "That one can't be closed from here."}


# ------------------------------------------------------------------ speed test
def _cpu_work(n):
    t = time.perf_counter()
    x = 0
    for i in range(n):
        x = (x * 1103515245 + 12345 + i) & 0xFFFFFFFF
    return time.perf_counter() - t


def run_bench():
    res = {"at": time.strftime("%Y-%m-%d %H:%M"), "oc": overclock_now(),
           "gov": read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")}
    try:
        N = 3_000_000
        BENCH["step"] = "CPU, one core"
        _cpu_work(200_000)
        res["cpu1"] = round(N / min(_cpu_work(N) for _ in range(2)) / 1e6, 2)      # million steps a second
        BENCH["step"] = "CPU, all cores"
        n = cpu_count()
        with Pool(n) as p:
            t = time.perf_counter()
            p.map(_cpu_work, [N] * n)
            res["cpuAll"] = round(N * n / (time.perf_counter() - t) / 1e6, 2)
        BENCH["step"] = "Memory"
        buf = bytearray(64 * 1024 * 1024)
        t = time.perf_counter()
        for _ in range(8):
            bytes(buf)
        res["ram"] = round(64 * 8 / (time.perf_counter() - t))                   # MB/s copied
        del buf
        BENCH["step"] = "SD card writing"
        os.makedirs(CONF, exist_ok=True)
        tf = os.path.join(CONF, "speedtest.tmp")
        size = 128
        try:
            r = subprocess.run(["dd", "if=/dev/zero", f"of={tf}", "bs=1M", f"count={size}", "oflag=direct",
                                "conv=fsync"], capture_output=True, text=True, timeout=180)
            res["sdWrite"] = dd_speed(r.stderr) if r.returncode == 0 else None
        except Exception:
            res["sdWrite"] = None
        if res["sdWrite"] is None:   # no dd or no direct writes: plain Python write
            t = time.perf_counter()
            with open(tf, "wb") as f:
                for _ in range(size):
                    f.write(os.urandom(1024 * 1024))
                f.flush(); os.fsync(f.fileno())
            res["sdWrite"] = round(size / (time.perf_counter() - t), 1)
        BENCH["step"] = "SD card reading"
        r = subprocess.run(["dd", f"if={tf}", "of=/dev/null", "bs=1M", "iflag=direct"],
                           capture_output=True, text=True, timeout=180)
        res["sdRead"] = dd_speed(r.stderr)
        try:
            os.remove(tf)
        except OSError:
            pass
        temp = read("/sys/class/thermal/thermal_zone0/temp")
        res["temp"] = round(int(temp) / 1000) if temp.isdigit() else None
        try:
            hist = json.load(open(BENCH_FILE))
        except Exception:
            hist = []
        hist.append(res)
        os.makedirs(CONF, exist_ok=True)
        json.dump(hist[-50:], open(BENCH_FILE, "w"), indent=1)
        BENCH["last"] = res
    finally:
        BENCH["running"] = False
        BENCH["step"] = ""


def dd_speed(text):
    m = re.search(r"([\d.,]+)\s*([kMG]?B)/s", text or "")
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return round(v * {"kB": 0.001, "B": 1e-6, "MB": 1, "GB": 1000}.get(m.group(2), 1), 1)


# ------------------------------------------------------------------ "Make it fast"
def make_fast(include_bt=False):
    steps, notes, reboot = [], [], False
    st = status()
    tu = st["tune"]
    plan = []
    if not tu["governor"]["on"]:
        plan.append(["governor", "performance"])
    if not tu["zram"]["on"]:
        plan.append(["zram", "on"])
    if not tu["sysctl"]["on"]:
        plan.append(["sysctl", "on"])
    if not tu["tmpram"]["on"]:
        plan.append(["tmpram", "on"])
    if not tu["journal"]["on"]:
        plan.append(["journal", "on"])
    if not tu["trim"]["on"]:
        plan.append(["trim", "on"])
    if not tu["noatime"]["on"]:
        plan.append(["noatime", "on"])
    for s in st["services"]:
        if s["installed"] and s["enabled"] and (s["recommend"] or (include_bt and s["name"] == "bluetooth")):
            plan.append(["service", s["name"] + ":off"])
    if not plan:
        return {"ok": True, "msg": "Already tuned. Everything on the list is done.", "steps": []}
    for a in plan:
        r = as_root(a)
        steps.append({"action": a, **r})
        reboot = reboot or bool(r.get("reboot"))
        if not r.get("ok") and "permission" in (r.get("msg") or "").lower():
            break
    good = sum(1 for s in steps if s.get("ok"))
    msg = f"Done: {good} of {len(steps)} tune-ups applied."
    if reboot:
        msg += " Restart the Pi to finish."
    return {"ok": good > 0, "msg": msg, "steps": steps, "reboot": reboot}


# ------------------------------------------------------------------ web server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"ok": False})
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read().replace("{{KEY}}", KEY)
            return self._send(200, html, "text/html; charset=utf-8")
        if p == "/probe.js":   # lets the My Apps tile find this app (other programs won't answer this)
            return self._send(200, "window.__rpi400perf=%d;" % PORT, "text/javascript")
        if p == "/icon.png":
            return self._send(200, open(os.path.join(HERE, "icon.png"), "rb").read(), "image/png")
        if p == "/api/status":
            return self._send(200, status())
        if p == "/api/hogs":
            return self._send(200, hogs())
        self._send(404, {"ok": False})

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Perf-Key") != KEY:
            return self._send(403, {"ok": False, "msg": "Reload the page."})
        p = urlparse(self.path).path
        n = min(int(self.headers.get("Content-Length") or 0), 10000)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        if p == "/api/do":
            action, value = str(body.get("action", "")), str(body.get("value", ""))
            if not re.fullmatch(r"[a-z_]{2,20}", action) or not re.fullmatch(r"[A-Za-z0-9:_-]{0,40}", value):
                return self._send(400, {"ok": False, "msg": "Bad request."})
            with LOCK:
                return self._send(200, as_root([action, value]))
        if p == "/api/fast":
            with LOCK:
                return self._send(200, make_fast(bool(body.get("bluetooth"))))
        if p == "/api/bench":
            if BENCH["running"]:
                return self._send(200, {"ok": False, "msg": "A speed test is already running."})
            BENCH["running"] = True
            threading.Thread(target=run_bench, daemon=True).start()
            return self._send(200, {"ok": True})
        if p == "/api/close":
            return self._send(200, close_group(str(body.get("name", ""))))
        self._send(404, {"ok": False})


def open_window(url):
    for b in ("chromium-browser", "chromium", "google-chrome"):
        if shutil.which(b):
            subprocess.Popen([b, f"--app={url}", "--window-size=1180,860"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return
    webbrowser.open(url)


def main():
    url = f"http://127.0.0.1:{PORT}/"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"{APP} is already running - opening it.")
        if "--no-browser" not in sys.argv:
            open_window(url)
        return
    print(f"{APP} {VERSION} running at {url}  (close this window to quit)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, open_window, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
