# Frostfall Cabins

A LAN multiplayer spin-off of Fernlight Frostfall. Five log cabins sit in different corners of one mountain valley — **Summit Lookout**, **Frozen Lake**, **Birch Glade**, **Old Mill** and **Hollow End**. Everyone on your Wi-Fi joins the same night, splits up between the cabins, hauls firewood, and tries to keep every stove burning through the **2:00 AM icy gale** until dawn.

![screenshot](screenshot.png)

## Start a game

One computer runs the server (a Raspberry Pi is plenty). Nothing to install — it only needs Python 3.

- **Raspberry Pi / Linux / Mac:** double-click `start-pi.sh` (or run `./start-pi.sh` in a terminal)
- **Windows:** double-click `start-windows.bat` (Python 3 must be installed)

The window prints an address like `http://192.168.1.42:8765`. Everyone opens that address in Chrome/Chromium on their own computer or Pi and picks a name and coat colour. The server computer can play too at `http://localhost:8765`.

Options: `python3 server.py --minutes 20` for a longer night (default 12), `--port 9000` to change the port.

## How to survive

| Key | Does |
|---|---|
| WASD / arrows | walk · **Shift** run · **Space** jump |
| Mouse | look (click the game to grab the mouse, Esc to free it) |
| **E** | pick up logs · stack the woodbox · feed the stove · take a can · heat/eat stew · light candles |
| **T** | chat · **Tab** who's where · **M** big map |

- Warmth drains outdoors, faster after midnight. Standing in a cabin with a lit stove warms you up; candles help a little more.
- Every cabin starts with 2 logs in the woodbox. Fallen logs are scattered around the cabins and along the trails — carry up to 4 at a time.
- Stew: grab a can from the pantry, set it on a lit stove, wait for it to heat, then eat it for food and a big warm-up.
- **The gale (2:00–5:30 AM):** stoves burn twice as fast, no new logs blow in, and being outdoors will freeze you in about a minute. Stock the woodboxes before 2 AM!
- Freeze solid and you thaw out 10 seconds later beside the strongest fire on the mountain (or the trailhead if every stove is out).
- At dawn you get a scoreboard (logs hauled, times frozen, which cabins survived), and the next night starts on its own.

Glowing windows, chimney smoke and the orange halo over a roof mean that cabin's fire is lit — you can see them through the storm. The red light on the summit radio mast helps you find north.

## Files

- `server.py` — game server and web server in one file (Python standard library only)
- `public/` — the game itself (three.js is bundled, so it works with no internet)
