#!/usr/bin/env python3
"""
MeshTap — see everything going through your Meshtastic radio.

Connects to a Meshtastic node over USB/serial, WiFi (TCP) or Bluetooth,
captures every packet the radio hands over, saves it to a local database
(~/.meshtap/meshtap.db) and serves a live dashboard at http://localhost:8742

  python meshtap.py                 # auto-detect a USB radio
  python meshtap.py --serial COM5   # specific port (Windows)  /dev/ttyACM0 (Pi)
  python meshtap.py --serial /dev/serial0 --baud 38400   # radio wired to Pi GPIO pins
  python meshtap.py --serial-text COM7 --baud 9600        # Serial Module in TEXTMSG/NMEA mode
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

VERSION = "1.4.2"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")
DATA_DIR = os.path.join(os.path.expanduser("~"), ".meshtap")
DB_PATH = os.path.join(DATA_DIR, "meshtap.db")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
PING_MIN_INTERVAL = 30          # seconds between repeated pings (be kind to shared airtime)
PING_TIMEOUT = {"ack": 45, "text": 45, "trace": 60, "hello": 60}
BROADCAST = 0xFFFFFFFF
BAUDS = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
GPIO_UARTS = [("/dev/serial0", "Pi GPIO pins 8/10 (TX/RX) — serial0"), ("/dev/ttyAMA0", "Pi GPIO UART ttyAMA0"),
              ("/dev/serial1", "Pi second UART — serial1"), ("/dev/ttyS0", "Pi mini UART ttyS0"),
              ("/dev/ttyAMA10", "Pi 5 debug UART ttyAMA10")]


def list_serial_ports():
    """Every serial port we can see, with likely Meshtastic radios marked and starred first."""
    out, seen = [], set()
    try:
        import meshtastic.util
        likely = set(meshtastic.util.findPorts(True))
    except Exception:
        likely = set()
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            real = os.path.realpath(p.device)
            seen.update({p.device, real})
            desc = p.description if p.description and p.description != "n/a" else "serial port"
            out.append({"device": p.device, "label": f"{'★ ' if p.device in likely else ''}{p.device} — {desc}",
                        "likely": p.device in likely})
    except Exception:
        pass
    for dev, label in GPIO_UARTS:  # GPIO UARTs often don't show up in comports()
        if os.path.exists(dev) and dev not in seen and os.path.realpath(dev) not in seen:
            seen.update({dev, os.path.realpath(dev)})
            out.append({"device": dev, "label": f"{dev} — {label}", "likely": False, "gpio": True})
    out.sort(key=lambda d: not d["likely"])
    return out


def serial_hint(err, port):
    """Turn common serial errors into plain English."""
    e = str(err)
    low = e.lower()
    if "permission denied" in low or "access is denied" in low:
        if os.name == "nt":
            return f"{port} is busy. Close the Meshtastic app, web client or any other program using it, then try again."
        return (f"No permission to open {port}. Run once:  sudo usermod -aG dialout $USER  then log out and back in. "
                "(Or another program is using the port.)")
    if "could not exclusively lock" in low or "resource busy" in low or "in use" in low:
        return f"{port} is already in use by another program (Meshtastic app/web client, another MeshTap?). Close it and retry."
    if "no such file" in low or "cannot find the file" in low or "filenotfounderror" in low:
        if port.startswith("/dev/serial") or port.startswith("/dev/ttyAMA"):
            return (f"{port} doesn't exist. Turn on the Pi's serial port: sudo raspi-config → Interface Options → "
                    "Serial Port → login shell: No, hardware: Yes, then reboot.")
        return f"{port} wasn't found. Check the cable (data, not charge-only) or pick another port."
    if "timed out" in low or "timeout" in low:
        return (f"The radio on {port} didn't answer. Check the baud rate matches the radio's Serial Module "
                "(USB is always 115200) and that its mode is PROTO, or use Serial text mode for TEXTMSG/NMEA.")
    return e

try:
    import meshtastic  # noqa: F401
    import meshtastic.serial_interface
    import meshtastic.tcp_interface
    import meshtastic.stream_interface
    from pubsub import pub

    HAVE_MESH = True
    MESH_ERR = ""

    class BaudSerialInterface(meshtastic.serial_interface.SerialInterface):
        """SerialInterface that opens the port at a chosen speed (the library always uses 115200).
        Needed for radios wired to GPIO pins whose Serial Module runs at another baud rate."""

        def __init__(self, devPath, baud=115200, **kw):
            self.baud = int(baud or 115200)
            super().__init__(devPath=devPath, **kw)

        def connect(self):
            if self.baud == 115200 or not hasattr(meshtastic.serial_interface.SerialInterface, "connect"):
                return super().connect()
            import serial as _serial
            import sys as _sys
            if _sys.platform != "win32":
                try:
                    with open(self.devPath, encoding="utf8") as f:
                        self._set_hupcl_with_termios(f)
                except Exception:
                    pass
                time.sleep(0.1)
            self.stream = _serial.Serial(self.devPath, self.baud, exclusive=True, timeout=0.5, write_timeout=0)
            self.stream.flush()
            time.sleep(0.1)
            meshtastic.stream_interface.StreamInterface.connect(self)
except Exception as exc:  # library missing — demo and serial text modes still work
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


ROUTING_ERRORS = {
    "NO_ROUTE": "No route — no neighbor could reach it",
    "GOT_NAK": "Refused (NAK) by the destination",
    "TIMEOUT": "Timed out inside the mesh",
    "NO_INTERFACE": "Your radio has no interface to send it on",
    "MAX_RETRANSMIT": "Sent 3 times, no one confirmed it (out of range?)",
    "NO_CHANNEL": "The destination doesn't share this channel",
    "TOO_LARGE": "Packet too large",
    "NO_RESPONSE": "Arrived, but the node didn't answer (traceroute off or rate-limited)",
    "DUTY_CYCLE_LIMIT": "Your radio hit its duty-cycle limit — wait and try again",
    "BAD_REQUEST": "The node rejected the request",
    "NOT_AUTHORIZED": "Not authorized",
    "PKI_FAILED": "Encryption key exchange failed",
    "PKI_UNKNOWN_PUBKEY": "The node doesn't know your key yet — wait for it to hear your node info",
    "RATE_LIMIT_EXCEEDED": "Rate-limited by the mesh — wait a bit and try again",
}


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
        self.settings = {"autosave": True}
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as fh:
                self.settings.update(json.load(fh))
        except (OSError, ValueError):
            pass
        self.unsaved = []              # packets caught while auto-save was off
        self.pings = collections.deque(maxlen=500)
        self.ping_pending = {}         # packet id -> ping record
        self.ping_job = None           # repeating ping settings
        self.ping_seq = 0
        self.ping_late = {}            # cancelled pings, so a late reply can still be noted
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS packets (ts REAL, port TEXT, data TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS pings (ts REAL, data TEXT)")
        self.db.commit()
        for (data,) in self.db.execute("SELECT data FROM pings ORDER BY rowid DESC LIMIT 300").fetchall()[::-1]:
            try:
                self.pings.append(json.loads(data))
            except ValueError:
                pass
        threading.Thread(target=self._ping_watchdog, daemon=True).start()
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
            "baud": getattr(self, "baud", 115200),
            "autosave": self.settings.get("autosave", True),
            "unsaved": len(self.unsaved),
            "ping_job": self.ping_job,
            "ping_waiting": next(iter(self.ping_pending.values()), None),
            "ping_min_interval": PING_MIN_INTERVAL,
            "my_hop_limit": self.my_hop_limit(),
            "bauds": BAUDS,
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
    def on_packet(self, packet, summary=None):
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
            "summary": summary or (
                "📡 Relayed by a neighbor (your radio heard it passed on)"
                if port == "ROUTING_APP" and self.my_id and frm == self.my_id
                and (dec.get("routing") or {}).get("errorReason", "NONE") in ("NONE", 0)
                else summarize(port, dec)),
            "raw": p,
        }
        with self.lock:
            node = self._account(rec)
            rec["from_name"] = self.name_of(frm)
            rec["to_name"] = self.name_of(to)
            self.seq += 1
            rec["k"] = self.seq
            self.packets.append(rec)
            if self.settings.get("autosave", True):
                self._save_rows([rec])
            else:
                rec["unsaved"] = True
                self.unsaved.append(rec)
                if len(self.unsaved) > 20000:  # don't grow forever while unsaved
                    self.unsaved = self.unsaved[-20000:]
        self.broadcast({"type": "packet", "packet": rec, "node": node})
        self._check_ping_reply(rec, dec)

    def _save_rows(self, recs):
        try:
            self.db.executemany("INSERT INTO packets VALUES (?,?,?)",
                                [(r["ts"], r["port"], json.dumps({k: v for k, v in r.items() if k != "unsaved"}, default=str))
                                 for r in recs])
            self.db.commit()
            self.saved_total += len(recs)
            return True
        except sqlite3.Error as exc:
            self.add_log(f"[MeshTap] database write failed: {exc}")
            return False

    def set_autosave(self, on):
        with self.lock:
            self.settings["autosave"] = bool(on)
            self._write_settings()
            saved = 0
            if on and self.unsaved:  # turning it back on keeps what was caught meanwhile
                saved = self.save_now()
        self.add_log(f"[MeshTap] auto-save {'on' if on else 'off'}" + (f" — saved {saved} waiting packets" if saved else ""))
        self.push_status()

    def save_now(self):
        with self.lock:
            rows, self.unsaved = self.unsaved, []
            if rows and not self._save_rows(rows):
                self.unsaved = rows + self.unsaved
                return 0
            for r in rows:
                r.pop("unsaved", None)
        if rows:
            self.broadcast({"type": "saved", "count": len(rows)})
        return len(rows)

    def _write_settings(self):
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.settings, fh, indent=1)
        except OSError as exc:
            self.add_log(f"[MeshTap] couldn't save settings: {exc}")

    # ---- real-time ping
    def _my_user_pb(self, mesh_pb2):
        """Our own User record (name, hardware, public key) as a protobuf, for a node-info exchange."""
        from google.protobuf import json_format
        me = (self.iface.getMyNodeInfo() or {}).get("user") or {}
        user = mesh_pb2.User()
        try:
            json_format.ParseDict({k: v for k, v in me.items() if k != "raw"}, user, ignore_unknown_fields=True)
        except Exception:
            user = mesh_pb2.User(id=me.get("id", ""), long_name=me.get("longName", ""), short_name=me.get("shortName", ""))
        return user

    def my_hop_limit(self):
        try:
            return int(self.iface.localNode.localConfig.lora.hop_limit) or None
        except Exception:
            return 3 if self.mode == "demo" else None

    def _dest_num(self, nid):
        if isinstance(nid, str) and nid.startswith("!"):
            return int(nid[1:], 16)
        return int(nid)

    def cancel_ping(self, reason="Cancelled"):
        """Stop waiting on the current ping (a late reply is still noted)."""
        n = 0
        for key, ping in list(self.ping_pending.items()):
            if isinstance(key, tuple):  # still being handed to the radio
                continue
            ping.update(status="cancelled", detail=reason)
            self.ping_late[ping["id"]] = ping
            self._finish_ping(ping)
            n += 1
        return n

    def send_ping(self, to, method="ack", replace=False, hop_limit=None):
        """Send one ping and start timing. Returns (ok, message)."""
        if method not in PING_TIMEOUT:
            return False, "Unknown ping type"
        if not self.connected:
            return False, "Connect to your radio first."
        if not to or to == "^all":
            return False, "Pick a node to ping."
        if self.my_id and to == self.my_id:
            return False, "That's your own radio — pick another node."
        if self.ping_pending and replace:
            old = next(iter(self.ping_pending.values()))
            self.cancel_ping(f"Cancelled to ping {self.name_of(to)} instead")
        with self.lock:
            if self.ping_pending:
                w = next(iter(self.ping_pending.values()))
                return False, (f"Still waiting on {w['to_name']} ({int(time.time() - w.get('sent', w['ts']))} s so far). "
                               "Press Cancel, or wait — one at a time keeps the channel clear.")
            self.ping_seq += 1
            rec = {"n": self.ping_seq, "ts": time.time(), "to": to, "to_name": self.name_of(to), "method": method,
                   "status": "waiting", "rtt": None, "relayed_at": None, "hops": None, "snr": None, "rssi": None,
                   "detail": "", "id": None, "hop_limit": hop_limit}
            rec["sent"] = rec["ts"]
            self.ping_pending[("sending", self.ping_seq)] = rec   # hold the slot so a double-click can't send two
        try:
            if self.mode == "demo":
                rec["id"] = random.randint(1, 2**31)
                threading.Thread(target=demo_ping_reply, args=(self, rec), daemon=True).start()
            else:
                iface = self.iface
                dest = self._dest_num(to)
                hl = int(hop_limit) if hop_limit else None
                if method == "ack":
                    pkt = iface.sendData(b"MeshTap ping", destinationId=dest, portNum=256, wantAck=True, hopLimit=hl)  # PRIVATE_APP
                elif method == "text":
                    pkt = iface.sendText("MeshTap ping \U0001F3D3", destinationId=dest, wantAck=True, hopLimit=hl)
                else:
                    try:
                        from meshtastic.protobuf import mesh_pb2
                    except ImportError:
                        from meshtastic import mesh_pb2
                    if method == "hello":  # node-info exchange: works even if they don't know our key yet
                        pkt = iface.sendData(self._my_user_pb(mesh_pb2), destinationId=dest, portNum=4,  # NODEINFO_APP
                                             wantResponse=True, hopLimit=hl)
                    else:
                        pkt = iface.sendData(mesh_pb2.RouteDiscovery(), destinationId=dest, portNum=70, wantResponse=True, hopLimit=hl)
                rec["id"] = getattr(pkt, "id", None)
                if not rec["id"]:
                    raise RuntimeError("the radio didn't give the ping an ID")
        except Exception as exc:
            with self.lock:
                self.ping_pending.pop(("sending", rec["n"]), None)
            rec.update(status="error", detail=f"Couldn't send: {exc}")
            self._finish_ping(rec)
            return False, rec["detail"]
        rec["sent"] = time.time()
        with self.lock:
            self.ping_pending.pop(("sending", rec["n"]), None)
            self.ping_pending[rec["id"]] = rec
        self.broadcast({"type": "ping", "ping": rec})
        return True, f"Ping sent to {rec['to_name']}"

    def _check_ping_reply(self, rec, dec):
        req = dec.get("requestId") if isinstance(dec, dict) else None
        if req and req in self.ping_late and rec["from"] == self.ping_late[req]["to"]:
            late = self.ping_late.pop(req)
            secs = rec["ts"] - late.get("sent", late["ts"])
            self.add_log(f"[MeshTap] late reply: {late['to_name']} answered the cancelled ping after {secs:.1f} s")
            return
        if not req or req not in self.ping_pending:
            return
        ping = self.ping_pending[req]
        now = time.time()
        if rec["port"] == "ROUTING_APP":
            err = (dec.get("routing") or {}).get("errorReason", "NONE") or "NONE"
            if err in ("NONE", 0):
                if rec["from"] == ping["to"]:
                    if ping["method"] == "trace":  # traceroute's own reply carries the route; keep waiting for it
                        ping["detail"] = "Delivered; waiting for the route…"
                        self.broadcast({"type": "ping", "ping": ping})
                        return
                    ping.update(status="ok", rtt=round((now - ping["sent"]) * 1000), hops=rec.get("hops"),
                                snr=rec.get("snr"), rssi=rec.get("rssi"),
                                detail=f"Delivered — {ping['to_name']} confirmed it" +
                                       (", reply came back directly" if rec.get("hops") == 0 else
                                        f", reply came back over {rec['hops']} hop{'s' if rec['hops'] != 1 else ''}" if rec.get("hops") is not None else ""))
                    self._finish_ping(ping)
                else:  # our own radio heard a neighbor pass it on (implicit ACK)
                    if not ping["relayed_at"]:
                        ping["relayed_at"] = round((now - ping["sent"]) * 1000)
                        ping["detail"] = f"Relayed by a neighbor after {ping['relayed_at']} ms — waiting for {ping['to_name']}…"
                        self.broadcast({"type": "ping", "ping": ping})
            else:
                ping.update(status="fail", rtt=round((now - ping["sent"]) * 1000), detail=ROUTING_ERRORS.get(err, str(err)))
                self._finish_ping(ping)
        elif rec["port"] == "NODEINFO_APP" and ping["method"] == "hello" and rec["from"] == ping["to"]:
            u = dec.get("user") or {}
            ping.update(status="ok", rtt=round((now - ping["sent"]) * 1000), hops=rec.get("hops"), snr=rec.get("snr"),
                        rssi=rec.get("rssi"),
                        detail=f"{u.get('longName') or ping['to_name']} answered with its node info"
                               + (f" over {rec['hops']} hop{'s' if rec['hops'] != 1 else ''}" if rec.get("hops") else " directly" if rec.get("hops") == 0 else "")
                               + " — you now know each other's keys")
            self._finish_ping(ping)
        elif rec["port"] == "TRACEROUTE_APP" and ping["method"] == "trace":
            tr = dec.get("traceroute") or {}
            route = [node_id(x) for x in tr.get("route") or []]
            back = [node_id(x) for x in tr.get("routeBack") or []]
            snr_to = [s / 4 for s in tr.get("snrTowards") or [] if s != -128]
            snr_back = [s / 4 for s in tr.get("snrBack") or [] if s != -128]
            me = self.my_id or "you"
            def path(nodes, snrs):  # "A →(+7.0 dB) B": the dB is how well each node heard the one before it
                s = self.name_of(nodes[0])
                for i, n in enumerate(nodes[1:]):
                    s += f" →({snrs[i]:+.1f} dB) " if i < len(snrs) else " → "
                    s += self.name_of(n)
                return s
            detail = "Out: " + path([me] + route + [ping["to"]], snr_to)
            if back or snr_back:
                detail += "  ·  Back: " + path([ping["to"]] + back + [me], snr_back)
            if not route and snr_to and snr_back:
                detail += (f"  ·  {ping['to_name']} hears you at {snr_to[0]:+.1f} dB, you hear it at {snr_back[0]:+.1f} dB"
                           + (" — a healthy two-way link" if min(snr_to[0], snr_back[0]) > -5 else
                              " — weak; your antenna or its height may be the limit" if snr_to[0] < snr_back[0] - 5 else ""))
            ping.update(status="ok", rtt=round((now - ping["sent"]) * 1000), hops=len(route), snr=rec.get("snr"),
                        rssi=rec.get("rssi"), detail=detail, route=route, route_back=back,
                        snr_towards=snr_to, snr_back=snr_back)
            self._finish_ping(ping)

    def _finish_ping(self, ping):
        with self.lock:
            if ping.get("id") in self.ping_pending:
                del self.ping_pending[ping["id"]]
            ping["done"] = time.time()
            self.pings.append(ping)
            if self.settings.get("autosave", True):
                try:
                    self.db.execute("INSERT INTO pings VALUES (?,?)", (ping["ts"], json.dumps(ping, default=str)))
                    self.db.commit()
                except sqlite3.Error:
                    pass
        word = {"ok": "✅", "fail": "❌", "timeout": "⏱", "error": "⚠️", "cancelled": "✖"}.get(ping["status"], "")
        self.add_log(f"[MeshTap] ping {ping['to_name']} ({ping['method']}): {word} {ping['status']}"
                     + (f" {ping['rtt']} ms" if ping.get("rtt") is not None and ping["status"] == "ok" else "")
                     + (f" — {ping['detail']}" if ping["status"] != "ok" else ""))
        self.broadcast({"type": "ping", "ping": ping})

    def _ping_watchdog(self):
        while True:
            time.sleep(1)
            now = time.time()
            for key, ping in list(self.ping_pending.items()):
                if isinstance(key, tuple):
                    continue
                if now - ping["sent"] > PING_TIMEOUT[ping["method"]]:
                    ping.update(status="timeout", detail=(
                        f"No answer in {PING_TIMEOUT[ping['method']]} s" +
                        (" — a neighbor relayed it, but it never confirmed" if ping.get("relayed_at") else " — nobody relayed it")))
                    self._finish_ping(ping)
            job = self.ping_job
            if job and self.connected and not self.ping_pending and now >= job["next"]:
                job["next"] = now + job["interval"]
                ok, msg = self.send_ping(job["to"], job["method"], hop_limit=job.get("hop_limit"))
                if not ok:
                    self.add_log(f"[MeshTap] repeat ping: {msg}")

    def start_ping_job(self, to, method, interval, hop_limit=None):
        if not to or to == "^all" or (self.my_id and to == self.my_id):
            return False, "Pick a node to ping."
        interval = max(PING_MIN_INTERVAL, int(interval or 60))
        if self.ping_pending and next(iter(self.ping_pending.values()))["to"] != to:
            self.cancel_ping(f"Cancelled — now pinging {self.name_of(to)}")
        self.ping_job = {"to": to, "method": method, "interval": interval, "next": 0, "hop_limit": hop_limit}
        self.push_status()
        return True, f"Pinging {self.name_of(to)} every {interval} s"

    def stop_ping_job(self):
        self.ping_job = None
        self.push_status()

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
        fname = rec.get("from_name")
        if fname and fname != frm and not n.get("long"):  # restore names from saved history
            n["long"] = fname
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
            if lat is not None and lon is not None and not (lat == 0 and lon == 0):
                n["lat"], n["lon"] = lat, lon
                n["pos_ts"] = rec["ts"]
                if pos.get("altitude") is not None:
                    n["alt"] = pos["altitude"]
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
                if pos.get("latitude") is not None and n.get("lat") is None and (pos.get("latitude") or pos.get("longitude")):
                    n["lat"], n["lon"] = pos.get("latitude"), pos.get("longitude")
                    n["pos_ts"] = pos.get("time") or info.get("lastHeard")
                    if pos.get("altitude") is not None:
                        n["alt"] = pos["altitude"]
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
    def connect(self, mode, address="", baud=115200):
        self.disconnect()
        try:
            baud = int(baud or 115200)
        except (TypeError, ValueError):
            baud = 115200
        self.mode, self.error, self.connecting, self.baud = mode, "", True, baud
        self.desc = {"serial": "Serial", "rawserial": "Serial text", "tcp": "WiFi", "ble": "Bluetooth", "demo": "Demo"}.get(mode, mode)
        self.desc += f" {address}" if address else (" (auto-detect)" if mode in ("serial", "ble") else "")
        if mode in ("serial", "rawserial") and baud != 115200:
            self.desc += f" @ {baud}"
        self.push_status()
        threading.Thread(target=self._connect_worker, args=(mode, address, baud), daemon=True).start()

    def _connect_worker(self, mode, address, baud=115200):
        try:
            if mode == "demo":
                self.demo_stop = threading.Event()
                threading.Thread(target=demo_loop, args=(self, self.demo_stop), daemon=True).start()
                self.connected, self.connecting = True, False
                self.add_log("[MeshTap] demo mode started — this is fake traffic")
                self.push_status()
                return
            if mode == "rawserial":
                self._start_raw_serial(address, baud)
                return
            if not HAVE_MESH:
                raise RuntimeError(f"The meshtastic Python library isn't installed ({MESH_ERR}). Run the start script again.")
            self.add_log(f"[MeshTap] connecting via {mode} {address or ''}...")
            if mode == "serial":
                if not address:
                    found = auto_serial()
                    if not found:
                        raise RuntimeError("No Meshtastic radio found on USB. Plug it in (data cable, not charge-only), "
                                           "or pick a port from the list — GPIO-wired radios must be picked by hand.")
                    if len(found) > 1:
                        raise RuntimeError(f"Several radios found ({', '.join(found)}). Pick one from the Port list.")
                    address = found[0]
                    self.desc = f"Serial {address}" + (f" @ {baud}" if baud != 115200 else "")
                    self.push_status()
                try:
                    iface = BaudSerialInterface(devPath=address, baud=baud, timeout=int(os.environ.get("MESHTAP_TIMEOUT", "60")))
                except Exception as exc:
                    raise RuntimeError(serial_hint(exc, address)) from exc
                if getattr(iface, "stream", None) is None and not getattr(iface, "isConnected", None):
                    raise RuntimeError(f"Couldn't open {address}.")
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
                self.desc = f"Serial {port}" + (f" @ {baud}" if baud != 115200 else "")
            self.add_log(f"[MeshTap] connected — {len(self.nodes)} nodes known, listening for traffic")
        except Exception as exc:
            self.connected, self.connecting = False, False
            self.error = str(exc) or exc.__class__.__name__
            self.add_log(f"[MeshTap] connection failed: {self.error}")
        self.push_status()

    # ---- serial text mode (Serial Module set to TEXTMSG / NMEA / SIMPLE)
    def _start_raw_serial(self, port, baud):
        try:
            import serial
        except Exception:
            raise RuntimeError("pyserial isn't installed. Run the start script again.")
        if not port:
            raise RuntimeError("Pick the serial port the radio's Serial Module is wired to.")
        try:
            ser = serial.Serial(port, baud, timeout=0.5)
        except Exception as exc:
            raise RuntimeError(serial_hint(exc, port)) from exc
        self.raw_stop = threading.Event()
        self.raw_ser = ser
        self.connected, self.connecting = True, False
        self.add_log(f"[MeshTap] reading plain text from {port} at {baud} baud (Serial Module TEXTMSG/NMEA mode)")
        self.push_status()
        threading.Thread(target=self._raw_loop, args=(ser, self.raw_stop, port), daemon=True).start()

    def _raw_loop(self, ser, stop, port):
        buf = b""
        while not stop.is_set():
            try:
                chunk = ser.read(256)
            except Exception as exc:
                if not stop.is_set():
                    self.connected = False
                    self.error = serial_hint(exc, port) or "Lost the serial port"
                    self.add_log(f"[MeshTap] serial text: {self.error}")
                    self.push_status()
                return
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf or len(buf) > 4096:
                line, _, buf = buf.partition(b"\n") if b"\n" in buf else (buf, b"", b"")
                text = line.decode("utf-8", "replace").strip().strip("\x00")
                if text:
                    self.on_text_line(text)

    def on_text_line(self, text):
        """Show one line from a Serial Module as a packet in the feed."""
        if text.startswith("$") and len(text) > 6 and text[1:6].isalpha():
            port, summary, frm = "SERIAL_NMEA", f"🛰 {text}", "serial"
        else:
            port, summary, frm = "SERIAL_TEXT", f"💬 {text}", "serial"
            if ": " in text[:40]:  # TEXTMSG mode prints "<short name>: <message>"
                who, msg = text.split(": ", 1)
                if who and len(who) <= 30:
                    frm, summary = who.strip(), f"💬 {msg}"
        pkt = {"fromId": frm, "toId": "^all", "decoded": {"portnum": port, "text": text}}
        self.on_packet(pkt, summary=summary)

    def disconnect(self):
        self.ping_job = None
        for ping in list(getattr(self, "ping_pending", {}).values()):
            ping.update(status="error", detail="Disconnected before an answer came back")
            self._finish_ping(ping)
        if getattr(self, "raw_stop", None):
            self.raw_stop.set()
            self.raw_stop = None
        ser, self.raw_ser = getattr(self, "raw_ser", None), None
        if ser:
            try:
                ser.close()
            except Exception:
                pass
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
            self.db.execute("DELETE FROM pings")
            self.pings.clear()
            self.unsaved = []
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
                    "unsaved": len(self.unsaved),
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
DEMO_HOME = {0x9E7C1A20: (42.3314, -83.0458), 0x4A11F0C3: (42.3602, -83.0710), 0x0B2D77E4: (42.3889, -82.9934),
             0x77AA0912: (42.3192, -83.1012), 0x3C5E8B01: (42.2981, -82.9611), 0xD00DFEED: (42.4103, -83.1290),
             0x1234ABCD: (42.3468, -82.9235)}
DEMO_TEXTS = [
    "Anyone copy?", "Checking in from the ridge", "Heading home now", "Signal is great today",
    "Battery at 40%, swapping soon", "Test 1 2 3", "Good morning mesh!", "Who's on the north side?",
]


def demo_ping_reply(hub, ping):
    """Fake a reply for a demo ping, through the same code path as a real one."""
    time.sleep(random.uniform(0.6, 1.8))
    if hub.mode != "demo":
        return
    me = int(hub.my_id[1:], 16)
    dest = int(ping["to"][1:], 16)
    roll = random.random()
    if roll < 0.8:  # neighbor relays it, then it gets through
        hub.on_packet({"from": me, "to": me, "fromId": hub.my_id, "toId": hub.my_id,
                       "decoded": {"portnum": "ROUTING_APP", "routing": {"errorReason": "NONE"}, "requestId": ping["id"]}})
    time.sleep(random.uniform(1.5, 9))
    hops = random.randint(0, 3)
    base = {"from": dest, "to": me, "fromId": ping["to"], "toId": hub.my_id, "rxSnr": round(random.uniform(-12, 9), 2),
            "rxRssi": random.randint(-118, -62), "hopStart": 3, "hopLimit": 3 - hops}
    if roll < 0.75:
        if ping["method"] == "hello":
            n = next((x for x in DEMO_NODES if x[0] == dest), DEMO_NODES[1])
            base["decoded"] = {"portnum": "NODEINFO_APP", "requestId": ping["id"],
                               "user": {"id": ping["to"], "longName": n[1], "shortName": n[2], "hwModel": n[3]}}
        elif ping["method"] == "trace":
            relays = random.sample([n[0] for n in DEMO_NODES[1:] if n[0] != dest], hops)
            base["decoded"] = {"portnum": "TRACEROUTE_APP", "requestId": ping["id"],
                               "traceroute": {"route": relays, "routeBack": relays[::-1],
                                              "snrTowards": [random.randint(-40, 40) for _ in range(hops + 1)],
                                              "snrBack": [random.randint(-40, 40) for _ in range(hops + 1)]}}
        else:
            base["decoded"] = {"portnum": "ROUTING_APP", "routing": {"errorReason": "NONE"}, "requestId": ping["id"]}
        hub.on_packet(base)
    elif roll < 0.88:
        hub.on_packet({"from": me, "to": me, "fromId": hub.my_id, "toId": hub.my_id,
                       "decoded": {"portnum": "ROUTING_APP", "routing": {"errorReason": "MAX_RETRANSMIT"}, "requestId": ping["id"]}})
    # else: silence -> times out


def demo_loop(hub, stop):
    me = DEMO_NODES[0]
    hub.my_id = node_id(me[0])
    for num, long_, short, hw in DEMO_NODES:
        n = hub.nodes.setdefault(node_id(num), {"id": node_id(num), "packets": 0, "ports": {}})
        n.update({"long": long_, "short": short, "hw": hw, "known": True})
        if num in (me[0], 0x0B2D77E4, 0x1234ABCD):  # fixed stations already have a position
            n["lat"], n["lon"], n["alt"], n["pos_ts"] = DEMO_HOME[num][0], DEMO_HOME[num][1], 200, time.time()
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
            hlat, hlon = DEMO_HOME[num]
            drift = 0.01 if num in (0x4A11F0C3, 0x3C5E8B01) else 0.0004  # the truck and the hiker move around
            dec["position"] = {"latitude": hlat + random.uniform(-drift, drift),
                               "longitude": hlon + random.uniform(-drift, drift),
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
        if url.path.startswith("/vendor/"):
            rel = os.path.normpath(url.path.lstrip("/"))
            if rel.startswith("vendor" + os.sep) and ".." not in rel.split(os.sep):
                ext = os.path.splitext(rel)[1]
                ctype = {".js": "application/javascript", ".css": "text/css", ".png": "image/png"}.get(ext, "application/octet-stream")
                return self._file(rel, ctype)
            return self._send(404, {"error": "not found"})
        if url.path == "/meshtap.js":  # lets launcher pages find a running MeshTap
            port = self.server.server_address[1]
            return self._send(200, f"(window.__meshtap=window.__meshtap||[]).push({port});", "application/javascript")
        if url.path == "/api/state":
            return self._send(200, HUB.state())
        if url.path == "/api/packets":
            limit = int((qs.get("limit") or ["1000"])[0])
            with HUB.lock:
                return self._send(200, list(HUB.packets)[-limit:])
        if url.path == "/api/pings":
            with HUB.lock:
                return self._send(200, list(HUB.pings))
        if url.path == "/api/pings.csv":
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["time", "to", "to_name", "type", "result", "round_trip_ms", "relayed_ms", "hops", "snr", "rssi", "detail"])
            with HUB.lock:
                for r in HUB.pings:
                    w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r["to"], r["to_name"], r["method"],
                                r["status"], r.get("rtt"), r.get("relayed_at"), r.get("hops"), r.get("snr"), r.get("rssi"), r.get("detail")])
            return self._send(200, out.getvalue(), "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="meshtap-pings-{time.strftime("%Y%m%d-%H%M")}.csv"'})
        if url.path == "/api/logs":
            return self._send(200, list(HUB.logs))
        if url.path == "/api/ports":
            return self._send(200, list_serial_ports())
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
            HUB.connect(body.get("mode", "serial"), (body.get("address") or "").strip(), body.get("baud") or 115200)
            return self._send(200, {"ok": True})
        if url.path == "/api/disconnect":
            HUB.disconnect()
            return self._send(200, {"ok": True})
        if url.path == "/api/shutdown":  # lets a newer MeshTap replace this one; only from this computer
            if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                return self._send(403, {"error": "only from this computer"})
            self._send(200, {"ok": True, "version": VERSION})
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return
        if url.path == "/api/autosave":
            HUB.set_autosave(bool(body.get("on")))
            return self._send(200, {"ok": True})
        if url.path == "/api/save":
            n = HUB.save_now()
            return self._send(200, {"ok": True, "saved": n})
        if url.path == "/api/ping":
            to, method = body.get("to"), body.get("method", "ack")
            if body.get("repeat"):
                ok, msg = HUB.start_ping_job(to, method, body.get("interval"), body.get("hop_limit") or None)
            else:
                ok, msg = HUB.send_ping(to, method, replace=bool(body.get("replace")), hop_limit=body.get("hop_limit") or None)
            return self._send(200, {"ok": ok, "msg": msg})
        if url.path == "/api/ping/cancel":
            HUB.cancel_ping()
            return self._send(200, {"ok": True})
        if url.path == "/api/ping/stop":
            HUB.stop_ping_job()
            HUB.cancel_ping("Stopped")
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


def running_version(port):
    """Version of a MeshTap already answering on this port, or None."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as r:
            return (json.loads(r.read()).get("status") or {}).get("version") or "?"
    except Exception:
        return None


def bind_or_take_over(host, port, url, no_browser):
    """Open the web port. If an older MeshTap holds it, ask it to close and take over;
    if the same version is already running, just open it."""
    import urllib.request
    for attempt in range(2):
        try:
            server = ThreadingHTTPServer((host, port), Handler)
            server.daemon_threads = True
            return server
        except OSError:
            if attempt:
                break
            old = running_version(port)
            if old is None:
                break
            if old == VERSION:
                print(f"\n  MeshTap {VERSION} is already running — opening it.\n")
                if not no_browser:
                    webbrowser.open(url)
                return None
            print(f"\n  An older MeshTap (v{old}) is running — closing it so v{VERSION} can take over...")
            try:
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}",
                                                              headers={"Content-Type": "application/json"}), timeout=3)
            except Exception:
                pass
            for _ in range(20):
                time.sleep(0.25)
                if running_version(port) is None:
                    break
    old = running_version(port)
    if old:
        print(f"\n  MeshTap v{old} is still running in another window and is using port {port}.\n"
              f"  Close that window (or end python.exe in Task Manager), then start MeshTap again.\n")
    else:
        print(f"\n  Port {port} is in use by another program. Start with a different one, e.g.:  --web-port 8743\n")
    return None


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
    ap.add_argument("--serial-text", metavar="PORT", help="read a Serial Module in TEXTMSG/NMEA mode")
    ap.add_argument("--baud", type=int, default=115200, help="serial speed (USB radios are always 115200)")
    ap.add_argument("--tcp", metavar="HOST", help="radio IP/hostname over WiFi")
    ap.add_argument("--ble", nargs="?", const="", metavar="NAME", help="Bluetooth name/address (blank = scan)")
    ap.add_argument("--demo", action="store_true", help="fake traffic, no radio needed")
    ap.add_argument("--web-port", type=int, default=8742)
    ap.add_argument("--lan", action="store_true", help="allow other devices on your network to view")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    url = f"http://localhost:{args.web_port}"
    server = bind_or_take_over(host, args.web_port, url, args.no_browser)
    if server is None:
        return
    HUB = Hub()
    if args.demo:
        HUB.connect("demo")
    elif args.tcp:
        HUB.connect("tcp", args.tcp)
    elif args.ble is not None:
        HUB.connect("ble", args.ble)
    elif args.serial_text:
        HUB.connect("rawserial", args.serial_text, args.baud)
    elif args.serial is not None:
        HUB.connect("serial", args.serial, args.baud)
    elif HAVE_MESH:
        found = auto_serial()
        if len(found) == 1:
            HUB.connect("serial", found[0], args.baud)
        else:
            HUB.add_log("[MeshTap] " + ("no USB radio found — use Connect to pick WiFi, Bluetooth or a port"
                                         if not found else f"several ports found ({', '.join(found)}) — pick one in Connect"))

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
