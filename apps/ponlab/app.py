#!/usr/bin/env python3
"""
PON Lab — outage simulator for AMI meter testing (Itron Riva / AMI 2.0)

Runs on a Raspberry Pi, drives relay channels that interrupt line power to
meters under test, and logs everything needed to answer:
  * Does the meter send a Power Outage Notification (PON / last gasp)?
  * Does it stay meshed / keep routing while unpowered, and for how long?
  * How long do the supercaps hold the radio up?
  * How long after restore does it rejoin the mesh (power restore notification)?

Runs in SIMULATION mode automatically when no GPIO is present, so you can try
the dashboard on any PC.
"""
import csv
import io
import socket
import json
import os
import threading
import time
import uuid
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory, Response

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("PONLAB_CONFIG") or os.path.join(BASE, "config.json")
DATA_DIR = os.environ.get("PONLAB_DATA") or os.path.join(BASE, "data")
LOG_PATH = os.path.join(DATA_DIR, "events.jsonl")
os.makedirs(DATA_DIR, exist_ok=True)

with open(CONFIG_PATH) as f:
    CONFIG = json.load(f)

VERSION = "1.1"
NODE_NAME = CONFIG.get("node_name") or socket.gethostname()
API_TOKEN = os.environ.get("PONLAB_TOKEN") or CONFIG.get("api_token") or ""

# ---------------------------------------------------------------- hardware
SIM = False
try:
    if os.environ.get("PONLAB_SIM") == "1":
        raise ImportError("simulation forced")
    from gpiozero import OutputDevice, DigitalInputDevice  # type: ignore
except Exception:  # no Pi / no gpiozero -> simulate
    SIM = True


class Relay:
    """One relay channel. 'powered' is the meter's state, not the coil's."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.id = cfg["id"]
        self.powered = True
        self.sense = None
        if not SIM:
            # active_high depends on the relay board (most cheap boards are active-low)
            self.dev = OutputDevice(cfg["gpio"], active_high=CONFIG.get("relay_active_high", False),
                                    initial_value=False)
            if cfg.get("sense_gpio") is not None:
                self.sense = DigitalInputDevice(cfg["sense_gpio"], pull_up=True)

    def set_power(self, on: bool):
        # Wiring assumption (recommended, fail-safe): meter feed goes through the
        # relay's NORMALLY-CLOSED contact. Energizing the coil = OUTAGE.
        # If the Pi crashes or loses power, the coil drops and the meter is powered.
        if not SIM:
            if CONFIG.get("wiring", "NC") == "NC":
                self.dev.value = 0 if on else 1
            else:  # NO wiring: coil on = power on (not fail-safe)
                self.dev.value = 1 if on else 0
        self.powered = on

    def sense_alive(self):
        if self.sense is None:
            return None
        # Opto / photo sensor pulls low when meter (display/radio LED) is alive
        return not self.sense.value


relays = {c["id"]: Relay(c) for c in CONFIG["channels"]}
lock = threading.RLock()

# ------------------------------------------------------------------- state
state = {
    ch: {
        "outage_started": None,   # epoch seconds
        "restore_at": None,       # scheduled auto-restore
        "sequence": None,         # running sequence info
        "marks": {},              # mark-type -> epoch for the current outage
        "last_result": None,
        "sense_dark_at": None,
    }
    for ch in relays
}
events = []  # in-memory copy; also appended to events.jsonl

if os.path.exists(LOG_PATH):
    with open(LOG_PATH) as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except Exception:
                pass


def log(channel, kind, note="", **extra):
    ev = {"id": uuid.uuid4().hex[:8], "t": time.time(), "channel": channel,
          "kind": kind, "note": note, **extra}
    with lock:
        events.append(ev)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(ev) + "\n")
    return ev


# ------------------------------------------------------------- operations
def cut(ch, duration=None, reason="manual", **extra):
    with lock:
        r = relays[ch]
        if not r.powered:
            return
        r.set_power(False)
        s = state[ch]
        s["outage_started"] = time.time()
        s["marks"] = {}
        s["sense_dark_at"] = None
        max_s = CONFIG.get("max_outage_seconds", 3600)
        dur = min(duration, max_s) if duration else max_s
        s["restore_at"] = s["outage_started"] + dur
        log(ch, "power_off", reason, planned_seconds=duration, **extra)


def restore(ch, reason="manual", **extra):
    with lock:
        r = relays[ch]
        if r.powered:
            return
        r.set_power(True)
        s = state[ch]
        start = s["outage_started"]
        dur = time.time() - start if start else None
        result = {"outage_seconds": round(dur, 2) if dur else None}
        for k, t in s["marks"].items():
            result[k + "_s"] = round(t - start, 2)
        if s["sense_dark_at"]:
            result["sense_holdup_s"] = round(s["sense_dark_at"] - start, 2)
        s["last_result"] = result
        s["outage_started"] = None
        s["restore_at"] = None
        log(ch, "power_on", reason, **result, **extra)


def mark(ch, kind, note=""):
    with lock:
        s = state[ch]
        start = s["outage_started"]
        rel = round(time.time() - start, 2) if start else None
        if start:
            s["marks"].setdefault(kind, time.time())
        return log(ch, "mark:" + kind, note, seconds_into_outage=rel)


def run_sequence(ch, steps, name, **extra):
    """steps: list of {"off": seconds, "on": seconds}"""
    seq_id = uuid.uuid4().hex[:6]
    with lock:
        state[ch]["sequence"] = {"id": seq_id, "name": name, "step": 0,
                                 "total": len(steps), "phase": "starting"}
    log(ch, "sequence_start", name, steps=steps, **extra)

    def worker():
        for i, st in enumerate(steps):
            seq = state[ch]["sequence"]
            if not seq or seq["id"] != seq_id:
                return  # aborted
            seq.update(step=i + 1, phase="off", phase_ends=time.time() + st["off"])
            cut(ch, st["off"], reason=f"{name} step {i+1}/{len(steps)}", **extra)
            if not _sleep_while(ch, seq_id, st["off"]):
                return
            restore(ch, reason=f"{name} step {i+1}/{len(steps)}", **extra)
            seq.update(phase="on", phase_ends=time.time() + st["on"])
            if not _sleep_while(ch, seq_id, st["on"]):
                return
        with lock:
            state[ch]["sequence"] = None
        log(ch, "sequence_done", name)

    threading.Thread(target=worker, daemon=True).start()


def _sleep_while(ch, seq_id, secs):
    end = time.time() + secs
    while time.time() < end:
        seq = state[ch]["sequence"]
        if not seq or seq["id"] != seq_id:
            return False
        time.sleep(0.1)
    return True


def abort_sequence(ch):
    with lock:
        if state[ch]["sequence"]:
            log(ch, "sequence_abort", state[ch]["sequence"]["name"])
        state[ch]["sequence"] = None
    restore(ch, reason="sequence aborted")


def run_at(at, fn):
    """Run fn at epoch time `at` on this Pi's clock (now if missing/past).
    Coarse sleep, then a short spin so many Pis switch within a few ms."""
    if not at or at <= time.time():
        fn()
        return
    def waiter():
        while True:
            left = at - time.time()
            if left <= 0:
                break
            time.sleep(left - 0.02 if left > 0.03 else 0.0005)
        fn()
    threading.Thread(target=waiter, daemon=True).start()


def do_action(a, run=None):
    ch = a.get("ch")
    if ch not in relays:
        return
    extra = {"run": run} if run else {}
    op = a.get("op")
    if op == "off":
        if not state[ch]["sequence"]:
            cut(ch, a.get("seconds"), reason=a.get("reason", "fleet"), **extra)
    elif op == "on":
        if state[ch]["sequence"]:
            abort_sequence(ch)
        else:
            restore(ch, reason=a.get("reason", "fleet"), **extra)
    elif op == "sequence":
        if not state[ch]["sequence"] and relays[ch].powered:
            run_sequence(ch, a["steps"], a.get("name", "fleet"), **extra)
    elif op == "mark":
        mark(ch, a.get("kind", "note"), a.get("note", ""))


def watchdog():
    """Auto-restore timed outages, enforce max outage, watch sense inputs."""
    while True:
        now = time.time()
        for ch, r in relays.items():
            s = state[ch]
            if not r.powered and s["restore_at"] and now >= s["restore_at"]:
                if not s["sequence"]:
                    restore(ch, reason="timer / max-outage limit")
            if not r.powered and s["sense_dark_at"] is None and r.sense_alive() is False:
                s["sense_dark_at"] = now
                log(ch, "sense_dark", "sense input went dark",
                    seconds_into_outage=round(now - s["outage_started"], 2))
        time.sleep(0.05)


threading.Thread(target=watchdog, daemon=True).start()

# ------------------------------------------------------------------- web
app = Flask(__name__, static_folder="static")


@app.before_request
def check_token():
    # Optional shared token so only your fleet server / people with it can switch relays
    if API_TOKEN and request.method == "POST" and request.path.startswith("/api/"):
        if request.headers.get("X-PonLab-Token") != API_TOKEN:
            return jsonify(ok=False, error="token required"), 401


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-PonLab-Token"
    return resp


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/pl.js")
def probe_js():
    # lets the My Apps menu tile find a running PON Lab without opening the wrong app
    port = request.host.split(":")[-1] if ":" in request.host else "80"
    return Response(f"window.__ponlab=(window.__ponlab||[]).concat([{port}]);",
                    mimetype="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/api/state")
def api_state():
    now = time.time()
    out = []
    with lock:
        for c in CONFIG["channels"]:
            ch = c["id"]
            r, s = relays[ch], state[ch]
            out.append({
                **c,
                "powered": r.powered,
                "sense": r.sense_alive(),
                "outage_elapsed": round(now - s["outage_started"], 1) if s["outage_started"] else None,
                "restore_in": round(s["restore_at"] - now, 1) if s["restore_at"] else None,
                "marks": {k: round(v - s["outage_started"], 2) for k, v in s["marks"].items()} if s["outage_started"] else {},
                "sequence": s["sequence"] and {**s["sequence"],
                                               "phase_left": round(s["sequence"].get("phase_ends", now) - now, 1)},
                "last_result": s["last_result"],
            })
    since = request.args.get("since", type=float)
    evs = [e for e in events if e["t"] > since][-500:] if since else events[-200:]
    return jsonify({"sim": SIM, "now": now, "channels": out, "node": NODE_NAME,
                    "version": VERSION, "token_required": bool(API_TOKEN),
                    "presets": CONFIG.get("presets", []),
                    "mark_types": CONFIG.get("mark_types", []),
                    "max_outage_seconds": CONFIG.get("max_outage_seconds", 3600),
                    "events": evs[::-1]})


@app.get("/api/time")
def api_time():
    return jsonify(t=time.time(), node=NODE_NAME, version=VERSION)


@app.post("/api/batch")
def api_batch():
    """Used by PON Fleet: {"at": epoch, "run": "id", "actions": [{"ch","op",...}]}
    All actions fire together at `at` (this Pi's clock)."""
    d = request.get_json(silent=True) or {}
    acts = d.get("actions", [])
    run = d.get("run")
    run_at(d.get("at"), lambda: [do_action(a, run) for a in acts])
    return jsonify(ok=True, scheduled_for=d.get("at"), now=time.time(), count=len(acts))


@app.post("/api/<ch>/off")
def api_off(ch):
    d = request.get_json(silent=True) or {}
    run_at(d.get("at"), lambda: cut(ch, d.get("seconds"), reason=d.get("reason", "manual")))
    return jsonify(ok=True)


@app.post("/api/<ch>/on")
def api_on(ch):
    if state[ch]["sequence"]:
        abort_sequence(ch)
    else:
        restore(ch)
    return jsonify(ok=True)


@app.post("/api/<ch>/mark")
def api_mark(ch):
    d = request.get_json(silent=True) or {}
    return jsonify(mark(ch, d.get("kind", "note"), d.get("note", "")))


@app.post("/api/<ch>/sequence")
def api_seq(ch):
    d = request.get_json(silent=True) or {}
    if state[ch]["sequence"]:
        return jsonify(ok=False, error="sequence already running"), 409
    run_sequence(ch, d["steps"], d.get("name", "custom"))
    return jsonify(ok=True)


@app.post("/api/all/restore")
def api_restore_all():
    for ch in relays:
        state[ch]["sequence"] = None
        restore(ch, reason="RESTORE ALL")
    return jsonify(ok=True)


@app.post("/api/note")
def api_note():
    d = request.get_json(silent=True) or {}
    return jsonify(log(d.get("channel", "-"), "note", d.get("note", "")))


@app.get("/api/export.csv")
def export_csv():
    buf = io.StringIO()
    cols = ["time", "channel", "meter", "kind", "note", "seconds_into_outage", "outage_seconds",
            "planned_seconds"]
    extra = sorted({k for e in events for k in e if k.endswith("_s") and k != "outage_seconds"})
    w = csv.writer(buf)
    w.writerow(cols + extra)
    names = {c["id"]: c.get("meter", "") for c in CONFIG["channels"]}
    for e in events:
        w.writerow([datetime.fromtimestamp(e["t"]).isoformat(timespec="milliseconds"),
                    e["channel"], names.get(e["channel"], ""), e["kind"], e.get("note", ""),
                    e.get("seconds_into_outage", ""), e.get("outage_seconds", ""),
                    e.get("planned_seconds", "")] + [e.get(k, "") for k in extra])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=ponlab_events.csv"})


@app.post("/api/log/clear")
def clear_log():
    with lock:
        events.clear()
        open(LOG_PATH, "w").close()
    return jsonify(ok=True)


if __name__ == "__main__":
    print(f"PON Lab {VERSION} '{NODE_NAME}' starting ({'SIMULATION' if SIM else 'GPIO'} mode)"
          + (" with access token" if API_TOKEN else ""))
    port = int(os.environ.get("PONLAB_PORT", CONFIG.get("port", 8080)))
    if "--open" in os.sys.argv:
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    app.run(host="0.0.0.0", port=port, threaded=True)
