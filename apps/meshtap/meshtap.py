#!/usr/bin/env python3
"""
MeshTap — see everything going through your Meshtastic radio.

Connects to a Meshtastic node over USB/serial, WiFi (TCP) or Bluetooth,
captures every packet the radio hands over, saves it to a local database
(~/.meshtap/meshtap.db) and serves a live dashboard at http://localhost:8742

  python meshtap.py                 # auto-detect a USB radio
  python meshtap.py --serial COM5   # specific port (Windows)  /dev/ttyACM0 (Pi)
  python meshtap.py --tcp 192.168.1.50
  python meshtap.py --ble           # Bluetooth (pick device in the app)
  python meshtap.py --demo          # fake traffic, no radio needed
  python meshtap.py --lan           # let phones/other PCs on your network view it
"""
import argparse
import collections
import csv
import io
import json
import os
import queue
import random
import sqlite3
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = "1.0.0"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")
DATA_DIR = os.path.join(os.path.expanduser("~"), ".meshtap")
DB_PATH = os.path.join(DATA_DIR, "meshtap.db")
BROADCAST = 0xFFFFFFFF

try:
    import meshtastic  # noqa: F401
    import meshtastic.serial_interface
    import meshtastic.tcp_interface
    from pubsub import pub

    HAVE_MESH = True
    MESH_ERR = ""
except Exception as exc:  # library missing — demo mode still works
    HAVE_MESH = False
    MESH_ERR = str(exc)


# ----------------------------------------------------------------- helpers
def node_id(num):
    if num is None:
        return "?"
    try:
        num = int(num)
    except (TypeError, ValueError):
        return str(num)
    if num == BROADCAST:
        return "^all"
    return "!%08x" % num


def clean(obj):
    """Make a packet dict JSON-safe (drop protobuf 'raw', hex-encode bytes)."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items() if k != "raw"}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


def fnum(v, fmt):
    try:
        return format(float(v), fmt)
    except (TypeError, ValueError):
        return "?"


def summarize(port, dec):
    try:
        if port == "ENCRYPTED":
            return "🔒 Encrypted — this radio doesn't have that channel's key"
        if port == "TEXT_MESSAGE_APP":
            return "💬 " + str(dec.get("text") or "")
        if port == "POSITION_APP":
            pos = dec.get("position") or {}
            lat, lon = pos.get("latitude"), pos.get("longitude")
            if lat is None and "latitudeI" in pos:
                lat = pos["latitudeI"] / 1e7
            if lon is None and "longitudeI" in pos:
                lon = pos["longitudeI"] / 1e7
            if lat is None or lon is None:
                return "📍 Position (no GPS fix)"
            s = f"📍 {fnum(lat, '.5f')}, {fnum(lon, '.5f')}"
            if pos.get("altitude") is not None:
                s += f"  alt {pos['altitude']} m"
            return s
        if port == "NODEINFO_APP":
            u = dec.get("user") or {}
            return f"🪪 {u.get('longName', '?')} [{u.get('shortName', '')}] {u.get('hwModel', '')}".strip()
        if port == "TELEMETRY_APP":
            t = dec.get("telemetry") or {}
            parts = []
            dm = t.get("deviceMetrics")
            if dm:
                if "batteryLevel" in dm:
                    parts.append(f"🔋 {dm['batteryLevel']}%")
                if "voltage" in dm:
                    parts.append(f"{fnum(dm['voltage'], '.2f')} V")
                if "channelUtilization" in dm:
                    parts.append(f"ch util {fnum(dm['channelUtilization'], '.1f')}%")
                if "airUtilTx" in dm:
                    parts.append(f"air TX {fnum(dm['airUtilTx'], '.1f')}%")
            em = t.get("environmentMetrics")
            if em:
                if "temperature" in em:
                    parts.append(f"🌡 {fnum(em['temperature'], '.1f')} °C")
                if "relativeHumidity" in em:
                    parts.append(f"💧 {fnum(em['relativeHumidity'], '.0f')}%")
                if "barometricPressure" in em:
                    parts.append(f"{fnum(em['barometricPressure'], '.0f')} hPa")
            if t.get("powerMetrics"):
                parts.append("⚡ power metrics")
            ls = t.get("localStats")
            if ls:
                parts.append(f"📊 {ls.get('numPacketsRx', '?')} rx / {ls.get('numPacketsTx', '?')} tx")
            return "📈 " + ("  ·  ".join(parts) if parts else "Telemetry")
        if port == "ROUTING_APP":
            r = dec.get("routing") or {}
            if "routeRequest" in r:
                return "🧭 Route request"
            if "routeReply" in r:
                return "🧭 Route reply"
            err = r.get("errorReason", "NONE")
            return "✅ ACK (delivered)" if err in ("NONE", 0, None) else f"⚠️ Routing error: {err}"
        if port == "TRACEROUTE_APP":
            tr = dec.get("traceroute") or {}
            route, back = tr.get("route") or [], tr.get("routeBack") or []
            s = f"🧭 Traceroute — {len(route)} hop(s) out"
            if back:
                s += f", {len(back)} back"
            return s
        if port == "NEIGHBORINFO_APP":
            ni = dec.get("neighborinfo") or {}
            return f"🕸 Neighbor report — {len(ni.get('neighbors') or [])} neighbor(s)"
        if port == "WAYPOINT_APP":
            w = dec.get("waypoint") or {}
            return f"🚩 Waypoint {w.get('name', '')}".strip()
        simple = {
            "ADMIN_APP": "🛠 Admin message",
            "STORE_FORWARD_APP": "📦 Store & Forward",
            "PAXCOUNTER_APP": "👥 Paxcounter",
            "MAP_REPORT_APP": "🗺 Map report",
            "ALERT_APP": "🚨 Alert " + str(dec.get("text") or ""),
            "RANGE_TEST_APP": "📶 Range test " + str(dec.get("text") or ""),
            "DETECTION_SENSOR_APP": "🚨 Detection sensor " + str(dec.get("text") or ""),
        }
        if port in simple:
            return simple[port].strip()
        payload = dec.get("payload")
        size = len(payload) // 2 if isinstance(payload, str) else 0
        return f"{port} ({size} bytes)"
    except Exception as exc:  # never let one odd packet break capture
        return f"{port} (couldn't summarize: {exc})"


# ----------------------------------------------------------------- the hub
class Hub:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.lock = threading.RLock()
        self.packets = collections.deque(maxlen=3000)
        self.logs = collections.deque(maxlen=1500)
        self.clients = []
        self.nodes = {}
        self.iface = None
        self.mode = None
        self.desc = "Not connected"
        self.connected = False
        self.connecting = False
        self.error = ""
        self.my_id = None
        self.demo_stop = None
        self.seq = 0
        self._reset_counters()
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS packets (ts REAL, port TEXT, data TEXT)")
        self.db.commit()
        self._load_history()

    def _reset_counters(self):
        self.total = 0
        self.encrypted = 0
        self.texts = 0
        self.port_counts = collections.Counter()
        self.per_minute = collections.OrderedDict()

    def _load_history(self):
        rows = self.db.execute("SELECT data FROM packets ORDER BY rowid DESC LIMIT 2000").fetchall()
        for (data,) in reversed(rows):
            try:
                rec = json.loads(data)
            except ValueError:
                continue
            self.seq += 1
            rec["k"] = self.seq
            self.packets.append(rec)
            self._account(rec)
        self.saved_total = self.db.execute("SELECT COUNT(*) FROM packets").fetchone()[0]

    # ---- live streaming to browsers
    def broadcast(self, msg):
        data = json.dumps(msg, default=str)
        with self.lock:
            for q in list(self.clients):
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass

    def status(self):
        my = self.nodes.get(self.my_id, {}) if self.my_id else {}
        return {
            "connected": self.connected,
            "connecting": self.connecting,
            "mode": self.mode,
            "desc": self.desc,
            "error": self.error,
            "my_id": self.my_id,
            "my_name": my.get("long") or self.my_id,
            "have_lib": HAVE_MESH,
            "lib_error": MESH_ERR,
            "version": VERSION,
        }

    def push_status(self):
        self.broadcast({"type": "status", "status": self.status()})

    def name_of(self, nid):
        if nid == "^all":
            return "Everyone"
        n = self.nodes.get(nid)
        if n and n.get("long"):
            return n["long"]
        return nid

    # ---- packet handling
    def on_packet(self, packet):
        p = clean(packet)
        dec = p.get("decoded") or {}
        if dec:
            port = str(dec.get("portnum") or "UNKNOWN_APP")
        elif "encrypted" in p:
            port = "ENCRYPTED"
        else:
            port = "UNKNOWN_APP"
        frm = p.get("fromId") or node_id(p.get("from"))
        to = p.get("toId") or node_id(p.get("to"))
        hop_start, hop_limit = p.get("hopStart"), p.get("hopLimit")
        hops = hop_start - hop_limit if isinstance(hop_start, int) and isinstance(hop_limit, int) else None
        rec = {
            "ts": time.time(),
            "id": p.get("id"),
            "from": frm,
            "to": to,
            "port": port,
            "channel": p.get("channel", 0),
            "snr": p.get("rxSnr"),
            "rssi": p.get("rxRssi"),
            "hops": hops,
            "hop_limit": hop_limit,
            "via_mqtt": bool(p.get("viaMqtt")),
            "want_ack": bool(p.get("wantAck")),
            "dir": "out" if self.my_id and frm == self.my_id else "in",
            "summary": summarize(port, dec),
            "raw": p,
        }
        with self.lock:
            node = self._account(rec)
            rec["from_name"] = self.name_of(frm)
            rec["to_name"] = self.name_of(to)
            self.seq += 1
            rec["k"] = self.seq
            self.packets.append(rec)
            try:
                self.db.execute(
                    "INSERT INTO packets VALUES (?,?,?)",
                    (rec["ts"], port, json.dumps(rec, default=str)),
                )
                self.db.commit()
                self.saved_total += 1
            except sqlite3.Error as exc:
                self.add_log(f"[MeshTap] database write failed: {exc}")
        self.broadcast({"type": "packet", "packet": rec, "node": node})

    def _account(self, rec):
        """Update counters + per-node stats. Returns the sender's node record."""
        self.total += 1
        self.port_counts[rec["port"]] += 1
        if rec["port"] == "ENCRYPTED":
            self.encrypted += 1
        if rec["port"] == "TEXT_MESSAGE_APP":
            self.texts += 1
        minute = int(rec["ts"] // 60) * 60
        self.per_minute[minute] = self.per_minute.get(minute, 0) + 1
        while len(self.per_minute) > 180:
            self.per_minute.popitem(last=False)

        frm = rec["from"]
        n = self.nodes.setdefault(frm, {"id": frm, "packets": 0, "ports": {}})
        n["packets"] = n.get("packets", 0) + 1
        n["last"] = rec["ts"]
        n["ports"][rec["port"]] = n["ports"].get(rec["port"], 0) + 1
        for key in ("snr", "rssi", "hops"):
            if rec.get(key) is not None:
                n[key] = rec[key]
        if rec.get("via_mqtt"):
            n["mqtt"] = True
        dec = (rec.get("raw") or {}).get("decoded") or {}
        if rec["port"] == "NODEINFO_APP":
            u = dec.get("user") or {}
            n["long"] = u.get("longName") or n.get("long")
            n["short"] = u.get("shortName") or n.get("short")
            n["hw"] = u.get("hwModel") or n.get("hw")
            n["role"] = u.get("role") or n.get("role")
        elif rec["port"] == "TELEMETRY_APP":
            dm = (dec.get("telemetry") or {}).get("deviceMetrics") or {}
            if "batteryLevel" in dm:
                n["battery"] = dm["batteryLevel"]
            if "voltage" in dm:
                n["voltage"] = dm["voltage"]
            if "channelUtilization" in dm:
                n["ch_util"] = dm["channelUtilization"]
        elif rec["port"] == "POSITION_APP":
            pos = dec.get("position") or {}
            lat = pos.get("latitude", pos.get("latitudeI", 0) / 1e7 if "latitudeI" in pos else None)
            lon = pos.get("longitude", pos.get("longitudeI", 0) / 1e7 if "longitudeI" in pos else None)
            if lat is not None and lon is not None:
                n["lat"], n["lon"] = lat, lon
        return n

    def seed_nodes(self, iface):
        """Pull in the node list the radio already knows about."""
        try:
            nodes = dict(getattr(iface, "nodes", None) or {})
        except Exception:
            nodes = {}
        with self.lock:
            for nid, info in nodes.items():
                info = clean(info)
                n = self.nodes.setdefault(nid, {"id": nid, "packets": 0, "ports": {}})
                u = info.get("user") or {}
                n["long"] = u.get("longName") or n.get("long")
                n["short"] = u.get("shortName") or n.get("short")
                n["hw"] = u.get("hwModel") or n.get("hw")
                if info.get("lastHeard") and not n.get("last"):
                    n["last"] = info["lastHeard"]
                if info.get("snr") is not None and n.get("snr") is None:
                    n["snr"] = info["snr"]
                if info.get("hopsAway") is not None and n.get("hops") is None:
                    n["hops"] = info["hopsAway"]
                dm = info.get("deviceMetrics") or {}
                if "batteryLevel" in dm and n.get("battery") is None:
                    n["battery"] = dm["batteryLevel"]
                pos = info.get("position") or {}
                if pos.get("latitude") is not None and n.get("lat") is None:
                    n["lat"], n["lon"] = pos.get("latitude"), pos.get("longitude")
                n["known"] = True
            try:
                my = iface.getMyNodeInfo() or {}
                self.my_id = (my.get("user") or {}).get("id") or node_id(my.get("num"))
            except Exception:
                pass

    def add_log(self, line):
        line = str(line).rstrip()
        if not line:
            return
        entry = {"ts": time.time(), "line": line}
        self.logs.append(entry)
        self.broadcast({"type": "log", "log": entry})

    # ---- connection management
    def connect(self, mode, address=""):
        self.disconnect()
        self.mode, self.error, self.connecting = mode, "", True
        self.desc = {"serial": "USB", "tcp": "WiFi", "ble": "Bluetooth", "demo": "Demo"}.get(mode, mode)
        self.desc += f" {address}" if address else (" (auto-detect)" if mode in ("serial", "ble") else "")
        self.push_status()
        threading.Thread(target=self._connect_worker, args=(mode, address), daemon=True).start()

    def _connect_worker(self, mode, address):
        try:
            if mode == "demo":
                self.demo_stop = threading.Event()
                threading.Thread(target=demo_loop, args=(self, self.demo_stop), daemon=True).start()
                self.connected, self.connecting = True, False
                self.add_log("[MeshTap] demo mode started — this is fake traffic")
                self.push_status()
                return
            if not HAVE_MESH:
                raise RuntimeError(f"The meshtastic Python library isn't installed ({MESH_ERR}). Run the start script again.")
            self.add_log(f"[MeshTap] connecting via {mode} {address or ''}...")
            if mode == "serial":
                iface = meshtastic.serial_interface.SerialInterface(devPath=address or None)
            elif mode == "tcp":
                if not address:
                    raise RuntimeError("Enter your radio's IP address or hostname (e.g. meshtastic.local).")
                iface = meshtastic.tcp_interface.TCPInterface(hostname=address)
            elif mode == "ble":
                from meshtastic.ble_interface import BLEInterface
                iface = BLEInterface(address or None)
            else:
                raise RuntimeError(f"Unknown connection type: {mode}")
            if self.mode != mode:  # user cancelled while we were connecting
                iface.close()
                return
            self.iface = iface
            self.seed_nodes(iface)
            self.connected, self.connecting = True, False
            self.desc = self.desc.replace("(auto-detect)", "").strip()
            port = getattr(iface, "devPath", None)
            if port and mode == "serial":
                self.desc = f"USB {port}"
            self.add_log(f"[MeshTap] connected — {len(self.nodes)} nodes known, listening for traffic")
        except Exception as exc:
            self.connected, self.connecting = False, False
            self.error = str(exc) or exc.__class__.__name__
            if mode == "serial" and "None" in self.error:
                self.error = "No Meshtastic radio found on USB. Plug it in (data cable, not charge-only) or pick a port."
            self.add_log(f"[MeshTap] connection failed: {self.error}")
        self.push_status()

    def disconnect(self):
        if self.demo_stop:
            self.demo_stop.set()
            self.demo_stop = None
        iface, self.iface = self.iface, None
        if iface:
            try:
                iface.close()
            except Exception:
                pass
        was = self.connected
        self.connected = self.connecting = False
        self.mode = None
        self.desc = "Not connected"
        if was:
            self.add_log("[MeshTap] disconnected")
        self.push_status()

    def clear(self):
        with self.lock:
            self.packets.clear()
            self.nodes = {}
            self._reset_counters()
            self.db.execute("DELETE FROM packets")
            self.db.commit()
            self.saved_total = 0
            if self.iface:
                self.seed_nodes(self.iface)
        self.broadcast({"type": "cleared"})

    def state(self):
        with self.lock:
            nodes = []
            for n in self.nodes.values():
                n = dict(n)
                n["name"] = n.get("long") or n["id"]
                nodes.append(n)
            return {
                "status": self.status(),
                "stats": {
                    "total": self.total,
                    "saved_total": self.saved_total,
                    "encrypted": self.encrypted,
                    "texts": self.texts,
                    "ports": dict(self.port_counts),
                    "per_minute": list(self.per_minute.items())[-60:],
                    "db_path": DB_PATH,
                },
                "nodes": nodes,
            }


# ------------------------------------------------- meshtastic event wiring
HUB = None


def _on_receive(packet, interface):
    if HUB and HUB.mode not in (None, "demo"):
        HUB.on_packet(packet)


def _on_lost(interface):
    if HUB and HUB.iface is interface:
        HUB.connected = False
        HUB.error = "Lost connection to the radio"
        HUB.add_log("[MeshTap] connection lost")
        HUB.push_status()


def _on_log(line, interface):
    if HUB:
        HUB.add_log(line)


def _on_node(node, interface):
    if HUB and HUB.iface is interface:
        n = clean(node)
        nid = (n.get("user") or {}).get("id")
        if nid:
            with HUB.lock:
                rec = HUB.nodes.setdefault(nid, {"id": nid, "packets": 0, "ports": {}})
                u = n.get("user") or {}
                rec["long"] = u.get("longName") or rec.get("long")
                rec["short"] = u.get("shortName") or rec.get("short")
                rec["hw"] = u.get("hwModel") or rec.get("hw")


if HAVE_MESH:
    pub.subscribe(_on_receive, "meshtastic.receive")
    pub.subscribe(_on_lost, "meshtastic.connection.lost")
    pub.subscribe(_on_log, "meshtastic.log.line")
    pub.subscribe(_on_node, "meshtastic.node.updated")


# ------------------------------------------------------------- demo traffic
DEMO_NODES = [
    (0x9E7C1A20, "Basecamp Router", "BASE", "RAK4631"),
    (0x4A11F0C3, "Truck Mobile", "TRK1", "HELTEC_V3"),
    (0x0B2D77E4, "Ridge Repeater", "RDG", "STATION_G2"),
    (0x77AA0912, "Kitchen Node", "KTCH", "T_ECHO"),
    (0x3C5E8B01, "Hiker Dave", "DAVE", "TBEAM"),
    (0xD00DFEED, "Solar Relay 2", "SOL2", "RAK4631"),
    (0x1234ABCD, "Weather Mast", "WX", "HELTEC_WSL_V3"),
]
DEMO_TEXTS = [
    "Anyone copy?", "Checking in from the ridge", "Heading home now", "Signal is great today",
    "Battery at 40%, swapping soon", "Test 1 2 3", "Good morning mesh!", "Who's on the north side?",
]


def demo_loop(hub, stop):
    me = DEMO_NODES[0]
    hub.my_id = node_id(me[0])
    for num, long_, short, hw in DEMO_NODES:
        n = hub.nodes.setdefault(node_id(num), {"id": node_id(num), "packets": 0, "ports": {}})
        n.update({"long": long_, "short": short, "hw": hw, "known": True})
    weights = [("TEXT_MESSAGE_APP", 3), ("POSITION_APP", 5), ("TELEMETRY_APP", 6), ("NODEINFO_APP", 2),
               ("ROUTING_APP", 3), ("TRACEROUTE_APP", 1), ("NEIGHBORINFO_APP", 1), ("ENCRYPTED", 2)]
    ports = [p for p, w in weights for _ in range(w)]
    while not stop.wait(random.uniform(0.6, 2.8)):
        num, long_, short, hw = random.choice(DEMO_NODES[1:])
        port = random.choice(ports)
        hop_start = 3
        pkt = {
            "from": num, "to": BROADCAST, "fromId": node_id(num), "toId": "^all",
            "id": random.randint(1, 2**31), "channel": 0, "rxTime": int(time.time()),
            "rxSnr": round(random.uniform(-14, 11), 2), "rxRssi": random.randint(-122, -58),
            "hopStart": hop_start, "hopLimit": random.randint(0, hop_start),
        }
        dec = {"portnum": port}
        if port == "TEXT_MESSAGE_APP":
            dec["text"] = random.choice(DEMO_TEXTS)
            if random.random() < 0.3:
                pkt["to"], pkt["toId"] = me[0], node_id(me[0])
        elif port == "POSITION_APP":
            dec["position"] = {"latitude": 42.33 + random.uniform(-0.08, 0.08),
                               "longitude": -83.05 + random.uniform(-0.08, 0.08),
                               "altitude": random.randint(170, 260)}
        elif port == "TELEMETRY_APP":
            if num == 0x1234ABCD:
                dec["telemetry"] = {"environmentMetrics": {"temperature": round(random.uniform(6, 14), 1),
                                                           "relativeHumidity": random.randint(50, 90),
                                                           "barometricPressure": random.randint(1005, 1022)}}
            else:
                dec["telemetry"] = {"deviceMetrics": {"batteryLevel": random.randint(30, 101),
                                                      "voltage": round(random.uniform(3.6, 4.2), 2),
                                                      "channelUtilization": round(random.uniform(2, 25), 2),
                                                      "airUtilTx": round(random.uniform(0.1, 3), 2)}}
        elif port == "NODEINFO_APP":
            dec["user"] = {"id": node_id(num), "longName": long_, "shortName": short, "hwModel": hw}
        elif port == "ROUTING_APP":
            dec["routing"] = {"errorReason": random.choice(["NONE"] * 5 + ["MAX_RETRANSMIT", "NO_ROUTE"])}
            pkt["to"], pkt["toId"] = me[0], node_id(me[0])
        elif port == "TRACEROUTE_APP":
            dec["traceroute"] = {"route": [0x0B2D77E4], "routeBack": [0x0B2D77E4]}
        elif port == "NEIGHBORINFO_APP":
            dec["neighborinfo"] = {"nodeId": num, "neighbors": [{"nodeId": n[0], "snr": 5.0} for n in DEMO_NODES[:3]]}
        if port == "ENCRYPTED":
            pkt["encrypted"] = os.urandom(24)
            pkt["channel"] = random.choice([8, 31, 104])
        else:
            dec["payload"] = os.urandom(random.randint(8, 40))
            pkt["decoded"] = dec
        if random.random() < 0.08:
            hub.add_log(f"DEBUG | Received packet from 0x{num:08x}, id=0x{pkt['id']:08x}, hops {hop_start - pkt['hopLimit']}")
        hub.on_packet(pkt)


# ----------------------------------------------------------------- web server
class Handler(BaseHTTPRequestHandler):
    server_version = "MeshTap/" + VERSION

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name, ctype):
        path = os.path.join(WEB_DIR, name)
        try:
            with open(path, "rb") as fh:
                self._send(200, fh.read(), ctype)
        except OSError:
            self._send(404, {"error": "not found"})

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if url.path == "/icon.svg":
            return self._file("icon.svg", "image/svg+xml")
        if url.path == "/meshtap.js":  # lets launcher pages find a running MeshTap
            port = self.server.server_address[1]
            return self._send(200, f"(window.__meshtap=window.__meshtap||[]).push({port});", "application/javascript")
        if url.path == "/api/state":
            return self._send(200, HUB.state())
        if url.path == "/api/packets":
            limit = int((qs.get("limit") or ["1000"])[0])
            with HUB.lock:
                return self._send(200, list(HUB.packets)[-limit:])
        if url.path == "/api/logs":
            return self._send(200, list(HUB.logs))
        if url.path == "/api/ports":
            ports = []
            try:
                from serial.tools import list_ports
                for p in list_ports.comports():
                    ports.append({"device": p.device, "label": f"{p.device} — {p.description}"})
            except Exception:
                pass
            return self._send(200, ports)
        if url.path == "/api/events":
            return self._events()
        if url.path == "/api/export.csv":
            return self._export_csv()
        if url.path == "/api/export.jsonl":
            rows = HUB.db.execute("SELECT data FROM packets ORDER BY rowid").fetchall()
            body = "\n".join(r[0] for r in rows) + "\n"
            return self._send(200, body, "application/x-ndjson",
                              {"Content-Disposition": f'attachment; filename="meshtap-{time.strftime("%Y%m%d-%H%M")}.jsonl"'})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        if url.path == "/api/connect":
            HUB.connect(body.get("mode", "serial"), (body.get("address") or "").strip())
            return self._send(200, {"ok": True})
        if url.path == "/api/disconnect":
            HUB.disconnect()
            return self._send(200, {"ok": True})
        if url.path == "/api/clear":
            HUB.clear()
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def _export_csv(self):
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["time", "from", "from_name", "to", "to_name", "type", "summary", "channel",
                    "snr", "rssi", "hops", "via_mqtt", "packet_id"])
        for (data,) in HUB.db.execute("SELECT data FROM packets ORDER BY rowid"):
            try:
                r = json.loads(data)
            except ValueError:
                continue
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r.get("from"),
                        r.get("from_name"), r.get("to"), r.get("to_name"), r.get("port"), r.get("summary"),
                        r.get("channel"), r.get("snr"), r.get("rssi"), r.get("hops"), r.get("via_mqtt"), r.get("id")])
        self._send(200, out.getvalue(), "text/csv; charset=utf-8",
                   {"Content-Disposition": f'attachment; filename="meshtap-{time.strftime("%Y%m%d-%H%M")}.csv"'})

    def _events(self):
        q = queue.Queue(maxsize=2000)
        with HUB.lock:
            HUB.clients.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps({'type': 'status', 'status': HUB.status()})}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    data = q.get(timeout=15)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with HUB.lock:
                if q in HUB.clients:
                    HUB.clients.remove(q)


def auto_serial():
    try:
        import meshtastic.util
        return meshtastic.util.findPorts(True)
    except Exception:
        return []


def main():
    global HUB
    ap = argparse.ArgumentParser(description="MeshTap — live Meshtastic traffic viewer")
    ap.add_argument("--serial", nargs="?", const="", metavar="PORT", help="USB/serial port (blank = auto)")
    ap.add_argument("--tcp", metavar="HOST", help="radio IP/hostname over WiFi")
    ap.add_argument("--ble", nargs="?", const="", metavar="NAME", help="Bluetooth name/address (blank = scan)")
    ap.add_argument("--demo", action="store_true", help="fake traffic, no radio needed")
    ap.add_argument("--web-port", type=int, default=8742)
    ap.add_argument("--lan", action="store_true", help="allow other devices on your network to view")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    HUB = Hub()
    if args.demo:
        HUB.connect("demo")
    elif args.tcp:
        HUB.connect("tcp", args.tcp)
    elif args.ble is not None:
        HUB.connect("ble", args.ble)
    elif args.serial is not None:
        HUB.connect("serial", args.serial)
    elif HAVE_MESH:
        found = auto_serial()
        if len(found) == 1:
            HUB.connect("serial", found[0])
        else:
            HUB.add_log("[MeshTap] " + ("no USB radio found — use Connect to pick WiFi, Bluetooth or a port"
                                         if not found else f"several ports found ({', '.join(found)}) — pick one in Connect"))

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    server = ThreadingHTTPServer((host, args.web_port), Handler)
    server.daemon_threads = True
    url = f"http://localhost:{args.web_port}"
    print(f"\n  MeshTap {VERSION} running at {url}")
    if args.lan:
        print(f"  (also reachable from other devices on your network on port {args.web_port})")
    print(f"  Saving traffic to {DB_PATH}\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        HUB.disconnect()
        server.server_close()


if __name__ == "__main__":
    main()
