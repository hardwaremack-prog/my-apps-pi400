# RPi400 Task Manager

> The full illustrated guide is in **User Manual.pdf** in this folder.

A Windows-style Task Manager for the Raspberry Pi 400 (Raspberry Pi OS Bookworm or newer), with extras for taking the Pi on the road.

## Install

Copy this folder to the Pi, open a terminal in it, and run:

```
sudo bash install.sh
sudo reboot
```

That's it. The installer:

- installs the app (Menu → System Tools → RPi400 Task Manager, or **Ctrl+Shift+Esc**)
- makes it open automatically when the desktop starts
- creates the **RPI400-OPEN** hotspot (password `fifty-seven57`, Pi at `192.168.1.250`)
- turns on **auto-hotspot at boot**: if the Pi hasn't joined any Wi-Fi or Ethernet ~60 s after powering on, it starts the hotspot by itself
- makes the **power light flash while the hotspot is on**, so you can tell at a glance that the Pi is broadcasting RPI400-OPEN
- turns on **VNC on port 5900** and sets the Pi to boot straight to the desktop so VNC works with no monitor (skip that with `sudo SKIP_AUTOLOGIN=1 bash install.sh`)
- adds a global **Ctrl+Alt+B** shortcut that opens Bluetooth pairing, keyboard-only, for when the mouse is gone

To remove everything: `sudo bash uninstall.sh`

## What's in it

| Tab | What you get |
|---|---|
| **F1 Overview** | Gauges for CPU, clock speed, temperature, memory, swap, disk · 60-second CPU graph · per-core bars · power panel with core voltage and under-voltage / throttling lights (now and since boot) · uptime, load, disk read/write · VNC / SSH / hotspot / Tailscale lights |
| **F2 Processes** | Sortable list (click headers), filter box, End task (**Del**), Force kill (**Shift+Del**) |
| **F3 Network** | A card per interface with blinking **LINK / RX / TX lights**, speeds, totals, Wi-Fi signal · traffic graph · list of devices connected to your hotspot |
| **F5 AI** | An on/off **AI** switch and a chat with a small AI that runs entirely on the Pi. It works offline once a model is downloaded |
| **F4 Tools** | Clean memory · Bluetooth rescue + pair a new device · hotspot on/off with a **join-by-QR-code**, your own name & password, and a switch so phones keep their mobile data · CPU mode (Battery saver / Balanced / Full speed) · overclock presets · stability test · **Update everything** · VNC · **Web page** for phones & PCs · **Tailscale** (reach the Pi from anywhere) · start-at-login · reboot / shut down |

The top bar always shows your IP addresses, a **👥 people counter** for the hotspot, and link lights for `eth0`, `wlan0` and Bluetooth, whatever tab you're on.

### 👥 Who's Connected window (Ctrl+P)

Click the 👥 counter in the top bar, press **Ctrl+P**, or use the button on the Network or Tools tab.

- **Connected right now:** every phone or laptop on RPI400-OPEN, with its name, IP, MAC, signal strength, when it joined, how long it's been on, and how much data it's used. Select one and press **Del** to disconnect it. They can rejoin if they still have the password.
- **Visitor log:** every join and leave, newest first. Joins are green, leaves are amber, and hotspot on/off shows in blue.
- **Auto-save switch (Ctrl+A), next to Save.** It's on by default and remembered between restarts.
  - **On:** every join and leave goes straight into `RPI400-OPEN visitor log.csv` on the Desktop. That file opens in LibreOffice Calc like a spreadsheet and keeps growing across days and reboots. `RPI400-OPEN visitors - latest.txt` is also rewritten whenever someone comes or goes (and every minute while people are on), so it always shows the current picture.
  - **Off:** nothing is written to the Desktop. Events still show in the window, and the footer counts how many are waiting. Press Save, or flip the switch back on, and the waiting events are written to the log. Nothing is lost.
- **💾 Save report to Desktop (Ctrl+S):** writes a readable `Hotspot visitors YYYY-MM-DD HHMM.txt` snapshot. It lists who's on right now, the recent activity, and totals.

Logging happens while the Task Manager is running. It starts itself with the desktop, so that's normally all the time. Device names come from what each phone reports about itself. Some phones don't share a name, or use a random MAC for privacy, so the same phone can show up as "(no name)" or as a new device on another day.

## Keyboard (everything works from the Pi 400 keyboard alone)

| Keys | Does |
|---|---|
| Ctrl+Shift+Esc | Open Task Manager (anywhere) |
| Ctrl+Alt+B | Bluetooth pairing window (anywhere, app doesn't need to be open). Inside it: ↑ ↓ choose, Enter pair, S scan again, R reconnect paired devices, Esc close |
| F1–F5 or Alt+1–5 | Switch tabs |
| Ctrl+P | Who's Connected window (Del = disconnect, Ctrl+S = save report, Ctrl+A = auto-save on/off, Esc = close) |
| Ctrl+M | Clean memory |
| Ctrl+B | Bluetooth rescue |
| Ctrl+H | Hotspot on/off |
| Ctrl+F | Filter processes |
| Del / Shift+Del | End / force kill selected process |
| Tab, arrows, Space, Enter | Move around and press buttons |
| Ctrl+Q | Quit |

## Good to know

**Running from a battery bank.** The Pi 400 has no sensor for battery level or input voltage. What it *does* report is an under-voltage flag (supply below about 4.63 V), and that's what the "Power" light shows. Red right now means the bank or cable can't keep up. Amber "since boot" means it dipped at some point. A short, thick USB-C cable and a 3 A port fix most of it. Battery saver mode helps stretch runtime.

**Clean memory.** Linux uses spare RAM as a file cache on purpose, and it refills over time. Cleaning is most useful right before launching something big, or to pull things back out of swap.

**Overclock.** 2.0 GHz is a mild, widely used setting for the Pi 400. 2.15 GHz works on many but not all. After changing it and rebooting, press **🧪 Stability test** in the Tools tab: it runs all four cores flat out for 1, 3 or 10 minutes, checks every answer is right, and watches heat and throttling. If the Pi freezes mid-test, the app tells you which speed caused it next time it starts. Changes apply after a reboot and a backup of `config.txt` is kept. If the Pi won't boot, put the SD card in another computer, open `config.txt` on the boot drive, and delete the lines between the `rpi400-taskmgr overclock` markers.

**Flashing power light.** Whenever RPI400-OPEN is running, whether it started by itself at boot or you pressed Ctrl+H, the power light blinks about once a second. It goes back to normal the moment the hotspot stops. This is handled by NetworkManager, so it works even with the Task Manager closed or no screen attached.
- It works on the Pi 400, Pi 500, Pi 4 and Pi 5. On the Pi 500 and Pi 5, the red and green parts of the light both flash, so the blink may look amber.
- While it flashes, the green light stops showing SD-card activity. That comes back when the hotspot stops.
- Turn it on or off with the **Flash power light while on** switch in the Tools tab, and press **Test** to watch it flash for 6 seconds. From a terminal, use `sudo rpi400-helper led on` or `led off`.

**Local AI (F5).** This is a small AI chat that runs on the Pi itself using Ollama, a free engine for running AI models locally. Nothing you type leaves the Pi, and after the one-time model download it needs no internet, so it works at the hotspot too.
- **The AI switch** starts and stops the engine. Off means it uses no memory or CPU, which is good on battery. The model also unloads itself after 5 idle minutes.
- **Models** that suit a Pi 400 (4 GB):

  | Model | Size | Notes |
  |---|---|---|
  | `qwen2.5:0.5b` | about 400 MB | The default. Fastest answers |
  | `gemma3:1b` | about 800 MB | A good middle ground |
  | `llama3.2:1b` | about 1.3 GB | The best answers of the three, but slower |

  To add one, pick it in the Model list and press **⬇ Download** while the Pi has internet.
- **Chatting:** type below and press **Enter**. **Esc** stops an answer partway, and **New chat** clears the conversation. Each answer shows its speed in tokens per second.
- **Limits:** these tiny models are handy for quick questions, explanations and drafting. They're much less knowledgeable than big online AIs and can be confidently wrong, so double-check anything important. While answering, the AI uses the CPU heavily, so the Pi warms up and drains a battery faster.
- **Needs** the 64-bit Raspberry Pi OS. The installer sets everything up if the Pi is online (skip with `SKIP_AI=1`). Otherwise, flip the AI switch later to install it.

**2.4 GHz or 5 GHz.** The **Wi-Fi band** switch in the Tools tab's hotspot box picks the band. It's also available from a terminal with `sudo rpi400-helper ap-band 2.4` or `5`.

| | 2.4 GHz (default) | 5 GHz |
|---|---|---|
| Range | Longest, and goes through walls better | Shorter, best in the same room |
| Speed | Slower, and busy where there's lots of Wi-Fi | Noticeably faster |
| Devices | Every phone, laptop and gadget | Most modern devices. Some older or cheap ones can't see it |

If the hotspot is running, switching restarts it and connected devices rejoin after a few seconds. Your choice is remembered, including for the automatic hotspot at boot. If 5 GHz ever won't start, because of local Wi-Fi rules or the chip refusing, the Pi falls back to 2.4 GHz by itself, so you're never locked out. The line under the switch shows the band and channel actually in use.

**How many people?** Roughly 8–10 devices at once on the built-in Wi-Fi. That limit comes from the chip's own software, so test yours by watching the 👥 counter as people join. Everyone also shares the Wi-Fi speed, so a handful browsing The Shelf is comfortable, while many big downloads at once will be slow.

**Phones keep their mobile data.** The hotspot has no internet of its own, and normally a phone on Wi-Fi sends everything over it, so apps like Teams go quiet while you're listening to The Shelf. The **Phones keep their mobile data** switch in the Tools tab (on from the start) makes the hotspot hand out an address but no route to the internet and no DNS server. Phones then use Wi-Fi only for the Pi and The Shelf, and their own mobile data for everything else. Turn it off only when the Pi itself is online (for example by Ethernet) and you want to share that. Changing it restarts the hotspot. If a phone still acts offline, forget the network and join again. On Android, answer **Yes** to *Stay connected?* and tick *Don't ask again*. From a terminal: `sudo rpi400-helper ap-data keep` or `share`.

**Your own name and password.** Press **✏ Change** next to the network name in the Tools tab, type a new name (1–32 characters) and password (8–63), and press Enter. The QR code updates, and the automatic hotspot at boot uses the new name. If the hotspot is on it restarts, and everyone joins again with the new password. **Back to default** returns to RPI400-OPEN / fifty-seven57, as does `sudo rpi400-helper ap-set --default`.

**The hotspot address.** `192.168.1.250` is on the same range many home routers use. That's fine because the hotspot only runs when the Pi isn't on another network. People join RPI400-OPEN and open `http://192.168.1.250:8080/` to reach The Shelf (its usual port). If you set The Shelf up on port 80 instead, the Tools tab notices and shows `http://192.168.1.250/`.

**Coming home.** Once the hotspot is on, the Pi won't hop back onto your home Wi-Fi by itself. Press Ctrl+H (Stop hotspot) and it reconnects.

**Security.** The hotspot password is shared with everyone you hand it to, and anyone on the hotspot can reach VNC. VNC still asks for your Pi username and password, so keep that password a strong one. Only devices signed in to your own Tailscale account can reach the Pi over Tailscale.

**Update everything (Ctrl+U).** One button in the Tools tab brings the whole Pi up to date: Raspberry Pi OS and every installed program (Tailscale included), the firmware, the local AI engine, **The Shelf** (newest version from GitHub; your pictures, music, notes and other files are kept, and it restarts) and this Task Manager itself (only if GitHub has a newer version). It also puts the newest Task Manager downloads in The Shelf's Programs section and deletes older copies there. It also removes old kernels and leftover packages, and any other edition of the Task Manager on the same Pi, so there's only one. A window shows each step live; closing it doesn't stop the update. It needs internet and takes 5–30 minutes, then asks you to restart if needed. From a terminal: `sudo rpi400-helper update start` (progress in `/var/log/rpi400-update.log`).

**Your username** is shown in the top bar (👤), next to the Pi's address.

**Use it from a phone or PC (web page).** Flip **Web page** on in the Tools tab. The first time, it asks you to choose a password (6+ characters) for the web page's controls. The line under the switch then shows the address, e.g. `http://192.168.1.20:8090` (on the hotspot: `http://192.168.1.250:8090`). Open it in any browser on the same network: anyone can see the gauges, processes and network lights, and after **Log in** you get the hotspot, who's connected, the visitor log, ending programs, Bluetooth, CPU mode, overclock, stability test, Tailscale and reboot. It keeps running when the Task Manager window is closed, and after reboots, until you switch it off. **🔑 Password** changes the password. The AI chat, Wi-Fi band switch and graphics boost stay on the Pi. Install options: `sudo WEB_PASSWORD=... bash install.sh` turns it on straight away, `WEB_PORT=` picks another port. From a terminal: `sudo rpi400-helper web on` / `off`. Pick a password that's different from the hotspot password, since everyone on the hotspot knows that one.

**Reach the Pi from anywhere (Tailscale).** VNC only works on the same network as the Pi. Tailscale is a free private network (a "VPN") that links your own devices together wherever they are, with nothing to set up on your router.
1. Flip the **Tailscale** switch in the Tools tab. The first time, it asks to download Tailscale (about 30 MB, needs internet).
2. A sign-in window shows a QR code. Scan it with your phone, or press **Open on this Pi**, and sign in with a Google, Microsoft, Apple or GitHub account. The free plan covers up to 100 devices.
3. Install the Tailscale app on your phone or PC and sign in with the **same** account.
4. The Tools tab now shows the Pi's Tailscale address (starts with `100.`). Point your VNC viewer at that address, port 5900, from anywhere. SSH and The Shelf work at that address too.

Switching it off disconnects the Pi from Tailscale; switching it on again reconnects without signing in again. To set it up during install with no sign-in window, create an auth key in the Tailscale admin page and run `sudo TAILSCALE_AUTHKEY=tskey-... bash install.sh`. From a terminal: `sudo rpi400-helper vpn up`, `vpn down`, or `vpn logout`. While the Pi is running its own hotspot it has no internet, so Tailscale can't connect until it's back on a normal network.

**Global shortcuts.** The installer sets up Ctrl+Shift+Esc and Ctrl+Alt+B for whichever desktop your Pi uses: labwc (current Raspberry Pi OS), Wayfire, or the older X11/Openbox desktop. The installer also replaces the older Ctrl+Alt+B (Bluetooth rescue) from earlier versions. If they ever stop working, for example after switching desktops in raspi-config, run this in the rpi400-taskmgr folder, then log out and back in:

```
sudo bash setup-shortcuts.sh
```

## Files

| File | Goes to | Role |
|---|---|---|
| `rpi400_taskmgr.py` | `/opt/rpi400-taskmgr/` | The app (Python + Tk) |
| `rpi400-helper` | `/usr/local/bin/` | Small root-only script for the privileged buttons |
| `rpi400_web.py` + `web/index.html` | `/opt/rpi400-taskmgr/` | The web page for phones and PCs (`rpi400-web.service`, off until switched on) |
| `rpi400-fallback-ap` + `.service` | `/usr/local/sbin/`, systemd | Starts the hotspot at boot when offline |
| `/etc/rpi400-taskmgr.conf` | (created) | Hotspot name/password (`AP_SSID`, `AP_PASS`), band (`AP_BAND`), mobile-data setting (`AP_SHARE`) and power-light setting (`LED_BLINK`) |
| `setup-shortcuts.sh` | (run from the folder) | Adds the global shortcuts for labwc, Wayfire or Openbox |
| `90-rpi400-led` | `/etc/NetworkManager/dispatcher.d/` | Flashes the power light while the hotspot is up |
