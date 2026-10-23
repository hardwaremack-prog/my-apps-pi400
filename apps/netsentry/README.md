# NetSentry — real-time network watchdog

Watches your home network live and tells you, in plain English, when something looks wrong.

## Start it
- **Windows:** double-click `Start NetSentry (Windows).bat`. If Python is missing it offers to install it. Keep the black window open while it runs.
- **Raspberry Pi:** run `bash install-pi.sh` once — NetSentry appears in the menu under *Internet* and on the Desktop. Or just run `./netsentry.sh`.
- **Mac/Linux:** `python3 netsentry.py`

Your browser opens to **http://127.0.0.1:8775**. Nothing to install — it's pure Python.

## First run
1. The first sweep takes about a minute and learns what's normal.
2. Look through the device list. Click a name to give it a nickname, and press **Mark trusted** on everything you recognise (or **Trust all online**).
3. From then on, anything new or odd shows up as an alert. Turn on **🔔 Desktop alerts** to get pop-ups.

## What it catches
| Alert | Why it matters |
|---|---|
| **A device is pretending to be your router** | ARP spoofing / man-in-the-middle — someone reading your traffic |
| **Router's hardware address changed** | Same attack, or you swapped routers (one click to accept) |
| **New device joined** | Someone on your Wi-Fi you didn't expect |
| **Dangerous ports open** — Telnet, ADB, Docker, ports 4444 / 1337 / 31337 / 12345, IRC | Classic botnet & backdoor signs on cameras, TV boxes, PCs |
| **Ports worth checking** — FTP, Remote Desktop, VNC, proxies, Tor, TR-069 | Fine if you set them up, risky if you didn't |
| **This PC talking to malware/mining/Tor ports**, or listening on a backdoor port | Possible infection on the computer running NetSentry |
| **One device answering for several addresses** | Normal for Wi-Fi extenders, suspicious otherwise |

Every alert says *what it means* and *what to do*, and can be dismissed if it's expected (e.g. VNC on your Pis).

## Notes
- Sweeps every 30 s; this PC's connections are checked every 5 s; a device's ports are re-checked every 5 min.
- Scans the /24 network you're on (e.g. 192.168.1.x). Change it under *Settings* at the bottom.
- On first run it downloads the official IEEE device-maker list once so devices show who made them.
- Phones use *private (random) addresses*, so a phone can appear "new" after it changes address — that's normal.
- Some devices hide from pings; NetSentry also reads your computer's address table to catch them.
- Your list, nicknames and trust choices live in `netsentry_data.json` next to the app.
- Only scan networks you own or have permission to monitor.
