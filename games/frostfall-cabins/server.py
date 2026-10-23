#!/usr/bin/env python3
"""
Frostfall Cabins - LAN multiplayer server
-----------------------------------------
Zero dependencies: just Python 3.8+ (already on every Raspberry Pi).

    python3 server.py                 # default 12-minute night, port 8765
    python3 server.py --minutes 20    # longer night
    python3 server.py --port 9000

Everyone on the same Wi-Fi opens  http://<this-computer's-IP>:8765  in a browser.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import math
import mimetypes
import os
import random
import socket
import struct
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "public")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# ----------------------------------------------------------------- world ---
# Game clock is in "game minutes" after 18:00. 720 = 06:00 dawn.
GALE_START = 480   # 02:00
GALE_END = 690     # 05:30
DAWN = 720

CABIN_W, CABIN_D = 10.0, 8.0          # interior footprint (local x, local z)
STOVE = (-3.2, -2.7)
WOODBOX = (-4.0, 0.6)
PANTRY = (3.9, -2.9)
CANDLES = [(1.5, 0.55), (2.5, 0.55), (4.2, 1.6)]
WOODBOX_MAX = 12
CARRY_MAX = 4
FUEL_PER_LOG = 18.0
REACH = 4.2

CABIN_SPECS = [
    # name, x, z, region
    ("Summit Lookout", 0.0, -205.0, "summit"),
    ("Frozen Lake",   -168.0, -52.0, "lake"),
    ("Birch Glade",    178.0, -78.0, "birch"),
    ("Old Mill",      -118.0, 168.0, "mill"),
    ("Hollow End",     146.0, 158.0, "hollow"),
]


def cabin_rot(x, z):
    # local +z (the door) faces the middle of the map
    return math.atan2(-x, -z)


def to_world(cab, lx, lz):
    c, s = math.cos(cab["rot"]), math.sin(cab["rot"])
    return cab["x"] + lx * c + lz * s, cab["z"] - lx * s + lz * c


def to_local(cab, wx, wz):
    dx, dz = wx - cab["x"], wz - cab["z"]
    c, s = math.cos(cab["rot"]), math.sin(cab["rot"])
    return dx * c - dz * s, dx * s + dz * c


def dist2(ax, az, bx, bz):
    return (ax - bx) ** 2 + (az - bz) ** 2


class Game:
    def __init__(self, night_seconds):
        self.night_seconds = night_seconds
        self.seed = random.randint(1, 999999)
        self.players = {}          # id -> dict
        self.clients = {}          # id -> Client
        self.next_id = 1
        self.events = []
        self.chat = []
        self.round_no = 0
        self.new_round()

    # -- setup ---------------------------------------------------------------
    def new_round(self):
        self.round_no += 1
        self.minutes = 0.0
        self.phase = "night"       # night | dawn
        self.dawn_timer = 0.0
        self.summary = None
        self.cabins = []
        for i, (name, x, z, region) in enumerate(CABIN_SPECS):
            self.cabins.append({
                "i": i, "name": name, "x": x, "z": z, "region": region,
                "rot": cabin_rot(x, z),
                "fuel": 0.0, "wood": 2, "candles": [0.0, 0.0, 0.0],
                "cans": 3, "pot": -1.0,   # -1 empty, 0..20 heating, >=20 hot
                "litBy": None,
            })
        self.logs = {}
        self.log_id = 1
        rng = random.Random(self.seed + self.round_no)
        for _ in range(70):
            self.spawn_log(rng)
        self.rng = rng
        self.respawn_acc = 0.0
        for p in self.players.values():
            self.reset_player(p, spawn=True)
        self.emit("Night %d falls on the mountain. The gale hits at 2:00 AM." % self.round_no)

    def spawn_log(self, rng):
        for _ in range(30):
            # cluster logs around cabins and along the routes between them
            if rng.random() < 0.6:
                c = rng.choice(CABIN_SPECS)
                a = rng.random() * math.tau
                r = 16 + rng.random() * 55
                x, z = c[1] + math.cos(a) * r, c[2] + math.sin(a) * r
            else:
                a = rng.random() * math.tau
                r = 20 + rng.random() * 220
                x, z = math.cos(a) * r, math.sin(a) * r
            if x * x + z * z > 280 ** 2:
                continue
            if any(dist2(x, z, c[1], c[2]) < 12 ** 2 for c in CABIN_SPECS):
                continue
            if dist2(x, z, -222, -8) < 44 ** 2:   # not on the frozen lake
                continue
            self.logs[self.log_id] = {"id": self.log_id, "x": round(x, 2), "z": round(z, 2),
                                      "r": round(rng.random() * math.pi, 2)}
            self.log_id += 1
            return

    def reset_player(self, p, spawn=False):
        p["warmth"] = 100.0
        p["hunger"] = 85.0
        p["carry"] = 0
        p["can"] = 0          # 0 none, 1 cold can in hand
        p["frozen"] = 0.0
        p["inside"] = -1
        if spawn:
            a = random.random() * math.tau
            p["x"], p["z"] = math.cos(a) * 4, 30 + math.sin(a) * 4
            p["teleport"] = True

    # -- helpers -------------------------------------------------------------
    def emit(self, text):
        self.events.append(text)

    def gale(self):
        return GALE_START <= self.minutes < GALE_END

    def gale_strength(self):
        m = self.minutes
        if m < GALE_START - 20 or m >= GALE_END + 15:
            return 0.0
        if m < GALE_START:
            return (m - (GALE_START - 20)) / 20 * 0.35
        if m < GALE_START + 15:
            return 0.35 + (m - GALE_START) / 15 * 0.65
        if m < GALE_END:
            return 1.0
        return max(0.0, 1.0 - (m - GALE_END) / 15)

    def inside_cabin(self, x, z):
        for cab in self.cabins:
            lx, lz = to_local(cab, x, z)
            if abs(lx) < CABIN_W / 2 and abs(lz) < CABIN_D / 2:
                return cab["i"]
        return -1

    def near(self, p, cab, spot):
        wx, wz = to_world(cab, *spot)
        return dist2(p["x"], p["z"], wx, wz) <= REACH ** 2

    # -- input ---------------------------------------------------------------
    def add_player(self, client, name, color):
        pid = self.next_id
        self.next_id += 1
        name = (name or "Hiker").strip()[:16] or "Hiker"
        color = color if isinstance(color, str) and len(color) <= 9 else "#e8a33d"
        p = {"id": pid, "name": name, "color": color, "x": 0.0, "y": 0.0, "z": 30.0,
             "ry": 0.0, "freezes": 0, "logsIn": 0}
        self.reset_player(p, spawn=True)
        self.players[pid] = p
        self.clients[pid] = client
        self.emit("%s arrived at the trailhead." % name)
        return pid

    def remove_player(self, pid):
        p = self.players.pop(pid, None)
        self.clients.pop(pid, None)
        if p:
            self.emit("%s headed home." % p["name"])

    def on_pos(self, p, m):
        if p["frozen"] > 0:
            return
        try:
            x, y, z, ry = float(m["x"]), float(m["y"]), float(m["z"]), float(m["ry"])
        except (KeyError, TypeError, ValueError):
            return
        if not all(map(math.isfinite, (x, y, z, ry))):
            return
        p["x"], p["y"], p["z"], p["ry"] = x, y, z, ry

    def on_act(self, p, m):
        if p["frozen"] > 0 or self.phase != "night":
            return
        a = m.get("a")
        if a == "pickup":
            log = self.logs.get(m.get("id"))
            if log and p["carry"] < CARRY_MAX and dist2(p["x"], p["z"], log["x"], log["z"]) < 4.5 ** 2:
                del self.logs[log["id"]]
                p["carry"] += 1
            return
        if a == "eat":
            return
        try:
            cab = self.cabins[int(m.get("cabin"))]
        except (TypeError, ValueError, IndexError):
            return
        if a == "woodbox" and self.near(p, cab, WOODBOX):
            if p["carry"] > 0:
                n = min(p["carry"], WOODBOX_MAX - cab["wood"])
                cab["wood"] += n
                p["carry"] -= n
                p["logsIn"] += n
            elif cab["wood"] > 0:
                cab["wood"] -= 1
                p["carry"] += 1
        elif a == "stove" and self.near(p, cab, STOVE):
            if cab["pot"] >= 20:                     # hot can ready -> eat it
                cab["pot"] = -1.0
                p["hunger"] = min(100.0, p["hunger"] + 55)
                p["warmth"] = min(100.0, p["warmth"] + 12)
                self.emit("%s ate hot stew at %s." % (p["name"], cab["name"]))
            elif p["can"] and cab["pot"] < 0:        # put can on to heat
                p["can"] = 0
                cab["pot"] = 0.0
            elif p["carry"] > 0 or cab["wood"] > 0:  # feed the fire
                if cab["fuel"] > 100 - FUEL_PER_LOG * 0.5:
                    return
                if p["carry"] > 0:
                    p["carry"] -= 1
                else:
                    cab["wood"] -= 1
                was_out = cab["fuel"] <= 0
                cab["fuel"] = min(100.0, cab["fuel"] + FUEL_PER_LOG)
                p["logsIn"] += 1
                if was_out:
                    self.emit("%s lit the stove at %s." % (p["name"], cab["name"]))
        elif a == "pantry" and self.near(p, cab, PANTRY):
            if not p["can"] and cab["cans"] > 0:
                cab["cans"] -= 1
                p["can"] = 1
        elif a == "candle":
            try:
                i = int(m.get("i"))
                spot = CANDLES[i]
            except (TypeError, ValueError, IndexError):
                return
            if self.near(p, cab, spot):
                if cab["candles"][i] > 0:
                    cab["candles"][i] = 0.0
                elif cab["fuel"] > 0:   # need a flame from the stove
                    cab["candles"][i] = 240.0

    def on_chat(self, p, m):
        text = str(m.get("text", "")).strip()[:120]
        if text:
            self.broadcast({"t": "chat", "from": p["name"], "color": p["color"], "text": text})

    # -- simulation ----------------------------------------------------------
    def step(self, dt):
        if self.phase == "dawn":
            self.dawn_timer -= dt
            if self.dawn_timer <= 0:
                self.new_round()
            return
        gm = dt * DAWN / self.night_seconds     # game minutes this tick
        self.minutes += gm
        gale = self.gale_strength()

        for cab in self.cabins:
            if cab["fuel"] > 0:
                cab["fuel"] = max(0.0, cab["fuel"] - gm * (0.12 + 0.16 * gale))
                if cab["fuel"] == 0:
                    self.emit("The stove at %s went out!" % cab["name"])
            for i in range(3):
                if cab["candles"][i] > 0:
                    cab["candles"][i] = max(0.0, cab["candles"][i] - gm * (1 + gale))
            if cab["pot"] >= 0 and cab["pot"] < 20 and cab["fuel"] > 0:
                cab["pot"] = min(20.0, cab["pot"] + gm)

        # logs trickle back in, but not during the gale
        if gale < 0.5:
            self.respawn_acc += gm
            while self.respawn_acc > 6:
                self.respawn_acc -= 6
                if len(self.logs) < 70:
                    self.spawn_log(self.rng)

        late = 1.0 if self.minutes > 360 else self.minutes / 360
        for p in self.players.values():
            if p["frozen"] > 0:
                p["frozen"] -= dt
                if p["frozen"] <= 0:
                    self.thaw(p)
                continue
            ci = self.inside_cabin(p["x"], p["z"])
            p["inside"] = ci
            if ci >= 0:
                cab = self.cabins[ci]
                if cab["fuel"] > 0:
                    lit = sum(1 for c in cab["candles"] if c > 0)
                    d = 0.55 + 0.12 * lit
                    if self.near(p, cab, STOVE):
                        d += 0.4
                else:
                    d = -(0.05 + 0.3 * gale)
            else:
                d = -(0.09 + 0.07 * late + 0.75 * gale)
            p["hunger"] = max(0.0, p["hunger"] - gm * 0.14)
            if p["hunger"] <= 0:
                d -= 0.15
            p["warmth"] = max(0.0, min(100.0, p["warmth"] + d * gm))
            if p["warmth"] <= 0:
                p["frozen"] = 10.0
                p["freezes"] += 1
                p["carry"] = 0
                p["can"] = 0
                self.emit("%s froze solid!" % p["name"])

        if self.minutes >= DAWN:
            self.end_round()

    def thaw(self, p):
        # wake up in the warmest cabin, or the trailhead if every stove is out
        lit = [c for c in self.cabins if c["fuel"] > 0]
        p["warmth"], p["hunger"], p["frozen"] = 45.0, max(p["hunger"], 30.0), 0.0
        if lit:
            c = max(lit, key=lambda c: c["fuel"])
            p["x"], p["z"] = to_world(c, 0.0, 1.5)
            self.emit("%s thawed out by the fire at %s." % (p["name"], c["name"]))
        else:
            p["x"], p["z"] = 0.0, 30.0
            self.emit("%s thawed out at the trailhead." % p["name"])
        p["teleport"] = True

    def end_round(self):
        self.phase = "dawn"
        self.dawn_timer = 25.0
        lit = [c["name"] for c in self.cabins if c["fuel"] > 0]
        self.summary = {
            "round": self.round_no,
            "litCabins": lit,
            "players": sorted(
                [{"name": p["name"], "color": p["color"], "freezes": p["freezes"],
                  "logs": p["logsIn"]} for p in self.players.values()],
                key=lambda r: (r["freezes"], -r["logs"])),
        }
        self.emit("Dawn breaks. %d of 5 cabins still burning." % len(lit))
        for p in self.players.values():
            p["freezes"] = 0
            p["logsIn"] = 0

    # -- networking ----------------------------------------------------------
    def world_info(self):
        return {"seed": self.seed, "cabins": [
            {"i": c["i"], "name": c["name"], "x": c["x"], "z": c["z"], "rot": c["rot"],
             "region": c["region"]} for c in self.cabins],
            "galeStart": GALE_START, "galeEnd": GALE_END, "dawn": DAWN,
            "nightSeconds": self.night_seconds}

    def snapshot(self):
        players = []
        for p in self.players.values():
            d = {"id": p["id"], "name": p["name"], "color": p["color"],
                 "x": round(p["x"], 2), "y": round(p["y"], 2), "z": round(p["z"], 2),
                 "ry": round(p["ry"], 2), "w": round(p["warmth"], 1), "h": round(p["hunger"], 1),
                 "c": p["carry"], "can": p["can"], "f": round(p["frozen"], 1), "in": p["inside"]}
            if p.pop("teleport", False):
                d["tp"] = 1
            players.append(d)
        return {
            "t": "state", "m": round(self.minutes, 2), "phase": self.phase,
            "gale": round(self.gale_strength(), 3), "round": self.round_no,
            "dawnIn": round(self.dawn_timer, 1) if self.phase == "dawn" else 0,
            "players": players,
            "cabins": [{"fuel": round(c["fuel"], 1), "wood": c["wood"],
                        "candles": [1 if v > 0 else 0 for v in c["candles"]],
                        "cans": c["cans"], "pot": round(c["pot"], 1)} for c in self.cabins],
            "logs": [[l["id"], l["x"], l["z"], l["r"]] for l in self.logs.values()],
            "events": self.events,
            "summary": self.summary if self.phase == "dawn" else None,
        }

    def broadcast(self, msg):
        data = json.dumps(msg, separators=(",", ":"))
        for c in list(self.clients.values()):
            c.send_text(data)


# ------------------------------------------------------------ websocket ---
class Client:
    def __init__(self, writer):
        self.writer = writer
        self.closed = False

    def send_text(self, text):
        if self.closed:
            return
        try:
            # drop updates for a client that has fallen far behind
            if self.writer.transport.get_write_buffer_size() > 2_000_000:
                return
            payload = text.encode("utf-8")
            n = len(payload)
            if n < 126:
                header = struct.pack("!BB", 0x81, n)
            elif n < 65536:
                header = struct.pack("!BBH", 0x81, 126, n)
            else:
                header = struct.pack("!BBQ", 0x81, 127, n)
            self.writer.write(header + payload)
        except Exception:
            self.closed = True


async def read_frame(reader):
    b1, b2 = await reader.readexactly(2)
    opcode = b1 & 0x0F
    fin = b1 & 0x80
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    if n > 1_000_000:
        raise ConnectionError("frame too big")
    mask = await reader.readexactly(4) if b2 & 0x80 else b"\0\0\0\0"
    data = bytearray(await reader.readexactly(n))
    for i in range(n):
        data[i] ^= mask[i & 3]
    return fin, opcode, bytes(data)


GAME = None


async def handle_ws(reader, writer, headers):
    key = headers.get("sec-websocket-key", "")
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
    await writer.drain()
    client = Client(writer)
    pid = None
    buf = b""
    try:
        while True:
            fin, op, data = await read_frame(reader)
            if op == 8:
                break
            if op == 9:
                writer.write(struct.pack("!BB", 0x8A, len(data)) + data)
                continue
            if op in (1, 0):
                buf += data
                if not fin:
                    continue
                text, buf = buf.decode("utf-8", "ignore"), b""
                try:
                    m = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                t = m.get("t")
                if pid is None:
                    if t == "join":
                        pid = GAME.add_player(client, m.get("name"), m.get("color"))
                        client.send_text(json.dumps({"t": "welcome", "id": pid,
                                                     "world": GAME.world_info()}))
                    continue
                p = GAME.players.get(pid)
                if not p:
                    break
                if t == "pos":
                    GAME.on_pos(p, m)
                elif t == "act":
                    GAME.on_act(p, m)
                elif t == "chat":
                    GAME.on_chat(p, m)
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    finally:
        client.closed = True
        if pid is not None:
            GAME.remove_player(pid)
        try:
            writer.close()
        except Exception:
            pass


async def handle(reader, writer):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
    except Exception:
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, path, _ = lines[0].split(" ", 2)
    except ValueError:
        writer.close()
        return
    headers = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    if headers.get("upgrade", "").lower() == "websocket":
        await handle_ws(reader, writer, headers)
        return
    path = path.split("?", 1)[0]
    if path == "/":
        path = "/index.html"
    full = os.path.realpath(os.path.join(PUBLIC, path.lstrip("/")))
    if not full.startswith(os.path.realpath(PUBLIC)) or not os.path.isfile(full):
        body = b"Not found"
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
    else:
        with open(full, "rb") as f:
            body = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".js"):
            ctype = "text/javascript"
        writer.write(("HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                      "Cache-Control: no-cache\r\nConnection: close\r\n\r\n" % (ctype, len(body))).encode() + body)
    try:
        await writer.drain()
    finally:
        writer.close()


async def game_loop():
    last = time.monotonic()
    while True:
        await asyncio.sleep(0.1)
        now = time.monotonic()
        dt = min(0.5, now - last)
        last = now
        GAME.step(dt)
        GAME.broadcast(GAME.snapshot())
        GAME.events = []


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127.")) or ["127.0.0.1"]


async def main():
    global GAME
    ap = argparse.ArgumentParser(description="Frostfall Cabins LAN server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--minutes", type=float, default=12, help="real minutes per night (default 12)")
    args = ap.parse_args()
    GAME = Game(max(2.0, args.minutes) * 60)
    try:
        server = await asyncio.start_server(handle, "0.0.0.0", args.port)
    except OSError:
        print("\n  Port %d is busy - is Frostfall Cabins already running?" % args.port)
        print("  Close the other one, or start with:  python3 server.py --port 8766\n")
        return
    print("\n  ❄  FROSTFALL CABINS server is running\n")
    for ip in lan_ips():
        print("     Join on your network:  http://%s:%d" % (ip, args.port))
    print("     On this machine:       http://localhost:%d" % args.port)
    print("\n  Night length: %.0f min.  Press Ctrl+C to stop.\n" % (GAME.night_seconds / 60))
    asyncio.create_task(game_loop())
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Server stopped. Stay warm!\n")
