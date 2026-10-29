#!/usr/bin/env python3
"""
RPi400 Task Manager
A Windows-Task-Manager-style dashboard for Raspberry Pi OS (Bookworm / Trixie).
Built for the Raspberry Pi 400, works on any Pi 4 / Pi 5.

Tabs
  F1 Overview   - gauges, per-core CPU, power / voltage / throttling lights
  F2 Processes  - sortable list, filter, end task / force kill
  F3 Network    - link / RX / TX lights per interface, speeds, hotspot clients
  F4 Tools      - clean memory, Bluetooth rescue & pairing, hotspot + QR,
                  CPU mode (battery saver), overclock presets, VNC, startup

Anything that needs root goes through /usr/local/bin/rpi400-helper,
which install.sh sets up (with a sudo rule so no password prompts).
"""
import os
import re
import csv
import json
import queue
import select
import fcntl
import urllib.request
import webbrowser
import sys
import glob
import time
import shutil
import socket
import threading
import subprocess
from collections import deque

import tkinter as tk
from tkinter import ttk, messagebox

try:
    import psutil
except ImportError:
    sys.stderr.write("psutil is missing. Install it with:  sudo apt install python3-psutil\n")
    sys.exit(1)

try:
    import qrcode  # optional: draws the "join my Wi-Fi" QR code
except Exception:
    qrcode = None

# ---------------------------------------------------------------- settings
APP_NAME = "RPi400 Task Manager"
VERSION = "1.0"
HELPER = "/usr/local/bin/rpi400-helper"
LAUNCHER = "/usr/local/bin/rpi400-taskmgr"
AP_CON = "RPI400-OPEN"            # NetworkManager's name for the hotspot profile (never changes)
AP_SSID_DEFAULT = "RPI400-OPEN"   # Wi-Fi name and password; can be changed in the Tools tab
AP_PASS_DEFAULT = "fifty-seven57"
AP_SSID, AP_PASS = AP_SSID_DEFAULT, AP_PASS_DEFAULT
AP_IP = "192.168.1.250"
SHELF_URL = f"http://{AP_IP}:8080/"   # The Shelf's usual address on the hotspot (see shelf_url)
VISITOR_LOG = "RPI400-OPEN visitor log.csv"   # kept on the Desktop
LIVE_REPORT = "RPI400-OPEN visitors - latest.txt"   # auto-saved report, kept current
LED_CONF = "/etc/rpi400-taskmgr.conf"
UPDATE_LOG = "/var/log/rpi400-update.log"      # written by "rpi400-helper update", readable by everyone
UPDATE_STATE = "/run/rpi400-update.state"      # running|done|failed  start  end  reboot-needed
WEB_UNIT = "/etc/systemd/system/rpi400-web.service"
WEB_SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpi400_web.py")
WEB_PASSWORD_FILE = os.path.expanduser("~/.config/rpi400-taskmgr/web-password.json")
OLLAMA = "http://127.0.0.1:11434"
RAM_GB = psutil.virtual_memory().total / 1024 ** 3
if RAM_GB >= 7:   # 8 or 16 GB, e.g. Pi 500+: room for much smarter models
    AI_SUGGESTED = [
        ("qwen2.5:1.5b", "Fastest, about 1 GB"),
        ("llama3.2:3b", "Balanced, about 2 GB"),
        ("gemma3:4b", "Smarter, about 3.3 GB"),
        ("qwen2.5:7b", "Smartest, about 4.7 GB, slow"),
    ]
    AI_DEFAULT = "llama3.2:3b"
else:             # 4 GB, e.g. Pi 400
    AI_SUGGESTED = [
        ("qwen2.5:0.5b", "Fastest, about 400 MB"),
        ("gemma3:1b", "Balanced, about 800 MB"),
        ("llama3.2:1b", "Smartest of the three, about 1.3 GB"),
    ]
    AI_DEFAULT = "qwen2.5:0.5b"
AI_SYSTEM = ("You are a friendly, helpful assistant running entirely offline on a Raspberry Pi. "
             "Keep answers short and clear unless asked for detail.")
SETTINGS_FILE = os.path.expanduser("~/.config/rpi400-taskmgr/settings.json")
AUTOSTART_FILE = os.path.expanduser("~/.config/autostart/rpi400-taskmgr.desktop")
CFG_PATH = "/boot/firmware/config.txt" if os.path.exists("/boot/firmware/config.txt") else "/boot/config.txt"
OC_BEGIN = "# >>> rpi400-taskmgr overclock >>>"
OC_END = "# <<< rpi400-taskmgr overclock <<<"

C = dict(
    bg="#14171c", panel="#1d2229", panel2="#262d36", fg="#e8ebf0", dim="#8b95a3",
    accent="#4fb3ff", green="#3ddc84", amber="#ffb020", red="#ff4d5a", off="#39414c",
    grid="#2c333d",
)
FAM = "DejaVu Sans"
FONT = (FAM, 10)
FONT_S = (FAM, 9)
FONT_B = (FAM, 10, "bold")
FONT_H = (FAM, 13, "bold")


# ---------------------------------------------------------------- helpers
def run(cmd, timeout=8):
    """Run a command, return (returncode, combined output)."""
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
    """Run a privileged action through the root helper."""
    if not os.path.exists(HELPER):
        return 1, "The root helper isn't installed yet. Run:  sudo bash install.sh"
    rc, out = run(["sudo", "-n", HELPER, *args], timeout)
    if rc != 0 and ("password" in out or "terminal is required" in out):
        rc, out = run(["pkexec", HELPER, *args], timeout)
    return rc, out


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


def fmt_pair(used, total):
    """Compact 'used / total' for gauge captions, e.g. '3.1 / 16 GB' or '480 / 512 MB'."""
    gb = 1024 ** 3
    if total >= gb:
        t = total / gb
        return f"{used / gb:.1f} / {t:.0f} GB" if t >= 10 else f"{used / gb:.1f} / {t:.1f} GB"
    return f"{used / 1024 ** 2:.0f} / {total / 1024 ** 2:.0f} MB"


def fmt_rate(bps):
    return fmt_bytes(bps) + "/s"


def fmt_uptime(sec):
    sec = int(sec)
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, _ = divmod(sec, 60)
    return (f"{d}d " if d else "") + f"{h}h {m:02d}m"


# ---------------------------------------------------------------- hardware profile
MODEL_NAME = read_file("/proc/device-tree/model").replace("\x00", "")
IS_PI5 = "bcm2712" in read_file("/proc/device-tree/compatible").replace("\x00", " ")   # Pi 5 / 500 / 500+
IS_500 = "500" in MODEL_NAME
OC_PRESETS = ([("stock", "Stock", "stock 2.4 GHz"), ("2600", "2.6", "2.6 GHz"), ("2800", "2.8", "2.8 GHz"),
               ("3000", "3.0", "3.0 GHz")]
              if IS_PI5 else
              [("stock", "Stock", "stock speed"), ("2000", "2.0 GHz", "2.0 GHz"), ("2147", "2.15", "2.15 GHz")])


def read_be_u32(path):
    try:
        with open(path, "rb") as f:
            b = f.read(4)
        return int.from_bytes(b, "big") if len(b) == 4 else None
    except OSError:
        return None


def pmic_power():
    """Pi 5-class boards: (input volts, estimated watts) from the power chip, or (None, None).
    Watts = sum of the measured rails, scaled by the calibration from github.com/jfikar/RPi5-power
    to include what the chip doesn't measure (USB, SSD)."""
    out = vcgen("pmic_read_adc")
    cur, vol = {}, {}
    for line in out.splitlines():
        m = re.match(r"\s*(\S+)_([AV])\s+(?:current|volt)\(\d+\)=([\d.]+)", line)
        if m:
            (cur if m.group(2) == "A" else vol)[m.group(1)] = float(m.group(3))
    vin = vol.get("EXT5V")
    raw = sum(cur[n] * vol[n] for n in cur if n in vol)
    return vin, (raw * 1.1451 + 0.5879 if cur else None)


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


def ipv4_addrs():
    out = []
    for nic, addrs in psutil.net_if_addrs().items():
        if nic == "lo":
            continue
        for a in addrs:
            if a.family == socket.AF_INET:
                out.append((nic, a.address))
    return out


def read_oc():
    txt = read_file(CFG_PATH)
    m = re.search(re.escape(OC_BEGIN) + r"(.*?)" + re.escape(OC_END), txt, re.S)
    if m:
        f = re.search(r"arm_freq=(\d+)", m.group(1))
        return f"{f.group(1)} MHz (overclocked)" if f else "Overclocked"
    if re.search(r"^\s*arm_freq\s*=", txt, re.M):
        return "Custom (set by hand in config.txt)"
    return "Stock"


GPU_BEGIN = OC_BEGIN.replace("overclock", "graphics boost")
GPU_END = OC_END.replace("overclock", "graphics boost")
STABILITY_MARK = os.path.expanduser("~/.config/rpi400-taskmgr/stability-running.json")


def read_gpu_boost():
    return GPU_BEGIN in read_file(CFG_PATH)


def listening_ports():
    try:
        return {c.laddr.port for c in psutil.net_connections("inet") if c.status == psutil.CONN_LISTEN}
    except Exception:
        return set()


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


def fmt_dur(sec):
    sec = int(max(0, sec))
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s" if m else f"{s}s"


def ollama_get(path, timeout=1.5):
    try:
        with urllib.request.urlopen(OLLAMA + path, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


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


def led_blink_enabled():
    """Mirrors the helper: on unless LED_BLINK=0 is set."""
    return not re.search(r"^LED_BLINK=0", read_file(LED_CONF), re.M)


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_setting(key, value):
    s = load_settings()
    s[key] = value
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(s, f, indent=2)
    except OSError:
        pass


_DESKTOP = None


def desktop_dir():
    global _DESKTOP
    if _DESKTOP is None:
        rc, out = run(["xdg-user-dir", "DESKTOP"], 2)
        d = out.strip() if rc == 0 and out.strip() and out.strip() != os.path.expanduser("~") else ""
        _DESKTOP = d or os.path.expanduser("~/Desktop")
    os.makedirs(_DESKTOP, exist_ok=True)
    return _DESKTOP


# ---------------------------------------------------------------- hotspot visitor log
class VisitorLog:
    """Tracks who joins and leaves the hotspot and appends every event to a CSV on the Desktop
    (opens in LibreOffice Calc or any spreadsheet)."""
    GRACE = 12  # seconds a device may vanish before we call it "left" (phones nap)
    FIELDS = ["Date", "Time", "Event", "Device name", "IP address", "MAC address", "Stayed", "Data used"]

    def __init__(self):
        self.active = {}
        self.history = []
        self.ap_on = None
        self.error = ""
        self.unsaved = []           # events waiting to be written while auto-save is off
        self.autosave = bool(load_settings().get("autosave", True))
        self.last_saved = None
        self._live_at = 0.0
        self._load_previous()

    @property
    def path(self):
        return os.path.join(desktop_dir(), VISITOR_LOG)

    def _load_previous(self):
        try:
            with open(self.path, newline="") as f:
                rows = list(csv.DictReader(f))
            self.history = [r for r in rows if r.get("Event")][-300:]
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
        if self.autosave and (pending or (self.active and now - self._live_at >= 60)):
            self._write_live(now)

    def set_autosave(self, on):
        self.autosave = bool(on)
        save_setting("autosave", self.autosave)
        if self.autosave:
            self._flush()
            self._write_live(time.time())

    def _flush(self):
        """Write any events that happened while auto-save was off."""
        if not self.unsaved:
            return
        try:
            new = not os.path.exists(self.path)
            with open(self.path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=self.FIELDS)
                if new:
                    w.writeheader()
                w.writerows(self.unsaved)
            self.unsaved.clear()
            self.last_saved = time.time()
            self.error = ""
        except OSError as e:
            self.error = f"Couldn't write the Desktop log: {e}"

    def _write_live(self, now):
        self._live_at = now
        try:
            self.save_report(os.path.join(desktop_dir(), LIVE_REPORT))
        except OSError as e:
            self.error = f"Couldn't auto-save the report: {e}"

    def _record(self, event, a, when):
        t = time.localtime(when)
        row = {"Date": time.strftime("%Y-%m-%d", t), "Time": time.strftime("%H:%M:%S", t), "Event": event,
               "Device name": a.get("name") or ("" if not a else "(no name)"),
               "IP address": a.get("ip", ""), "MAC address": a.get("mac", ""),
               "Stayed": fmt_dur(a["last"] - a["first"]) if event == "Left" else "",
               "Data used": fmt_bytes(a.get("rx", 0) + a.get("tx", 0)) if event == "Left" else ""}
        self.history.append(row)
        del self.history[:-300]
        self.unsaved.append(row)
        if self.autosave:
            self._flush()

    def save_report(self, path=None):
        now = time.time()
        if path is None:  # manual save: timestamped copy, and catch the spreadsheet log up too
            path = os.path.join(desktop_dir(), time.strftime("Hotspot visitors %Y-%m-%d %H%M.txt"))
            self._flush()
        lines = [f"{AP_SSID} hotspot — visitor report", time.strftime("Saved %A %B %d, %Y at %I:%M %p"), ""]
        lines.append(f"CONNECTED RIGHT NOW ({len(self.active)})")
        if not self.active:
            lines.append("  nobody")
        for a in sorted(self.active.values(), key=lambda x: x["first"]):
            lines.append(f"  {a.get('name') or '(no name)':<24} {a.get('ip') or '—':<16} {a['mac']}   "
                         f"since {time.strftime('%I:%M %p', time.localtime(a['first']))} "
                         f"({fmt_dur(now - a['first'])}, {fmt_bytes(a.get('rx', 0) + a.get('tx', 0))})")
        lines += ["", "ACTIVITY (most recent last)"]
        for r in self.history:
            extra = f"  stayed {r['Stayed']}, used {r['Data used']}" if r.get("Stayed") else ""
            who = f"{r.get('Device name') or ''} {r.get('IP address') or ''} {r.get('MAC address') or ''}".strip()
            lines.append(f"  {r['Date']} {r['Time']}  {r['Event']:<8} {who}{extra}")
        joins = [r for r in self.history if r["Event"] == "Joined"]
        lines += ["", f"Total joins in this log: {len(joins)} · different devices: "
                      f"{len({r['MAC address'] for r in joins})}",
                  f"Full running log (spreadsheet): {self.path}"]
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        self.last_saved = now
        return path


def signal_words(dbm):
    if dbm is None:
        return ""
    return "excellent" if dbm >= -55 else "good" if dbm >= -67 else "fair" if dbm >= -75 else "weak"


def strip_ansi(s):
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]|[\x01\x02]", "", s)


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


# ---------------------------------------------------------------- background poller
class SlowPoller(threading.Thread):
    """Gathers the slower facts (vcgencmd, nmcli, bluetoothctl...) off the UI thread."""

    def __init__(self):
        super().__init__(daemon=True)
        self._data = {}
        self._lock = threading.Lock()
        self._n = 0
        self.vpn_now = True      # set to refresh Tailscale on the next poll

    def get(self):
        with self._lock:
            return dict(self._data)

    def run(self):
        while True:
            try:
                d = self.collect()
            except Exception as e:  # noqa
                d = {"error": str(e)}
            with self._lock:
                self._data.update(d)
            self._n += 1
            time.sleep(2)

    def collect(self):
        d = {}
        m = re.search(r"([\d.]+)V", vcgen("measure_volts", "core"))
        d["vcore"] = float(m.group(1)) if m else None
        m = re.search(r"0x([0-9a-fA-F]+)", vcgen("get_throttled"))
        d["throttled"] = int(m.group(1), 16) if m else None
        m = re.search(r"=(\d+)", vcgen("measure_clock", "arm"))
        d["arm_hz"] = int(m.group(1)) if m else None
        d["governor"] = read_file("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        d["oc"] = read_oc()
        if IS_PI5:
            d["vin"], d["watts"] = pmic_power()
            if not hasattr(self, "_psu_ma"):
                self._psu_ma = read_be_u32("/proc/device-tree/chosen/power/max_current")
            d["psu_ma"] = self._psu_ma
        try:
            nv = getattr(psutil, "sensors_temperatures", lambda: {})().get("nvme")
            d["ssd_temp"] = nv[0].current if nv else None
        except Exception:
            d["ssd_temp"] = None
        fans = glob.glob("/sys/class/hwmon/hwmon*/fan1_input")
        d["fan_rpm"] = int(read_file(fans[0], "0") or 0) if fans else None

        _, out = run(["iw", "dev", "wlan0", "link"], 3)
        m = re.search(r"SSID: (.+)", out)
        d["ssid"] = m.group(1).strip() if m else ""
        m = re.search(r"signal: (-?\d+) dBm", out)
        d["signal"] = int(m.group(1)) if m else None

        rc, out = run(["nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active"], 4)
        d["nm"] = rc == 0
        d["ap_on"] = any(line.split(":")[0] == AP_CON for line in out.splitlines()) if rc == 0 else False
        d["clients"] = hotspot_clients() if d["ap_on"] else []
        d["ap_band"] = "a" if re.search(r"^AP_BAND=a", read_file(LED_CONF), re.M) else "bg"
        d["ap_freq"] = None
        if d["ap_on"]:
            _, out = run(["iw", "dev", "wlan0", "info"], 3)
            m = re.search(r"channel (\d+) \((\d+) MHz", out)
            d["ap_freq"] = (int(m.group(1)), int(m.group(2))) if m else None

        ports = listening_ports()
        d["vnc"] = 5900 in ports
        d["shelf_url"] = shelf_url(ports)
        d["web_port"] = web_port()
        d["web"] = d["web_port"] in ports
        if self._n % 3 == 0 or self.vpn_now:   # (kept between polls: _data is merged)
            rc, out = run(["systemctl", "is-enabled", "rpi400-web.service"], 3)
            d["web_enabled"] = out.strip() == "enabled"
        d["ssh"] = 22 in ports

        rc, out = run(["bluetoothctl", "show"], 3)
        d["bt_powered"] = "Powered: yes" in out
        d["bt_present"] = rc == 0 and "Controller" in out
        rc, out = run(["bluetoothctl", "devices", "Connected"], 3)
        d["bt_connected"] = [l.split(" ", 2)[2] for l in out.splitlines()
                             if l.startswith("Device ") and len(l.split(" ", 2)) == 3]

        d["ai_installed"] = shutil.which("ollama") is not None
        tags = ollama_get("/api/tags") if d["ai_installed"] else None
        d["ai_active"] = tags is not None
        d["ai_models"] = sorted(m.get("name", "") for m in (tags or {}).get("models", []))
        ps = ollama_get("/api/ps") if tags is not None else None
        d["ai_loaded"] = [(m.get("name", ""), m.get("size", 0)) for m in (ps or {}).get("models", [])]

        if self._n % 3 == 0 or self.vpn_now:
            self.vpn_now = False
            d["vpn"] = vpn_status()

        if self._n % 5 == 0:
            _, out = run(["systemctl", "is-enabled", "rpi400-fallback-ap"], 3)
            d["fallback"] = out.strip() == "enabled"
        return d


# ---------------------------------------------------------------- widgets
class Gauge(tk.Canvas):
    def __init__(self, parent, label, unit="%", vmax=100, warn=70, crit=90, colored=True, size=140,
                 vmin=0.0, low=None):
        super().__init__(parent, width=size, height=size, bg=C["panel"], highlightthickness=0)
        self.unit, self.vmax, self.warn, self.crit, self.colored = unit, vmax, warn, crit, colored
        self.vmin, self.low = vmin, low   # low=(warn, crit) means LOW values are the bad ones
        p = 14
        box = (p, p, size - p, size - p)
        self.create_arc(*box, start=-30, extent=240, style="arc", width=11, outline=C["off"])
        self.arc = self.create_arc(*box, start=210, extent=-0.5, style="arc", width=11, outline=C["green"])
        cx = size / 2
        self.txt = self.create_text(cx, cx - 4, text="--", fill=C["fg"], font=(FAM, 17, "bold"))
        self.sub = self.create_text(cx, cx + 19, text="", fill=C["dim"], font=(FAM, 8 if size >= 136 else 7))
        self.create_text(cx, size - 12, text=label, fill=C["fg"], font=(FAM, 9, "bold"))

    def set(self, value, text=None, sub=""):
        if value is None:
            self.itemconfig(self.arc, extent=-0.5, outline=C["off"])
            self.itemconfig(self.txt, text="--")
            self.itemconfig(self.sub, text=sub)
            return
        frac = max(0.0, min(1.0, (value - self.vmin) / (self.vmax - self.vmin)))
        if self.low:
            color = C["red"] if value <= self.low[1] else C["amber"] if value <= self.low[0] else C["green"]
        elif self.colored:
            color = C["red"] if value >= self.crit else C["amber"] if value >= self.warn else C["green"]
        else:
            color = C["accent"]
        self.itemconfig(self.arc, extent=-max(frac * 240, 0.5), outline=color)
        self.itemconfig(self.txt, text=text if text is not None else f"{value:.0f}{self.unit}")
        self.itemconfig(self.sub, text=sub)


class Light(tk.Frame):
    def __init__(self, parent, text, bg=None, size=12):
        bg = bg or C["panel"]
        super().__init__(parent, bg=bg)
        self.c = tk.Canvas(self, width=size + 4, height=size + 4, bg=bg, highlightthickness=0)
        self.dot = self.c.create_oval(2, 2, size + 2, size + 2, fill=C["off"], outline="#0b0d10")
        self.c.pack(side="left")
        self.lbl = tk.Label(self, text=text, bg=bg, fg=C["dim"], font=FONT_S)
        self.lbl.pack(side="left", padx=(2, 6))
        self._color = None

    def set(self, color, text=None):
        color = color or C["off"]
        if color != self._color:
            self.c.itemconfig(self.dot, fill=color)
            self._color = color
        if text is not None and text != self.lbl.cget("text"):
            self.lbl.config(text=text)


class NicLights(tk.Frame):
    def __init__(self, parent, name, bg, show_name=True):
        super().__init__(parent, bg=bg)
        if show_name:
            tk.Label(self, text=name, bg=bg, fg=C["fg"], font=FONT_B).pack(side="left", padx=(0, 5))
        self.link = Light(self, "LINK", bg)
        self.rx = Light(self, "RX", bg)
        self.tx = Light(self, "TX", bg)
        for w in (self.link, self.rx, self.tx):
            w.pack(side="left")

    def set_state(self, up, rx, tx):
        self.link.set(C["green"] if up else C["red"])
        self.rx.set(C["green"] if rx else None)
        self.tx.set(C["amber"] if tx else None)


class Graph(tk.Canvas):
    def __init__(self, parent, series, height=120, maxlen=60, fixed_max=None, fmt=lambda v: f"{v:.0f}%"):
        super().__init__(parent, height=height, bg=C["panel2"], highlightthickness=0)
        self.series, self.maxlen, self.fixed_max, self.fmt = series, maxlen, fixed_max, fmt
        self.data = [deque([0.0] * maxlen, maxlen=maxlen) for _ in series]
        self.bind("<Configure>", lambda e: self.redraw())

    def push(self, *vals):
        for d, v in zip(self.data, vals):
            d.append(float(v))
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20:
            return
        top = self.fixed_max or max(1.0, max(max(d) for d in self.data) * 1.2)
        for i in range(1, 4):
            y = h * i / 4
            self.create_line(0, y, w, y, fill=C["grid"])
        for (name, color), d in zip(self.series, self.data):
            pts = []
            for i, v in enumerate(d):
                pts += [i * w / (self.maxlen - 1), h - 3 - (v / top) * (h - 8)]
            self.create_line(*pts, fill=color, width=2, smooth=True)
        self.create_text(6, 5, anchor="nw", text=self.fmt(top), fill=C["dim"], font=FONT_S)
        x = w - 6
        for name, color in reversed(self.series):
            t = self.create_text(x, 5, anchor="ne", text=f"● {name}", fill=color, font=FONT_S)
            x = self.bbox(t)[0] - 10


class CoreBars(tk.Canvas):
    def __init__(self, parent, n):
        super().__init__(parent, height=n * 20 + 6, bg=C["panel"], highlightthickness=0)
        self.vals = [0.0] * n
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, vals):
        self.vals = vals
        self.redraw()

    def redraw(self):
        self.delete("all")
        w = self.winfo_width()
        if w < 50:
            return
        for i, v in enumerate(self.vals):
            y = 4 + i * 20
            self.create_text(4, y + 8, anchor="w", text=f"Core {i}", fill=C["dim"], font=FONT_S)
            x0, x1 = 58, w - 48
            self.create_rectangle(x0, y + 3, x1, y + 14, fill=C["panel2"], outline="")
            col = C["red"] if v >= 90 else C["amber"] if v >= 70 else C["accent"]
            self.create_rectangle(x0, y + 3, x0 + (x1 - x0) * v / 100, y + 14, fill=col, outline="")
            self.create_text(w - 6, y + 8, anchor="e", text=f"{v:.0f}%", fill=C["fg"], font=FONT_S)


class ToggleSwitch(tk.Frame):
    """A slide switch with a label. Click it, or Tab to it and press Space/Enter."""
    W, H = 46, 24

    def __init__(self, parent, text, value=False, command=None, bg=None):
        bg = bg or C["panel"]
        super().__init__(parent, bg=bg)
        self.value, self.command = bool(value), command
        self.c = tk.Canvas(self, width=self.W + 4, height=self.H + 4, bg=bg, highlightthickness=2,
                           highlightbackground=bg, highlightcolor=C["accent"], takefocus=1, cursor="hand2")
        self.c.pack(side="left")
        self.lbl = tk.Label(self, text=text, bg=bg, fg=C["fg"], font=FONT_B, cursor="hand2")
        self.lbl.pack(side="left", padx=(4, 0))
        for w in (self.c, self.lbl):
            w.bind("<Button-1>", lambda e: self.toggle())
        for key in ("<space>", "<Return>"):
            self.c.bind(key, lambda e: self.toggle())
        self.draw()

    def _pill(self, x0, y0, x1, y1, color):
        r = (y1 - y0) / 2
        self.c.create_oval(x0, y0, x0 + 2 * r, y1, fill=color, outline=color)
        self.c.create_oval(x1 - 2 * r, y0, x1, y1, fill=color, outline=color)
        self.c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline=color)

    def draw(self):
        self.c.delete("all")
        x0, y0, x1, y1 = 2, 2, self.W + 2, self.H + 2
        self._pill(x0, y0, x1, y1, C["green"] if self.value else C["off"])
        k = self.H - 6
        kx = x1 - k - 3 if self.value else x0 + 3
        self.c.create_oval(kx, y0 + 3, kx + k, y0 + 3 + k, fill="white", outline="")

    def set(self, value):
        self.value = bool(value)
        self.draw()

    def toggle(self):
        self.set(not self.value)
        if self.command:
            self.command(self.value)


def panel(parent, **kw):
    return tk.Frame(parent, bg=C["panel"], highlightbackground=C["grid"], highlightthickness=1, **kw)


def heading(parent, text):
    return tk.Label(parent, text=text, bg=C["panel"], fg=C["accent"], font=FONT_B, anchor="w")


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
    """('idle'|'running'|'done'|'failed', start, end, reboot_needed, log_text)"""
    parts = read_file(UPDATE_STATE).split()
    st = parts[0] if parts else "idle"
    nums = [int(x) if x.isdigit() else 0 for x in (parts[1:4] + ["0", "0", "0"])[:3]]
    if st == "running" and nums[0] and time.time() - nums[0] > 3 * 3600:
        st = "failed"   # the Pi was restarted or the update was stopped part-way
    return st, nums[0], nums[1], nums[2] == 1, read_file(UPDATE_LOG)


def web_port():
    m = re.search(r"^Environment=RZ_PORT=(\d+)", read_file(WEB_UNIT), re.M)
    return int(m.group(1)) if m else 8090


def local_ips():
    """This Pi's IPv4 addresses on its networks (hotspot included), without the Tailscale one."""
    ips = []
    for name, addrs in psutil.net_if_addrs().items():
        if name == "lo" or name.startswith("tailscale"):
            continue
        ips += [a.address for a in addrs if a.family == socket.AF_INET and not a.address.startswith("127.")]
    return ips


def set_web_password(pw):
    """Saves the web page's control password (scrambled) using the web server's own code."""
    try:
        p = subprocess.run(["python3", WEB_SERVER, "--set-password"], input=pw + "\n", capture_output=True,
                           text=True, timeout=20)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:  # noqa
        return 1, str(e)


def load_ap_settings():
    """Pick up a changed hotspot name/password (the helper keeps them in /etc/rpi400-taskmgr.conf)."""
    global AP_SSID, AP_PASS
    conf = read_file(LED_CONF)
    vals = {k: (re.findall(rf"^{k}=(.*)$", conf, re.M) or [""])[-1] for k in ("AP_SSID", "AP_PASS")}
    AP_SSID = vals["AP_SSID"] or AP_SSID_DEFAULT
    AP_PASS = vals["AP_PASS"] or AP_PASS_DEFAULT


def ap_keeps_mobile_data():
    return not re.search(r"^AP_SHARE=1", read_file(LED_CONF), re.M)


def wifi_qr_text(ssid, password):
    esc = lambda v: re.sub(r'([\\;,:"])', r"\\\1", v)
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"


def ap_text_problem(ssid, password):
    """Same rules as the helper. Returns a message, or '' when both are fine."""
    if not (1 <= len(ssid) <= 32) or ssid != ssid.strip() or not re.fullmatch(r"[ -~]+", ssid):
        return "The network name must be 1 to 32 letters, numbers or symbols, with no spaces at the start or end."
    if not (8 <= len(password) <= 63) or not re.fullmatch(r"[ -~]+", password):
        return "The password must be 8 to 63 letters, numbers or symbols."
    return ""


def kv_row(parent, row, key):
    tk.Label(parent, text=key, bg=C["panel"], fg=C["dim"], font=FONT_S, anchor="w").grid(
        row=row, column=0, sticky="w", padx=(10, 6), pady=1)
    v = tk.Label(parent, text="—", bg=C["panel"], fg=C["fg"], font=FONT, anchor="w")
    v.grid(row=row, column=1, sticky="w", pady=1)
    return v


def draw_qr(canvas, text, size):
    canvas.delete("all")
    if not qrcode:
        canvas.create_text(size / 2, size / 2, text="Install python3-qrcode\nfor a join QR code",
                           fill=C["dim"], font=FONT_S, justify="center")
        return
    q = qrcode.QRCode(border=2)
    q.add_data(text)
    q.make(fit=True)
    m = q.get_matrix()
    cell = size / len(m)
    canvas.create_rectangle(0, 0, size, size, fill="white", outline="")
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                canvas.create_rectangle(x * cell, y * cell, (x + 1) * cell, (y + 1) * cell,
                                        fill="black", outline="")


# ---------------------------------------------------------------- Bluetooth pairing window
class BtPairDialog(tk.Toplevel):
    """Keyboard-only Bluetooth window. Opened from the Tools tab, or on its own with
    Ctrl+Alt+B (rpi400-taskmgr --pair), so it works even when the mouse is gone."""

    def __init__(self, parent, app=None):
        super().__init__(parent)
        self.app = app
        self.q = queue.Queue()
        self.busy = False
        self.devices = []
        self.title("Bluetooth — pair a device")
        self.configure(bg=C["panel"])
        self.geometry("660x520")
        self.minsize(600, 460)
        if app is not None:
            self.transient(app)

        top = tk.Frame(self, bg=C["panel"])
        top.pack(fill="x", padx=14, pady=(14, 4))
        tk.Label(top, text="Bluetooth", bg=C["panel"], fg=C["fg"], font=(FAM, 15, "bold")).pack(side="left")
        self.light = Light(top, "Turning Bluetooth on…")
        self.light.pack(side="left", padx=14)
        tk.Label(self, text="1. Put the mouse or keyboard into pairing mode.\n"
                            "2. Use ↑ ↓ to choose it, then press Enter.",
                 bg=C["panel"], fg=C["fg"], font=(FAM, 11), justify="left").pack(anchor="w", padx=14, pady=(2, 6))

        self.lb = tk.Listbox(self, bg=C["panel2"], fg=C["fg"], selectbackground=C["accent"],
                             selectforeground="#000", font=(FAM, 12), activestyle="none", height=8,
                             highlightthickness=2, highlightcolor=C["accent"], relief="flat")
        self.lb.pack(fill="both", expand=True, padx=14)

        self.code = tk.Label(self, text="", bg=C["panel"], fg=C["amber"], font=(FAM, 26, "bold"))
        self.code.pack(pady=(6, 0))
        self.status = tk.Label(self, text="", bg=C["panel"], fg=C["dim"], font=(FAM, 10), anchor="w",
                               justify="left", wraplength=560)
        self.status.pack(fill="x", padx=14, pady=(4, 6))

        keys = tk.Frame(self, bg=C["panel"])
        keys.pack(fill="x", padx=14, pady=(0, 14))
        for text, cmd, style in (("Enter  Pair", self.pair, "Accent.TButton"), ("S  Scan again", self.scan, None),
                                 ("R  Reconnect my devices", self.rescue, None), ("Esc  Close", self.close, None)):
            b = ttk.Button(keys, text=text, command=cmd, **({"style": style} if style else {}))
            b.pack(side="left", padx=(0, 6))

        for key in ("<Return>", "<KP_Enter>"):
            self.bind(key, lambda e: self.pair())
        self.bind("<Escape>", lambda e: self.close())
        for key in ("s", "S"):
            self.bind(f"<Key-{key}>", lambda e: self.scan())
        for key in ("r", "R"):
            self.bind(f"<Key-{key}>", lambda e: self.rescue())
        self.bind("<Up>", lambda e: self._move(-1))
        self.bind("<Down>", lambda e: self._move(1))
        self.protocol("WM_DELETE_WINDOW", self.close)

        # grab the keyboard focus: there may be no mouse to click the window with
        self.lift()
        self.attributes("-topmost", True)
        self.after(1500, lambda: self.winfo_exists() and self.attributes("-topmost", False))
        self.after(150, self._focus)
        self.after(100, self._drain)
        self.after(300, self.scan)

    # -- helpers
    def _focus(self):
        try:
            self.focus_force()
            self.lb.focus_set()
        except tk.TclError:
            pass

    def _move(self, step):
        if self.focus_get() is self.lb or not self.devices:
            return
        cur = self.lb.curselection()
        i = max(0, min(len(self.devices) - 1, (cur[0] if cur else -1) + step))
        self.lb.selection_clear(0, "end")
        self.lb.selection_set(i)
        self.lb.activate(i)
        self.lb.see(i)
        return "break"

    def _say(self, text, color=None):
        self.status.config(text=text, fg=color or C["dim"])

    def _log(self, text, ok=None):
        if self.app is not None:
            self.app.log(text, ok=ok)

    def _work(self, fn):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=lambda: (fn(), self.q.put(("done", None))), daemon=True).start()

    def _drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "devices":
                    self._fill(*val)
                elif kind == "say":
                    self._say(*val)
                elif kind == "light":
                    self.light.set(*val)
                elif kind == "passkey":
                    self.code.config(text=val)
                    self._say("Type this code on the NEW keyboard, then press Enter on it.", C["amber"])
                elif kind == "confirm":
                    self.code.config(text=val)
                    self._say("Code confirmed automatically. If the device shows the same number, you're set.")
                elif kind == "info":
                    self._say(val)
                elif kind == "done":
                    self.busy = False
        except queue.Empty:
            pass
        if self.winfo_exists():
            self._drain_id = self.after(100, self._drain)

    # -- actions
    def scan(self):
        def work():
            self.q.put(("say", ("Turning Bluetooth on…",)))
            rc, _ = run_helper("bt-on", timeout=30)
            if rc != 0:
                run(["bluetoothctl", "power", "on"], 5)
            _, show = run(["bluetoothctl", "show"], 5)
            if "Powered: yes" not in show:
                self.q.put(("light", (C["red"], "Bluetooth is off")))
                self.q.put(("say", ("Couldn't turn Bluetooth on. Press R to restart it.", C["red"])))
                return
            self.q.put(("light", (C["green"], "Bluetooth is on")))
            self.q.put(("say", ("Scanning for 10 seconds… keep the device in pairing mode.",)))
            run(["bluetoothctl", "--timeout", "10", "scan", "on"], 20)
            _, out = run(["bluetoothctl", "devices"], 5)
            _, pout = run(["bluetoothctl", "devices", "Paired"], 5)
            if "Device" not in pout:
                _, pout = run(["bluetoothctl", "paired-devices"], 5)
            paired = {ln.split()[1].upper() for ln in pout.splitlines() if ln.startswith("Device ")}
            devs = []
            for ln in out.splitlines():
                parts = ln.split(" ", 2)
                if len(parts) == 3 and parts[0] == "Device":
                    mac, name = parts[1].upper(), parts[2]
                    named = name.replace("-", ":").upper() != mac
                    devs.append((mac in paired, not named, name.lower(), mac, name))
            devs.sort()
            self.q.put(("devices", ([(m, n, m in paired) for _, _, _, m, n in devs],)))
        self.code.config(text="")
        self._work(work)

    def _fill(self, devs):
        sel = self.lb.curselection()
        self.devices = devs
        self.lb.delete(0, "end")
        for mac, name, paired in devs:
            self.lb.insert("end", f"{name}   ({mac}){'   · paired' if paired else ''}")
        if devs:
            i = sel[0] if sel and sel[0] < len(devs) else 0
            self.lb.selection_set(i)
            self.lb.activate(i)
            self._say(f"Found {len(devs)} device{'s' if len(devs) != 1 else ''}. "
                      "Choose one with ↑ ↓ and press Enter. S scans again.")
        else:
            self._say("Nothing found yet. Check the device is in pairing mode, then press S to scan again.", C["amber"])
        self._focus()

    def pair(self):
        sel = self.lb.curselection()
        if not sel or self.busy:
            return
        mac, name, _ = self.devices[sel[0]]
        self.code.config(text="")
        self._say(f"Pairing with {name}… this can take up to a minute.", C["fg"])

        def work():
            rc, msg = bt_pair(mac, notify=lambda kind, text: self.q.put((kind, text)))
            self.q.put(("say", (f"{name}: {msg}", C["green"] if rc == 0 else C["red"])))
            self._log(("✔ Paired " if rc == 0 else "✖ Couldn't pair ") + f"{name}\n{msg}", ok=rc == 0)
        self._work(work)

    def rescue(self):
        if self.busy:
            return
        self._say("Restarting Bluetooth and reconnecting your paired devices… (about 15 seconds)", C["fg"])

        def work():
            rc, out = run_helper("bt-rescue", timeout=90)
            self.q.put(("say", (out or "Done.", C["green"] if rc == 0 else C["red"])))
            self._log(("✔ " if rc == 0 else "✖ ") + "Bluetooth rescue" + (f"\n{out}" if out else ""), ok=rc == 0)
        self._work(work)

    def close(self):
        root = self.master if self.app is None else None
        try:
            self.after_cancel(self._drain_id)
        except (AttributeError, tk.TclError):
            pass
        self.destroy()
        if root is not None:
            root.destroy()


# ---------------------------------------------------------------- Tailscale sign-in
class UpdateWindow(tk.Toplevel):
    """Shows an update's progress live. Closing it doesn't stop the update."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Update everything")
        self.configure(bg=C["panel"])
        self.geometry("720x520")
        self.transient(app)
        tk.Label(self, text="⬆  Update everything", bg=C["panel"], fg=C["fg"], font=(FAM, 15, "bold")).pack(
            anchor="w", padx=16, pady=(16, 2))
        self.status = tk.Label(self, text="Starting…", bg=C["panel"], fg=C["amber"], font=FONT_B, anchor="w",
                               justify="left", wraplength=680)
        self.status.pack(fill="x", padx=16)
        self.text = tk.Text(self, bg=C["bg"], fg=C["fg"], font=("DejaVu Sans Mono", 9), relief="flat", wrap="word",
                            highlightthickness=0, padx=8, pady=6)
        self.text.pack(fill="both", expand=True, padx=16, pady=8)
        self.text.tag_configure("step", foreground=C["accent"], font=("DejaVu Sans Mono", 9, "bold"))
        row = tk.Frame(self, bg=C["panel"])
        row.pack(fill="x", padx=16, pady=(0, 14))
        self.reboot_btn = ttk.Button(row, text="🔄  Restart the Pi now", style="Accent.TButton",
                                     command=lambda: app.act_power("reboot"))
        tk.Label(row, text="Closing this window doesn't stop the update.", bg=C["panel"], fg=C["dim"],
                 font=FONT_S).pack(side="left")
        ttk.Button(row, text="Esc  Close", command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        self._last = None
        self.after(150, lambda: self.winfo_exists() and self.focus_force())
        self.after(300, self._poll)

    def _poll(self):
        if not self.winfo_exists():
            return
        st, t0, t1, rb, log = update_state()
        if log != self._last:
            self._last = log
            at_end = self.text.yview()[1] > 0.98
            self.text.config(state="normal")
            self.text.delete("1.0", "end")
            for line in log.splitlines()[-400:]:
                self.text.insert("end", line + "\n", "step" if line.startswith("== ") else ())
            self.text.config(state="disabled")
            if at_end or st != "running":
                self.text.see("end")
        steps = [l for l in log.splitlines() if l.startswith("== ") and "/6 " in l]
        if st == "running":
            now = steps[-1][3:] if steps else "Starting…"
            self.status.config(text=f"{now}   ·   {fmt_dur(time.time() - t0)} so far", fg=C["amber"])
        elif st == "done":
            self.status.config(text="✔ Everything is up to date." + (" Restart the Pi to finish." if rb else ""),
                               fg=C["green"])
            if rb and not self.reboot_btn.winfo_ismapped():
                self.reboot_btn.pack(side="right", padx=(0, 8))
        elif st == "failed":
            last = [l for l in log.splitlines() if l.startswith("== ")]
            self.status.config(text="✖ " + (last[-1][3:] if last else "The update didn't finish."), fg=C["red"])
        self.after(1500, self._poll)


class WebPasswordDialog(tk.Toplevel):
    """Sets the web page's control password. Anyone on the network can look; changing things needs this."""

    def __init__(self, app, then_turn_on=False):
        super().__init__(app)
        self.app, self.then_turn_on = app, then_turn_on
        self.title("Web page password")
        self.configure(bg=C["panel"])
        self.transient(app)
        self.resizable(False, False)
        tk.Label(self, text="🔑  Web page password", bg=C["panel"], fg=C["fg"], font=(FAM, 15, "bold")).pack(
            anchor="w", padx=16, pady=(16, 4))
        tk.Label(self, text="Anyone on your network can open the web page and look. Starting the hotspot, ending "
                            "programs and the other controls need this password. Use at least 6 characters, and not "
                            "the hotspot password, since everyone on the hotspot knows that one.",
                 bg=C["panel"], fg=C["fg"], font=FONT_S, wraplength=440, justify="left").pack(anchor="w", padx=16)
        form = tk.Frame(self, bg=C["panel"])
        form.pack(fill="x", padx=16, pady=(8, 0))
        self.pw1, self.pw2 = tk.StringVar(), tk.StringVar()
        self.entries = []
        for r, (label, var) in enumerate((("Password", self.pw1), ("Type it again", self.pw2))):
            tk.Label(form, text=label, bg=C["panel"], fg=C["fg"], font=FONT_B, anchor="w").grid(
                row=r, column=0, sticky="w", pady=4)
            e = ttk.Entry(form, textvariable=var, width=28, font=FONT, show="•")
            e.grid(row=r, column=1, sticky="we", padx=(10, 0), pady=4)
            self.entries.append(e)
        self.show = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="Show", variable=self.show,
                        command=lambda: [e.config(show="" if self.show.get() else "•") for e in self.entries]).grid(
            row=0, column=2, padx=(8, 0))
        self.msg = tk.Label(self, text="", bg=C["panel"], fg=C["red"], font=FONT_S, wraplength=440, justify="left",
                            anchor="w")
        self.msg.pack(fill="x", padx=16, pady=(6, 0))
        row = tk.Frame(self, bg=C["panel"])
        row.pack(fill="x", padx=16, pady=(8, 16))
        ttk.Button(row, text="Enter  Save" + (" & turn on" if then_turn_on else ""), style="Accent.TButton",
                   command=self.save).pack(side="left")
        ttk.Button(row, text="Esc  Cancel", command=self.destroy).pack(side="right")
        self.bind("<Return>", lambda e: self.save())
        self.bind("<KP_Enter>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(150, lambda: self.winfo_exists() and (self.focus_force(), self.entries[0].focus_set()))

    def save(self):
        a, b = self.pw1.get(), self.pw2.get()
        if len(a) < 6:
            self.msg.config(text="Please use at least 6 characters.")
            return
        if a != b:
            self.msg.config(text="Those two don't match. Type them again.")
            return
        if a == AP_PASS:
            self.msg.config(text="Please pick something different from the hotspot password.")
            return
        rc, out = set_web_password(a)
        if rc != 0:
            self.msg.config(text="Couldn't save it: " + (out.splitlines()[-1] if out else "unknown error"))
            return
        self.app.log("Web page password saved.", ok=True)
        turn_on = self.then_turn_on
        self.destroy()
        if turn_on:
            self.app.web_switch.set(True)
            self.app.act_web(True)


class ApSettingsDialog(tk.Toplevel):
    """Change the hotspot's Wi-Fi name and password. Works from the keyboard alone."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Hotspot name & password")
        self.configure(bg=C["panel"])
        self.transient(app)
        self.resizable(False, False)
        tk.Label(self, text="📶  Hotspot name & password", bg=C["panel"], fg=C["fg"], font=(FAM, 15, "bold")).pack(
            anchor="w", padx=16, pady=(16, 6))
        form = tk.Frame(self, bg=C["panel"])
        form.pack(fill="x", padx=16)
        self.ssid = tk.StringVar(value=AP_SSID)
        self.pw = tk.StringVar(value=AP_PASS)
        for r, (label, var, hint) in enumerate((("Network name", self.ssid, "1 to 32 characters"),
                                                ("Password", self.pw, "8 to 63 characters"))):
            tk.Label(form, text=label, bg=C["panel"], fg=C["fg"], font=FONT_B, anchor="w").grid(
                row=r * 2, column=0, sticky="w", pady=(6, 0))
            e = ttk.Entry(form, textvariable=var, width=34, font=FONT)
            e.grid(row=r * 2, column=1, sticky="we", padx=(10, 0), pady=(6, 0))
            tk.Label(form, text=hint, bg=C["panel"], fg=C["dim"], font=FONT_S, anchor="w").grid(
                row=r * 2 + 1, column=1, sticky="w", padx=(10, 0))
            if r == 0:
                self.first = e
        self.msg = tk.Label(self, text="Everyone joining needs the new name and password. If the hotspot is on, "
                                       "it restarts and connected devices drop off until they join again.",
                            bg=C["panel"], fg=C["dim"], font=FONT_S, wraplength=440, justify="left", anchor="w")
        self.msg.pack(fill="x", padx=16, pady=(10, 4))
        row = tk.Frame(self, bg=C["panel"])
        row.pack(fill="x", padx=16, pady=(6, 16))
        ttk.Button(row, text="Enter  Save", style="Accent.TButton", command=self.save).pack(side="left")
        ttk.Button(row, text="Back to default", command=self.defaults).pack(side="left", padx=6)
        ttk.Button(row, text="Esc  Cancel", command=self.destroy).pack(side="right")
        self.bind("<Return>", lambda e: self.save())
        self.bind("<KP_Enter>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        for v in (self.ssid, self.pw):
            v.trace_add("write", lambda *a: self.msg.cget("fg") == C["red"] and self.msg.config(fg=C["dim"], text=""))
        self.after(150, self._focus)

    def _focus(self):
        if self.winfo_exists():
            self.focus_force()
            self.first.focus_set()
            self.first.select_range(0, "end")

    def defaults(self):
        self.ssid.set(AP_SSID_DEFAULT)
        self.pw.set(AP_PASS_DEFAULT)
        self.msg.config(text=f"Press Save to go back to {AP_SSID_DEFAULT} / {AP_PASS_DEFAULT}.", fg=C["dim"])

    def save(self):
        ssid, pw = self.ssid.get(), self.pw.get()
        problem = ap_text_problem(ssid, pw)
        if problem:
            self.msg.config(text=problem, fg=C["red"])
            return
        if ssid == AP_SSID and pw == AP_PASS:
            self.destroy()
            return
        self.destroy()
        self.app.apply_ap_settings(ssid, pw)


class VpnLoginDialog(tk.Toplevel):
    """One-time Tailscale sign-in: shows the link as a QR code for a phone and waits until the Pi is connected."""

    def __init__(self, app, url):
        super().__init__(app)
        self.app, self.url = app, url
        self.title("Sign in to Tailscale")
        self.configure(bg=C["panel"])
        self.geometry("520x600")
        self.transient(app)
        tk.Label(self, text="🌍  Sign in to Tailscale", bg=C["panel"], fg=C["fg"], font=(FAM, 15, "bold")).pack(
            anchor="w", padx=16, pady=(16, 4))
        tk.Label(self, text="One time only. Point your phone's camera at this code (or open the link on any device), "
                            "sign in, and press Connect. This Pi then joins your own private Tailscale network.",
                 bg=C["panel"], fg=C["fg"], font=FONT, wraplength=480, justify="left").pack(anchor="w", padx=16)
        self.qr = tk.Canvas(self, width=230, height=230, bg=C["panel"], highlightthickness=0)
        self.qr.pack(pady=12)
        draw_qr(self.qr, url, 230)
        link = ttk.Entry(self, font=FONT_S)
        link.insert(0, url)
        link.configure(state="readonly")
        link.pack(fill="x", padx=16)
        row = tk.Frame(self, bg=C["panel"])
        row.pack(fill="x", padx=16, pady=10)
        ttk.Button(row, text="Copy link", command=self.copy).pack(side="left")
        ttk.Button(row, text="Open on this Pi", command=lambda: webbrowser.open(url)).pack(side="left", padx=6)
        ttk.Button(row, text="Esc  Close", command=self.destroy).pack(side="right")
        self.status = tk.Label(self, text="Waiting for you to sign in…", bg=C["panel"], fg=C["amber"],
                               font=(FAM, 11, "bold"), wraplength=480, justify="left", anchor="w")
        self.status.pack(fill="x", padx=16)
        tk.Label(self, text="Tip: install the Tailscale app on your phone and PC and sign in with the same account. "
                            "They can then reach this Pi from anywhere.",
                 bg=C["panel"], fg=C["dim"], font=FONT_S, wraplength=480, justify="left").pack(anchor="w", padx=16, pady=(8, 12))
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(150, lambda: self.winfo_exists() and self.focus_force())
        self.after(2000, self._poll)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.url)
        self.status.config(text="Link copied.")

    def _poll(self):
        if not self.winfo_exists():
            return

        def work():
            v = vpn_status()
            self.after(0, lambda: self._show(v))
        threading.Thread(target=work, daemon=True).start()

    def _show(self, v):
        if not self.winfo_exists():
            return
        if v.get("state") == "Running":
            self.status.config(text=f"✔ Connected! This Pi is {v['ip']} on your Tailscale network.", fg=C["green"])
            self.app.log(f"✔ Tailscale on. This Pi is {v['ip']}", ok=True)
            self.app.slow.vpn_now = True
            self.after(4000, lambda: self.winfo_exists() and self.destroy())
        else:
            self.after(2000, self._poll)


# ---------------------------------------------------------------- stability test
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


class StabilityTest(tk.Toplevel):
    """Loads every core for a few minutes while checking the answers are right and watching heat,
    clock speed and the firmware's throttle flags. Ends with a plain verdict."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.procs = []
        self.running = False
        self.title("Stability test")
        self.configure(bg=C["panel"])
        self.geometry("620x560")
        self.minsize(560, 520)
        self.transient(app)

        tk.Label(self, text="🧪  Stability test", bg=C["panel"], fg=C["fg"], font=(FAM, 15, "bold")).pack(
            anchor="w", padx=14, pady=(14, 2))
        self.setting = tk.Label(self, text="", bg=C["panel"], fg=C["dim"], font=FONT, anchor="w", justify="left")
        self.setting.pack(fill="x", padx=14)
        tk.Label(self, text="Runs all 4 cores flat out, checks every answer is correct, and watches temperature "
                            "and throttling. Save your work first: if the speed is too high, the Pi may freeze.",
                 bg=C["panel"], fg=C["dim"], font=FONT_S, wraplength=580, justify="left").pack(fill="x", padx=14, pady=(4, 6))

        dur = tk.Frame(self, bg=C["panel"])
        dur.pack(fill="x", padx=14)
        tk.Label(dur, text="Length:", bg=C["panel"], fg=C["fg"], font=FONT).pack(side="left")
        self.minutes = tk.IntVar(value=3)
        for m in (1, 3, 10):
            ttk.Radiobutton(dur, text=f"{m} min", value=m, variable=self.minutes).pack(side="left", padx=6)

        self.graph = Graph(self, [("Temperature", C["amber"])], height=130, maxlen=120, fixed_max=90,
                           fmt=lambda v: f"{v:.0f}°C")
        self.graph.pack(fill="x", padx=14, pady=(8, 6))
        stats = tk.Frame(self, bg=C["panel"])
        stats.pack(fill="x", padx=14)
        self.v = {}
        for i, key in enumerate(("Temperature", "Hottest", "CPU clock", "Time left")):
            tk.Label(stats, text=key, bg=C["panel"], fg=C["dim"], font=FONT_S).grid(row=0, column=i, sticky="w", padx=(0, 26))
            self.v[key] = tk.Label(stats, text="—", bg=C["panel"], fg=C["fg"], font=(FAM, 14, "bold"))
            self.v[key].grid(row=1, column=i, sticky="w", padx=(0, 26))
        lights = tk.Frame(self, bg=C["panel"])
        lights.pack(fill="x", padx=12, pady=(8, 0))
        self.l_power, self.l_throttle, self.l_hot, self.l_errors = (Light(lights, n) for n in
                                                                    ("Power", "Throttled", "Hot", "Wrong answers"))
        for w in (self.l_power, self.l_throttle, self.l_hot, self.l_errors):
            w.pack(side="left")
        self.bar = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.bar.pack(fill="x", padx=14, pady=(10, 4))
        self.verdict = tk.Label(self, text="Press Enter to start.", bg=C["panel"], fg=C["fg"], font=(FAM, 11, "bold"),
                                wraplength=580, justify="left", anchor="w")
        self.verdict.pack(fill="x", padx=14, pady=(4, 6))
        btns = tk.Frame(self, bg=C["panel"])
        btns.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(btns, text="Enter  Start", style="Accent.TButton", command=self.start).pack(side="left")
        ttk.Button(btns, text="Esc  Stop / Close", command=self.stop_or_close).pack(side="left", padx=6)
        self.bind("<Return>", lambda e: self.start())
        self.bind("<Escape>", lambda e: self.stop_or_close())
        self.protocol("WM_DELETE_WINDOW", self.stop_or_close)
        self._show_setting()
        self.after(150, lambda: self.winfo_exists() and self.focus_force())

    def _show_setting(self):
        oc = read_oc()
        self.speed_text = oc.replace(" (overclocked)", "")
        self.setting.config(text=f"Testing the current setting: {oc}"
                                 + (" · graphics boost on" if IS_PI5 and read_gpu_boost() else ""))

    def start(self):
        if self.running:
            return
        self.secs = self.minutes.get() * 60
        self.t0 = time.time()
        self.max_t = 0.0
        self.seen = 0
        self.errors = False
        self.running = True
        for w in (self.l_power, self.l_throttle, self.l_hot, self.l_errors):
            w.set(C["green"])
        try:
            os.makedirs(os.path.dirname(STABILITY_MARK), exist_ok=True)
            with open(STABILITY_MARK, "w") as f:   # if the Pi freezes, the app finds this after the restart
                json.dump({"speed": self.speed_text, "started": self.t0, "pid": os.getpid(),
                           "boot": psutil.boot_time()}, f)
        except OSError:
            pass
        n = psutil.cpu_count() or 4
        self.procs = [subprocess.Popen([sys.executable, "-c", STRESS_WORKER, str(self.secs)],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) for _ in range(n)]
        self.verdict.config(text=f"Running on {n} cores… keep an eye on the temperature.", fg=C["fg"])
        self.app.log(f"▶ Stability test started ({self.minutes.get()} min at {self.speed_text})")
        self._tick()

    def _tick(self):
        if not self.running or not self.winfo_exists():
            return
        el = time.time() - self.t0
        t = cpu_temp() or 0.0
        self.max_t = max(self.max_t, t)
        self.graph.push(t)
        fr = psutil.cpu_freq()
        th = self.app.slow.get().get("throttled") or 0
        self.seen |= th & 0xF
        self.v["Temperature"].config(text=f"{t:.0f}°C", fg=C["red"] if t >= 80 else C["amber"] if t >= 70 else C["fg"])
        self.v["Hottest"].config(text=f"{self.max_t:.0f}°C")
        self.v["CPU clock"].config(text=f"{fr.current:.0f} MHz" if fr else "—")
        self.v["Time left"].config(text=fmt_dur(max(0, self.secs - el)))
        self.bar.config(value=min(100, el * 100 / self.secs))
        self.l_power.set(C["red"] if self.seen & 0x1 else C["green"])
        self.l_throttle.set(C["amber"] if self.seen & 0x6 else C["green"])
        self.l_hot.set(C["amber"] if self.seen & 0x8 else C["green"])
        done = [p for p in self.procs if p.poll() is not None]
        for p in done:
            if p.returncode != 0:
                self.errors = True
                self.l_errors.set(C["red"])
        if self.errors or len(done) == len(self.procs):
            self._finish(stopped=False)
        else:
            self.after(1000, self._tick)

    def _kill(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        self.procs = []

    def _finish(self, stopped):
        self.running = False
        self._kill()
        try:
            os.remove(STABILITY_MARK)
        except OSError:
            pass
        self.bar.config(value=0 if stopped else 100)
        hot = f"Hottest {self.max_t:.0f}°C."
        if stopped:
            text, color, ok = "Stopped. No verdict.", C["dim"], None
        elif self.errors:
            text, color, ok = (f"✖ FAILED: the CPU got wrong answers at {self.speed_text}. This speed isn't stable on "
                               "your board. Choose a lower speed and reboot."), C["red"], False
        elif self.seen & 0x1:
            text, color, ok = (f"⚠ POWER PROBLEM: the supply dipped under load. {hot} Use the 27 W supply or a stronger "
                               "battery bank before trusting this speed."), C["amber"], False
        elif self.seen & 0xE:
            text, color, ok = (f"⚠ STABLE, BUT HOT: no errors, but it got hot enough to slow itself down. {hot} Short "
                               "bursts get the full speed, long heavy jobs won't."), C["amber"], True
        else:
            text, color, ok = f"✔ PASSED at {self.speed_text}. No errors, no throttling. {hot}", C["green"], True
        self.verdict.config(text=text, fg=color)
        if ok is not None:
            self.app.log(("✔ " if ok else "✖ ") + "Stability test: " + text.lstrip("✔✖⚠ "), ok=ok)

    def stop_or_close(self):
        if self.running:
            self._finish(stopped=True)
        else:
            self._kill()
            self.destroy()


# ---------------------------------------------------------------- Who's Connected window
class PeopleWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(f"Who's connected — {AP_SSID}")
        self.configure(bg=C["bg"])
        self.geometry("1020x660")
        self.minsize(760, 480)

        top = tk.Frame(self, bg=C["bg"])
        top.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(top, text=f"👥  Who's connected to {AP_SSID}", bg=C["bg"], fg=C["fg"], font=FONT_H).pack(side="left")
        self.state = Light(top, "", C["bg"])
        self.state.pack(side="left", padx=14)

        now = panel(self)
        now.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        hdr = tk.Frame(now, bg=C["panel"])
        hdr.pack(fill="x", padx=10, pady=(8, 4))
        self.now_title = heading(hdr, "Connected right now")
        self.now_title.pack(side="left")
        ttk.Button(hdr, text="Disconnect selected (Del)", style="Danger.TButton",
                   command=self.kick).pack(side="right")
        cols = [("name", "Device name", 190, "w"), ("ip", "IP address", 120, "w"), ("mac", "MAC address", 150, "w"),
                ("signal", "Signal", 150, "w"), ("since", "Joined at", 90, "w"), ("dur", "Connected for", 120, "e"),
                ("data", "Data used", 100, "e")]
        self.now_tree = self._tree(now, cols, 7)

        hist = panel(self)
        hist.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        hdr2 = tk.Frame(hist, bg=C["panel"])
        hdr2.pack(fill="x", padx=10, pady=(8, 4))
        heading(hdr2, "Visitor log").pack(side="left")
        ttk.Button(hdr2, text="📂 Open log", command=self.open_log).pack(side="right")
        ttk.Button(hdr2, text="💾 Save report to Desktop (Ctrl+S)", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=6)
        self.auto = ToggleSwitch(hdr2, "Auto-save", app.visitors.autosave, command=self.set_auto)
        self.auto.pack(side="right", padx=(0, 10))
        cols2 = [("when", "When", 160, "w"), ("event", "Event", 150, "w"), ("name", "Device name", 150, "w"),
                 ("ip", "IP address", 115, "w"), ("mac", "MAC address", 145, "w"), ("stayed", "Stayed", 75, "e"),
                 ("data", "Data", 85, "e")]
        self.hist_tree = self._tree(hist, cols2, 8)
        self.hist_tree.tag_configure("Joined", foreground=C["green"])
        self.hist_tree.tag_configure("Left", foreground=C["amber"])
        self.hist_tree.tag_configure("sys", foreground=C["accent"])

        self.foot = tk.Label(self, text="", bg=C["panel"], fg=C["dim"], font=FONT_S, anchor="w", padx=10, pady=4)
        self.foot.pack(fill="x", side="bottom")

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Delete>", lambda e: self.kick())
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-a>", lambda e: self.auto.toggle())
        self._hist_len = -1
        self.now_tree.focus_set()
        self.refresh()

    def _tree(self, parent, cols, height):
        wrap = tk.Frame(parent, bg=C["panel"])
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        t = ttk.Treeview(wrap, columns=[c[0] for c in cols], show="headings", height=height, selectmode="browse")
        for key, title, width, anchor in cols:
            t.heading(key, text=title)
            stretch = key in ("name", "event")
            t.column(key, width=width, minwidth=60 if stretch else width, anchor=anchor, stretch=stretch)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return t

    def refresh(self):
        if not self.winfo_exists():
            return
        v = self.app.visitors
        now = time.time()
        sd = self.app.slow.get()
        if sd.get("ap_on"):
            self.state.set(C["green"], f"Hotspot ON  ·  password {AP_PASS}  ·  Pi at {AP_IP}")
        else:
            self.state.set(C["off"], "Hotspot is off — press Ctrl+H in the Task Manager to start it")
        self.now_title.config(text=f"Connected right now  ({len(v.active)})")

        rows = sorted(v.active.values(), key=lambda a: a["first"])
        want = [a["mac"] for a in rows]
        if list(self.now_tree.get_children()) != want:
            sel = self.now_tree.selection()
            self.now_tree.delete(*self.now_tree.get_children())
            for mac in want:
                self.now_tree.insert("", "end", iid=mac)
            if sel and self.now_tree.exists(sel[0]):
                self.now_tree.selection_set(sel[0])
        for a in rows:
            sig = a.get("signal")
            self.now_tree.item(a["mac"], values=(
                a.get("name") or "(no name)", a.get("ip") or "…", a["mac"],
                f"{sig} dBm  {signal_words(sig)}" if sig is not None else "—",
                time.strftime("%I:%M %p", time.localtime(a["first"])).lstrip("0"),
                fmt_dur(now - a["first"]), fmt_bytes(a.get("rx", 0) + a.get("tx", 0))))

        if len(v.history) != self._hist_len or (v.history and self.hist_tree.get_children() == ()):
            self._hist_len = len(v.history)
            self.hist_tree.delete(*self.hist_tree.get_children())
            for r in reversed(v.history):
                ev = r.get("Event", "")
                self.hist_tree.insert("", "end", values=(
                    f"{r.get('Date', '')} {r.get('Time', '')}", ev, r.get("Device name", ""), r.get("IP address", ""),
                    r.get("MAC address", ""), r.get("Stayed", ""), r.get("Data used", "")),
                    tags=(ev if ev in ("Joined", "Left") else "sys",))
        short = lambda p: p.replace(os.path.expanduser("~"), "~", 1)
        if v.error:
            text, color = v.error, C["red"]
        elif v.autosave:
            text, color = (f"Auto-save ON — saving to your Desktop as people come and go  "
                           f"({VISITOR_LOG}  +  {LIVE_REPORT})"), C["green"]
        else:
            n = len(v.unsaved)
            text = (f"Auto-save OFF — {n} event{'s' if n != 1 else ''} not saved yet. Press Save (Ctrl+S) "
                    "or turn Auto-save on to write them to the Desktop.") if n else \
                   "Auto-save OFF — nothing is written to the Desktop until you press Save (Ctrl+S)."
            color = C["amber"] if n else C["dim"]
        self.foot.config(text=text, fg=color)
        if self.auto.value != v.autosave:
            self.auto.set(v.autosave)
        self.after(1000, self.refresh)

    def set_auto(self, on):
        self.app.visitors.set_autosave(on)
        self.app.log("Visitor auto-save turned " + ("ON — saving to the Desktop as things happen" if on
                                                    else "OFF — save by hand with Ctrl+S"), ok=True)

    def kick(self):
        sel = self.now_tree.selection()
        if not sel:
            return
        mac = sel[0]
        a = self.app.visitors.active.get(mac, {})
        who = a.get("name") or a.get("ip") or mac
        if messagebox.askyesno(APP_NAME, f"Disconnect {who}?\n\nThey can rejoin if they still have the password.",
                               parent=self):
            self.app.run_action(f"Disconnect {who}", lambda: run_helper("kick", mac))

    def save(self):
        try:
            path = self.app.visitors.save_report()
            self.app.log(f"Saved visitor report to {path}", ok=True)
            messagebox.showinfo(APP_NAME, f"Saved to your Desktop:\n\n{os.path.basename(path)}", parent=self)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Couldn't save the report:\n{e}", parent=self)

    def open_log(self):
        path = self.app.visitors.path
        if not os.path.exists(path):
            messagebox.showinfo(APP_NAME, "Nothing saved yet. The log file appears on the Desktop once someone joins "
                                "(with Auto-save on) or when you press Save.", parent=self)
            return
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply_style(root):
    """Dark theme for ttk widgets (shared by the main window and the Bluetooth window)."""
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=C["bg"], foreground=C["fg"], fieldbackground=C["panel2"], font=FONT,
                bordercolor=C["grid"], lightcolor=C["panel2"], darkcolor=C["panel2"])
    s.configure("TFrame", background=C["bg"])
    s.configure("TNotebook", background=C["bg"], borderwidth=0, tabmargins=(0, 4, 0, 0))
    s.configure("TNotebook.Tab", background=C["panel"], foreground=C["dim"], padding=(14, 6), font=FONT_B)
    s.map("TNotebook.Tab", background=[("selected", C["panel2"])], foreground=[("selected", C["fg"])])
    s.configure("Treeview", background=C["panel"], fieldbackground=C["panel"], foreground=C["fg"],
                rowheight=22, borderwidth=0)
    s.configure("Treeview.Heading", background=C["panel2"], foreground=C["fg"], relief="flat", font=FONT_B)
    s.map("Treeview.Heading", background=[("active", "#34404d")])
    s.map("Treeview", background=[("selected", C["accent"])], foreground=[("selected", "#000")])
    s.configure("TButton", background=C["panel2"], foreground=C["fg"], padding=(10, 6))
    s.map("TButton", background=[("active", "#34404d"), ("focus", "#2f3a46")])
    s.configure("Accent.TButton", background="#1d5a88")
    s.map("Accent.TButton", background=[("active", "#2a78b3"), ("focus", "#236ca0")])
    s.configure("Danger.TButton", background="#7a2630")
    s.map("Danger.TButton", background=[("active", "#9a3140"), ("focus", "#8a2c38")])
    for w in ("TCheckbutton", "TRadiobutton"):
        s.configure(w, background=C["panel"], foreground=C["fg"])
        s.configure(w, indicatorbackground=C["panel2"], indicatorforeground=C["accent"])   # dot / tick colour
        s.map(w, background=[("active", C["panel"])], indicatorbackground=[("active", C["panel2"])],
              indicatorforeground=[("selected", C["accent"])])
    s.configure("TEntry", fieldbackground=C["panel2"], foreground=C["fg"], insertcolor=C["fg"])
    s.configure("TCombobox", fieldbackground=C["panel2"], foreground=C["fg"], background=C["panel2"],
                arrowcolor=C["fg"])
    s.map("TCombobox", fieldbackground=[("readonly", C["panel2"])], foreground=[("readonly", C["fg"])],
          selectbackground=[("readonly", C["panel2"])], selectforeground=[("readonly", C["fg"])])
    root.option_add("*TCombobox*Listbox.background", C["panel2"])
    root.option_add("*TCombobox*Listbox.foreground", C["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    s.configure("Vertical.TScrollbar", background=C["panel2"], troughcolor=C["panel"], arrowcolor=C["dim"])


# ---------------------------------------------------------------- main window
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        load_ap_settings()
        self.title(APP_NAME)
        self.geometry("1200x760" if self.winfo_screenwidth() >= 1300 else "1100x760")
        self.minsize(920, 640)
        self.configure(bg=C["bg"])
        self._style()

        self.slow = SlowPoller()
        self.slow.start()
        self.ncpu = psutil.cpu_count() or 1
        psutil.cpu_percent()
        psutil.cpu_percent(percpu=True)
        self.prev_net = psutil.net_io_counters(pernic=True)
        self.prev_net_t = time.time()
        self.fast_prev = dict(self.prev_net)
        self.prev_disk = psutil.disk_io_counters()
        self.header_lights = {}
        self.card_lights = {}
        self.cards = {}
        self.sort_col, self.sort_rev = "cpu", True
        self._gov_touched = 0.0
        self.visitors = VisitorLog()
        self.people_win = None
        self.ai_q = queue.Queue()
        self.ai_messages = []
        self.ai_busy = None          # "chat", "pull" or "switch" while something is running
        self.ai_cancel = False
        self.ai_resp = None
        self._ai_models_seen = None

        self.status_var = tk.StringVar(
            value="F1–F5 tabs · Ctrl+P who's connected · Ctrl+M clean memory · Ctrl+B Bluetooth rescue · "
                  "Ctrl+H hotspot · Ctrl+U update · Ctrl+Q quit")

        self._build_header()
        bar = tk.Frame(self, bg=C["panel"])
        bar.pack(fill="x", side="bottom")   # reserve the status bar first
        bt_hint = tk.Label(bar, text="ᛒ  Ctrl+Alt+B  Bluetooth tool", bg=C["panel2"], fg=C["accent"],
                           font=FONT_B, padx=10, pady=4, cursor="hand2")
        bt_hint.pack(side="right")          # always visible, never replaced by status messages
        bt_hint.bind("<Button-1>", lambda e: self.act_bt_pair())
        tk.Label(bar, textvariable=self.status_var, bg=C["panel"], fg=C["dim"], font=FONT_S,
                 anchor="w", padx=10, pady=4).pack(side="left", fill="x", expand=True)
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        tabs = [("  Overview  ", self._build_overview), ("  Processes  ", self._build_processes),
                ("  Network  ", self._build_network), ("  Tools  ", self._build_tools), ("  AI  ", self._build_ai)]
        for title, builder in tabs:
            f = ttk.Frame(self.nb)
            self.nb.add(f, text=title)
            builder(f)
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._on_tab())
        self._bind_keys()

        self.after(300, self.tick)
        self.after(2500, self._check_unfinished_test)
        self.after(200, self.fast_tick)
        self.after(800, self.proc_tick)

    # ---------- styling
    def _style(self):
        apply_style(self)


    # ---------- header with always-visible link lights
    def _build_header(self):
        h = tk.Frame(self, bg=C["bg"])
        h.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(h, text="▣ " + APP_NAME, bg=C["bg"], fg=C["fg"], font=FONT_H).pack(side="left")
        tk.Label(h, text="👤 " + current_user(), bg=C["panel2"], fg=C["fg"], font=FONT_B, padx=8, pady=2).pack(
            side="left", padx=(12, 0))
        right = tk.Frame(h, bg=C["bg"])
        right.pack(side="right")   # packed before the IP text so the lights always get their room
        self.hdr_ip = tk.Label(h, text="", bg=C["bg"], fg=C["dim"], font=FONT, anchor="w")
        self.hdr_ip.pack(side="left", padx=(12, 6), fill="x", expand=True)
        self.hdr_people = tk.Label(right, text="👥 0", bg=C["panel2"], fg=C["fg"], font=FONT_B,
                                   padx=8, pady=2, cursor="hand2")
        self.hdr_people.pack(side="left", padx=(0, 10))
        self.hdr_people.bind("<Button-1>", lambda e: self.open_people())
        stats = psutil.net_if_stats()
        for nic in ("eth0", "wlan0"):
            if nic in stats:
                w = NicLights(right, nic, C["bg"])
                w.pack(side="left", padx=(0, 4))
                self.header_lights[nic] = w
        self.hdr_bt = Light(right, "Bluetooth", C["bg"])
        self.hdr_bt.pack(side="left")

    # ---------- Overview
    def _build_overview(self, f):
        g = panel(f)
        g.pack(fill="x", padx=6, pady=(8, 6))
        gs = 124 if IS_PI5 else 140     # Pi 5-class boards get two extra gauges
        self.g_cpu = Gauge(g, "CPU", size=gs)
        self.g_clk = Gauge(g, "CPU clock", unit="", vmax=2200, colored=False, size=gs)
        self.g_temp = Gauge(g, "Temperature", unit="°C", vmax=85, warn=65, crit=80, size=gs)
        self.g_ram = Gauge(g, "Memory", warn=75, crit=90, size=gs)
        self.g_swap = Gauge(g, "Swap", warn=50, crit=80, size=gs)
        self.g_disk = Gauge(g, "Disk  /", warn=80, crit=92, size=gs)
        gauges = [self.g_cpu, self.g_clk, self.g_temp, self.g_ram, self.g_swap, self.g_disk]
        if IS_PI5:
            self.g_vin = Gauge(g, "Input voltage", unit="V", vmin=4.5, vmax=5.3, low=(4.95, 4.8), size=gs)
            self.g_pwr = Gauge(g, "Power draw", unit="W", vmax=25, warn=15, crit=21, size=gs)
            gauges += [self.g_vin, self.g_pwr]
        for i, w in enumerate(gauges):
            w.grid(row=0, column=i, padx=2, pady=6)
            g.columnconfigure(i, weight=1)

        mid = tk.Frame(f, bg=C["bg"])
        mid.pack(fill="both", expand=True, padx=6)
        mid.columnconfigure(0, weight=1)

        left = panel(mid)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        heading(left, "CPU usage — last 60 seconds").pack(fill="x", padx=10, pady=(8, 4))
        self.cpu_graph = Graph(left, [("CPU", C["accent"])], fixed_max=100)
        self.cpu_graph.pack(fill="x", padx=10)
        self.core_bars = CoreBars(left, self.ncpu)
        self.core_bars.pack(fill="x", padx=10, pady=8)

        pw = panel(mid, width=340)
        pw.grid(row=0, column=1, sticky="nsew", pady=(0, 6))
        heading(pw, "Power").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 4))
        r = 1
        if IS_PI5:
            self.v_psu = kv_row(pw, r, "Power supply"); r += 1
        self.v_vcore = kv_row(pw, r, "Core voltage"); r += 1
        self.v_arm = kv_row(pw, r, "ARM clock (firmware)"); r += 1
        self.v_gov = kv_row(pw, r, "CPU mode"); r += 1
        self.v_oc = kv_row(pw, r, "Overclock"); r += 1
        self.v_ssd = kv_row(pw, r, "SSD temperature"); r += 1
        self.v_fan = kv_row(pw, r, "Fan"); r += 1
        tk.Label(pw, text="Right now", bg=C["panel"], fg=C["dim"], font=FONT_S).grid(
            row=r, column=0, sticky="w", padx=10, pady=(8, 0))
        now = tk.Frame(pw, bg=C["panel"])
        now.grid(row=r + 1, column=0, columnspan=2, sticky="w", padx=8)
        tk.Label(pw, text="Since boot", bg=C["panel"], fg=C["dim"], font=FONT_S).grid(
            row=r + 2, column=0, sticky="w", padx=10, pady=(6, 0))
        past = tk.Frame(pw, bg=C["panel"])
        past.grid(row=r + 3, column=0, columnspan=2, sticky="w", padx=8)
        self._power_hint_row = r + 4
        names = ["Power", "Throttled", "Capped", "Hot"]
        self.l_now = [Light(now, n) for n in names]
        self.l_past = [Light(past, n) for n in names]
        for i, w in enumerate(self.l_now):
            w.grid(row=0, column=i, sticky="w")
        for i, w in enumerate(self.l_past):
            w.grid(row=0, column=i, sticky="w")
        self.power_hint = tk.Label(pw, text="", bg=C["panel"], fg=C["dim"], font=FONT_S,
                                   justify="left", wraplength=310, anchor="w")
        self.power_hint.grid(row=self._power_hint_row, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 10))

        sysp = panel(f)
        sysp.pack(fill="x", padx=6, pady=(0, 8))
        for c in range(4):
            sysp.columnconfigure(c * 2 + 1, weight=1)
        heading(sysp, "System").grid(row=0, column=0, columnspan=8, sticky="w", padx=10, pady=(8, 2))
        items = [("Host", 1, 0), ("Uptime", 1, 1), ("Load (1/5/15)", 1, 2), ("Processes", 1, 3),
                 ("Disk read", 2, 0), ("Disk write", 2, 1), ("Model", 2, 2), ("OS", 2, 3)]
        self.sys = {}
        for key, r, c in items:
            tk.Label(sysp, text=key, bg=C["panel"], fg=C["dim"], font=FONT_S).grid(
                row=r, column=c * 2, sticky="w", padx=(10, 6))
            v = tk.Label(sysp, text="—", bg=C["panel"], fg=C["fg"], font=FONT, anchor="w")
            v.grid(row=r, column=c * 2 + 1, sticky="w")
            self.sys[key] = v
        lr = tk.Frame(sysp, bg=C["panel"])
        lr.grid(row=3, column=0, columnspan=8, sticky="w", padx=8, pady=(6, 8))
        self.l_vnc = Light(lr, "VNC :5900")
        self.l_ssh = Light(lr, "SSH :22")
        self.l_ap = Light(lr, "Hotspot")
        self.l_fallback = Light(lr, "Auto-hotspot at boot")
        self.l_vpn = Light(lr, "Tailscale")
        self.l_web = Light(lr, "Web page")
        for w in (self.l_vnc, self.l_ssh, self.l_ap, self.l_fallback, self.l_vpn, self.l_web):
            w.pack(side="left", padx=(0, 10))
        model = read_file("/proc/device-tree/model").replace("\x00", "") or os.uname().machine
        self.sys["Model"].config(text=model.replace("Raspberry Pi", "Pi"))
        osname = "Linux"
        for line in read_file("/etc/os-release").splitlines():
            if line.startswith("PRETTY_NAME="):
                osname = line.split("=", 1)[1].strip('"')
        self.sys["OS"].config(text=osname[:40])
        self.sys["Host"].config(text=socket.gethostname())

    # ---------- Processes
    def _build_processes(self, f):
        top = tk.Frame(f, bg=C["bg"])
        top.pack(fill="x", padx=6, pady=8)
        tk.Label(top, text="Filter (Ctrl+F):", bg=C["bg"], fg=C["dim"], font=FONT_S).pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(top, textvariable=self.filter_var, width=24)
        self.filter_entry.pack(side="left", padx=6)
        self.filter_var.trace_add("write", lambda *a: self._refresh_procs())
        self.proc_count = tk.Label(top, text="", bg=C["bg"], fg=C["dim"], font=FONT_S)
        self.proc_count.pack(side="left", padx=10)
        ttk.Button(top, text="Force kill (Shift+Del)", style="Danger.TButton",
                   command=lambda: self._kill(True)).pack(side="right")
        ttk.Button(top, text="End task (Del)", command=lambda: self._kill(False)).pack(side="right", padx=6)

        cols = [("pid", "PID", 70, "e"), ("name", "Name", 260, "w"), ("user", "User", 90, "w"),
                ("cpu", "CPU %", 85, "e"), ("mem", "Mem %", 85, "e"), ("rss", "Memory", 90, "e"),
                ("threads", "Threads", 70, "e"), ("status", "Status", 90, "w")]
        wrap = tk.Frame(f, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        self.tree = ttk.Treeview(wrap, columns=[c[0] for c in cols], show="headings", selectmode="browse")
        self.col_titles = {}
        for key, title, width, anchor in cols:
            self.col_titles[key] = title
            self.tree.heading(key, text=title, command=lambda k=key: self._sort_by(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "name"))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._show_sort_arrow()

    def _sort_by(self, col):
        if self.sort_col == col:
            self.sort_rev = not self.sort_rev
        else:
            self.sort_col, self.sort_rev = col, col in ("cpu", "mem", "rss", "threads")
        self._show_sort_arrow()
        self._refresh_procs()

    def _show_sort_arrow(self):
        for k, t in self.col_titles.items():
            arrow = (" ▼" if self.sort_rev else " ▲") if k == self.sort_col else ""
            self.tree.heading(k, text=t + arrow)

    def _refresh_procs(self):
        flt = self.filter_var.get().strip().lower()
        rows = []
        attrs = ["pid", "name", "username", "cpu_percent", "memory_percent", "memory_info",
                 "num_threads", "status", "cmdline"]
        for p in psutil.process_iter(attrs):
            i = p.info
            name = i.get("name") or "?"
            cl = i.get("cmdline") or []
            if name.startswith(("python", "node", "bash", "sh", "perl")) and len(cl) > 1:
                arg = next((a for a in cl[1:] if not a.startswith("-")), None)
                if arg:
                    name = f"{name}  ({os.path.basename(arg)})"
            if flt and flt not in name.lower() and flt not in str(i["pid"]):
                continue
            rss = i["memory_info"].rss if i.get("memory_info") else 0
            rows.append((i["pid"], name, i.get("username") or "", (i.get("cpu_percent") or 0) / self.ncpu,
                         i.get("memory_percent") or 0, rss, i.get("num_threads") or 0, i.get("status") or ""))
        idx = {"pid": 0, "name": 1, "user": 2, "cpu": 3, "mem": 4, "rss": 5, "threads": 6, "status": 7}[self.sort_col]
        rows.sort(key=lambda r: r[idx].lower() if isinstance(r[idx], str) else r[idx], reverse=self.sort_rev)

        sel = self.tree.selection()
        y = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            self.tree.insert("", "end", iid=str(r[0]), values=(
                r[0], r[1], r[2], f"{r[3]:.1f}", f"{r[4]:.1f}", fmt_bytes(r[5]), r[6], r[7]))
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
            self.tree.focus(sel[0])
        self.tree.yview_moveto(y)
        self.proc_count.config(text=f"{len(rows)} processes")

    def _kill(self, force):
        sel = self.tree.selection()
        if not sel:
            self.status("Pick a process first (arrow keys or click).")
            return
        pid = int(sel[0])
        name = self.tree.item(sel[0], "values")[1]
        if pid <= 1:
            return
        verb = "Force kill" if force else "End"
        if not messagebox.askyesno(APP_NAME, f"{verb} “{name}” (PID {pid})?", parent=self):
            return
        try:
            p = psutil.Process(pid)
            p.kill() if force else p.terminate()
            self.log(f"{verb}: {name} (PID {pid})", ok=True)
        except psutil.NoSuchProcess:
            self.log(f"{name} already exited.", ok=True)
        except psutil.AccessDenied:
            self.run_action(f"{verb} {name} (as root)",
                            lambda: run_helper("kill", str(pid), "KILL" if force else "TERM"))
        self.after(400, self._refresh_procs)

    # ---------- Network
    def _build_network(self, f):
        self.cards_frame = tk.Frame(f, bg=C["bg"])
        self.cards_frame.pack(fill="x", padx=6, pady=8)
        gp = panel(f)
        gp.pack(fill="x", padx=6)
        heading(gp, "All traffic — last 60 seconds").pack(fill="x", padx=10, pady=(8, 4))
        self.net_graph = Graph(gp, [("Download", C["green"]), ("Upload", C["amber"])], height=130, fmt=fmt_rate)
        self.net_graph.pack(fill="x", padx=10, pady=(0, 10))
        cp = panel(f)
        cp.pack(fill="both", expand=True, padx=6, pady=8)
        chdr = tk.Frame(cp, bg=C["panel"])
        chdr.pack(fill="x", padx=10, pady=(8, 4))
        heading(chdr, f"Devices connected to the {AP_SSID} hotspot").pack(side="left")
        ttk.Button(chdr, text="👥 Who's connected & visitor log (Ctrl+P)", style="Accent.TButton",
                   command=self.open_people).pack(side="right")
        self.client_tree = ttk.Treeview(cp, columns=("ip", "mac", "name"), show="headings", height=5)
        for k, t, w in (("ip", "IP address", 150), ("mac", "MAC", 180), ("name", "Device name", 260)):
            self.client_tree.heading(k, text=t)
            self.client_tree.column(k, width=w, anchor="w")
        self.client_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _rebuild_cards(self, nics):
        for w in self.cards_frame.winfo_children():
            w.destroy()
        self.cards, self.card_lights = {}, {}
        for i, n in enumerate(nics):
            card = panel(self.cards_frame)
            card.grid(row=0, column=i, sticky="nsew", padx=(0, 6))
            self.cards_frame.columnconfigure(i, weight=1)
            kind = "Wi-Fi" if n.startswith("wl") else "Ethernet" if n.startswith(("eth", "en")) else "Network"
            tk.Label(card, text=f"{n}  ·  {kind}", bg=C["panel"], fg=C["accent"], font=FONT_B).pack(
                anchor="w", padx=10, pady=(8, 2))
            lights = NicLights(card, n, C["panel"], show_name=False)
            lights.pack(anchor="w", padx=8)
            self.card_lights[n] = lights
            labels = {}
            for key in ("ip", "rate", "total", "extra"):
                labels[key] = tk.Label(card, text="", bg=C["panel"], fg=C["fg"] if key != "extra" else C["dim"],
                                       font=FONT if key != "extra" else FONT_S, anchor="w", justify="left")
                labels[key].pack(anchor="w", padx=10)
            tk.Frame(card, bg=C["panel"], height=6).pack()
            self.cards[n] = labels

    # ---------- Tools
    def _build_tools(self, f):
        grid = tk.Frame(f, bg=C["bg"])
        grid.pack(fill="both", expand=True, padx=6, pady=8)
        for c in range(3):
            grid.columnconfigure(c, weight=1, uniform="tools")

        def box(title, r, c, **kw):
            lf = panel(grid)
            lf.grid(row=r, column=c, sticky="nsew", padx=(0, 6) if c < 2 else 0, pady=(0, 6), **kw)
            heading(lf, title).pack(fill="x", padx=10, pady=(8, 4))
            return lf

        def note(parent, text):
            tk.Label(parent, text=text, bg=C["panel"], fg=C["dim"], font=FONT_S, justify="left",
                     wraplength=300, anchor="w").pack(fill="x", padx=10, pady=(4, 8))

        m = box("Memory & updates", 0, 0)
        ttk.Button(m, text="🧹  Clean & optimize memory   (Ctrl+M)", style="Accent.TButton",
                   command=self.act_clean_memory).pack(fill="x", padx=10)
        note(m, "Drops cached files, compacts RAM and pulls swap back into memory. "
                "Best right before opening something big.")
        ttk.Button(m, text="⬆  Update everything   (Ctrl+U)", command=self.act_update).pack(fill="x", padx=10)
        self.t_update = tk.Label(m, text="", bg=C["panel"], fg=C["dim"], font=FONT_S, anchor="w", justify="left",
                                 wraplength=300)
        self.t_update.pack(fill="x", padx=10, pady=(4, 8))

        b = box("Bluetooth", 0, 1)
        ttk.Button(b, text="🛟  Rescue Bluetooth   (Ctrl+B)", style="Accent.TButton",
                   command=self.act_bt_rescue).pack(fill="x", padx=10)
        ttk.Button(b, text="➕  Scan & pair a device   (Ctrl+Alt+B)", command=self.act_bt_pair).pack(
            fill="x", padx=10, pady=(6, 0))
        note(b, "Rescue restarts Bluetooth and reconnects your paired mouse/keyboard. "
                "Lost your mouse? Ctrl+Alt+B opens pairing from anywhere, keyboard only.")

        h = box("Wi-Fi hotspot", 0, 2, rowspan=2)
        top = tk.Frame(h, bg=C["panel"])
        top.pack(fill="x", padx=8)
        self.t_ap = Light(top, "Hotspot off")
        self.t_ap.pack(side="left")
        self.ap_btn = ttk.Button(h, text="📶  Start hotspot   (Ctrl+H)", style="Accent.TButton",
                                 command=self.act_toggle_hotspot)
        self.ap_btn.pack(fill="x", padx=10, pady=(4, 4))
        ttk.Button(h, text="👥  Who's connected   (Ctrl+P)", command=self.open_people).pack(
            fill="x", padx=10, pady=(0, 6))
        info = tk.Frame(h, bg=C["panel"])
        info.pack(fill="x")
        self.ap_vals = {}
        for r, (k, v) in enumerate((("Network", AP_SSID), ("Password", AP_PASS),
                                    ("Pi address", AP_IP), ("The Shelf", SHELF_URL))):
            self.ap_vals[k] = kv_row(info, r, k)
            self.ap_vals[k].config(text=v, wraplength=170 if r < 2 else 260, justify="left")
        ttk.Button(info, text="✏ Change", width=8, command=self.act_ap_settings).grid(
            row=0, column=2, rowspan=2, sticky="e", padx=(4, 10))
        info.columnconfigure(1, weight=1)
        tk.Label(info, text="Wi-Fi band", bg=C["panel"], fg=C["dim"], font=FONT_S, anchor="w").grid(
            row=4, column=0, sticky="w", padx=(10, 6), pady=(3, 1))
        bandrow = tk.Frame(info, bg=C["panel"])
        bandrow.grid(row=4, column=1, columnspan=2, sticky="w", pady=(3, 1))
        self.band_24 = tk.Label(bandrow, text="2.4 GHz", bg=C["panel"], font=FONT_B, cursor="hand2")
        self.band_24.pack(side="left", padx=(0, 4))
        self.band_switch = ToggleSwitch(bandrow, "5 GHz", False, command=self.act_band)
        self.band_switch.pack(side="left")
        self.band_24.bind("<Button-1>", lambda e: self.band_switch.value and self.band_switch.toggle())
        self.band_info = tk.Label(h, text="", bg=C["panel"], fg=C["dim"], font=FONT_S, anchor="w",
                                  justify="left", wraplength=320)
        self.band_info.pack(fill="x", padx=10)
        self._band_busy = 0.0
        dr = tk.Frame(h, bg=C["panel"])
        dr.pack(fill="x", padx=10, pady=(4, 0))
        self.data_switch = ToggleSwitch(dr, "Phones keep their mobile data", ap_keeps_mobile_data(),
                                        command=self.act_ap_data)
        self.data_switch.pack(side="left")
        self.data_info = tk.Label(h, text="", bg=C["panel"], fg=C["dim"], font=FONT_S, anchor="w",
                                  justify="left", wraplength=320)
        self.data_info.pack(fill="x", padx=10)
        self._show_data_info()
        self.qsize = qsize = 104
        qrow = tk.Frame(h, bg=C["panel"])
        qrow.pack(fill="x", padx=10, pady=(6, 0))
        self.qr = tk.Canvas(qrow, width=qsize, height=qsize, bg=C["panel"], highlightthickness=0)
        self.qr.pack(side="left")
        draw_qr(self.qr, wifi_qr_text(AP_SSID, AP_PASS), qsize)
        tk.Label(qrow, text="Scan with a phone camera to join the hotspot. No typing needed.", bg=C["panel"],
                 fg=C["dim"], font=FONT_S, wraplength=180, justify="left").pack(side="left", padx=(12, 0))
        fb = tk.Frame(h, bg=C["panel"])
        fb.pack(fill="x", padx=8, pady=(6, 6))
        self.t_fallback = Light(fb, "Starts by itself at boot when there's no network")
        self.t_fallback.pack(side="left")
        lr = tk.Frame(h, bg=C["panel"])
        lr.pack(fill="x", padx=10, pady=(0, 10))
        self.led_switch = ToggleSwitch(lr, "Flash light & keys when on" if IS_500 else "Flash power light while on",
                                       led_blink_enabled(),
                                       command=self.act_led)
        self.led_switch.pack(side="left")
        ttk.Button(lr, text="Test", width=5, command=self.act_led_test).pack(side="right")

        p = box("Performance", 1, 0)
        tk.Label(p, text="CPU mode", bg=C["panel"], fg=C["dim"], font=FONT_S).pack(anchor="w", padx=10)
        self.gov_var = tk.StringVar()
        for val, text in (("powersave", "🔋 Battery saver"), ("ondemand", "⚖ Balanced (normal)"),
                          ("performance", "🚀 Full speed")):
            ttk.Radiobutton(p, text=text, value=val, variable=self.gov_var,
                            command=self.act_governor).pack(anchor="w", padx=14)
        tk.Label(p, text="Overclock (applies after a reboot)", bg=C["panel"], fg=C["dim"],
                 font=FONT_S).pack(anchor="w", padx=10, pady=(8, 2))
        ocr = tk.Frame(p, bg=C["panel"])
        ocr.pack(fill="x", padx=10)
        for prof, text, full in OC_PRESETS:
            ttk.Button(ocr, text=text, width=6, command=lambda pr=prof, t=full: self.act_overclock(pr, t)).pack(
                side="left", expand=True, fill="x", padx=(0, 4))
        self.t_oc = tk.Label(p, text="", bg=C["panel"], fg=C["fg"], font=FONT_S, anchor="w")
        self.t_oc.pack(fill="x", padx=10, pady=(4, 2))
        prow = tk.Frame(p, bg=C["panel"])
        prow.pack(fill="x", padx=10, pady=(2, 8))
        if IS_PI5:
            self.gpu_switch = ToggleSwitch(prow, "Graphics boost", read_gpu_boost(), command=self.act_gpu_boost)
            self.gpu_switch.pack(side="left")
        ttk.Button(prow, text="🧪 Stability test", command=self.act_stability).pack(side="right")

        r = box("Remote access & startup", 1, 1)
        lr = tk.Frame(r, bg=C["panel"])
        lr.pack(fill="x", padx=8)
        self.t_vnc = Light(lr, "VNC on port 5900")
        self.t_vnc.pack(side="left")
        ttk.Button(lr, text="Turn on VNC", command=self.act_vnc).pack(side="right")
        vr = tk.Frame(r, bg=C["panel"])
        vr.pack(fill="x", padx=10, pady=(8, 0))
        self.vpn_switch = ToggleSwitch(vr, "Tailscale", False, command=self.act_vpn)
        self.vpn_switch.pack(side="left")
        tk.Label(vr, text="reach this Pi from anywhere", bg=C["panel"], fg=C["dim"], font=FONT_S).pack(side="left", padx=6)
        self.vpn_info = tk.Label(r, text="", bg=C["panel"], fg=C["dim"], font=FONT_S, justify="left", anchor="w",
                                 wraplength=320)
        self.vpn_info.pack(fill="x", padx=10, pady=(2, 6))
        self._vpn_busy = 0.0
        wr = tk.Frame(r, bg=C["panel"])
        wr.pack(fill="x", padx=10)
        ttk.Button(wr, text="🔑 Password", command=lambda: WebPasswordDialog(self)).pack(side="right")
        self.web_switch = ToggleSwitch(wr, "Web page", False, command=self.act_web)
        self.web_switch.pack(side="left")
        self.web_info = tk.Label(r, text="", bg=C["panel"], fg=C["dim"], font=FONT_S, justify="left", anchor="w",
                                 wraplength=320)
        self.web_info.pack(fill="x", padx=10, pady=(2, 6))
        self._web_busy = 0.0
        self.autostart_var = tk.BooleanVar(value=os.path.exists(AUTOSTART_FILE))
        ttk.Checkbutton(r, text="Open at desktop startup",
                        variable=self.autostart_var, command=self.act_autostart).pack(anchor="w", padx=10)
        pr = tk.Frame(r, bg=C["panel"])
        pr.pack(fill="x", padx=10, pady=(8, 10))
        ttk.Button(pr, text="Reboot", command=lambda: self.act_power("reboot")).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(pr, text="Shut down", style="Danger.TButton",
                   command=lambda: self.act_power("poweroff")).pack(side="left", expand=True, fill="x")

        lg = panel(grid)
        lg.grid(row=2, column=0, columnspan=3, sticky="nsew")
        grid.rowconfigure(2, weight=1)
        heading(lg, "Activity log").pack(fill="x", padx=10, pady=(8, 4))
        self.logbox = tk.Text(lg, height=6, bg=C["panel2"], fg=C["fg"], font=(FAM, 9), relief="flat",
                              wrap="word", state="disabled", padx=8, pady=6)
        self.logbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.logbox.tag_config("ok", foreground=C["green"])
        self.logbox.tag_config("bad", foreground=C["red"])
        self.logbox.tag_config("t", foreground=C["dim"])

    # ---------- AI
    def _build_ai(self, f):
        top = panel(f)
        top.pack(fill="x", padx=6, pady=(8, 6))
        row = tk.Frame(top, bg=C["panel"])
        row.pack(fill="x", padx=10, pady=(10, 4))
        self.ai_switch = ToggleSwitch(row, "AI", False, command=self.act_ai_toggle)
        self.ai_switch.lbl.config(font=(FAM, 13, "bold"))
        self.ai_switch.pack(side="left")
        self.ai_light = Light(row, "Checking…")
        self.ai_light.pack(side="left", padx=(16, 0))
        ttk.Button(row, text="New chat", command=self.ai_new_chat).pack(side="right")
        self.ai_dl_btn = ttk.Button(row, text="⬇ Download", command=self.ai_pull)
        self.ai_dl_btn.pack(side="right", padx=6)
        self.ai_model = tk.StringVar(value=load_settings().get("ai_model", AI_DEFAULT))
        self.ai_combo = ttk.Combobox(row, textvariable=self.ai_model, width=18, state="readonly",
                                     values=[m for m, _ in AI_SUGGESTED])
        self.ai_combo.pack(side="right")
        self.ai_combo.bind("<<ComboboxSelected>>", lambda e: self._ai_model_changed())
        tk.Label(row, text="Model", bg=C["panel"], fg=C["dim"], font=FONT_S).pack(side="right", padx=(0, 6))
        self.ai_hint = tk.Label(top, text="", bg=C["panel"], fg=C["dim"], font=FONT_S, anchor="w", justify="left")
        self.ai_hint.pack(fill="x", padx=12, pady=(0, 10))

        chat = panel(f)
        chat.pack(fill="both", expand=True, padx=6)
        wrap = tk.Frame(chat, bg=C["panel"])
        wrap.pack(fill="both", expand=True, padx=10, pady=10)
        self.ai_text = tk.Text(wrap, height=8, bg=C["panel2"], fg=C["fg"], font=(FAM, 11), relief="flat", wrap="word",
                               padx=12, pady=10, state="disabled", spacing1=2, spacing3=2, insertbackground=C["fg"])
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.ai_text.yview)
        self.ai_text.configure(yscrollcommand=sb.set)
        self.ai_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.ai_text.tag_config("you", foreground=C["accent"], font=(FAM, 11, "bold"))
        self.ai_text.tag_config("ai", foreground=C["green"], font=(FAM, 11, "bold"))
        self.ai_text.tag_config("meta", foreground=C["dim"], font=(FAM, 9))
        self.ai_text.tag_config("err", foreground=C["red"])
        self.ai_text.tag_config("pending", foreground=C["dim"], font=(FAM, 10, "italic"))

        inp = tk.Frame(f, bg=C["bg"])
        inp.pack(fill="x", padx=6, pady=8)
        self.ai_entry = ttk.Entry(inp, font=(FAM, 11))
        self.ai_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.ai_entry.bind("<Return>", lambda e: self.ai_send())
        self.ai_entry.bind("<Escape>", lambda e: self.ai_stop())
        self.ai_stop_btn = ttk.Button(inp, text="■ Stop (Esc)", command=self.ai_stop)
        self.ai_stop_btn.pack(side="right", padx=(6, 0))
        ttk.Button(inp, text="Send (Enter)", style="Accent.TButton", command=self.ai_send).pack(side="right", padx=(6, 0))
        self._ai_welcome()
        self.after(60, self._ai_drain)

    def _ai_write(self, text, tag=None):
        self.ai_text.config(state="normal")
        self.ai_text.insert("end", text, tag or ())
        self.ai_text.see("end")
        self.ai_text.config(state="disabled")

    def _ai_welcome(self):
        self._ai_write("Local AI\n", "ai")
        self._ai_write("Everything here runs on this Pi. Nothing you type leaves it, and once a model is "
                       "downloaded it works with no internet at all.\nTurn on the AI switch, type a question "
                       "below and press Enter. The first answer takes a little longer while the model loads.\n\n",
                       "meta")

    def ai_new_chat(self):
        self.ai_stop()
        self.ai_messages = []
        self.ai_text.config(state="normal")
        self.ai_text.delete("1.0", "end")
        self.ai_text.config(state="disabled")
        self._ai_welcome()
        self.ai_entry.focus_set()

    def _ai_model_changed(self):
        save_setting("ai_model", self.ai_model.get())

    def act_ai_toggle(self, on):
        sd = self.slow.get()
        if on and not sd.get("ai_installed"):
            if not messagebox.askyesno(APP_NAME,
                    "The AI engine (Ollama) isn't installed yet.\n\nInstall it now? The Pi needs internet for "
                    "this, it's a large download, and it can take several minutes. Afterwards the AI works "
                    "offline.", parent=self):
                self.ai_switch.set(False)
                return
            self.ai_busy = "switch"

            def done(rc):
                self.ai_busy = None
                if rc == 0 and not sd.get("ai_models"):
                    self.after(2000, self._ai_pull_when_ready)
            self.run_action("Install the AI engine", lambda: run_helper("ai", "install", timeout=3600), on_done=done)
            return
        self.ai_busy = "switch"
        self.run_action("AI " + ("on" if on else "off"), lambda: run_helper("ai", "on" if on else "off"),
                        on_done=lambda rc: self.after(2500, lambda: setattr(self, "ai_busy", None)))
        if not on:
            self.ai_stop()

    def _ai_pull_when_ready(self, tries=30):
        if self.slow.get().get("ai_active"):
            self.ai_pull()
        elif tries:
            self.after(2000, lambda: self._ai_pull_when_ready(tries - 1))

    def ai_pull(self):
        model = self.ai_model.get() or AI_DEFAULT
        sd = self.slow.get()
        if not sd.get("ai_active"):
            self._ai_write("Turn on the AI switch first, then press Download.\n\n", "err")
            return
        if self.ai_busy:
            return
        self.ai_busy = "pull"
        self.ai_cancel = False
        self._ai_write(f"Downloading {model}… (needs internet; you can keep using the Pi)\n", "meta")

        def work():
            body = json.dumps({"model": model, "stream": True}).encode()
            req = urllib.request.Request(OLLAMA + "/api/pull", data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    self.ai_resp = r
                    for line in r:
                        if self.ai_cancel:
                            break
                        j = json.loads(line or b"{}")
                        if j.get("error"):
                            self.ai_q.put(("err", j["error"]))
                            break
                        tot, done = j.get("total"), j.get("completed")
                        pct = f" {done * 100 // tot}% of {fmt_bytes(tot)}" if tot and done else ""
                        self.ai_q.put(("pull", f"{j.get('status', '')}{pct}"))
                        if j.get("status") == "success":
                            self.ai_q.put(("meta", f"✔ {model} is ready. Ask it anything.\n\n"))
            except Exception as e:  # noqa
                if not self.ai_cancel:
                    self.ai_q.put(("err", f"Download stopped: {e}"))
            self.ai_q.put(("end", None))

        threading.Thread(target=work, daemon=True).start()

    def ai_send(self):
        text = self.ai_entry.get().strip()
        if not text or self.ai_busy in ("chat", "pull"):
            return
        sd = self.slow.get()
        model = self.ai_model.get()
        if not sd.get("ai_active"):
            self._ai_write("The AI is off. Flip the AI switch on first.\n\n", "err")
            return
        if model not in sd.get("ai_models", []):
            self._ai_write(f"{model} isn't downloaded yet. Press ⬇ Download while the Pi is online.\n\n", "err")
            return
        self.ai_entry.delete(0, "end")
        save_setting("ai_model", model)
        self._ai_write("You  ", "you")
        self._ai_write(text + "\n")
        self._ai_write("AI  ", "ai")
        self._ai_write("thinking…", "pending")
        self.ai_messages.append({"role": "user", "content": text})
        self.ai_busy = "chat"
        self.ai_cancel = False
        self._ai_reply = ""
        msgs = [{"role": "system", "content": AI_SYSTEM}] + self.ai_messages[-12:]

        def work():
            body = json.dumps({"model": model, "messages": msgs, "stream": True,
                               "options": {"num_ctx": 2048}}).encode()
            req = urllib.request.Request(OLLAMA + "/api/chat", data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    self.ai_resp = r
                    for line in r:
                        if self.ai_cancel:
                            break
                        j = json.loads(line or b"{}")
                        if j.get("error"):
                            self.ai_q.put(("err", j["error"]))
                            break
                        piece = j.get("message", {}).get("content", "")
                        if piece:
                            self.ai_q.put(("tok", piece))
                        if j.get("done"):
                            n, dur = j.get("eval_count", 0), j.get("eval_duration", 0)
                            if n and dur:
                                self.ai_q.put(("stats", f"{n} tokens · {n / (dur / 1e9):.1f} tokens/s"))
                            break
            except Exception as e:  # noqa
                if not self.ai_cancel:
                    self.ai_q.put(("err", f"The AI didn't answer: {e}"))
            self.ai_q.put(("end", None))

        threading.Thread(target=work, daemon=True).start()

    def ai_stop(self):
        if self.ai_busy in ("chat", "pull"):
            self.ai_cancel = True
            try:
                if self.ai_resp:
                    self.ai_resp.close()
            except Exception:
                pass

    def _ai_clear_pending(self):
        rng = self.ai_text.tag_ranges("pending")
        if rng:
            self.ai_text.config(state="normal")
            self.ai_text.delete(rng[0], rng[-1])
            self.ai_text.config(state="disabled")

    def _ai_drain(self):
        try:
            while True:
                kind, val = self.ai_q.get_nowait()
                if kind == "tok":
                    self._ai_clear_pending()
                    self._ai_reply += val
                    self._ai_write(val)
                elif kind == "stats":
                    self._ai_write(f"\n{val}", "meta")
                elif kind == "pull":
                    self.ai_hint.config(text=f"Downloading {self.ai_model.get()}: {val}", fg=C["amber"])
                elif kind == "meta":
                    self._ai_write(val, "meta")
                elif kind == "err":
                    self._ai_clear_pending()
                    self._ai_write(f"\n{val}\n", "err")
                elif kind == "end":
                    self._ai_clear_pending()
                    if self.ai_busy == "chat":
                        if self._ai_reply:
                            self.ai_messages.append({"role": "assistant", "content": self._ai_reply})
                        if self.ai_cancel:
                            self._ai_write("  (stopped)", "meta")
                        self._ai_write("\n\n")
                    elif self.ai_cancel:
                        self._ai_write("Download stopped.\n\n", "meta")
                    self.ai_busy = None
                    self.ai_resp = None
        except queue.Empty:
            pass
        self.after(60, self._ai_drain)

    def _update_ai(self, sd):
        installed, active = sd.get("ai_installed"), sd.get("ai_active")
        models, loaded = sd.get("ai_models", []), sd.get("ai_loaded", [])
        if self.ai_busy != "switch" and "ai_active" in sd:
            self.ai_switch.set(bool(active))
        if models != self._ai_models_seen:
            self._ai_models_seen = models
            extra = [m for m, _ in AI_SUGGESTED if m not in models]
            self.ai_combo.config(values=models + extra)
            if models and self.ai_model.get() not in models:
                self.ai_model.set(models[0])
        model = self.ai_model.get()
        have = model in models
        self.ai_dl_btn.config(text="✔ Downloaded" if have else "⬇ Download",
                              state="disabled" if have or not active else "normal")
        if self.ai_busy == "pull":
            return
        if not installed and "ai_installed" in sd:
            self.ai_light.set(C["off"], "AI engine not installed. Flip the switch to install it (needs internet).")
            hint = "Needs the 64-bit Raspberry Pi OS. After a one-time download it works with no internet."
        elif not active:
            self.ai_light.set(C["off"], "AI is off. It isn't using any memory or battery.")
            hint = "Flip the switch to start it. Starting takes a few seconds."
        elif not models:
            self.ai_light.set(C["amber"], "Running, but no model downloaded yet")
            hint = "Pick a model and press ⬇ Download while the Pi has internet."
        else:
            if loaded:
                name, size = loaded[0]
                self.ai_light.set(C["green"], f"Ready · {name} loaded ({fmt_bytes(size)} of memory)")
            else:
                self.ai_light.set(C["green"], f"Ready · {len(models)} model{'s' if len(models) != 1 else ''} on this Pi")
            hint = ("While answering, the AI keeps the CPU busy and warm. The model unloads itself after "
                    "5 idle minutes to free memory.")
        if not have:
            desc = dict(AI_SUGGESTED).get(model)
            if desc and active:
                hint = f"{model}: {desc}. Press ⬇ Download while the Pi has internet."
        self.ai_hint.config(text=hint, fg=C["dim"])

    # ---------- keys
    def _bind_keys(self):
        for i, key in enumerate(("F1", "F2", "F3", "F4", "F5")):
            self.bind_all(f"<{key}>", lambda e, i=i: self.nb.select(i))
            self.bind_all(f"<Alt-Key-{i + 1}>", lambda e, i=i: self.nb.select(i))
        self.bind_all("<Control-m>", lambda e: self._not_typing(e) and self.act_clean_memory())
        self.bind_all("<Control-u>", lambda e: self._not_typing(e) and self.act_update())
        self.bind_all("<Control-b>", lambda e: self._not_typing(e) and self.act_bt_rescue())
        self.bind_all("<Control-Alt-b>", lambda e: self.act_bt_pair())
        self.bind_all("<Control-h>", lambda e: self._not_typing(e) and self.act_toggle_hotspot())
        self.bind_all("<Control-q>", lambda e: self.destroy())
        self.bind_all("<Control-p>", lambda e: self._not_typing(e) and self.open_people())
        self.bind_all("<Control-f>", lambda e: (self.nb.select(1), self.filter_entry.focus_set()))
        self.bind_all("<Delete>", lambda e: self._on_tab_idx() == 1 and self._not_typing(e) and self._kill(False))
        self.bind_all("<Shift-Delete>", lambda e: self._on_tab_idx() == 1 and self._not_typing(e) and self._kill(True))
        self.bind_all("<Escape>", lambda e: self.tree.focus_set() if e.widget is self.filter_entry else None)

    def _not_typing(self, e):
        """True when the key was pressed in the main window and not while typing in a text box."""
        try:
            w = self.focus_get() or e.widget   # the widget with the typing cursor
        except (KeyError, tk.TclError):
            w = e.widget
        try:
            in_main = w.winfo_toplevel() is self
        except (AttributeError, tk.TclError):
            return False
        return in_main and not isinstance(w, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox))

    def _on_tab_idx(self):
        try:
            return self.nb.index(self.nb.select())
        except tk.TclError:
            return 0

    def _on_tab(self):
        if self._on_tab_idx() == 1:
            self._refresh_procs()
            self.tree.focus_set()
            kids = self.tree.get_children()
            if kids and not self.tree.selection():
                self.tree.selection_set(kids[0])
                self.tree.focus(kids[0])

    # ---------- logging / actions
    def status(self, text):
        self.status_var.set(text)

    def log(self, text, ok=None):
        self.logbox.config(state="normal")
        self.logbox.insert("end", time.strftime("%H:%M:%S  "), "t")
        self.logbox.insert("end", text + "\n", "ok" if ok else "bad" if ok is False else "")
        self.logbox.see("end")
        self.logbox.config(state="disabled")
        self.status(text.splitlines()[0])

    def run_action(self, label, fn, confirm=None, on_done=None):
        if confirm and not messagebox.askyesno(APP_NAME, confirm, parent=self):
            return
        self.log(f"▶ {label}…")

        def work():
            try:
                rc, out = fn()
            except Exception as e:  # noqa
                rc, out = 1, str(e)

            def done():
                self.log(("✔ " if rc == 0 else "✖ ") + label + (f"\n{out}" if out else ""), ok=rc == 0)
                if on_done:
                    on_done(rc)
            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def act_clean_memory(self):
        self.run_action("Clean & optimize memory", lambda: run_helper("dropcaches"))

    def act_bt_rescue(self):
        self.run_action("Bluetooth rescue", lambda: run_helper("bt-rescue"))

    def act_bt_pair(self):
        w = getattr(self, "_pair_win", None)
        if w is not None and w.winfo_exists():
            w.lift()
            w._focus()
            return
        self._pair_win = BtPairDialog(self, app=self)

    def open_people(self):
        if self.people_win is not None and self.people_win.winfo_exists():
            self.people_win.deiconify()
            self.people_win.lift()
            self.people_win.focus_force()
        else:
            self.people_win = PeopleWindow(self)

    def act_toggle_hotspot(self):
        sd = self.slow.get()
        if sd.get("ap_on"):
            self.run_action("Stop hotspot (reconnecting to your usual Wi-Fi)", lambda: run_helper("ap-down"))
            return
        msg = f"Start the {AP_SSID} hotspot?\n\nPeople nearby join with password {AP_PASS} and reach the Pi at {AP_IP}."
        if sd.get("ssid"):
            msg += (f"\n\nThis disconnects Wi-Fi from “{sd['ssid']}”. If you're remoted in over that Wi-Fi, "
                    f"join {AP_SSID} and connect to {AP_IP} instead.")
        self.run_action("Start hotspot", lambda: run_helper("ap-up"), confirm=msg)

    def act_governor(self):
        self._gov_touched = time.time()
        g = self.gov_var.get()
        self.run_action(f"CPU mode → {g}", lambda: run_helper("gov", g))

    def act_vpn(self, on):
        vpn = self.slow.get().get("vpn") or {}
        self._vpn_busy = time.time() + 600
        if not on:
            def done(rc):
                self._vpn_busy = 0.0
                self.slow.vpn_now = True
            self.run_action("Tailscale off", lambda: run_helper("vpn", "down"), on_done=done)
            return
        steps = [("vpn", "up")]
        if not vpn.get("installed"):
            if not messagebox.askyesno(APP_NAME,
                    "Set up Tailscale?\n\nIt lets you reach this Pi from anywhere (VNC and SSH from your phone or PC, "
                    "even on mobile data) with no router setup. It's free for personal use: you sign in once with a "
                    "Google, Microsoft, Apple or GitHub account.\n\nThe Pi needs internet for the one-minute install.",
                    parent=self):
                self._vpn_busy = 0.0
                self.vpn_switch.set(False)
                return
            steps.insert(0, ("vpn", "install"))
        self.log("▶ Tailscale on…")

        def work():
            res = (0, "")
            for st in steps:
                res = run_helper(*st, timeout=300)
                if res[0] != 0:
                    break
            self.after(0, lambda: self._vpn_after_up(*res))
        threading.Thread(target=work, daemon=True).start()

    def _vpn_after_up(self, rc, out):
        self._vpn_busy = 0.0
        self.slow.vpn_now = True
        last = out.strip().splitlines()[-1] if out.strip() else ""
        if rc == 0 and last.startswith("LOGIN "):
            self.log("Tailscale needs a one-time sign-in. Scan the code with your phone.")
            VpnLoginDialog(self, last.split(" ", 1)[1])
        else:
            self.log(("✔ " if rc == 0 else "✖ ") + "Tailscale on" + (f"\n{out}" if out else ""), ok=rc == 0)

    def act_gpu_boost(self, on):
        def done(rc):
            self.gpu_switch.set(read_gpu_boost())
            if rc == 0 and messagebox.askyesno(APP_NAME, "Saved. Reboot now to apply it?", parent=self):
                run_helper("reboot")
        self.run_action("Graphics boost " + ("on (1000 MHz)" if on else "off"),
                        lambda: run_helper("gpu-boost", "on" if on else "off"), on_done=done)

    def act_stability(self):
        w = getattr(self, "_stab_win", None)
        if w is not None and w.winfo_exists():
            w.lift()
            return
        self._stab_win = StabilityTest(self)

    def _check_unfinished_test(self):
        if not os.path.exists(STABILITY_MARK):
            return
        try:
            with open(STABILITY_MARK) as f:
                info = json.load(f)
        except (OSError, ValueError):
            info = {}
        same_boot = abs(info.get("boot", 0) - psutil.boot_time()) < 5
        if same_boot and psutil.pid_exists(info.get("pid", -1)):
            return   # a test is still running in this session, not a leftover from a freeze
        try:
            os.remove(STABILITY_MARK)
        except OSError:
            pass
        speed = info.get("speed", "the overclock")
        self.log(f"✖ The last stability test (at {speed}) never finished. The Pi probably froze or restarted.", ok=False)
        messagebox.showwarning(APP_NAME,
            f"The last stability test, at {speed}, never finished.\n\nThat usually means the Pi froze or restarted "
            "under load, so this speed isn't stable on your board. Choose a lower speed on the Tools tab.", parent=self)

    def act_overclock(self, prof, text):
        extra = ("\n\n3.0 GHz is the edge of what these chips do. Many boards manage it, some don't. After the "
                 "reboot, run the Stability test. If the Pi freezes or won't start, go back to 2.8 GHz."
                 if prof == "3000" else "")
        msg = (f"Set CPU speed to {text}?{extra}\n\nThis edits {CFG_PATH} (a backup is kept) and takes effect after a reboot. "
               "Faster speeds run warmer and draw more power, so a weak battery bank may trigger the under-voltage light.\n\n"
               "If the Pi ever fails to boot: put the SD card in another computer and delete the lines between "
               "the 'rpi400-taskmgr overclock' markers at the end of config.txt.")

        def done(rc):
            if rc == 0 and messagebox.askyesno(APP_NAME, "Saved. Reboot now to apply it?", parent=self):
                run_helper("reboot")

        self.run_action(f"Overclock → {text}", lambda: run_helper("oc", prof), confirm=msg, on_done=done)

    def act_band(self, five):
        sd = self.slow.get()
        name = "5 GHz" if five else "2.4 GHz"
        if sd.get("ap_on"):
            n = len(sd.get("clients", []))
            msg = (f"Switch the hotspot to {name}?\n\nThe hotspot restarts, so "
                   + (f"the {n} connected device{'s' if n != 1 else ''} will drop off for a few seconds and "
                      "need to rejoin." if n else "it will be off for a few seconds.")
                   + (f"\n\nOlder phones, laptops and gadgets that only have 2.4 GHz Wi-Fi won't see {AP_SSID} "
                      "on 5 GHz." if five else "")
                   + ("\n\nIf you're connected by VNC over the hotspot, you'll need to reconnect." ))
            if not messagebox.askyesno(APP_NAME, msg, parent=self):
                self.band_switch.set(not five)
                return
        self._band_busy = time.time()
        self.run_action(f"Hotspot band → {name}", lambda: run_helper("ap-band", "5" if five else "2.4", timeout=60),
                        on_done=lambda rc: setattr(self, "_band_busy", time.time() - 3))

    def _show_data_info(self):
        self.data_info.config(text=(
            "Wi-Fi for the Pi, mobile data for the internet."
            if self.data_switch.value else
            "Devices use the Pi's own internet (e.g. by Ethernet)."))

    def act_ap_data(self, keep):
        sd = self.slow.get()
        if sd.get("ap_on") and not messagebox.askyesno(
                APP_NAME, "The hotspot restarts to apply this, so connected devices drop off for a few seconds and "
                          "then rejoin by themselves.\n\nGo ahead?", parent=self):
            self.data_switch.set(not keep)
            return
        self._show_data_info()

        def done(rc):
            if rc != 0:
                self.data_switch.set(ap_keeps_mobile_data())
                self._show_data_info()
        self.run_action("Phones keep their mobile data: " + ("ON" if keep else "OFF"),
                        lambda: run_helper("ap-data", "keep" if keep else "share", timeout=60), on_done=done)

    def act_ap_settings(self):
        ApSettingsDialog(self)

    def apply_ap_settings(self, ssid, password):
        """Called by the dialog once its checks pass."""

        def done(rc):
            load_ap_settings()
            self.refresh_ap_labels()
        self.run_action(f"Hotspot name → {ssid}",
                        lambda: run_helper("ap-set", ssid, password, timeout=60), on_done=done)

    def refresh_ap_labels(self):
        self.ap_vals["Network"].config(text=AP_SSID)
        self.ap_vals["Password"].config(text=AP_PASS)
        draw_qr(self.qr, wifi_qr_text(AP_SSID, AP_PASS), self.qsize)

    def act_led(self, on):
        def done(rc):
            if rc != 0:
                self.led_switch.set(led_blink_enabled())
        self.run_action("Flash power light while the hotspot is on: " + ("ON" if on else "OFF"),
                        lambda: run_helper("led", "on" if on else "off"), on_done=done)

    def act_led_test(self):
        self.run_action("Power light test (flashes for 6 seconds)", lambda: run_helper("led", "test", "6"))

    def act_web(self, on):
        if on and not os.path.exists(WEB_PASSWORD_FILE):
            self.web_switch.set(False)
            WebPasswordDialog(self, then_turn_on=True)
            return
        self._web_busy = time.time() + 8
        self.web_info.config(text="Starting…" if on else "Stopping…", fg=C["amber"])

        def done(rc):
            self.slow.vpn_now = True   # re-check the web page's state on the next poll too
            self._web_busy = time.time() + (2 if rc == 0 else 0)
            if rc != 0:
                self.web_switch.set(not on)
        self.run_action("Web page: " + ("ON" if on else "OFF"), lambda: run_helper("web", "on" if on else "off", timeout=40),
                        on_done=done)

    def act_update(self):
        for w in self.winfo_children():
            if isinstance(w, UpdateWindow):
                w.deiconify(); w.lift(); w.focus_force()
                return
        if update_state()[0] != "running":
            if not messagebox.askyesno(
                    APP_NAME, "Update everything on this Pi now?\n\n"
                              "• Raspberry Pi OS and all installed programs, including Tailscale\n"
                              "• Firmware\n"
                              "• The Shelf, the local AI engine and RPi Task Manager itself\n"
                              "• Old versions and leftovers are removed\n\n"
                              "It needs internet and can take 5 to 30 minutes. Keep the Pi plugged in. "
                              "You can keep using the Pi meanwhile.", parent=self):
                return

            def started(rc):
                if rc == 0:
                    UpdateWindow(self)
            self.run_action("Update everything", lambda: run_helper("update", "start", timeout=30), on_done=started)
        else:
            UpdateWindow(self)

    def act_vnc(self):
        self.run_action("Turn on VNC", lambda: run_helper("vnc-enable"))

    def act_autostart(self):
        try:
            if self.autostart_var.get():
                os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
                with open(AUTOSTART_FILE, "w") as f:
                    f.write("[Desktop Entry]\nType=Application\nName=RPi400 Task Manager\n"
                            f"Exec={LAUNCHER}\nIcon=utilities-system-monitor\nX-GNOME-Autostart-enabled=true\n")
                self.log("Task Manager will open when the desktop starts.", ok=True)
            else:
                if os.path.exists(AUTOSTART_FILE):
                    os.remove(AUTOSTART_FILE)
                self.log("Task Manager won't open by itself anymore.", ok=True)
        except OSError as e:
            self.log(f"Couldn't change startup setting: {e}", ok=False)

    def act_power(self, what):
        word = "Reboot" if what == "reboot" else "Shut down"
        if messagebox.askyesno(APP_NAME, f"{word} the Pi now?", parent=self):
            self.run_action(word, lambda: run_helper(what))

    # ---------- periodic updates
    def fast_tick(self):
        try:
            cur = psutil.net_io_counters(pernic=True)
            for group in (self.header_lights, self.card_lights):
                for n, w in list(group.items()):
                    c, p = cur.get(n), self.fast_prev.get(n)
                    up = read_file(f"/sys/class/net/{n}/carrier") == "1"
                    rx = bool(c and p and c.bytes_recv > p.bytes_recv)
                    tx = bool(c and p and c.bytes_sent > p.bytes_sent)
                    w.set_state(up, rx, tx)
            self.fast_prev = cur
        except Exception:
            pass
        self.after(200, self.fast_tick)

    def proc_tick(self):
        if self._on_tab_idx() == 1:
            try:
                self._refresh_procs()
            except Exception as e:  # noqa
                self.status(f"process list: {e}")
        self.after(2000, self.proc_tick)

    def tick(self):
        sd = self.slow.get()
        if "ap_on" in sd:
            try:
                self.visitors.update(sd.get("clients", []), sd["ap_on"], time.time())
            except Exception as e:  # noqa
                self.status(f"visitor log: {e}")
        n = len(self.visitors.active)
        self.hdr_people.config(text=f"👥 {n} on hotspot" if sd.get("ap_on") else "👥 hotspot off",
                               fg=C["green"] if n else C["fg"])
        for fn in (self._update_overview, self._update_network, self._update_tools, self._update_header,
                   self._update_ai):
            try:
                fn(sd)
            except Exception as e:  # noqa
                self.status(f"{fn.__name__}: {e}")
        self.after(1000, self.tick)

    def _update_header(self, sd):
        ips = [a for _, a in ipv4_addrs()]
        self.hdr_ip.config(text="  ·  ".join(ips) if ips else "no network")
        if not sd.get("bt_present", True):
            self.hdr_bt.set(C["red"], "Bluetooth: no adapter")
        elif not sd.get("bt_powered"):
            self.hdr_bt.set(C["red"], "Bluetooth off")
        elif sd.get("bt_connected"):
            names = sd["bt_connected"]
            label = ", ".join(names)
            self.hdr_bt.set(C["green"], "ᛒ " + (label if len(label) <= 14 else label[:13] + "…"))
        else:
            self.hdr_bt.set(C["amber"], "BT: nothing connected")

    def _update_overview(self, sd):
        cpu = psutil.cpu_percent()
        per = psutil.cpu_percent(percpu=True)
        self.g_cpu.set(cpu, sub=f"{self.ncpu} cores")
        self.cpu_graph.push(cpu)
        self.core_bars.set(per)

        fr = psutil.cpu_freq()
        if fr:
            if fr.max:
                self.g_clk.vmax = max(fr.max, 1000)
            self.g_clk.set(fr.current, text=f"{fr.current:.0f}",
                           sub=f"max {fr.max:.0f}" if fr.max else "MHz")
        t = cpu_temp()
        self.g_temp.set(t, sub="limit 80°C" if t is not None else "")

        vm = psutil.virtual_memory()
        self.g_ram.set(vm.percent, sub=fmt_pair(vm.used, vm.total))
        sw = psutil.swap_memory()
        if sw.total:
            self.g_swap.set(sw.percent, sub=fmt_pair(sw.used, sw.total))
        else:
            self.g_swap.set(None, sub="no swap")
        du = psutil.disk_usage("/")
        free_gb = du.free / 1024 ** 3
        self.g_disk.set(du.percent, sub=f"{free_gb:.0f} GB free" if free_gb >= 10 else f"{fmt_bytes(du.free)} free")

        v = sd.get("vcore")
        self.v_vcore.config(text=f"{v:.3f} V" if v else ("—" if HAS_VCGEN else "vcgencmd not found"))
        a = sd.get("arm_hz")
        self.v_arm.config(text=f"{a / 1e6:.0f} MHz" if a else "—")
        gov = sd.get("governor") or "—"
        self.v_gov.config(text={"powersave": "Battery saver", "ondemand": "Balanced",
                                "performance": "Full speed", "schedutil": "Balanced (schedutil)"}.get(gov, gov))
        self.v_oc.config(text=sd.get("oc", "—"))
        st = sd.get("ssd_temp")
        self.v_ssd.config(text=f"{st:.0f} °C" if st is not None else "no NVMe SSD",
                          fg=C["red"] if st and st >= 70 else C["amber"] if st and st >= 60 else C["fg"])
        fan = sd.get("fan_rpm")
        self.v_fan.config(text=(f"{fan} RPM" if fan else "stopped") if fan is not None else "none (passive cooling)")
        if IS_PI5:
            vin, watts, ma = sd.get("vin"), sd.get("watts"), sd.get("psu_ma")
            self.g_vin.set(vin, text=f"{vin:.2f}V" if vin else None, sub="USB-C in" if vin else "no reading")
            self.g_pwr.set(watts, text=f"{watts:.1f}W" if watts else None, sub="estimated" if watts else "")
            if ma:
                full = ma >= 5000
                self.v_psu.config(text=(f"{ma / 1000:.0f} A · USB ports full power" if full
                                        else f"{ma / 1000:.0f} A · USB ports limited"),
                                  fg=C["fg"] if full else C["amber"])
            else:
                self.v_psu.config(text="—")

        th = sd.get("throttled")
        if th is None:
            for w in self.l_now + self.l_past:
                w.set(None)
            self.power_hint.config(text="Power readings appear once the Pi firmware tools respond.")
        else:
            self.l_now[0].set(C["red"] if th & 0x1 else C["green"], "Under-voltage!" if th & 0x1 else "Power OK")
            for i, bit in ((1, 0x4), (2, 0x2), (3, 0x8)):
                self.l_now[i].set(C["red"] if th & bit else C["green"])
            for i, bit in enumerate((0x10000, 0x40000, 0x20000, 0x80000)):
                self.l_past[i].set(C["amber"] if th & bit else C["off"])
            if th & 0x1:
                hint = ("⚠ The supply voltage is too low right now. On a battery bank: use a short, thick "
                        f"USB-C cable and a port rated {'5' if IS_PI5 else '3'} A, or charge the bank.")
            elif th & 0x10000:
                hint = ("Voltage dipped at some point since boot. Common on battery banks under load — "
                        "watch it when the CPU is busy.")
            else:
                hint = ("Power has been solid since boot." + ("" if IS_PI5 else
                        " (This Pi can't measure its input voltage, only warn when it drops too low.)"))
            ma = sd.get("psu_ma")
            if IS_PI5 and ma and ma < 5000 and not th & 0x1:
                hint += (f" The supply only offers {ma / 1000:.0f} A, so USB ports are limited to 600 mA. That's fine "
                         "for a keyboard, mouse or USB stick, but not for power-hungry drives.")
            self.power_hint.config(text=hint)

        up = time.time() - psutil.boot_time()
        self.sys["Uptime"].config(text=fmt_uptime(up))
        la = os.getloadavg()
        self.sys["Load (1/5/15)"].config(text=f"{la[0]:.2f}  {la[1]:.2f}  {la[2]:.2f}")
        self.sys["Processes"].config(text=str(len(psutil.pids())))
        dio = psutil.disk_io_counters()
        if dio and self.prev_disk:
            self.sys["Disk read"].config(text=fmt_rate(max(0, dio.read_bytes - self.prev_disk.read_bytes)))
            self.sys["Disk write"].config(text=fmt_rate(max(0, dio.write_bytes - self.prev_disk.write_bytes)))
        self.prev_disk = dio

        self.l_vnc.set(C["green"] if sd.get("vnc") else C["red"])
        self.l_ssh.set(C["green"] if sd.get("ssh") else C["off"])
        self.l_ap.set(C["green"] if sd.get("ap_on") else C["off"],
                      f"Hotspot ({len(sd.get('clients', []))} connected)" if sd.get("ap_on") else "Hotspot")
        if "fallback" in sd:
            self.l_fallback.set(C["green"] if sd["fallback"] else C["off"])
        self.l_web.set(C["green"] if sd.get("web") else C["off"],
                       f"Web page :{sd.get('web_port', 8090)}" if sd.get("web") else "Web page")
        vpn = sd.get("vpn") or {}
        self.l_vpn.set(C["green"] if vpn.get("state") == "Running" else C["amber"] if vpn.get("state") == "NeedsLogin"
                       else C["off"], f"Tailscale {vpn['ip']}" if vpn.get("state") == "Running" else "Tailscale")

    def _update_network(self, sd):
        now = time.time()
        cur = psutil.net_io_counters(pernic=True)
        dt = max(0.2, now - self.prev_net_t)
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        nics = sorted(n for n in cur if n != "lo" and not n.startswith(
            ("docker", "veth", "br-", "virbr", "ifb", "dummy", "sit", "ip6tnl", "tunl", "gre", "erspan", "p2p-dev")))
        if nics != sorted(self.cards):
            self._rebuild_cards(nics)
        trx = ttx = 0.0
        for n in nics:
            c = cur[n]
            p = self.prev_net.get(n, c)
            rx = max(0, c.bytes_recv - p.bytes_recv) / dt
            tx = max(0, c.bytes_sent - p.bytes_sent) / dt
            trx += rx
            ttx += tx
            lab = self.cards[n]
            ip = next((a.address for a in addrs.get(n, []) if a.family == socket.AF_INET), "no address")
            lab["ip"].config(text=f"IP  {ip}")
            lab["rate"].config(text=f"↓ {fmt_rate(rx)}     ↑ {fmt_rate(tx)}")
            lab["total"].config(text=f"Since boot: ↓ {fmt_bytes(c.bytes_recv)}  ↑ {fmt_bytes(c.bytes_sent)}")
            carrier = read_file(f"/sys/class/net/{n}/carrier") == "1"
            if n.startswith("wl"):
                if sd.get("ap_on"):
                    fq = sd.get("ap_freq")
                    bandtxt = (f" on {'5' if fq[1] > 4000 else '2.4'} GHz" if fq else "")
                    extra = f"Hosting hotspot {AP_SSID}{bandtxt} · {len(sd.get('clients', []))} connected"
                elif sd.get("ssid"):
                    sig = sd.get("signal")
                    extra = f"Joined “{sd['ssid']}”" + (f" · {sig} dBm ({signal_words(sig)})" if sig is not None else "")
                else:
                    extra = "Not connected"
            else:
                sp = stats[n].speed if n in stats else 0
                extra = (f"Cable connected · {sp} Mb/s" if sp else "Cable connected") if carrier else "No cable"
            lab["extra"].config(text=extra)
        self.prev_net, self.prev_net_t = cur, now
        self.net_graph.push(trx, ttx)

        clients = sd.get("clients", [])
        existing = self.client_tree.get_children()
        want = [c["mac"] for c in clients]
        if list(existing) != want:
            self.client_tree.delete(*existing)
            for mac in want:
                self.client_tree.insert("", "end", iid=mac)
        for c in clients:
            self.client_tree.item(c["mac"], values=(c.get("ip") or "…", c["mac"], c.get("name") or "—"))

    def _update_tools(self, sd):
        if sd.get("ap_on"):
            self.t_ap.set(C["green"], f"Hotspot ON · {len(sd.get('clients', []))} connected")
            self.ap_btn.config(text="⏹  Stop hotspot   (Ctrl+H)")
        else:
            self.t_ap.set(C["off"], "Hotspot off" if sd.get("nm", True) else "NetworkManager not found")
            self.ap_btn.config(text="📶  Start hotspot   (Ctrl+H)")
        if "fallback" in sd:
            self.t_fallback.set(C["green"] if sd["fallback"] else C["off"])
        band = sd.get("ap_band", "bg")
        if time.time() - self._band_busy > 5 and "ap_band" in sd:
            self.band_switch.set(band == "a")
        self.band_24.config(fg=C["fg"] if band != "a" else C["dim"])
        self.band_switch.lbl.config(fg=C["fg"] if band == "a" else C["dim"])
        freq = sd.get("ap_freq")
        if sd.get("ap_on") and freq:
            live = "5 GHz" if freq[1] > 4000 else "2.4 GHz"
            text = f"Broadcasting on {live}, channel {freq[0]}."
            if (band == "a") != (freq[1] > 4000):
                text += f" (Set to {'5' if band == 'a' else '2.4'} GHz; 5 GHz wasn't allowed here, so it fell back.)"
        else:
            text = ("5 GHz: faster, shorter range, not seen by older devices." if band == "a"
                    else "2.4 GHz: longest range, works with every device.")
        self.band_info.config(text=text)
        if sd.get("shelf_url"):
            self.ap_vals["The Shelf"].config(text=sd["shelf_url"])
        st, t0, t1, rb, _ = update_state()
        if st == "running":
            self.t_update.config(text=f"Updating now… ({fmt_dur(time.time() - t0)} so far). Press the button to watch.",
                                 fg=C["amber"])
        elif st == "done":
            self.t_update.config(text=f"Last updated {time.strftime('%b %d at %I:%M %p', time.localtime(t1))}."
                                 + (" Restart the Pi to finish." if rb else ""), fg=C["amber"] if rb else C["dim"])
        elif st == "failed":
            self.t_update.config(text="The last update didn't finish. Press the button to try again.", fg=C["red"])
        else:
            self.t_update.config(text="System, firmware, The Shelf, the AI and this app, in one go.", fg=C["dim"])
        self.t_vnc.set(C["green"] if sd.get("vnc") else C["red"],
                       "VNC running on port 5900" if sd.get("vnc") else "VNC not running")
        if "web_enabled" in sd and time.time() > self._web_busy:
            self.web_switch.set(sd["web_enabled"])
            port = sd.get("web_port", 8090)
            if sd.get("web"):
                addrs = " or ".join(f"http://{ip}:{port}" for ip in local_ips()[:2]) or f"this Pi's address, port {port}"
                self.web_info.config(text=f"On. Open {addrs} on any phone or PC on the same network.", fg=C["green"])
            elif sd["web_enabled"]:
                self.web_info.config(text="Starting…", fg=C["amber"])
            else:
                self.web_info.config(text="Off. Anyone can look once it's on; the controls need your web password.",
                                     fg=C["dim"])
        vpn = sd.get("vpn")
        if vpn is not None and time.time() > self._vpn_busy:
            st = vpn.get("state")
            self.vpn_switch.set(st == "Running")
            user = os.environ.get("USER") or "pi"
            if not vpn.get("installed"):
                text, col = "Off. Flip it on to set up (needs internet once, free for personal use).", C["dim"]
            elif st == "Running":
                text, col = (f"On · this Pi is {vpn['ip']}" + (f" ({vpn['name'].split('.')[0]})" if vpn.get("name") else "")
                             + f". From your phone or PC on Tailscale: VNC {vpn['ip']}:5900 · ssh {user}@{vpn['ip']}"
                             + (f" · web http://{vpn['ip']}:{sd.get('web_port', 8090)}" if sd.get("web") else "")), C["green"]
            elif st == "NeedsLogin":
                text, col = "Needs a one-time sign-in. Flip it on to get the sign-in code.", C["amber"]
            else:
                text, col = "Off. You're still signed in, so switching on is instant.", C["dim"]
            self.vpn_info.config(text=text, fg=col)
        self.t_oc.config(text=f"Current: {sd.get('oc', '—')}" + (" · graphics boost" if IS_PI5 and read_gpu_boost() else ""))
        gov = sd.get("governor")
        if gov == "schedutil":
            gov = "ondemand"  # both count as "Balanced"
        if gov and time.time() - self._gov_touched > 6 and self.gov_var.get() != gov:
            self.gov_var.set(gov)


def pair_window():
    """Ctrl+Alt+B: just the Bluetooth window, usable with no mouse, even if the Task Manager is closed."""
    lock_path = os.path.expanduser("~/.cache/rpi400-taskmgr-pair.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return   # already open; don't stack a second window
    root = tk.Tk()
    root.withdraw()
    apply_style(root)
    BtPairDialog(root)
    root.mainloop()


# ---------- one copy only
# If the Task Manager is started again while it's already open (two startup entries, a double-click,
# Ctrl+Shift+Esc), the new copy asks the open one to come to the front and quits, instead of opening
# a second window.
_INSTANCE_LOCK = None


def _instance_paths():
    run = os.environ.get("XDG_RUNTIME_DIR") or os.path.expanduser("~/.cache")
    os.makedirs(run, exist_ok=True)
    return os.path.join(run, "rpi400-taskmgr.lock"), os.path.join(run, "rpi400-taskmgr.sock")


def claim_single_instance():
    """True if this is the only copy running. Otherwise wakes the open copy and returns False."""
    global _INSTANCE_LOCK
    lock_path, sock_path = _instance_paths()
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(sock_path)
            s.sendall(b"show\n")
            s.close()
        except OSError:
            pass
        return False
    _INSTANCE_LOCK = f      # held until the app closes; the system releases it even after a crash
    return True


def listen_for_second_copy(app):
    """Bring the window to the front whenever another copy is started."""
    _, sock_path = _instance_paths()
    try:
        os.unlink(sock_path)
    except OSError:
        pass
    try:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(4)
    except OSError:
        return
    wake = threading.Event()

    def accept_loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                conn.settimeout(1)
                conn.recv(16)
            except OSError:
                pass
            conn.close()
            wake.set()

    def check():
        if wake.is_set():
            wake.clear()
            try:
                app.deiconify()
                app.lift()
                app.attributes("-topmost", True)
                app.after(400, lambda: app.attributes("-topmost", False))
                app.focus_force()
            except tk.TclError:
                pass
        app.after(400, check)

    threading.Thread(target=accept_loop, daemon=True).start()
    app.after(400, check)


def main():
    if "--version" in sys.argv:
        print(f"{APP_NAME} {VERSION}")
        return
    if "--pair" in sys.argv:
        pair_window()
        return
    if not claim_single_instance():
        return          # already open; that window was brought to the front
    app = App()
    listen_for_second_copy(app)
    app.mainloop()


if __name__ == "__main__":
    main()
