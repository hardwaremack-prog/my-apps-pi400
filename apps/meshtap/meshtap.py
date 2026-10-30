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

VERSION = "1.6.1"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")
DATA_DIR = os.path.join(os.path.expanduser("~"), ".meshtap")
DB_PATH = os.path.join(DATA_DIR, "meshtap.db")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
CONFIG_SECTIONS = ["device", "position", "power", "network", "display", "lora", "bluetooth", "security"]
MODULE_SECTIONS = ["mqtt", "serial", "external_notification", "store_forward", "range_test", "telemetry", "canned_message",
                   "audio", "remote_hardware", "neighbor_info", "ambient_lighting", "detection_sensor", "paxcounter",
                   "traffic_management"]
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


# ------------------------------------------------------- radio settings
def _pb():
    try:
        from meshtastic.protobuf import localonly_pb2, channel_pb2, config_pb2, mesh_pb2
    except ImportError:  # very old library
        from meshtastic import localonly_pb2, channel_pb2, config_pb2, mesh_pb2
    from google.protobuf import json_format
    return localonly_pb2, channel_pb2, config_pb2, mesh_pb2, json_format


def pb_to_dict(msg):
    _, _, _, _, jf = _pb()
    try:
        return jf.MessageToDict(msg, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
    except TypeError:  # older protobuf
        return jf.MessageToDict(msg, preserving_proto_field_name=True, including_default_value_fields=True)


def pb_schema(desc, depth=0):
    """Describe a protobuf message's fields so the page can build a settings form for it."""
    from google.protobuf.descriptor import FieldDescriptor as F
    out = []
    for f in desc.fields:
        t = f.type
        rep_ = getattr(f, "is_repeated", None)
        if rep_ is None:
            rep_ = getattr(f, "label", None) == getattr(F, "LABEL_REPEATED", 3)
        item = {"name": f.name, "repeated": bool(rep_)}
        if t == F.TYPE_BOOL:
            item["kind"] = "bool"
        elif t == F.TYPE_STRING:
            item["kind"] = "str"
        elif t == F.TYPE_BYTES:
            item["kind"] = "bytes"
        elif t == F.TYPE_ENUM:
            item["kind"], item["enum"] = "enum", [v.name for v in f.enum_type.values]
        elif t in (F.TYPE_FLOAT, F.TYPE_DOUBLE):
            item["kind"] = "float"
        elif t == F.TYPE_MESSAGE:
            if depth >= 2:
                continue
            item["kind"], item["fields"] = "msg", pb_schema(f.message_type, depth + 1)
        else:
            item["kind"] = "int"
        out.append(item)
    return out


class DemoNode:
    """Stand-in radio for demo mode so the Radio tab can be explored without hardware."""
    def __init__(self):
        lo, ch, cfg, _, _ = _pb()
        self.localConfig = lo.LocalConfig()
        self.localConfig.lora.region = cfg.Config.LoRaConfig.RegionCode.Value("US")
        self.localConfig.lora.modem_preset = cfg.Config.LoRaConfig.ModemPreset.Value("LONG_FAST")
        self.localConfig.lora.hop_limit = 3
        self.localConfig.lora.use_preset = True
        self.localConfig.lora.tx_enabled = True
        self.localConfig.device.role = cfg.Config.DeviceConfig.Role.Value("CLIENT")
        self.localConfig.bluetooth.enabled = True
        self.localConfig.position.position_broadcast_secs = 900
        self.moduleConfig = lo.LocalModuleConfig()
        self.moduleConfig.telemetry.device_update_interval = 1800
        self.channels = [ch.Channel(index=i, role=ch.Channel.Role.Value("PRIMARY" if i == 0 else "DISABLED")) for i in range(8)]
        self.channels[0].settings.psk = b"\x01"
        self.owner = {"longName": "Basecamp Router", "shortName": "BASE", "isLicensed": False}

    def writeConfig(self, name): pass
    def writeChannel(self, i, adminIndex=0): pass
    def setOwner(self, long_name=None, short_name=None, is_licensed=False, **k):
        self.owner.update({k2: v for k2, v in (("longName", long_name), ("shortName", short_name), ("isLicensed", is_licensed)) if v is not None})
    def getURL(self, includeAll=True): return "https://meshtastic.org/e/#CgMSAQESBggBQANIAQ"
    def setURL(self, url, addOnly=False): pass
    def reboot(self, secs=10): pass
    def beginSettingsTransaction(self): pass
    def commitSettingsTransaction(self): pass
    def factoryReset(self, full=False):
        self.__init__()


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
        self.settings = {"autosave": True, "home": None, "goals": []}
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as fh:
                self.settings.update(json.load(fh))
        except (OSError, ValueError):
            pass
        self.my_id = self.settings.get("my_id")   # last radio we were connected to, until we connect again
        self.unsaved = []              # packets caught while auto-save was off
        self.pings = collections.deque(maxlen=500)
        self.ping_pending = {}         # packet id -> ping record
        self.ping_job = None           # repeating ping settings
        self.ping_seq = 0
        self.ping_late = {}            # cancelled pings, so a late reply can still be noted
        self.links = {}                # (a, b) sorted node ids -> what we know about that radio link
        self.scan = None               # spider scan: traceroute every node with a high hop limit
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS packets (ts REAL, port TEXT, data TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS pings (ts REAL, data TEXT)")
        self.db.commit()
        for (data,) in self.db.execute("SELECT data FROM pings ORDER BY rowid DESC LIMIT 300").fetchall()[::-1]:
            try:
                self.pings.append(json.loads(data))
            except ValueError:
                pass
        self._pings_confirm_pending = list(self.pings)
        threading.Thread(target=self._ping_watchdog, daemon=True).start()
        self._load_history()
        for pg in self._pings_confirm_pending:  # nodes that answered a ping before = proven two-way
            if pg.get("status") == "ok":
                self._mark_confirmed(pg)
                self._learn_trace_links(pg)

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
            "scan": self.scan_status(),
            "home": self.settings.get("home"),
            "goals": self.settings.get("goals") or [],
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
        if self.mode == "demo":
            try:
                return self._radio()[0].localConfig.lora.hop_limit
            except Exception:
                return 3
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
        if ping.get("status") == "ok":
            self._mark_confirmed(ping)
        s = self.scan
        if s and not s.get("finished") and s.get("current") == ping.get("to") and ping.get("method") == "trace":
            s["done"] += 1
            s["ok"] += ping.get("status") == "ok"
            s["current"] = None
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
            self._scan_tick(now)
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
        if self.scan and not self.scan.get("finished"):
            return False, "A spider scan is running — stop it first (Spider tab)."
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
        if rec.get("hops") is not None and rec["dir"] == "in" and not rec.get("via_mqtt"):
            n["min_hops"] = min(n.get("min_hops", 99), rec["hops"])
            if rec["hops"] == 0:
                n["direct_ts"], n["direct_snr"] = rec["ts"], rec.get("snr")
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
        elif rec["port"] == "NEIGHBORINFO_APP":
            ni = dec.get("neighborinfo") or {}
            owner = node_id(ni.get("nodeId")) if ni.get("nodeId") else frm
            for nb in ni.get("neighbors") or []:
                if nb.get("nodeId"):
                    self._add_link(owner, node_id(nb["nodeId"]), None, nb.get("snr"), rec["ts"], "neighbors")  # snr = how owner heard it
        elif rec["port"] == "TRACEROUTE_APP" and dec.get("requestId"):
            tr = dec.get("traceroute") or {}
            self._learn_route(rec["to"], frm, tr, rec["ts"])
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

    # ---- links between radios (for the Spider tab)
    def _add_link(self, a, b, snr_ab, snr_ba, ts, source):
        """snr_ab = how well b heard a; snr_ba = how well a heard b."""
        if not a or not b or a == b or "^all" in (a, b):
            return
        key = tuple(sorted((a, b)))
        L = self.links.setdefault(key, {"a": key[0], "b": key[1], "snr": {}, "sources": [], "ts": 0})
        for frm_, to_, v in ((a, b, snr_ab), (b, a, snr_ba)):
            if v is not None:
                L["snr"][f"{frm_}>{to_}"] = round(float(v), 2)
        if source not in L["sources"]:
            L["sources"].append(source)
        L["ts"] = max(L["ts"], ts)

    def _learn_route(self, origin, dest, tr, ts):
        """A traceroute answer: origin → route… → dest, and dest → routeBack… → origin, with SNR per hop."""
        route = [node_id(x) for x in tr.get("route") or []]
        back = [node_id(x) for x in tr.get("routeBack") or []]
        snr_to = [s / 4 for s in tr.get("snrTowards") or []]
        snr_back = [s / 4 for s in tr.get("snrBack") or []]
        hops = [origin] + route + [dest]
        for i in range(len(hops) - 1):
            s = snr_to[i] if i < len(snr_to) and snr_to[i] != -32 else None
            self._add_link(hops[i], hops[i + 1], s, None, ts, "traceroute")
        if snr_back:
            bh = [dest] + back + [origin]
            for i in range(len(bh) - 1):
                s = snr_back[i] if i < len(snr_back) and snr_back[i] != -32 else None
                self._add_link(bh[i], bh[i + 1], s, None, ts, "traceroute")
        for i, nid in enumerate(hops[1:], 1):  # everyone on the path is reachable within i-1 relays
            nn = self.nodes.setdefault(nid, {"id": nid, "packets": 0, "ports": {}})
            nn["path_hops"] = min(nn.get("path_hops", 99), i - 1)

    def _learn_trace_links(self, ping):
        if ping.get("method") == "trace" and ping.get("status") == "ok" and ping.get("route") is not None and self.my_id:
            raw = {"route": [int(x[1:], 16) for x in ping.get("route") or [] if str(x).startswith("!")],
                   "routeBack": [int(x[1:], 16) for x in ping.get("route_back") or [] if str(x).startswith("!")],
                   "snrTowards": [s * 4 for s in ping.get("snr_towards") or []],
                   "snrBack": [s * 4 for s in ping.get("snr_back") or []]}
            self._learn_route(self.my_id, ping["to"], raw, ping.get("done") or ping["ts"])

    def _mark_confirmed(self, ping):
        n = self.nodes.setdefault(ping["to"], {"id": ping["to"], "packets": 0, "ports": {}})
        n["confirmed"] = max(n.get("confirmed", 0), ping.get("done") or ping["ts"])
        n["confirmed_rtt"] = ping.get("rtt")

    def spider(self):
        """Everything the Spider tab draws: nodes with hop counts, known links, and scan progress."""
        with self.lock:
            me = self.my_id
            links = [dict(L) for L in self.links.values()]
            if me:  # nodes heard directly are linked to us
                have = {(L["a"], L["b"]) for L in links}
                for nid, n in self.nodes.items():
                    if n.get("direct_ts") and nid != me:
                        key = tuple(sorted((me, nid)))
                        if key not in have:
                            links.append({"a": key[0], "b": key[1], "snr": {f"{nid}>{me}": n.get("direct_snr")},
                                          "sources": ["heard directly"], "ts": n["direct_ts"]})
            nodes = []
            for nid, n in self.nodes.items():
                if not nid.startswith("!"):
                    continue
                nodes.append({k: n.get(k) for k in ("id", "long", "short", "hw", "hops", "min_hops", "path_hops", "last",
                                                    "snr", "lat", "lon", "confirmed", "confirmed_rtt", "packets", "mqtt")})
            return {"me": me, "hop_limit": self.my_hop_limit(), "nodes": nodes, "links": links, "scan": self.scan_status(),
                    "home": self.settings.get("home"), "goals": self.settings.get("goals") or []}

    def update_settings(self, body):
        """Home location and goal places for range planning."""
        def place(d):
            try:
                lat, lon = float(d["lat"]), float(d["lon"])
            except (KeyError, TypeError, ValueError):
                return None
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return None
            return {"name": str(d.get("name") or "")[:60], "lat": round(lat, 5), "lon": round(lon, 5)}
        with self.lock:
            if "home" in body:
                self.settings["home"] = place(body["home"]) if body["home"] else None
            if "goals" in body:
                self.settings["goals"] = [g for g in (place(x) for x in body["goals"] or []) if g and g["name"]][:12]
            self._write_settings()
        return True

    def scan_status(self):
        s = self.scan
        if not s:
            return None
        return {k: s[k] for k in ("hop_limit", "total", "done", "ok", "current", "started", "finished", "next")}

    def start_scan(self, hop_limit=7, interval=PING_MIN_INTERVAL):
        if not self.connected:
            return False, "Connect to your radio first."
        targets = sorted([n for nid, n in self.nodes.items() if nid.startswith("!") and nid != self.my_id
                          and (n.get("packets") or n.get("last")) and not n.get("mqtt")],
                         key=lambda n: (n.get("hops") if n.get("hops") is not None else 9, -(n.get("last") or 0)))
        if not targets:
            return False, "No nodes heard yet — wait for some traffic first."
        self.ping_job = None
        self.scan = {"queue": [n["id"] for n in targets], "hop_limit": int(hop_limit or 7), "interval": max(PING_MIN_INTERVAL, int(interval)),
                     "total": len(targets), "done": 0, "ok": 0, "current": None, "started": time.time(), "finished": None, "next": 0}
        self.add_log(f"[MeshTap] spider scan started: traceroute to {len(targets)} nodes, hop limit {self.scan['hop_limit']}")
        self.push_status()
        return True, f"Scanning {len(targets)} nodes, one every {self.scan['interval']} s"

    def stop_scan(self):
        if self.scan and not self.scan.get("finished"):
            self.scan["finished"] = time.time()
            self.scan["queue"] = []
            self.add_log("[MeshTap] spider scan stopped")
        self.push_status()

    def _scan_tick(self, now):
        s = self.scan
        if not s or s.get("finished") or not self.connected or self.ping_pending or now < s["next"]:
            return
        if not s["queue"]:
            s["finished"], s["current"] = now, None
            self.add_log(f"[MeshTap] spider scan done: {s['ok']} of {s['total']} nodes answered a 7-hop traceroute")
            self.push_status()
            return
        target = s["queue"].pop(0)
        s["current"] = target
        s["next"] = now + s["interval"]
        ok, msg = self.send_ping(target, "trace", hop_limit=s["hop_limit"])
        if not ok:
            s["done"] += 1
            self.add_log(f"[MeshTap] spider scan: {msg}")
        self.push_status()

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
                if self.my_id and self.settings.get("my_id") != self.my_id:
                    self.settings["my_id"] = self.my_id
                    self._write_settings()
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
    def connect(self, mode, address="", baud=115200, _auto=False):
        self.disconnect(_auto=_auto)
        if not _auto:
            self.reconnect_gen = getattr(self, "reconnect_gen", 0) + 1   # a manual connect cancels any auto-reconnect
        self.last_conn = (mode, address, baud)
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
    def auto_reconnect(self):
        """The radio went away (unplugged, rebooted after a settings change...). Keep trying to get it back."""
        conn = getattr(self, "last_conn", None)
        if not conn or conn[0] not in ("serial", "tcp", "ble"):
            return
        gen = self.reconnect_gen = getattr(self, "reconnect_gen", 0) + 1
        def run():
            self.add_log("[MeshTap] radio dropped — reconnecting automatically…")
            for attempt in range(40):            # about 3 minutes
                time.sleep(4 if attempt else 3)
                if self.reconnect_gen != gen:      # user connected/disconnected by hand
                    return
                self.connect(*conn, _auto=True)
                for _ in range(40):
                    time.sleep(0.5)
                    if self.connected or (not self.connecting and self.error):
                        break
                if self.connected:
                    self.add_log(f"[MeshTap] reconnected after {attempt + 1} tr{'y' if attempt == 0 else 'ies'}")
                    return
            self.add_log("[MeshTap] couldn't reconnect to the radio — press Connect to try again")
        threading.Thread(target=run, daemon=True).start()

    # ---- radio settings (like the Meshtastic web client), backups and cloning
    def _radio(self):
        if self.mode == "demo":
            self.demo_node = getattr(self, "demo_node", None) or DemoNode()
            return self.demo_node, None
        iface = self.iface
        node = getattr(iface, "localNode", None) if iface else None
        if not self.connected or node is None or getattr(node, "localConfig", None) is None:
            raise RuntimeError("Connect to your radio first (USB, WiFi or Bluetooth).")
        return node, iface

    def _owner(self, node, iface):
        if iface is None:
            return dict(node.owner)
        u = ((iface.getMyNodeInfo() or {}).get("user") or {})
        return {"longName": u.get("longName"), "shortName": u.get("shortName"), "isLicensed": bool(u.get("isLicensed")),
                "id": u.get("id"), "hwModel": u.get("hwModel")}

    def radio_info(self):
        node, iface = self._radio()
        lo, ch, _, mesh, _ = _pb()
        cfg = {s: pb_to_dict(getattr(node.localConfig, s)) for s in CONFIG_SECTIONS if hasattr(node.localConfig, s)}
        sec = cfg.get("security") or {}
        sec["private_key_set"] = bool(sec.pop("private_key", None))   # never sent to the page
        mc = getattr(node, "moduleConfig", None)
        mods = {s: pb_to_dict(getattr(mc, s)) for s in MODULE_SECTIONS if mc is not None and hasattr(mc, s)}
        meta = {}
        try:
            md = getattr(iface, "metadata", None) if iface else None
            meta = pb_to_dict(md) if md is not None else ({"firmware_version": "2.6.11.demo", "hw_model": "HELTEC_V3"} if iface is None else {})
        except Exception:
            pass
        chans = [pb_to_dict(c) for c in (node.channels or [])]
        try:
            url = node.getURL()
        except Exception:
            url = ""
        return {"owner": self._owner(node, iface), "metadata": meta, "config": {k: v for k, v in cfg.items() if k in CONFIG_SECTIONS},
                "modules": {k: v for k, v in mods.items() if k in MODULE_SECTIONS}, "channels": chans, "channel_url": url,
                "schema": {"config": {s: pb_schema(lo.LocalConfig.DESCRIPTOR.fields_by_name[s].message_type) for s in CONFIG_SECTIONS},
                           "modules": {s: pb_schema(lo.LocalModuleConfig.DESCRIPTOR.fields_by_name[s].message_type)
                                       for s in MODULE_SECTIONS if s in lo.LocalModuleConfig.DESCRIPTOR.fields_by_name},
                           "channel": pb_schema(ch.Channel.DESCRIPTOR)},
                "backups": self.list_backups()}

    def _build_section(self, parent, section, values):
        """A complete new copy of one settings section from the page's values (keys the radio's own private key)."""
        _, _, _, _, jf = _pb()
        cur = getattr(parent, section)
        new = type(cur)()
        vals = dict(values or {})
        vals.pop("private_key_set", None)
        jf.ParseDict(vals, new, ignore_unknown_fields=True)
        if section == "security":  # a radio's identity stays its own
            new.private_key = cur.private_key
            new.public_key = cur.public_key
        return cur, new

    def write_section(self, kind, section, values):
        node, iface = self._radio()
        if kind == "config" and section in CONFIG_SECTIONS:
            parent = node.localConfig
        elif kind == "modules" and section in MODULE_SECTIONS and getattr(node, "moduleConfig", None) is not None:
            parent = node.moduleConfig
        else:
            return False, f"Unknown settings section: {section}"
        self.auto_backup(f"before-{section}")
        try:
            cur, new = self._build_section(parent, section, values)
        except Exception as exc:
            return False, f"Those values didn't fit: {exc}"
        if cur.SerializeToString() == new.SerializeToString():
            return True, "Nothing changed."
        cur.CopyFrom(new)
        try:
            node.writeConfig(section)
        except SystemExit:
            return False, "The radio hasn't finished sending its settings yet — try again in a few seconds."
        except Exception as exc:
            return False, f"Couldn't save to the radio: {exc}"
        self.add_log(f"[MeshTap] saved {section} settings to the radio")
        return True, f"Saved {section.replace('_', ' ')} settings. Some changes make the radio restart; MeshTap reconnects by itself."

    def write_owner(self, long_name, short_name, licensed=False):
        node, iface = self._radio()
        long_name, short_name = (long_name or "").strip()[:39], (short_name or "").strip()[:4]
        if not long_name or not short_name:
            return False, "Both names are needed (short name: up to 4 characters)."
        self.auto_backup("before-owner")
        try:
            node.setOwner(long_name=long_name, short_name=short_name, is_licensed=bool(licensed))
        except Exception as exc:
            return False, f"Couldn't rename the radio: {exc}"
        if self.my_id and self.my_id in self.nodes:
            self.nodes[self.my_id]["long"], self.nodes[self.my_id]["short"] = long_name, short_name
        self.add_log(f"[MeshTap] radio renamed to {long_name} [{short_name}]")
        return True, f"Radio renamed to {long_name} [{short_name}]."

    def write_channel(self, values):
        node, iface = self._radio()
        _, ch, _, _, jf = _pb()
        try:
            idx = int(values.get("index", -1))
            assert 0 <= idx < len(node.channels)
        except Exception:
            return False, "Unknown channel."
        new = ch.Channel()
        try:
            jf.ParseDict(values, new, ignore_unknown_fields=True)
        except Exception as exc:
            return False, f"Those values didn't fit: {exc}"
        new.index = idx
        if idx == 0 and new.role != ch.Channel.Role.Value("PRIMARY"):
            return False, "Channel 0 must stay the primary channel."
        self.auto_backup(f"before-channel-{idx}")
        node.channels[idx].CopyFrom(new)
        try:
            node.writeChannel(idx)
        except Exception as exc:
            return False, f"Couldn't save the channel: {exc}"
        self.add_log(f"[MeshTap] saved channel {idx} ({new.settings.name or 'default'})")
        return True, f"Channel {idx} saved."

    def set_channel_url(self, url):
        node, iface = self._radio()
        if "meshtastic.org/e/" not in (url or ""):
            return False, "That doesn't look like a Meshtastic channel link (https://meshtastic.org/e/#…)."
        self.auto_backup("before-channel-link")
        try:
            node.setURL(url.strip())
        except SystemExit:
            return False, "That channel link couldn't be read."
        except Exception as exc:
            return False, f"Couldn't apply the link: {exc}"
        self.add_log("[MeshTap] channels replaced from a channel link")
        return True, "Channels set from the link."

    def reboot_radio(self):
        node, iface = self._radio()
        try:
            node.reboot(3)
        except Exception as exc:
            return False, f"Couldn't restart the radio: {exc}"
        self.add_log("[MeshTap] radio restart requested")
        return True, "Restarting the radio — MeshTap reconnects by itself."

    def make_backup(self, full=False):
        """A clone file of this radio: owner, all settings, all channels. full=True also keeps its private key."""
        node, iface = self._radio()
        info = self.radio_info()
        cfg = info["config"]
        sec = cfg.get("security") or {}
        sec.pop("private_key_set", None)
        if full:
            import base64
            sec["private_key"] = base64.b64encode(node.localConfig.security.private_key).decode()
        else:
            sec.pop("public_key", None)
        return {"meshtap_backup": 1, "kind": "full" if full else "clone", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "app_version": VERSION, "firmware": (info["metadata"] or {}).get("firmware_version"),
                "hardware": (info["metadata"] or {}).get("hw_model") or info["owner"].get("hwModel"),
                "owner": {k: info["owner"].get(k) for k in ("longName", "shortName", "isLicensed")}, "node_id": info["owner"].get("id") or self.my_id,
                "config": cfg, "modules": info["modules"], "channels": info["channels"], "channel_url": info["channel_url"]}

    def save_snapshot(self, label="", full=False):
        """Save the radio's current config on this computer (and return it for download)."""
        b = self.make_backup(full=full)
        os.makedirs(BACKUP_DIR, exist_ok=True)
        tag = "".join(c if c.isalnum() or c in "-_" else "-" for c in (label or "saved").strip())[:40] or "saved"
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{(b['owner'].get('shortName') or 'radio')}-{'FULL-' if full else ''}{tag}.json"
        name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        with open(os.path.join(BACKUP_DIR, name), "w", encoding="utf-8") as fh:
            json.dump(b, fh, indent=1)
        self.add_log(f"[MeshTap] saved the radio's config: {name}")
        return name, b

    def load_backup(self, name):
        path = os.path.join(BACKUP_DIR, os.path.basename(name or ""))
        if not path.endswith(".json") or not os.path.isfile(path):
            raise RuntimeError("That saved config wasn't found.")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def factory_reset(self, full=False):
        """Reset the radio. A FULL backup (with its private key) is saved on this computer first."""
        node, iface = self._radio()
        try:
            name, _ = self.save_snapshot("before-factory-reset", full=True)
        except Exception as exc:
            return False, f"Didn't reset: couldn't save a backup first ({exc})."
        try:
            node.factoryReset(full=bool(full))
        except Exception as exc:
            return False, f"Couldn't reset the radio: {exc}"
        self.add_log(f"[MeshTap] factory reset ({'full wipe' if full else 'settings'}) sent; backup saved as {name}")
        return True, (f"Reset sent. The radio restarts with factory settings{' and a new identity' if full else ''}; MeshTap reconnects by itself. "
                      f"Your previous setup is saved as {name} — restore it any time from Clone & backup.")

    def auto_backup(self, why):
        """Before any change, keep a copy of the radio's settings (last 50 kept)."""
        try:
            b = self.make_backup(full=False)
            os.makedirs(BACKUP_DIR, exist_ok=True)
            name = f"{time.strftime('%Y%m%d-%H%M%S')}-{(b['owner'].get('shortName') or 'radio')}-{why}.json"
            name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
            with open(os.path.join(BACKUP_DIR, name), "w", encoding="utf-8") as fh:
                json.dump(b, fh, indent=1)
            files = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".json") and "-before-" in f and "-FULL-" not in f)
            for f in files[:-50]:
                os.remove(os.path.join(BACKUP_DIR, f))
        except Exception as exc:
            self.add_log(f"[MeshTap] couldn't make the automatic backup: {exc}")

    def list_backups(self):
        try:
            files = sorted((f for f in os.listdir(BACKUP_DIR) if f.endswith(".json")), reverse=True)
        except OSError:
            return []
        return [{"name": f, "size": os.path.getsize(os.path.join(BACKUP_DIR, f)), "full": "-FULL-" in f} for f in files[:50]]

    def restore(self, b, parts):
        """Apply a clone file to the connected radio. Never copies another radio's private key."""
        node, iface = self._radio()
        if not isinstance(b, dict) or not b.get("meshtap_backup"):
            return False, "That isn't a MeshTap backup file."
        parts = parts or {}
        self.auto_backup("before-restore")
        done = []
        try:
            node.beginSettingsTransaction()
            ident = None
            if parts.get("identity") and b.get("kind") == "full" and (b.get("config") or {}).get("security", {}).get("private_key"):
                if b.get("node_id") and self.my_id and b["node_id"] != self.my_id:
                    raise RuntimeError(f"that full backup is from {b['node_id']}, not this radio ({self.my_id}) — identity not copied")
                import base64
                ident = base64.b64decode(b["config"]["security"]["private_key"])
            if parts.get("config", True) or ident:
                for s in CONFIG_SECTIONS:
                    if s in (b.get("config") or {}) and (parts.get("config", True) or s == "security"):
                        vals = dict(b["config"][s])
                        if s == "security":
                            vals.pop("private_key", None), vals.pop("public_key", None)
                        cur, new = self._build_section(node.localConfig, s, vals)
                        if s == "security" and ident:
                            new.private_key = ident
                            new.public_key = b""   # the radio works its public key out from the private one
                            done.append("identity")
                        if cur.SerializeToString() != new.SerializeToString():
                            cur.CopyFrom(new); node.writeConfig(s); done.append(s)
            if parts.get("modules", True) and getattr(node, "moduleConfig", None) is not None:
                for s in MODULE_SECTIONS:
                    if s in (b.get("modules") or {}) and hasattr(node.moduleConfig, s):
                        cur, new = self._build_section(node.moduleConfig, s, b["modules"][s])
                        if cur.SerializeToString() != new.SerializeToString():
                            cur.CopyFrom(new); node.writeConfig(s); done.append(s)
            if parts.get("channels", True):
                _, ch, _, _, jf = _pb()
                for c in b.get("channels") or []:
                    i = int(c.get("index", -1))
                    if 0 <= i < len(node.channels):
                        new = ch.Channel(); jf.ParseDict(c, new, ignore_unknown_fields=True); new.index = i
                        if node.channels[i].SerializeToString() != new.SerializeToString():
                            node.channels[i].CopyFrom(new); node.writeChannel(i); done.append(f"channel {i}")
            node.commitSettingsTransaction()
        except SystemExit:
            return False, "The radio rejected part of the file (it may be from a very different firmware)."
        except Exception as exc:
            return False, f"Stopped partway: {exc}. An automatic backup of the radio was saved first."
        if parts.get("owner") and b.get("owner", {}).get("longName"):
            self.write_owner(b["owner"]["longName"], b["owner"].get("shortName"), b["owner"].get("isLicensed"))
            done.append("name")
        self.add_log(f"[MeshTap] cloned onto the radio: {', '.join(done) or 'nothing different'}")
        return True, (f"Copied {len(done)} part{'s' if len(done) != 1 else ''}: {', '.join(done)}. The radio restarts to apply; MeshTap reconnects."
                      if done else "This radio already matches that file.")

    def set_radio_hop_limit(self, value):
        """Write the LoRa hop limit to the radio (1–7). Most radios restart afterwards; we reconnect."""
        try:
            value = int(value)
        except (TypeError, ValueError):
            return False, "Pick a number from 1 to 7."
        if not 1 <= value <= 7:
            return False, "Meshtastic allows a hop limit from 1 to 7."
        if self.mode == "demo":
            self._radio()[0].localConfig.lora.hop_limit = value
            return True, f"Demo radio hop limit set to {value}."
        iface = self.iface
        node = getattr(iface, "localNode", None) if iface else None
        if not self.connected or node is None or getattr(node, "localConfig", None) is None:
            return False, "Connect to your radio over USB, WiFi or Bluetooth first."
        old = self.my_hop_limit()
        try:
            node.localConfig.lora.hop_limit = value
            node.writeConfig("lora")
        except SystemExit:
            return False, "The radio hasn't sent its settings yet — wait a few seconds and try again."
        except Exception as exc:
            return False, f"Couldn't change it: {exc}"
        self.add_log(f"[MeshTap] radio hop limit changed {old} → {value}; the radio may restart — MeshTap will reconnect")
        return True, f"Hop limit set to {value} (was {old}). Your radio may restart for a moment; MeshTap reconnects by itself."

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

    def disconnect(self, _auto=False):
        if not _auto:
            self.reconnect_gen = getattr(self, "reconnect_gen", 0) + 1   # user chose to disconnect: stop retrying
        self.ping_job = None
        if getattr(self, "scan", None) and not self.scan.get("finished"):
            self.scan["finished"] = time.time()
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
            self.links = {}
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
        HUB.error = "Lost connection to the radio — reconnecting…"
        HUB.add_log("[MeshTap] connection lost")
        HUB.push_status()
        HUB.auto_reconnect()


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
        if url.path == "/api/radio":
            try:
                return self._send(200, HUB.radio_info())
            except Exception as exc:
                return self._send(200, {"error": str(exc)})
        if url.path in ("/api/radio/backup.json", "/api/radio/fullbackup.json"):
            try:
                full = "full" in url.path
                b = HUB.make_backup(full=full)
            except Exception as exc:
                return self._send(400, {"error": str(exc)})
            name = f"{b['owner'].get('shortName') or 'radio'}-{'FULL-' if full else ''}clone-{time.strftime('%Y%m%d-%H%M')}.json"
            return self._send(200, json.dumps(b, indent=1), "application/json",
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        if url.path == "/api/radio/backups/peek":
            try:
                return self._send(200, HUB.load_backup((qs.get("name") or [""])[0]))
            except Exception as exc:
                return self._send(404, {"error": str(exc)})
        if url.path == "/api/radio/backups/get":
            name = os.path.basename((qs.get("name") or [""])[0])
            path = os.path.join(BACKUP_DIR, name)
            if not name.endswith(".json") or not os.path.isfile(path):
                return self._send(404, {"error": "not found"})
            with open(path, "rb") as fh:
                return self._send(200, fh.read(), "application/json", {"Content-Disposition": f'attachment; filename="{name}"'})
        if url.path == "/api/spider":
            return self._send(200, HUB.spider())
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
        if url.path.startswith("/api/radio/") and url.path != "/api/radio/hop_limit":
            if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                return self._send(403, {"ok": False, "msg": "Radio settings can only be changed from this computer."})
            try:
                if url.path == "/api/radio/section":
                    ok, msg = HUB.write_section(body.get("kind"), body.get("section"), body.get("values"))
                elif url.path == "/api/radio/owner":
                    ok, msg = HUB.write_owner(body.get("longName"), body.get("shortName"), body.get("isLicensed"))
                elif url.path == "/api/radio/channel":
                    ok, msg = HUB.write_channel(body.get("channel") or {})
                elif url.path == "/api/radio/channel_url":
                    ok, msg = HUB.set_channel_url(body.get("url"))
                elif url.path == "/api/radio/reboot":
                    ok, msg = HUB.reboot_radio()
                elif url.path == "/api/radio/restore":
                    b = HUB.load_backup(body["saved"]) if body.get("saved") else body.get("backup")
                    ok, msg = HUB.restore(b, body.get("parts"))
                elif url.path == "/api/radio/save":
                    name, _ = HUB.save_snapshot(body.get("label") or "", full=bool(body.get("full")))
                    ok, msg = True, f"Saved as {name} (in your .meshtap\\backups folder)."
                elif url.path == "/api/radio/factory_reset":
                    if body.get("confirm") != "RESET":
                        ok, msg = False, "Type RESET to confirm."
                    else:
                        ok, msg = HUB.factory_reset(full=bool(body.get("full")))
                else:
                    return self._send(404, {"ok": False, "msg": "not found"})
            except Exception as exc:
                ok, msg = False, str(exc)
            return self._send(200, {"ok": ok, "msg": msg})
        if url.path == "/api/radio/hop_limit":
            if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                return self._send(403, {"ok": False, "msg": "Radio settings can only be changed from this computer."})
            ok, msg = HUB.set_radio_hop_limit(body.get("value"))
            return self._send(200, {"ok": ok, "msg": msg})
        if url.path == "/api/settings":
            HUB.update_settings(body)
            return self._send(200, {"ok": True})
        if url.path == "/api/scan":
            ok, msg = HUB.start_scan(body.get("hop_limit") or 7, body.get("interval") or PING_MIN_INTERVAL)
            return self._send(200, {"ok": ok, "msg": msg})
        if url.path == "/api/scan/stop":
            HUB.stop_scan()
            HUB.cancel_ping("Scan stopped")
            return self._send(200, {"ok": True})
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
