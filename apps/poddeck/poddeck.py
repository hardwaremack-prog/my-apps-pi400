#!/usr/bin/env python3
"""
PodDeck — a lightweight web dashboard for Podman, made for Raspberry Pi.

No extra packages needed: just Python 3 and Podman.
Run:   python3 poddeck.py
Open:  http://<your-pi-ip>:8070

Options (environment variables):
  PODDECK_PORT=8070          port to listen on
  PODDECK_HOST=0.0.0.0       use 127.0.0.1 to allow only this Pi
  PODDECK_PASSWORD=secret    require a password (strongly recommended on a shared network)
  PODDECK_PASSWORD_FILE=path read the password from a file instead (install.sh uses this)
  PODDECK_PODMAN=podman      path to the podman command
"""
import base64
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PODDECK_PORT", "8070"))
HOST = os.environ.get("PODDECK_HOST", "0.0.0.0")
PASSWORD = os.environ.get("PODDECK_PASSWORD", "")
_pwfile = os.environ.get("PODDECK_PASSWORD_FILE", "")
if _pwfile:
    try:
        PASSWORD = Path(_pwfile).expanduser().read_text().rstrip("\r\n")
    except OSError as e:
        print(f"Couldn't read the password file ({e}); refusing to start without a password.")
        raise SystemExit(1)
PODMAN = os.environ.get("PODDECK_PODMAN", "podman")
HERE = Path(__file__).resolve().parent
UI_FILE = HERE / "ui.html"

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-:/@]{0,254}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$")
SAFE_PORT = re.compile(r"^(\d{1,5}:)?\d{1,5}(/(tcp|udp))?$")
SAFE_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.{0,500}$")
SAFE_VOL = re.compile(r"^[^\x00:]{1,300}:[^\x00:]{1,300}(:(ro|rw|z|Z))?$")
RESTART = {"no", "always", "on-failure", "unless-stopped"}

SESSIONS = set()
VERSION = "1.1"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "poddeck"


def qualify_image(name):
    """Turn a short name like 'nginx' or 'louislam/uptime-kuma' into a full one.
    Raspberry Pi OS ships Podman with no default search registry, so short names fail."""
    first, slash, _ = name.partition("/")
    if slash and ("." in first or ":" in first or first == "localhost"):
        return name                      # already has a registry
    if not slash:
        return "docker.io/library/" + name
    return "docker.io/" + name


def friendly(err):
    """Swap Podman's most confusing errors for plain English."""
    e = err.strip()
    low = e.lower()
    if "privileged port" in low:
        return ("Ports below 1024 (like 80 or 443) need special permission on the Pi. "
                "Use a bigger number on the Pi side instead, e.g. 8080:80.")
    if re.search(r'container name ".*" is already in use', e):
        return "That name is already taken by another container. Pick a different name or remove the old one."
    if "address already in use" in low or ("port" in low and "already" in low and "bind" in low):
        return "That port is already being used by something else on this Pi. Pick a different Pi-side port."
    if "manifest unknown" in low or "not found" in low and "manifest" in low:
        return "That image or tag doesn't exist. Check the spelling (e.g. docker.io/library/nginx:latest)."
    if "no image found in manifest list for architecture" in low or "exec format error" in low:
        return "That image isn't built for Raspberry Pi (ARM). Look for an arm64 version."
    if "image is in use" in low:
        return "A container is still using this image. Remove that container first."
    if "requested access to the resource is denied" in low or "unauthorized" in low:
        return "The registry refused that image. It may be private or the name may be misspelled."
    return e


def run(args, timeout=60):
    """Run podman with a list of args (never through a shell)."""
    try:
        p = subprocess.run([PODMAN, *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"'{PODMAN}' not found. Install it with: sudo apt install podman"
    except subprocess.TimeoutExpired:
        return 124, "", "Podman took too long to respond."


def run_json(args, default):
    code, out, err = run(args)
    if code != 0:
        raise RuntimeError(err.strip() or f"podman exited with {code}")
    out = out.strip()
    if not out:
        return default
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # some versions print one JSON object per line
        return [json.loads(l) for l in out.splitlines() if l.strip()]


# ---------- Pi health ----------
def read_first(path, default=""):
    try:
        return Path(path).read_text().strip()
    except Exception:
        return default


_last_cpu = None


def cpu_percent():
    global _last_cpu
    try:
        parts = [int(x) for x in read_first("/proc/stat").splitlines()[0].split()[1:]]
    except Exception:
        return None
    idle, total = parts[3] + (parts[4] if len(parts) > 4 else 0), sum(parts)
    prev, _last_cpu = _last_cpu, (idle, total)
    if not prev or total == prev[1]:
        return None
    return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def host_stats():
    mem = {}
    for line in read_first("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        try:
            mem[k] = int(v.split()[0]) * 1024
        except Exception:
            pass
    temp = read_first("/sys/class/thermal/thermal_zone0/temp")
    disk = shutil.disk_usage("/")
    up = read_first("/proc/uptime", "0").split()[0]
    return {
        "hostname": os.uname().nodename,
        "model": read_first("/proc/device-tree/model").replace("\x00", "") or os.uname().machine,
        "arch": os.uname().machine,
        "cpu": cpu_percent(),
        "load": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
        "cores": os.cpu_count(),
        "mem_total": mem.get("MemTotal"),
        "mem_used": (mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)) or None,
        "temp_c": round(int(temp) / 1000, 1) if temp.isdigit() else None,
        "disk_total": disk.total,
        "disk_used": disk.used,
        "uptime": float(up),
    }


# ---------- Podman actions ----------
def list_containers():
    data = run_json(["ps", "-a", "--format", "json"], [])
    out = []
    for c in data:
        names = c.get("Names") or []
        ports = []
        for p in c.get("Ports") or []:
            hp, cp = p.get("host_port") or p.get("hostPort"), p.get("container_port") or p.get("containerPort")
            if hp:
                ports.append({"host": hp, "container": cp, "proto": p.get("protocol", "tcp")})
        out.append({
            "id": c.get("Id", "")[:12],
            "name": names[0] if names else c.get("Id", "")[:12],
            "image": c.get("Image", ""),
            "state": (c.get("State") or "").lower(),
            "status": c.get("Status", ""),
            "created": c.get("Created") or c.get("CreatedAt"),
            "ports": ports,
            "pod": c.get("PodName", ""),
        })
    return out


def container_stats():
    try:
        data = run_json(["stats", "--no-stream", "--format", "json"], [])
    except Exception:
        return {}
    res = {}
    for s in data:
        cid = (s.get("id") or s.get("ID") or s.get("ContainerID") or "")[:12]
        res[cid] = {
            "cpu": s.get("cpu_percent") or s.get("CPU") or s.get("CPUPerc"),
            "mem": s.get("mem_usage") or s.get("MemUsage"),
            "mem_pct": s.get("mem_percent") or s.get("MemPerc"),
        }
    return res


def list_images():
    data = run_json(["images", "--format", "json"], [])
    out = []
    for i in data:
        tags = i.get("Names") or i.get("RepoTags") or []
        out.append({
            "id": (i.get("Id") or i.get("ID") or "")[:12],
            "tags": tags or ["<none>"],
            "size": i.get("Size") or i.get("VirtualSize"),
            "created": i.get("Created") or i.get("CreatedAt"),
        })
    return out


def podman_version():
    code, out, err = run(["version", "--format", "json"], timeout=15)
    if code != 0:
        return {"ok": False, "error": err.strip() or out.strip()}
    try:
        v = json.loads(out)
        return {"ok": True, "version": (v.get("Client") or v).get("Version")}
    except Exception:
        return {"ok": True, "version": out.strip()}


def check_id(value):
    if not value or not SAFE_ID.match(value) or value.startswith("-"):
        raise ValueError("That name or ID doesn't look right.")
    return value


def container_action(cid, action):
    check_id(cid)
    cmds = {
        "start": ["start", cid],
        "stop": ["stop", "-t", "10", cid],
        "restart": ["restart", cid],
        "pause": ["pause", cid],
        "unpause": ["unpause", cid],
        "remove": ["rm", "-f", cid],
    }
    if action not in cmds:
        raise ValueError("Unknown action.")
    code, out, err = run(cmds[action], timeout=90)
    if code != 0:
        raise RuntimeError(friendly(err or out))
    return {"ok": True}


def container_logs(cid, lines=200):
    check_id(cid)
    lines = max(10, min(int(lines), 2000))
    code, out, err = run(["logs", "--tail", str(lines), "-t", cid], timeout=30)
    if code != 0 and not out:
        raise RuntimeError(err.strip())
    # podman sends app stderr to stderr; show both
    return {"logs": (out + err) if code == 0 else out}


def inspect(cid):
    check_id(cid)
    data = run_json(["inspect", cid], [])
    return data[0] if data else {}


def pull_image(name):
    name = qualify_image(check_id(name))
    code, out, err = run(["pull", name], timeout=900)
    if code != 0:
        raise RuntimeError(friendly(err or out))
    return {"ok": True, "image": name, "output": (out + err)[-3000:]}


def remove_image(iid):
    check_id(iid)
    code, out, err = run(["rmi", iid], timeout=120)
    if code != 0:
        raise RuntimeError(friendly(err or out))
    return {"ok": True}


def prune():
    code, out, err = run(["image", "prune", "-f"], timeout=300)
    if code != 0:
        raise RuntimeError(err.strip())
    return {"ok": True, "output": out.strip() or "Nothing to clean up."}


def run_container(spec):
    image = qualify_image(check_id((spec.get("image") or "").strip()))
    args = ["run", "-d"]
    name = (spec.get("name") or "").strip()
    if name:
        if not SAFE_NAME.match(name):
            raise ValueError("Names can use letters, numbers, dots, dashes and underscores.")
        args += ["--name", name]
    restart = spec.get("restart") or "unless-stopped"
    if restart not in RESTART:
        raise ValueError("Unknown restart policy.")
    args += ["--restart", restart]
    for p in spec.get("ports") or []:
        p = p.strip()
        if not p:
            continue
        if not SAFE_PORT.match(p):
            raise ValueError(f"Port '{p}' should look like 8080:80")
        nums = [int(x) for x in re.findall(r"\d+", p)]
        if any(n < 1 or n > 65535 for n in nums):
            raise ValueError(f"Port '{p}': port numbers go from 1 to 65535.")
        if ":" in p and os.geteuid() != 0 and nums[0] < unprivileged_start():
            raise ValueError(f"Port {nums[0]} is below 1024, which needs special permission on the Pi. "
                             f"Use a bigger number on the Pi side instead, e.g. {8000 + nums[0]}:{nums[1]}.")
        args += ["-p", p]
    for e in spec.get("env") or []:
        e = e.strip()
        if not e:
            continue
        if not SAFE_ENV.match(e):
            raise ValueError(f"Setting '{e}' should look like KEY=value")
        args += ["-e", e]
    for v in spec.get("volumes") or []:
        v = v.strip()
        if not v:
            continue
        if not SAFE_VOL.match(v) or v.startswith("-"):
            raise ValueError(f"Folder '{v}' should look like /home/pi/data:/data")
        args += ["-v", v]
    args.append(image)
    code, out, err = run(args, timeout=900)
    if code != 0:
        # A failed start (busy port, etc.) leaves a never-started container that blocks the name.
        if name and not re.search(r'container name ".*" is already in use', err or ""):
            c2, st, _ = run(["inspect", "--format", "{{.State.Status}}", name], timeout=15)
            if c2 == 0 and st.strip() == "created":
                run(["rm", "-f", name], timeout=30)
        raise RuntimeError(friendly(err or out))
    return {"ok": True, "id": out.strip().splitlines()[-1][:12] if out.strip() else ""}


def unprivileged_start():
    try:
        return int(read_first("/proc/sys/net/ipv4/ip_unprivileged_port_start", "1024"))
    except ValueError:
        return 1024


# ---------- After a reboot ----------
def restore_after_boot(force=False):
    """Podman's own restart service only brings back containers set to 'Always'.
    Once per boot, also start 'Unless I stop it' containers you didn't stop yourself."""
    boot = read_first("/proc/sys/kernel/random/boot_id")
    mark = STATE_DIR / "last_boot"
    if not force and boot and read_first(mark) == boot:
        return []
    started = []
    try:
        ids = [c["Id"] for c in run_json(["ps", "-a", "--format", "json"], [])
               if (c.get("State") or "").lower() not in ("running", "paused")]
        info = run_json(["inspect", *ids], []) if ids else []
        for c in info:
            policy = ((c.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name") or ""
            stopped_by_you = bool((c.get("State") or {}).get("StoppedByUser"))
            if policy == "always" or (policy == "unless-stopped" and not stopped_by_you):
                code, _, err = run(["start", c["Id"]], timeout=120)
                name = (c.get("Name") or c["Id"][:12]).lstrip("/")
                started.append(name)
                print(("Started %s after reboot" if code == 0 else "Couldn't start %s: " + err.strip()[:200]) % name)
    except Exception as e:
        print("After-reboot check skipped:", e)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        mark.write_text(boot)
    except OSError:
        pass
    return started


# ---------- Web server ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "PodDeck/" + VERSION

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        if not PASSWORD:
            return True
        cookie = self.headers.get("Cookie", "")
        m = re.search(r"poddeck=([A-Za-z0-9_\-]+)", cookie)
        return bool(m and m.group(1) in SESSIONS)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            raise ValueError("Request too large.")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            return self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/poddeck.js":
            # lets the My Apps menu tile find PodDeck (reveals only the port)
            js = f"(window.__poddeck=window.__poddeck||[]).push({self.server.server_port});"
            return self._send(200, js.encode(), "application/javascript")
        if url.path == "/api/auth":
            return self._send(200, {"needed": bool(PASSWORD), "ok": self._authed()})
        if not self._authed():
            return self._send(401, {"error": "Please sign in."})
        try:
            if url.path == "/api/overview":
                return self._send(200, {"host": host_stats(), "podman": podman_version(), "version": VERSION})
            if url.path == "/api/containers":
                cs = list_containers()
                st = container_stats() if q.get("stats") else {}
                for c in cs:
                    c["stats"] = st.get(c["id"])
                return self._send(200, cs)
            if url.path == "/api/images":
                return self._send(200, list_images())
            if url.path == "/api/logs":
                return self._send(200, container_logs(q.get("id", [""])[0], q.get("lines", ["200"])[0]))
            if url.path == "/api/inspect":
                return self._send(200, inspect(q.get("id", [""])[0]))
            self._send(404, {"error": "Not found"})
        except Exception as e:
            self._send(400, {"error": str(e)})

    def do_POST(self):
        url = urlparse(self.path)
        # simple CSRF guard: only accept requests from our own page
        if self.headers.get("X-PodDeck") != "1":
            return self._send(403, {"error": "Forbidden"})
        try:
            body = self._body()
            if url.path == "/api/login":
                if PASSWORD and hmac.compare_digest(str(body.get("password", "")), PASSWORD):
                    tok = secrets.token_urlsafe(32)
                    SESSIONS.add(tok)
                    data = json.dumps({"ok": True}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Set-Cookie", f"poddeck={tok}; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    return self.wfile.write(data)
                time.sleep(1)
                return self._send(401, {"error": "Wrong password."})
            if not self._authed():
                return self._send(401, {"error": "Please sign in."})
            if url.path == "/api/container":
                return self._send(200, container_action(body.get("id"), body.get("action")))
            if url.path == "/api/run":
                return self._send(200, run_container(body))
            if url.path == "/api/pull":
                return self._send(200, pull_image((body.get("image") or "").strip()))
            if url.path == "/api/rmi":
                return self._send(200, remove_image(body.get("id")))
            if url.path == "/api/prune":
                return self._send(200, prune())
            self._send(404, {"error": "Not found"})
        except Exception as e:
            self._send(400, {"error": str(e)})


def main():
    cpu_percent()  # prime the CPU counter
    try:
        srv = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print(f"PodDeck couldn't use port {PORT}: {e}. Is it already running? Try http://localhost:{PORT}")
        raise SystemExit(1)
    threading.Thread(target=restore_after_boot, daemon=True).start()
    shown = "localhost" if HOST in ("0.0.0.0", "") else HOST
    print(f"PodDeck is running → http://{shown}:{PORT}")
    if HOST == "0.0.0.0":
        print("Other devices on your network can reach it at http://<this-pi's-ip>:%d" % PORT)
    if not PASSWORD:
        print("Tip: set PODDECK_PASSWORD=yourpassword to require a sign-in.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nPodDeck stopped.")


if __name__ == "__main__":
    main()
