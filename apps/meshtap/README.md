# MeshTap 📡

![MeshTap](screenshot.png)

See everything going through your Meshtastic radio — live.

MeshTap connects to your radio (USB, WiFi or Bluetooth), captures every packet it hands over, saves them all to your computer, and shows them in a live dashboard in your browser.

## What you get
- **Live traffic** — every packet as it arrives: who sent it, who it's for, what it is (text, position, telemetry, node info, routing/ACKs, traceroute, neighbor reports, encrypted…), plus SNR, RSSI and hop count. Click any packet to see the full decoded contents.
- **Nodes** — every node heard, with packet counts, last heard, signal, hops, battery, hardware and a map link.
- **Map** — every node that shares a position on one live map (dark, street or satellite), colored by how recently it was heard, with solid lines to nodes your radio hears directly and dashed lines to ones reached through relays. Click a node for signal, battery, distance and its packets. Map pictures need internet; positions come from your mesh.
- **Ping** — prove messages really get through, in real time. Pick a node and send a **quiet ping** (waits for its delivery ACK), a **Hello** (swaps node info, so it works even if they don't know your key yet), a **traceroute** (shows the path both ways with signal per hop) or a **text ping** (shows on their screen). You see *relayed by a neighbor* and then *delivered* with the round-trip time, or exactly why it failed. Choose a hop limit, repeat every 30 s or more for a live success rate and round-trip chart, and read the **Link check**, which explains in plain words whether nodes can hear you and where messages stop. Export to CSV.
- **Breakdown** — packets per minute for the last hour, traffic by type, top talkers, signal quality and how far packets travelled.
- **Device log** — MeshTap events plus the radio's own debug lines.
- **Search & filters**, "to me only", "hide my radio", pause, and **export to CSV / JSON**.
- **Auto-save** switch (top right): on, every packet is saved as it arrives; off, traffic shows live but isn't kept, with a **Save now** button for what you've caught. Saved to `~/.meshtap/meshtap.db` (Windows: `C:\Users\<you>\.meshtap\`) so history survives restarts.

## Windows
1. Install Python 3 from python.org (tick "Add to PATH").
2. Double-click **start-windows.bat**. First run installs what it needs, then your browser opens.
3. No radio handy? Double-click **start-demo-windows.bat** to explore with fake traffic.

## Raspberry Pi (400 / 500 / 5)
```bash
chmod +x install-pi.sh && ./install-pi.sh
```
Then double-click the **MeshTap** icon on the desktop. (Log out/in once after install so USB access works.)

## Connecting
Click **Connect** in the top-right:
- **USB / Serial** — plug the radio in with a *data* cable (baud 115200). Every serial port is listed, likely Meshtastic radios marked ★, or pick **Other port…** to type one in. Close the Meshtastic web/desktop client first (only one app can use the port).
- **Wired to a Pi's GPIO pins** — radio TX → Pi pin 10 (RX), radio RX → Pi pin 8 (TX), GND → GND. Turn on the Pi's serial port (`sudo raspi-config` → Interface Options → Serial Port → login shell **No**, hardware **Yes**, reboot). Set the radio's Serial Module to mode **PROTO**, then in MeshTap pick `/dev/serial0` and the same baud rate.
- **Serial text** — for a radio whose Serial Module is in **TEXTMSG**, **NMEA** or **SIMPLE** mode. MeshTap shows each line it sends: messages as "name: text" and GPS sentences.
- **WiFi** — radio needs WiFi on; type its IP (shown on its screen) or `meshtastic.local`.
- **Bluetooth** — disconnect the radio from your phone first; a radio only pairs with one device at a time.
- **Demo** — fake traffic, no radio needed.

## View it from your phone
Run with `--lan` (e.g. `start-windows.bat --lan` or `./start-pi.sh --lan`) and open `http://<computer-ip>:8742` on your phone.

## Command line options
```
--serial [PORT]  --serial-text PORT  --baud 115200  --tcp HOST  --ble [NAME]  --demo  --lan  --web-port 8742  --no-browser

e.g.  python meshtap.py --serial /dev/serial0 --baud 38400
      python meshtap.py --serial-text COM7 --baud 9600
```

## Good to know
- MeshTap shows what **your radio** hears and decodes. Packets on channels your radio doesn't have the key for show up as **Encrypted** (if the firmware passes them along).
- Messages you send *from your phone* through the radio don't come back over USB, so they won't appear — but replies and ACKs will.
- For the most detail in the Device log, turn on the debug log API:
  `meshtastic --set security.debug_log_api_enabled true`
