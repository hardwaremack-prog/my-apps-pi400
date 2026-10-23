#!/usr/bin/env python3
"""
PiMesh - a zero-config mesh for Raspberry Pi fleets.

Every Pi running this joins the mesh automatically (UDP broadcast + gossip),
shares live stats, measures link latency to every other member, and serves
the same dashboard at http://<any-pi>:8470

Pure Python 3 standard library - nothing to pip install.
"""
import argparse
import hashlib
import hmac
import json
import os
import platform
import random
import shutil
import socket
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VERSION = "1.0.0"
BEACON_EVERY = 2.0      # seconds between heartbeats
STALE_AFTER = 7         # seconds without a beacon -> "stale"
OFFLINE_AFTER = 20      # seconds without a beacon -> "offline"
FORGET_AFTER = 86400    # drop offline members after a day
HISTORY = 180           # points kept per member (~6 minutes)
HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "mesh_name": "home-mesh",
    "secret": "",          # shared key: only Pis with the same key can join
    "node_name": "",       # blank = hostname
    "peers": [],           # extra IPs to reach (other subnets, VPN, Tailscale)
    "mesh_port": 47474,
    "web_port": 8470,
}


# --------------------------------------------------------------------------- utils
def read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    return cfg


def stable_node_id():
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        s = read(p).strip()
        if s:
            return hashlib.sha1(s.encode()).hexdigest()[:10]
    return hashlib.sha1(str(uuid.getnode()).encode()).hexdigest()[:10]


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packet is sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# ----------------------------------------------------------------------- stats
class Collector:
    """Reads live system stats straight from /proc and /sys."""

    def __init__(self, name=""):
        self.name = name or socket.gethostname()
        model = read("/proc/device-tree/model").replace("\x00", "").strip()
        osr = {}
        for line in read("/etc/os-release").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                osr[k] = v.strip('"')
        self.static = {
            "name": self.name,
            "model": model or f"{platform.system()} {platform.machine()}",
            "os": osr.get("PRETTY_NAME", platform.system()),
            "kernel": platform.release(),
            "arch": platform.machine(),
            "cores": os.cpu_count() or 1,
            "python": platform.python_version(),
        }
        self._cpu = None
        self._net = None

    def cpu_pct(self):
        parts = read("/proc/stat").splitlines()
        if not parts:
            return 0.0
        vals = list(map(int, parts[0].split()[1:]))
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        prev, self._cpu = self._cpu, (idle, total)
        if not prev or total == prev[1]:
            return 0.0
        return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)

    def net(self):
        rx = tx = 0
        for line in read("/proc/net/dev").splitlines()[2:]:
            iface, data = line.split(":", 1)
            if iface.strip() == "lo":
                continue
            f = data.split()
            rx += int(f[0])
            tx += int(f[8])
        now = time.time()
        prev, self._net = self._net, (now, rx, tx)
        if not prev:
            return rx, tx, 0, 0
        dt = max(now - prev[0], 0.001)
        return rx, tx, max(0, (rx - prev[1]) / dt), max(0, (tx - prev[2]) / dt)

    def temp(self):
        best = None
        for i in range(4):
            t = read(f"/sys/class/thermal/thermal_zone{i}/temp").strip()
            if t.lstrip("-").isdigit():
                v = int(t) / 1000
                best = v if best is None else max(best, v)
        return round(best, 1) if best is not None else None

    def sample(self):
        mem = {}
        for line in read("/proc/meminfo").splitlines():
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) * 1024
        mt = mem.get("MemTotal", 0)
        ma = mem.get("MemAvailable", mem.get("MemFree", 0))
        du = shutil.disk_usage("/")
        rx, tx, rxr, txr = self.net()
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except (OSError, AttributeError):   # Windows has no load average
            load = [0, 0, 0]
        up = read("/proc/uptime").split()
        return dict(
            self.static,
            ip=local_ip(),
            cpu=self.cpu_pct(),
            load=load,
            temp=self.temp(),
            mem_total=mt,
            mem_used=mt - ma,
            disk_total=du.total,
            disk_used=du.used,
            rx_total=rx,
            tx_total=tx,
            rx_rate=round(rxr),
            tx_rate=round(txr),
            uptime=float(up[0]) if up else 0,
        )


# ------------------------------------------------------------------------ mesh
class Mesh:
    def __init__(self, cfg, node_id=None):
        self.cfg = cfg
        self.id = node_id or stable_node_id()
        self.run = uuid.uuid4().hex[:8]          # changes every restart
        self.collector = Collector(cfg.get("node_name"))
        self.secret = (cfg.get("secret") or "").encode()
        self.port = int(cfg["mesh_port"])
        self.lock = threading.RLock()
        self.nodes = {}
        self.rtt = {}              # peer id -> smoothed round-trip ms (my view)
        self.learned = {}          # ip -> last time someone gossiped it
        self.events = deque(maxlen=300)
        self.seq = 0
        self.started = time.time()
        self.my_ip = local_ip()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", self.port))

    # --- wire format: "<hmac>|<json>"
    def _sign(self, body):
        if not self.secret:
            return "-"
        return hmac.new(self.secret, body, hashlib.sha256).hexdigest()[:32]

    def pack(self, obj):
        body = json.dumps(obj, separators=(",", ":")).encode()
        return self._sign(body).encode() + b"|" + body

    def unpack(self, data):
        sig, _, body = data.partition(b"|")
        if self.secret and not hmac.compare_digest(sig.decode("ascii", "ignore"), self._sign(body)):
            return None
        obj = json.loads(body)
        if obj.get("mesh") != self.cfg["mesh_name"]:
            return None
        return obj

    def event(self, kind, name, msg):
        self.events.append({"t": time.time(), "kind": kind, "name": name, "msg": msg})

    # --- member table
    def ingest(self, pkt, ip, is_self=False):
        """Record a beacon. Returns True if it was new (not a duplicate)."""
        now = time.time()
        nid = pkt["id"]
        name = pkt["stats"].get("name", nid)
        with self.lock:
            n = self.nodes.get(nid)
            if n and n["run"] == pkt["run"] and pkt["seq"] <= n["seq"]:
                return False                                   # duplicate
            if n is None:
                n = {
                    "id": nid, "first_seen": now, "seq": 0, "run": None,
                    "status": "new", "self": is_self,
                    "hist": {k: deque(maxlen=HISTORY) for k in ("t", "cpu", "temp", "mem")},
                }
                self.nodes[nid] = n
                self.event("join", name, "This node started the mesh" if is_self else f"Joined the mesh from {ip}")
            elif n["status"] == "offline":
                self.event("rejoin", name, f"Back online at {ip}")
            elif n["run"] and n["run"] != pkt["run"]:
                self.event("restart", name, "PiMesh restarted on this node")
            st = pkt["stats"]
            n.update(run=pkt["run"], seq=pkt["seq"], ip=ip, web=pkt.get("web", 8470),
                     version=pkt.get("v"), stats=st, links=pkt.get("links", {}),
                     last_seen=now, status="online", name=name)
            h = n["hist"]
            h["t"].append(round(now))
            h["cpu"].append(st.get("cpu"))
            h["temp"].append(st.get("temp"))
            mt = st.get("mem_total") or 1
            h["mem"].append(round(100 * st.get("mem_used", 0) / mt, 1))
            return True

    def sweep_loop(self):
        while True:
            now = time.time()
            with self.lock:
                for nid, n in list(self.nodes.items()):
                    if n["self"]:
                        continue
                    age = now - n["last_seen"]
                    new = "online" if age < STALE_AFTER else "stale" if age < OFFLINE_AFTER else "offline"
                    if new == "offline" and n["status"] != "offline":
                        self.event("leave", n["name"], f"Lost contact ({int(age)}s silent)")
                        self.rtt.pop(nid, None)
                    elif new == "stale" and n["status"] == "online":
                        self.event("stale", n["name"], "Missed heartbeats")
                    n["status"] = new
                    if age > FORGET_AFTER:
                        del self.nodes[nid]
                for ip, t in list(self.learned.items()):
                    if now - t > 600:
                        del self.learned[ip]
            time.sleep(1)

    # --- network
    def targets(self):
        t = {("<broadcast>", self.port)}
        t |= {(ip, self.port) for ip in self.cfg.get("peers", [])}
        t |= {(ip, self.port) for ip in self.learned}
        return t

    def beacon_loop(self):
        while True:
            try:
                st = self.collector.sample()
                self.my_ip = st["ip"]
                self.seq += 1
                with self.lock:
                    alive = {k: n for k, n in self.nodes.items() if not n["self"] and n["status"] != "offline"}
                    peers = sorted({n["ip"] for n in alive.values()})
                    links = {k: round(v, 2) for k, v in self.rtt.items() if k in alive}
                pkt = {"t": "beacon", "mesh": self.cfg["mesh_name"], "id": self.id, "run": self.run,
                       "seq": self.seq, "ts": time.time(), "web": int(self.cfg["web_port"]), "v": VERSION,
                       "stats": st, "links": links, "peers": peers}
                self.ingest(pkt, st["ip"], is_self=True)
                data = self.pack(pkt)
                for addr in self.targets():
                    try:
                        self.sock.sendto(data, addr)
                    except OSError:
                        pass
            except Exception as e:  # keep the heartbeat alive no matter what
                print("beacon error:", e, flush=True)
            time.sleep(BEACON_EVERY)

    def recv_loop(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(65535)
                obj = self.unpack(data)
            except Exception:
                continue
            if not obj or obj.get("id") == self.id:
                continue
            if obj.get("t") == "beacon":
                if self.ingest(obj, addr[0]):
                    pong = {"t": "pong", "mesh": self.cfg["mesh_name"], "id": self.id,
                            "to": obj["id"], "echo": obj["ts"]}
                    try:
                        self.sock.sendto(self.pack(pong), addr)
                    except OSError:
                        pass
                    with self.lock:
                        for ip in obj.get("peers", []):
                            if ip != self.my_ip:
                                self.learned[ip] = time.time()
            elif obj.get("t") == "pong" and obj.get("to") == self.id:
                ms = max(0.0, (time.time() - obj["echo"]) * 1000)
                with self.lock:
                    prev = self.rtt.get(obj["id"])
                    self.rtt[obj["id"]] = ms if prev is None else prev * 0.7 + ms * 0.3

    # --- API
    def snapshot(self):
        now = time.time()
        with self.lock:
            nodes = []
            for n in self.nodes.values():
                nodes.append({
                    "id": n["id"], "name": n["name"], "ip": n["ip"], "web": n["web"],
                    "self": n["self"], "status": n["status"], "version": n.get("version"),
                    "age": round(now - n["last_seen"], 1), "first_seen": n["first_seen"],
                    "stats": n["stats"], "links": n["links"],
                    "hist": {k: list(v) for k, v in n["hist"].items()},
                })
            online = sorted(n["id"] for n in nodes if n["status"] != "offline")
            return {
                "mesh": self.cfg["mesh_name"], "version": VERSION, "self_id": self.id,
                "secured": bool(self.secret), "now": now, "started": self.started,
                "coordinator": online[0] if online else None,
                "nodes": sorted(nodes, key=lambda x: (not x["self"], x["name"])),
                "events": list(self.events)[-80:][::-1],
            }

    def start(self):
        for fn in (self.recv_loop, self.beacon_loop, self.sweep_loop):
            threading.Thread(target=fn, daemon=True).start()


# ------------------------------------------------------------------------ demo
class DemoFleet:
    """Simulated Pis so you can try the dashboard on any computer."""

    FLEET = [
        ("pi-kitchen", "Raspberry Pi 5 Model B Rev 1.0", 4, 8),
        ("pi-garage", "Raspberry Pi 4 Model B Rev 1.5", 4, 4),
        ("pi400-den", "Raspberry Pi 400 Rev 1.1", 4, 4),
        ("pi-office", "Raspberry Pi 5 Model B Rev 1.0", 4, 16),
        ("pi-zero-door", "Raspberry Pi Zero 2 W Rev 1.0", 4, 0.5),
        ("pi-shed-cam", "Raspberry Pi 3 Model B Plus Rev 1.3", 4, 1),
    ]

    def __init__(self, mesh):
        self.mesh = mesh
        self.t0 = time.time()
        self.nodes = []
        for i, (name, model, cores, gb) in enumerate(self.FLEET):
            self.nodes.append({
                "id": hashlib.sha1(name.encode()).hexdigest()[:10], "run": uuid.uuid4().hex[:8],
                "seq": 0, "ip": f"192.168.1.{41 + i * 3}", "cpu": random.uniform(5, 40),
                "temp": random.uniform(42, 58), "mem": random.uniform(0.2, 0.6),
                "disk": random.uniform(0.2, 0.8), "up": random.uniform(3600, 86400 * 20),
                "rx": random.randint(10**8, 10**10), "tx": random.randint(10**8, 10**10),
                "base_rtt": random.uniform(0.8, 6) * (4 if "zero" in name else 1),
                "static": {"name": name, "model": model, "cores": cores, "arch": "aarch64",
                           "os": "Debian GNU/Linux 12 (bookworm)", "kernel": "6.6.51+rpt-rpi-v8",
                           "python": "3.11.2", "_gb": gb},
            })

    def online(self, n, el):
        if n["static"]["name"] == "pi-shed-cam":
            return el > 12                      # joins late
        if n["static"]["name"] == "pi-zero-door":
            return not (60 < el % 150 < 100)    # flaky wifi
        return True

    def loop(self):
        while True:
            el = time.time() - self.t0
            ids = [n["id"] for n in self.nodes if self.online(n, el)] + [self.mesh.id]
            for n in self.nodes:
                if not self.online(n, el):
                    continue
                n["seq"] += 1
                n["cpu"] = min(98, max(1, n["cpu"] + random.gauss(0, 6) + (25 if random.random() < 0.03 else 0)))
                n["temp"] = min(84, max(38, n["temp"] + (n["cpu"] - 30) * 0.02 + random.gauss(0, 0.4) - (n["temp"] - 50) * 0.05))
                n["mem"] = min(0.95, max(0.1, n["mem"] + random.gauss(0, 0.01)))
                rxr, txr = random.randint(2_000, 900_000), random.randint(1_000, 300_000)
                n["rx"] += rxr * 2
                n["tx"] += txr * 2
                n["up"] += BEACON_EVERY
                mt = int(n["static"]["_gb"] * 1024**3)
                st = {k: v for k, v in n["static"].items() if k != "_gb"}
                st.update(ip=n["ip"], cpu=round(n["cpu"], 1), temp=round(n["temp"], 1),
                          load=[round(n["cpu"] / 25, 2)] * 3, mem_total=mt, mem_used=int(mt * n["mem"]),
                          disk_total=64 * 10**9, disk_used=int(64 * 10**9 * n["disk"]),
                          rx_total=n["rx"], tx_total=n["tx"], rx_rate=rxr, tx_rate=txr, uptime=n["up"])
                links = {o: round(n["base_rtt"] + random.uniform(0, 2.5), 2) for o in ids if o != n["id"]}
                pkt = {"t": "beacon", "mesh": self.mesh.cfg["mesh_name"], "id": n["id"], "run": n["run"],
                       "seq": n["seq"], "ts": time.time(), "web": 8470, "v": VERSION, "stats": st, "links": links}
                self.mesh.ingest(pkt, n["ip"])
                with self.mesh.lock:
                    self.mesh.rtt[n["id"]] = n["base_rtt"] + random.uniform(0, 2)
            time.sleep(BEACON_EVERY)


# ------------------------------------------------------------------------- web
def make_handler(mesh):
    html_path = os.path.join(HERE, "dashboard.html")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                with open(html_path, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/api/mesh":
                self._send(200, json.dumps(mesh.snapshot()).encode(), "application/json")
            elif path == "/icon-256.png":
                with open(os.path.join(HERE, "icon-256.png"), "rb") as f:
                    self._send(200, f.read(), "image/png")
            elif path == "/pm.js":
                # lets the My Apps menu tile find a running PiMesh without opening the wrong app
                body = f"window.__pimesh=(window.__pimesh||[]).concat([{int(mesh.cfg['web_port'])}]);".encode()
                self._send(200, body, "application/javascript")
            elif path == "/healthz":
                self._send(200, b"ok", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

    return Handler


def main():
    ap = argparse.ArgumentParser(description="PiMesh node")
    ap.add_argument("--config", default=os.path.join(HERE, "pimesh.json"))
    ap.add_argument("--demo", action="store_true", help="add simulated Pis to try the dashboard")
    ap.add_argument("--name", help="override node name")
    ap.add_argument("--web-port", type=int)
    ap.add_argument("--mesh", help="override mesh name")
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    ap.add_argument("--id", help=argparse.SUPPRESS)
    a = ap.parse_args()

    cfg = load_config(a.config)
    if a.name:
        cfg["node_name"] = a.name
    if a.web_port:
        cfg["web_port"] = a.web_port
    if a.mesh:
        cfg["mesh_name"] = a.mesh

    mesh = Mesh(cfg, node_id=a.id)
    mesh.start()
    if a.demo:
        threading.Thread(target=DemoFleet(mesh).loop, daemon=True).start()

    web = ThreadingHTTPServer(("0.0.0.0", int(cfg["web_port"])), make_handler(mesh))
    print(f"PiMesh {VERSION}  node={mesh.collector.name} ({mesh.id})  mesh='{cfg['mesh_name']}'"
          f"{'  [secured]' if mesh.secret else ''}", flush=True)
    print(f"Dashboard: http://{local_ip()}:{cfg['web_port']}", flush=True)
    if a.open:
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{cfg['web_port']}/")).start()
    try:
        web.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
