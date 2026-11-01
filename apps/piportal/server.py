#!/usr/bin/env python3
"""
PiPortal — HTML5 remote desktop *with sound* + management for Raspberry Pi 5 / 500 / 4 / 400.

  * Desktop: proxies a localhost-only VNC server (wayvnc on Wayland, x11vnc on X11)
    to the browser over a WebSocket (noVNC client).
  * Sound:   captures the Pi's speaker output (PipeWire/Pulse monitor) with `parec`
    and streams raw PCM to the browser, which plays it with Web Audio.
  * Manage:  live stats, processes, services, terminal, files, Pi volume, power.

Run:  python3 server.py               (uses ~/.config/piportal/config.json)
      python3 server.py --set-password
"""
import argparse, asyncio, base64, getpass, hashlib, hmac, json, os, pwd, re, secrets
import shutil, signal, socket, ssl, struct, subprocess, sys, time
from pathlib import Path

try:
    from aiohttp import web, WSMsgType
except ImportError:
    sys.exit("PiPortal needs aiohttp:  sudo apt install python3-aiohttp")

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
CONF_DIR = Path(os.environ.get("PIPORTAL_CONF", Path.home() / ".config" / "piportal"))
CONF_FILE = CONF_DIR / "config.json"
UID = os.getuid()
USER = pwd.getpwuid(UID).pw_name
HOME = Path(pwd.getpwuid(UID).pw_dir)
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{UID}"))

DEFAULTS = {
    "port": 6090,
    "bind": "0.0.0.0",
    "tls": False,                 # self-signed HTTPS (enables mic/clipboard APIs in browsers)
    "password_hash": "",
    "vnc": {
        "mode": "auto",           # auto = start our own private VNC on localhost; external = use host/port
        "host": "127.0.0.1",
        "port": 5910,
        "render_cursor": True,    # wayvnc: draw cursor into the picture
    },
    "audio": {"device": "@DEFAULT_MONITOR@"},
}


# --------------------------------------------------------------------------- config / auth
def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONF_FILE.exists():
        try:
            user = json.loads(CONF_FILE.read_text())
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            print(f"[piportal] bad config, using defaults: {e}", file=sys.stderr)
    return cfg


def save_config(cfg):
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    CONF_FILE.write_text(json.dumps(cfg, indent=2))
    os.chmod(CONF_FILE, 0o600)


def hash_password(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return "pbkdf2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def check_password(pw, stored):
    try:
        _, s, h = stored.split("$")
        return hmac.compare_digest(hash_password(pw, base64.b64decode(s)), stored)
    except Exception:
        return False


CFG = load_config()
SESSIONS = {}          # token -> expiry
SESSION_TTL = 7 * 24 * 3600
FAILS = {}             # ip -> (count, first_time)


def authed(request):
    tok = request.cookies.get("pp_session")
    exp = SESSIONS.get(tok) if tok else None
    if exp and exp > time.time():
        return True
    return False


@web.middleware
async def auth_mw(request, handler):
    p = request.path
    public = (p == "/" or p.startswith("/static/") or p in ("/api/login", "/api/ping", "/api/me")
              or p in ("/favicon.svg", "/piportal.js"))
    if not public and not authed(request):
        return web.json_response({"error": "login required"}, status=401)
    resp = await handler(request)
    return resp


async def h_login(request):
    ip = request.remote or "?"
    cnt, t0 = FAILS.get(ip, (0, time.time()))
    if time.time() - t0 > 600:
        cnt, t0 = 0, time.time()
    if cnt >= 8:
        return web.json_response({"error": "Too many tries — wait 10 minutes."}, status=429)
    data = await request.json()
    if not CFG.get("password_hash") or not check_password(data.get("password", ""), CFG["password_hash"]):
        FAILS[ip] = (cnt + 1, t0)
        await asyncio.sleep(1)
        return web.json_response({"error": "Wrong password"}, status=403)
    FAILS.pop(ip, None)
    tok = secrets.token_urlsafe(32)
    SESSIONS[tok] = time.time() + SESSION_TTL
    resp = web.json_response({"ok": True})
    resp.set_cookie("pp_session", tok, max_age=SESSION_TTL, httponly=True, samesite="Strict",
                    secure=bool(CFG.get("tls")))
    return resp


async def h_logout(request):
    SESSIONS.pop(request.cookies.get("pp_session"), None)
    resp = web.json_response({"ok": True})
    resp.del_cookie("pp_session")
    return resp


async def h_me(request):
    return web.json_response({"authed": authed(request), "hostname": socket.gethostname(),
                              "model": pi_model(), "configured": bool(CFG.get("password_hash"))})


async def h_ping(request):
    # Public & CORS-enabled so the Fleet view on another Pi can see this one is up.
    return web.json_response({"app": "piportal", "version": VERSION, "hostname": socket.gethostname(),
                              "model": pi_model(), "uptime": uptime_s()},
                             headers={"Access-Control-Allow-Origin": "*"})


async def h_probe(request):
    # "Open PiPortal.html" loads this as a <script> to find which port PiPortal is on.
    js = f"window.__piportal=(window.__piportal||[]).concat([{CFG['port']}]);"
    return web.Response(text=js, content_type="application/javascript",
                        headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- helpers
def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return default


def pi_model():
    m = read("/proc/device-tree/model").replace("\x00", "")
    return m or "Linux machine"


def pi_family():
    m = pi_model()
    if "500" in m: return "pi500"
    if "400" in m: return "pi400"
    if "Pi 5" in m: return "pi5"
    if "Pi 4" in m: return "pi4"
    return "other"


def uptime_s():
    try:
        return float(read("/proc/uptime").split()[0])
    except Exception:
        return 0


def user_env():
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = str(RUNTIME)
    env.setdefault("HOME", str(HOME))
    pulse = RUNTIME / "pulse" / "native"
    if pulse.exists():
        env["PULSE_SERVER"] = f"unix:{pulse}"
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={RUNTIME}/bus")
    return env


async def run(*cmd, timeout=10, sudo=False):
    if sudo and UID != 0:
        cmd = ("sudo", "-n") + cmd
    try:
        p = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                 env=user_env())
        out, err = await asyncio.wait_for(p.communicate(), timeout)
        return p.returncode, out.decode(errors="replace"), err.decode(errors="replace")
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not installed"
    except asyncio.TimeoutError:
        return 124, "", "timed out"


# --------------------------------------------------------------------------- stats
_prev_cpu = None
_prev_net = None


def cpu_times():
    rows = {}
    for line in read("/proc/stat").splitlines():
        if line.startswith("cpu"):
            parts = line.split()
            vals = list(map(int, parts[1:9]))
            idle = vals[3] + vals[4]
            rows[parts[0]] = (sum(vals), idle)
    return rows


def cpu_percent():
    global _prev_cpu
    now = cpu_times()
    prev = _prev_cpu or now
    _prev_cpu = now
    res = {}
    for k, (tot, idle) in now.items():
        pt, pi = prev.get(k, (tot, idle))
        dt = tot - pt
        res[k] = 0.0 if dt <= 0 else round(100 * (1 - (idle - pi) / dt), 1)
    return res


def mem_info():
    info = {}
    for line in read("/proc/meminfo").splitlines():
        k, v = line.split(":", 1)
        info[k] = int(v.split()[0]) * 1024
    return info


def net_rates():
    global _prev_net
    now_t = time.time()
    rx = tx = 0
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, data = line.split(":", 1)
        if name.strip() == "lo":
            continue
        f = data.split()
        rx += int(f[0]); tx += int(f[8])
    prev = _prev_net or (now_t, rx, tx)
    _prev_net = (now_t, rx, tx)
    dt = max(now_t - prev[0], 0.001)
    return {"rx": max(0, (rx - prev[1]) / dt), "tx": max(0, (tx - prev[2]) / dt)}


def fan_rpm():
    for p in Path("/sys/devices/platform").glob("cooling_fan/hwmon/*/fan1_input"):
        v = read(p)
        if v.isdigit():
            return int(v)
    return None


def ip_addrs():
    out = []
    try:
        res = subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True, timeout=3)
        for itf in json.loads(res.stdout or "[]"):
            if itf.get("ifname") == "lo":
                continue
            for a in itf.get("addr_info", []):
                out.append({"if": itf["ifname"], "ip": a["local"]})
    except Exception:
        pass
    return out


async def h_info(request):
    os_name = ""
    for line in read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip('"')
    session = "wayland" if wayland_socket() else ("x11" if Path("/tmp/.X11-unix/X0").exists() else "none")
    return web.json_response({
        "hostname": socket.gethostname(), "model": pi_model(), "family": pi_family(), "os": os_name,
        "kernel": os.uname().release, "arch": os.uname().machine, "cpus": os.cpu_count(),
        "mem_total": mem_info().get("MemTotal", 0), "ips": ip_addrs(), "user": USER,
        "session": session, "version": VERSION, "port": CFG["port"],
    })


async def h_stats(request):
    mi = mem_info()
    du = shutil.disk_usage("/")
    temp = read("/sys/class/thermal/thermal_zone0/temp")
    freq = read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    rc, thr, _ = await run("vcgencmd", "get_throttled", timeout=2)
    throttled = None
    if rc == 0 and "=" in thr:
        try:
            v = int(thr.strip().split("=")[1], 16)
            throttled = {"raw": hex(v), "undervolt_now": bool(v & 1), "throttled_now": bool(v & 4),
                         "undervolt_ever": bool(v & 0x10000), "throttled_ever": bool(v & 0x40000)}
        except ValueError:
            pass
    cpu = cpu_percent()
    return web.json_response({
        "cpu": cpu.get("cpu", 0), "cores": [cpu[k] for k in sorted(cpu) if k != "cpu"],
        "load": os.getloadavg(),
        "temp": int(temp) / 1000 if temp.isdigit() else None,
        "freq_mhz": int(freq) // 1000 if freq.isdigit() else None,
        "mem": {"total": mi.get("MemTotal", 0), "avail": mi.get("MemAvailable", 0)},
        "swap": {"total": mi.get("SwapTotal", 0), "free": mi.get("SwapFree", 0)},
        "disk": {"total": du.total, "used": du.used, "free": du.free},
        "net": net_rates(), "fan": fan_rpm(), "throttled": throttled, "uptime": uptime_s(),
        "streams": {"desktop": len(VNC_CLIENTS), "audio": len(AUDIO_CLIENTS)},
    })


# --------------------------------------------------------------------------- processes
_prev_proc = {}
_prev_proc_total = 0
CLK = os.sysconf("SC_CLK_TCK")


async def h_processes(request):
    global _prev_proc, _prev_proc_total
    total = sum(v[0] for k, v in cpu_times().items() if k == "cpu")
    ncpu = os.cpu_count() or 1
    dtotal = max(total - _prev_proc_total, 1) if _prev_proc_total else None
    users = {}
    out, cur = [], {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = read(f"/proc/{d}/stat")
            rp = stat.rfind(")")
            name = stat[stat.find("(") + 1:rp]
            f = stat[rp + 2:].split()
            ticks = int(f[11]) + int(f[12])
            rss = int(f[21]) * os.sysconf("SC_PAGE_SIZE")
            uid = os.stat(f"/proc/{d}").st_uid
            if uid not in users:
                try: users[uid] = pwd.getpwuid(uid).pw_name
                except KeyError: users[uid] = str(uid)
            cmd = read(f"/proc/{d}/cmdline").replace("\x00", " ").strip()
            cur[d] = ticks
            cpu = 0.0
            if dtotal and d in _prev_proc:
                cpu = round(100 * ncpu * (ticks - _prev_proc[d]) / dtotal, 1)
            out.append({"pid": int(d), "name": name, "user": users[uid], "cpu": cpu, "rss": rss,
                        "state": f[0], "cmd": cmd[:300]})
        except Exception:
            continue
    _prev_proc, _prev_proc_total = cur, total
    out.sort(key=lambda p: (-p["cpu"], -p["rss"]))
    return web.json_response(out)


async def h_kill(request):
    pid = int(request.match_info["pid"])
    sig = (await request.json()).get("signal", "TERM")
    signum = {"TERM": signal.SIGTERM, "KILL": signal.SIGKILL}.get(sig, signal.SIGTERM)
    try:
        os.kill(pid, signum)
        return web.json_response({"ok": True})
    except PermissionError:
        rc, _, err = await run("kill", f"-{sig}", str(pid), sudo=True)
        if rc == 0:
            return web.json_response({"ok": True})
        return web.json_response({"error": "Not allowed (owned by another user)."}, status=403)
    except ProcessLookupError:
        return web.json_response({"error": "Process already gone."}, status=404)


# --------------------------------------------------------------------------- services
SERVICE_RE = re.compile(r"^[A-Za-z0-9@._:\-]+$")


async def h_services(request):
    rc, out, err = await run("systemctl", "list-units", "--type=service", "--all", "--no-pager",
                             "--no-legend", "--plain")
    rc2, files, _ = await run("systemctl", "list-unit-files", "--type=service", "--no-pager",
                              "--no-legend", "--plain")
    enabled = {}
    for line in files.splitlines():
        p = line.split()
        if len(p) >= 2:
            enabled[p[0]] = p[1]
    rows = []
    for line in out.splitlines():
        p = line.split(None, 4)
        if len(p) >= 4 and p[0].endswith(".service"):
            rows.append({"name": p[0], "load": p[1], "active": p[2], "sub": p[3],
                         "desc": p[4] if len(p) > 4 else "", "enabled": enabled.get(p[0], "")})
    return web.json_response(rows)


async def h_service_action(request):
    name, action = request.match_info["name"], request.match_info["action"]
    if not SERVICE_RE.match(name) or action not in ("start", "stop", "restart", "enable", "disable"):
        return web.json_response({"error": "bad request"}, status=400)
    rc, out, err = await run("systemctl", action, name, sudo=True, timeout=30)
    if rc != 0:
        return web.json_response({"error": err.strip() or "failed (sudo needed — rerun install.sh)"}, status=500)
    return web.json_response({"ok": True})


async def h_service_logs(request):
    name = request.match_info["name"]
    if not SERVICE_RE.match(name):
        return web.json_response({"error": "bad"}, status=400)
    rc, out, err = await run("journalctl", "-u", name, "-n", "200", "--no-pager", "-o", "short-iso",
                             sudo=True, timeout=10)
    if rc != 0:
        rc, out, err = await run("journalctl", "-u", name, "-n", "200", "--no-pager", "-o", "short-iso")
    return web.json_response({"log": out or err})


# --------------------------------------------------------------------------- power & Pi volume
async def h_power(request):
    action = (await request.json()).get("action")
    cmd = {"reboot": ("systemctl", "reboot"), "shutdown": ("systemctl", "poweroff")}.get(action)
    if not cmd:
        return web.json_response({"error": "bad action"}, status=400)
    rc, _, err = await run(*cmd, sudo=True)
    if rc != 0:
        return web.json_response({"error": err.strip() or "needs sudo"}, status=500)
    return web.json_response({"ok": True})


async def h_pivol_get(request):
    rc, out, err = await run("pactl", "get-sink-volume", "@DEFAULT_SINK@")
    rc2, mute, _ = await run("pactl", "get-sink-mute", "@DEFAULT_SINK@")
    rc3, sink, _ = await run("pactl", "get-default-sink")
    m = re.search(r"(\d+)%", out)
    return web.json_response({"volume": int(m.group(1)) if m else None, "muted": "yes" in mute,
                              "sink": sink.strip(), "error": err.strip() if rc else None})


async def h_pivol_set(request):
    data = await request.json()
    if "volume" in data:
        v = max(0, min(150, int(data["volume"])))
        await run("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{v}%")
    if "muted" in data:
        await run("pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if data["muted"] else "0")
    return await h_pivol_get(request)


# --------------------------------------------------------------------------- files
def safe_path(p):
    p = Path(os.path.expanduser(p or "~")).resolve()
    return p


async def h_files(request):
    p = safe_path(request.query.get("path"))
    if not p.is_dir():
        return web.json_response({"error": "Not a folder"}, status=400)
    items = []
    try:
        for e in os.scandir(p):
            try:
                st = e.stat(follow_symlinks=True)
                items.append({"name": e.name, "dir": e.is_dir(), "size": st.st_size, "mtime": st.st_mtime})
            except OSError:
                items.append({"name": e.name, "dir": False, "size": 0, "mtime": 0})
    except PermissionError:
        return web.json_response({"error": "Permission denied"}, status=403)
    items.sort(key=lambda i: (not i["dir"], i["name"].lower()))
    return web.json_response({"path": str(p), "parent": str(p.parent), "items": items, "home": str(HOME)})


async def h_download(request):
    p = safe_path(request.query.get("path"))
    if not p.is_file():
        return web.json_response({"error": "Not a file"}, status=404)
    return web.FileResponse(p, headers={"Content-Disposition": f'attachment; filename="{p.name}"'})


async def h_upload(request):
    dest = safe_path(request.query.get("path"))
    reader = await request.multipart()
    saved = []
    async for part in reader:
        if not part.filename:
            continue
        name = Path(part.filename).name
        target = dest / name
        with open(target, "wb") as f:
            while chunk := await part.read_chunk(1 << 20):
                f.write(chunk)
        saved.append(name)
    return web.json_response({"ok": True, "saved": saved})


async def h_file_op(request):
    data = await request.json()
    op = data.get("op")
    p = safe_path(data.get("path"))
    try:
        if op == "mkdir":
            (p / Path(data["name"]).name).mkdir()
        elif op == "rename":
            p.rename(p.parent / Path(data["name"]).name)
        elif op == "delete":
            if p == HOME or p == Path("/"):
                raise ValueError("Refusing to delete that folder")
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
        else:
            return web.json_response({"error": "bad op"}, status=400)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True})


# --------------------------------------------------------------------------- VNC backend
VNC_PROC = None
VNC_CLIENTS = set()
VNC_LOCK = asyncio.Lock()
VNC_LOG = []


def wayland_socket():
    for name in ("wayland-0", "wayland-1"):
        if (RUNTIME / name).exists():
            return name
    return None


def port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


async def ensure_vnc():
    """Start our private, localhost-only VNC server if it isn't running. Returns (host, port) or raises."""
    global VNC_PROC
    v = CFG["vnc"]
    host, port = v.get("host", "127.0.0.1"), int(v.get("port", 5910))
    if v.get("mode") == "external":
        return host, port
    async with VNC_LOCK:
        if VNC_PROC and VNC_PROC.returncode is None and port_open(host, port):
            return host, port
        if VNC_PROC and VNC_PROC.returncode is None:
            VNC_PROC.terminate()
        env = user_env()
        wl = wayland_socket()
        if wl and shutil.which("wayvnc"):
            env["WAYLAND_DISPLAY"] = wl
            cfgfile = CONF_DIR / "wayvnc.conf"
            CONF_DIR.mkdir(parents=True, exist_ok=True)
            cfgfile.write_text("enable_auth=false\n")
            cmd = ["wayvnc", "-C", str(cfgfile), "-S", str(RUNTIME / "piportal-wayvnc.sock")]
            if v.get("render_cursor", True):
                cmd.append("-r")
            cmd += ["127.0.0.1", str(port)]
        elif Path("/tmp/.X11-unix/X0").exists() and shutil.which("x11vnc"):
            env["DISPLAY"] = ":0"
            env.setdefault("XAUTHORITY", str(HOME / ".Xauthority"))
            cmd = ["x11vnc", "-display", ":0", "-localhost", "-rfbport", str(port), "-nopw", "-shared",
                   "-forever", "-quiet", "-noxdamage", "-cursor", "most"]
        else:
            raise RuntimeError("No desktop found. Is the Pi booted to the desktop (and is wayvnc / x11vnc "
                               "installed)? For a headless Pi, set a resolution in raspi-config → Display.")
        VNC_LOG.clear()
        VNC_PROC = await asyncio.create_subprocess_exec(*cmd, env=env, stdout=subprocess.DEVNULL,
                                                        stderr=subprocess.PIPE)
        asyncio.create_task(_drain(VNC_PROC.stderr, VNC_LOG))
        for _ in range(50):
            await asyncio.sleep(0.1)
            if port_open(host, port):
                return host, port
            if VNC_PROC.returncode is not None:
                break
        raise RuntimeError("VNC server failed to start: " + " ".join(VNC_LOG[-5:])[:400])


async def _drain(stream, sink):
    while True:
        line = await stream.readline()
        if not line:
            break
        sink.append(line.decode(errors="replace").strip())
        del sink[:-50]


async def ws_vnc(request):
    ws = web.WebSocketResponse(protocols=("binary",), max_msg_size=0, heartbeat=20)
    await ws.prepare(request)
    try:
        host, port = await ensure_vnc()
        reader, writer = await asyncio.open_connection(host, port)
    except Exception as e:
        await ws.close(code=4000, message=str(e)[:120].encode())
        return ws
    VNC_CLIENTS.add(ws)

    async def tcp_to_ws():
        try:
            while data := await reader.read(65536):
                await ws.send_bytes(data)
        finally:
            await ws.close()

    pump = asyncio.create_task(tcp_to_ws())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                writer.write(msg.data)
                await writer.drain()
            elif msg.type == WSMsgType.TEXT:
                writer.write(msg.data.encode())
                await writer.drain()
    finally:
        VNC_CLIENTS.discard(ws)
        pump.cancel()
        writer.close()
    return ws


# --------------------------------------------------------------------------- audio
AUDIO_CLIENTS = set()


async def ws_audio(request):
    rate = int(request.query.get("rate", 48000))
    ch = int(request.query.get("ch", 2))
    if rate not in (16000, 24000, 32000, 44100, 48000) or ch not in (1, 2):
        rate, ch = 48000, 2
    ws = web.WebSocketResponse(max_msg_size=0, heartbeat=20)
    await ws.prepare(request)
    dev = CFG["audio"].get("device", "@DEFAULT_MONITOR@")
    cmd = None
    if shutil.which("parec"):
        cmd = ["parec", "-d", dev, "--format=s16le", f"--rate={rate}", f"--channels={ch}",
               "--latency-msec=20", "--raw"]
    elif shutil.which("pw-record"):
        cmd = ["pw-record", "-P", "{ stream.capture.sink=true }", "--rate", str(rate), "--channels", str(ch),
               "--format", "s16", "--latency", "20ms", "-"]
    if not cmd:
        await ws.send_str(json.dumps({"error": "No audio capture tool. Run: sudo apt install pulseaudio-utils"}))
        await ws.close()
        return ws
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                env=user_env())
    await ws.send_str(json.dumps({"rate": rate, "ch": ch, "format": "s16le"}))
    AUDIO_CLIENTS.add(ws)
    chunk = rate * ch * 2 // 50          # 20 ms

    async def pump():
        try:
            while True:
                try:
                    data = await proc.stdout.readexactly(chunk)
                except asyncio.IncompleteReadError:
                    data = b""
                if not data:
                    err = (await proc.stderr.read()).decode(errors="replace").strip()
                    if not ws.closed:
                        await ws.send_str(json.dumps({"error": "Audio capture stopped: " + (err[:300] or "no sound server — is someone logged in to the desktop?")}))
                    break
                if ws.closed:
                    break
                await ws.send_bytes(data)
        finally:
            await ws.close()

    task = asyncio.create_task(pump())
    try:
        async for _ in ws:
            pass
    finally:
        AUDIO_CLIENTS.discard(ws)
        task.cancel()
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    return ws


# --------------------------------------------------------------------------- terminal
async def ws_term(request):
    import pty, fcntl, termios
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(HOME)
        env = user_env()
        env["TERM"] = "xterm-256color"
        shell = pwd.getpwuid(UID).pw_shell or "/bin/bash"
        os.execvpe(shell, [shell, "-l"], env)
    loop = asyncio.get_running_loop()
    q = asyncio.Queue()

    def on_read():
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        q.put_nowait(data)
        if not data:
            loop.remove_reader(fd)

    loop.add_reader(fd, on_read)

    async def out():
        while True:
            data = await q.get()
            if not data:
                await ws.close()
                break
            await ws.send_bytes(data)

    task = asyncio.create_task(out())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if "i" in m:
                os.write(fd, m["i"].encode())
            elif "r" in m:
                cols, rows = m["r"]
                fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    finally:
        task.cancel()
        try: loop.remove_reader(fd)
        except Exception: pass
        try: os.kill(pid, signal.SIGHUP)
        except ProcessLookupError: pass
        os.close(fd)
        try: os.waitpid(pid, os.WNOHANG)
        except ChildProcessError: pass
    return ws


# --------------------------------------------------------------------------- misc
async def h_index(request):
    return web.FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


async def h_vnc_status(request):
    return web.json_response({"running": bool(VNC_PROC and VNC_PROC.returncode is None),
                              "mode": CFG["vnc"]["mode"], "log": VNC_LOG[-10:],
                              "wayland": wayland_socket(), "have_wayvnc": bool(shutil.which("wayvnc")),
                              "have_x11vnc": bool(shutil.which("x11vnc")),
                              "have_parec": bool(shutil.which("parec"))})


def make_app():
    app = web.Application(middlewares=[auth_mw], client_max_size=4 * 1024 ** 3)
    r = app.router
    r.add_get("/", h_index)
    r.add_static("/static/", WEB)
    r.add_post("/api/login", h_login)
    r.add_post("/api/logout", h_logout)
    r.add_get("/api/me", h_me)
    r.add_get("/api/ping", h_ping)
    r.add_get("/piportal.js", h_probe)
    r.add_get("/api/info", h_info)
    r.add_get("/api/stats", h_stats)
    r.add_get("/api/processes", h_processes)
    r.add_post("/api/processes/{pid:\\d+}/kill", h_kill)
    r.add_get("/api/services", h_services)
    r.add_post("/api/services/{name}/{action}", h_service_action)
    r.add_get("/api/services/{name}/logs", h_service_logs)
    r.add_post("/api/power", h_power)
    r.add_get("/api/pivolume", h_pivol_get)
    r.add_post("/api/pivolume", h_pivol_set)
    r.add_get("/api/files", h_files)
    r.add_get("/api/files/download", h_download)
    r.add_post("/api/files/upload", h_upload)
    r.add_post("/api/files/op", h_file_op)
    r.add_get("/api/vnc", h_vnc_status)
    r.add_get("/ws/vnc", ws_vnc)
    r.add_get("/ws/audio", ws_audio)
    r.add_get("/ws/term", ws_term)

    async def cleanup(app):
        if VNC_PROC and VNC_PROC.returncode is None:
            VNC_PROC.terminate()
    app.on_cleanup.append(cleanup)
    return app


def tls_context():
    crt, key = CONF_DIR / "cert.pem", CONF_DIR / "key.pem"
    if not crt.exists():
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        host = socket.gethostname()
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                        "-keyout", str(key), "-out", str(crt), "-subj", f"/CN={host}",
                        "-addext", f"subjectAltName=DNS:{host},DNS:{host}.local"], check=True,
                       capture_output=True)
        os.chmod(key, 0o600)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(crt, key)
    return ctx


def main():
    ap = argparse.ArgumentParser(description="PiPortal")
    ap.add_argument("--set-password", action="store_true", help="set the login password and exit")
    ap.add_argument("--password", help="(with --set-password) non-interactive")
    ap.add_argument("--port", type=int)
    a = ap.parse_args()
    if a.set_password:
        pw = a.password or getpass.getpass("New PiPortal password: ")
        if not a.password and pw != getpass.getpass("Again: "):
            sys.exit("Passwords didn't match.")
        if len(pw) < 4:
            sys.exit("Use at least 4 characters.")
        cfg = load_config()
        cfg["password_hash"] = hash_password(pw)
        save_config(cfg)
        print(f"Password saved to {CONF_FILE}")
        return
    if a.port:
        CFG["port"] = a.port
    if not CFG.get("password_hash"):
        print("No password set yet. Run:  python3 server.py --set-password", file=sys.stderr)
    ctx = tls_context() if CFG.get("tls") else None
    scheme = "https" if ctx else "http"
    print(f"PiPortal {VERSION} on {pi_model()} → {scheme}://{socket.gethostname()}.local:{CFG['port']}")
    web.run_app(make_app(), host=CFG["bind"], port=CFG["port"], ssl_context=ctx, print=None,
                access_log=None)


if __name__ == "__main__":
    main()
