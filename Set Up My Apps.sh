#!/bin/bash
# Set Up My Apps (Raspberry Pi 400) - puts this folder on the Desktop and adds a "My Apps" icon
# to the Desktop and to the Raspberry Pi menu (under Games).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
mkdir -p "$DESK"
DEST="$DESK/My Apps"
NEED_REBOOT=""

# ask "question [y/N] " n   -> yes/no (no answer = the default; nothing typed in a window with no keyboard = skip)
ask() {
  local a
  if [ ! -t 0 ]; then return 1; fi
  read -r -p "$1" a || a=""
  a="${a:-$2}"
  case "$a" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# 1. Make sure the folder lives on the Desktop
if [ "$HERE" != "$DEST" ]; then
  if [ -e "$DEST" ]; then
    rm -rf "$DEST.old"; mv "$DEST" "$DEST.old"
  fi
  cp -r "$HERE" "$DEST"
fi

# 2. Pick the browser
BROWSER=""
for b in chromium-browser chromium firefox x-www-browser; do
  if command -v "$b" >/dev/null 2>&1; then BROWSER="$b"; break; fi
done
[ -z "$BROWSER" ] && BROWSER="xdg-open"

# 3. Launcher script (handles the space in "My Apps")
cat > "$DEST/.open-menu.sh" <<EOS
#!/bin/bash
exec $BROWSER "$DEST/My Apps Menu.html"
EOS
chmod +x "$DEST/.open-menu.sh"

LAUNCHER="[Desktop Entry]
Type=Application
Name=My Apps
Comment=All my apps and games
Exec=\"$DEST/.open-menu.sh\"
Icon=$DEST/icon.png
Terminal=false
Categories=Game;"

mkdir -p "$HOME/.local/share/applications"
printf '%s\n' "$LAUNCHER" > "$HOME/.local/share/applications/my-apps.desktop"
printf '%s\n' "$LAUNCHER" > "$DESK/My Apps.desktop"
chmod +x "$DESK/My Apps.desktop" "$HOME/.local/share/applications/my-apps.desktop"
gio set "$DESK/My Apps.desktop" metadata::trusted true 2>/dev/null || true

# 3b. NetPulse network monitor (runs a small Python program, then opens in the browser)
NP="$DEST/apps/netpulse"
if [ -f "$NP/netpulse.py" ]; then
  chmod +x "$NP/netpulse.py" "$NP/start-netpulse.sh" 2>/dev/null || true
  command -v ping >/dev/null 2>&1 || echo "  Note: install ping for NetPulse with:  sudo apt install iputils-ping"
  NPL="[Desktop Entry]
Type=Application
Name=NetPulse
Comment=Network monitor - up/down board and ping graphs
Exec=python3 \"$NP/netpulse.py\"
Path=$NP
Icon=$NP/netpulse-icon.png
Terminal=true
Categories=Network;"
  printf '%s\n' "$NPL" > "$HOME/.local/share/applications/netpulse.desktop"
  printf '%s\n' "$NPL" > "$DESK/NetPulse.desktop"
  chmod +x "$DESK/NetPulse.desktop" "$HOME/.local/share/applications/netpulse.desktop"
  gio set "$DESK/NetPulse.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3b2. NetSentry network watchdog (runs a small Python program, then opens in the browser)
NS="$DEST/apps/netsentry"
if [ -f "$NS/netsentry.py" ]; then
  chmod +x "$NS/netsentry.py" "$NS/start-netsentry.sh" 2>/dev/null || true
  NSL="[Desktop Entry]
Type=Application
Name=NetSentry
Comment=Real-time network watchdog - spots intruders and suspicious devices
Exec=python3 \"$NS/netsentry.py\"
Path=$NS
Icon=$NS/netsentry-icon.png
Terminal=true
Categories=Network;"
  printf '%s\n' "$NSL" > "$HOME/.local/share/applications/netsentry.desktop"
  printf '%s\n' "$NSL" > "$DESK/NetSentry.desktop"
  chmod +x "$DESK/NetSentry.desktop" "$HOME/.local/share/applications/netsentry.desktop"
  gio set "$DESK/NetSentry.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3b3. Halo Studio AI image editor (a small Python web app; first start sets itself up)
HS="$DEST/apps/halo-studio"
if [ -f "$HS/server.py" ]; then
  chmod +x "$HS/start.sh" 2>/dev/null || true
  HSL="[Desktop Entry]
Type=Application
Name=Halo Studio
Comment=Easy AI image editor - batch thumbnails, GIFs, compression and 3D-print models
Exec=bash \"$HS/start.sh\"
Path=$HS
Icon=$HS/icon.png
Terminal=true
Categories=Graphics;"
  printf '%s\n' "$HSL" > "$HOME/.local/share/applications/halo-studio.desktop"
  printf '%s\n' "$HSL" > "$DESK/Halo Studio.desktop"
  chmod +x "$DESK/Halo Studio.desktop" "$HOME/.local/share/applications/halo-studio.desktop"
  gio set "$DESK/Halo Studio.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3c. PiDirStat disk usage viewer (runs a small Python program, then opens in the browser)
PD="$DEST/apps/pidirstat"
if [ -f "$PD/pidirstat.py" ]; then
  chmod +x "$PD/pidirstat.py" "$PD/start-pidirstat.sh" 2>/dev/null || true
  PDL="[Desktop Entry]
Type=Application
Name=PiDirStat
Comment=See what's filling up the SD card
Exec=python3 \"$PD/pidirstat.py\" / --open
Path=$PD
Icon=$PD/pidirstat-icon.png
Terminal=true
Categories=System;Utility;"
  printf '%s\n' "$PDL" > "$HOME/.local/share/applications/pidirstat.desktop"
  printf '%s\n' "$PDL" > "$DESK/PiDirStat.desktop"
  chmod +x "$DESK/PiDirStat.desktop" "$HOME/.local/share/applications/pidirstat.desktop"
  gio set "$DESK/PiDirStat.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3c1. RPi400 Performance (makes the Pi run as fast as it can; opens in its own window)
RP="$DEST/apps/rpi400-performance"
if [ -f "$RP/rpi400perf.py" ]; then
  chmod +x "$RP/rpi400perf.py" "$RP/perfhelper.py" "$RP/start.sh" 2>/dev/null || true
  RPL="[Desktop Entry]
Type=Application
Name=RPi400 Performance
Comment=Make the Pi run as fast as it can - tune-ups, overclock, RAM and SD card
Exec=python3 \"$RP/rpi400perf.py\"
Path=$RP
Icon=$RP/icon.png
Terminal=false
Categories=Settings;System;"
  printf '%s\n' "$RPL" > "$HOME/.local/share/applications/rpi400-performance.desktop"
  printf '%s\n' "$RPL" > "$DESK/RPi400 Performance.desktop"
  chmod +x "$DESK/RPi400 Performance.desktop" "$HOME/.local/share/applications/rpi400-performance.desktop"
  gio set "$DESK/RPi400 Performance.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3c2. PON Lab meter outage tester (opens in SIMULATION mode here; for a real relay bench run its install.sh)
PL="$DEST/apps/ponlab"
if [ -f "$PL/app.py" ]; then
  chmod +x "$PL/app.py" "$PL/try-sim.sh" "$PL/install.sh" 2>/dev/null || true
  python3 -c "import flask" 2>/dev/null || echo "  Note: PON Lab needs Flask:  sudo apt install python3-flask"
  PLL="[Desktop Entry]
Type=Application
Name=PON Lab (simulation)
Comment=AMI meter outage and supercap holdup tester
Exec=bash \"$PL/try-sim.sh\"
Path=$PL
Icon=$PL/ponlab-icon.png
Terminal=true
Categories=Utility;Development;"
  printf '%s\n' "$PLL" > "$HOME/.local/share/applications/ponlab.desktop"
  printf '%s\n' "$PLL" > "$DESK/PON Lab.desktop"
  chmod +x "$DESK/PON Lab.desktop" "$HOME/.local/share/applications/ponlab.desktop"
  gio set "$DESK/PON Lab.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3c3. PON Fleet (control server for many PON Lab Pis)
PF="$DEST/apps/ponfleet"
if [ -f "$PF/fleet.py" ]; then
  chmod +x "$PF/fleet.py" "$PF/start-fleet.sh" "$PF/install.sh" 2>/dev/null || true
  PFL="[Desktop Entry]
Type=Application
Name=PON Fleet
Comment=Control many PON Lab Pis and cut up to 16 meters at once
Exec=bash \"$PF/start-fleet.sh\"
Path=$PF
Icon=$PF/ponfleet-icon.png
Terminal=true
Categories=Utility;Network;"
  printf '%s\n' "$PFL" > "$HOME/.local/share/applications/ponfleet.desktop"
  printf '%s\n' "$PFL" > "$DESK/PON Fleet.desktop"
  chmod +x "$DESK/PON Fleet.desktop" "$HOME/.local/share/applications/ponfleet.desktop"
  gio set "$DESK/PON Fleet.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3d. WiFi Share (shares the Pi's Wi-Fi through its Ethernet port; opens its own window)
WS="$DEST/apps/wifi-share"
if [ -f "$WS/wifi_share.py" ]; then
  chmod +x "$WS/wifi_share.py" "$WS/start-wifi-share.sh" 2>/dev/null || true
  command -v nmcli >/dev/null 2>&1 || echo "  Note: WiFi Share needs NetworkManager: sudo raspi-config > Advanced Options > Network Config"
  python3 -c "import tkinter" 2>/dev/null || echo "  Note: install the window toolkit for WiFi Share with:  sudo apt install python3-tk"
  WSL="[Desktop Entry]
Type=Application
Name=WiFi Share
Comment=Share Wi-Fi to the Ethernet port and see who's connected
Exec=python3 \"$WS/wifi_share.py\"
Path=$WS
Icon=$WS/wifi-share-icon.png
Terminal=false
Categories=Network;"
  printf '%s\n' "$WSL" > "$HOME/.local/share/applications/wifi-share.desktop"
  printf '%s\n' "$WSL" > "$DESK/WiFi Share.desktop"
  chmod +x "$DESK/WiFi Share.desktop" "$HOME/.local/share/applications/wifi-share.desktop"
  gio set "$DESK/WiFi Share.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3e. Blueprint Wiki (a small local web server; the menu tile finds it once it's running)
BW="$DEST/apps/blueprint-wiki"
if [ -f "$BW/server.py" ]; then
  # keep wiki pages from an earlier copy of My Apps
  if [ ! -d "$BW/data" ] && [ -d "$DEST.old/apps/blueprint-wiki/data" ]; then
    cp -r "$DEST.old/apps/blueprint-wiki/data" "$BW/data"
  fi
  chmod +x "$BW/server.py" "$BW/start.sh" "$BW/install-pi.sh" 2>/dev/null || true
  BWL="[Desktop Entry]
Type=Application
Name=Blueprint Wiki
Comment=Your wiki for projects, networks and prototypes
Exec=bash \"$BW/start.sh\"
Path=$BW
Icon=$BW/blueprint-wiki-icon.png
Terminal=false
Categories=Office;Development;"
  printf '%s\n' "$BWL" > "$HOME/.local/share/applications/blueprint-wiki.desktop"
  printf '%s\n' "$BWL" > "$DESK/Blueprint Wiki.desktop"
  chmod +x "$DESK/Blueprint Wiki.desktop" "$HOME/.local/share/applications/blueprint-wiki.desktop"
  gio set "$DESK/Blueprint Wiki.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3f. PodDeck (Podman container control panel; first run installs Podman and makes it start on boot)
PK="$DEST/apps/poddeck"
if [ -f "$PK/poddeck.py" ]; then
  chmod +x "$PK/poddeck.py" "$PK/start-poddeck.sh" "$PK/install.sh" "$PK/uninstall.sh" 2>/dev/null || true
  PKL="[Desktop Entry]
Type=Application
Name=PodDeck
Comment=Control panel for Podman containers
Exec=bash \"$PK/start-poddeck.sh\"
Path=$PK
Icon=$PK/poddeck-icon.png
Terminal=true
Categories=System;Utility;"
  printf '%s\n' "$PKL" > "$HOME/.local/share/applications/poddeck.desktop"
  printf '%s\n' "$PKL" > "$DESK/PodDeck.desktop"
  chmod +x "$DESK/PodDeck.desktop" "$HOME/.local/share/applications/poddeck.desktop"
  gio set "$DESK/PodDeck.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3f2. Frostfall Cabins (LAN multiplayer game server; everyone else joins from a browser)
FC="$DEST/games/frostfall-cabins"
if [ -f "$FC/server.py" ]; then
  chmod +x "$FC/server.py" "$FC/start-pi.sh" 2>/dev/null || true
  FCL="[Desktop Entry]
Type=Application
Name=Frostfall Cabins Server
Comment=Host a Frostfall Cabins night for everyone on your Wi-Fi
Exec=bash \"$FC/start-pi.sh\"
Path=$FC
Icon=$DEST/thumbs/frostfall-cabins.jpg
Terminal=true
Categories=Game;"
  printf '%s\n' "$FCL" > "$HOME/.local/share/applications/frostfall-cabins.desktop"
  printf '%s\n' "$FCL" > "$DESK/Frostfall Cabins Server.desktop"
  chmod +x "$DESK/Frostfall Cabins Server.desktop" "$HOME/.local/share/applications/frostfall-cabins.desktop"
  gio set "$DESK/Frostfall Cabins Server.desktop" metadata::trusted true 2>/dev/null || true
fi

# 3f3. RAID Forge (software RAID 0/1/5/6/10 builder; opens in its own window)
RF="$DEST/apps/raidforge"
if [ -f "$RF/raidforge.py" ]; then
  chmod +x "$RF/raidforge.py" "$RF/install.sh" 2>/dev/null || true
  RFL="[Desktop Entry]
Type=Application
Name=RAID Forge
Comment=Build and manage RAID 0/1/5/6/10 arrays
Exec=python3 \"$RF/raidforge.py\"
Path=$RF
Icon=$RF/raidforge.svg
Terminal=false
Categories=System;Utility;"
  RFD="[Desktop Entry]
Type=Application
Name=RAID Forge (Demo)
Comment=Try RAID Forge with pretend drives - changes nothing
Exec=python3 \"$RF/raidforge.py\" --demo
Path=$RF
Icon=$RF/raidforge.svg
Terminal=false
Categories=System;Utility;"
  printf '%s\n' "$RFL" > "$HOME/.local/share/applications/raidforge.desktop"
  printf '%s\n' "$RFD" > "$HOME/.local/share/applications/raidforge-demo.desktop"
  printf '%s\n' "$RFL" > "$DESK/RAID Forge.desktop"
  chmod +x "$DESK/RAID Forge.desktop" "$HOME/.local/share/applications/raidforge.desktop" \
    "$HOME/.local/share/applications/raidforge-demo.desktop"
  gio set "$DESK/RAID Forge.desktop" metadata::trusted true 2>/dev/null || true
  if ! python3 -c "import tkinter" 2>/dev/null || ! command -v mdadm >/dev/null 2>&1; then
    echo
    echo "  RAID Forge needs two small packages (python3-tk for its window, mdadm for RAID)."
    if ask "  Install them now? You'll be asked for your password. [Y/n] " y; then
      sudo apt-get install -y --no-install-recommends python3-tk mdadm smartmontools \
        || echo "  Note: that didn't finish. Try:  sudo apt-get install python3-tk mdadm smartmontools"
    fi
  fi
fi

# 3g. PiSnip (Snipping Tool) - runs its own installer the first time, or when there's a newer copy.
#     The installer adds the Snipping Tool icon, menu entry and Print Screen shortcut.
PS="$DEST/apps/pisnip"
if [ -f "$PS/pisnip.py" ]; then
  chmod +x "$PS/pisnip.py" "$PS/install.sh" "$PS/uninstall.sh" 2>/dev/null || true
  if ! cmp -s "$PS/pisnip.py" "$HOME/.local/share/pisnip/pisnip.py"; then
    echo
    echo "  Installing PiSnip (Snipping Tool). You may be asked for your password."
    bash "$PS/install.sh" || echo "  Note: PiSnip didn't finish installing. Try:  bash \"$PS/install.sh\""
  fi
fi

# 3h. PiMesh - joins this Pi to the mesh (background service). Runs its installer the
#     first time, or when there's a newer copy. The installer asks for the mesh name and key.
PM="$DEST/apps/pimesh"
if [ -f "$PM/pimesh.py" ]; then
  chmod +x "$PM/pimesh.py" "$PM/install.sh" "$PM/uninstall.sh" "$PM/try-demo.sh" "$PM/start.sh" 2>/dev/null || true
  if ! cmp -s "$PM/pimesh.py" /opt/pimesh/pimesh.py; then
    echo
    echo "  Setting up PiMesh (links your Pis together). You may be asked for your password."
    echo "  Use the SAME mesh name and key on every Pi."
    sudo bash "$PM/install.sh" || echo "  Note: PiMesh didn't finish installing. Try:  sudo bash \"$PM/install.sh\""
  fi
fi

# 3h2. RPi400 Task Manager (installs for the whole Pi, so it asks first)
TM="$DEST/apps/task-manager"
if [ -f "$TM/install.sh" ]; then
  chmod +x "$TM"/*.sh "$TM/rpi400_taskmgr.py" 2>/dev/null || true
  NEW_V="$(cat "$TM/VERSION" 2>/dev/null)"
  OLD_V="$(cat /opt/rpi400-taskmgr/VERSION 2>/dev/null || true)"
  if [ -z "$OLD_V" ]; then
    echo
    echo "  RPi400 Task Manager: Windows-style Task Manager (Ctrl+Shift+Esc), Update everything button and offline AI."
    echo "  Installing it also sets up the RPI400-OPEN travel hotspot (only starts when there's no network),"
    echo "  turns on VNC and makes the Pi boot straight to the desktop."
    if ask "  Install the Task Manager now? You'll be asked for your password. [y/N] " n; then
      sudo bash "$TM/install.sh" && NEED_REBOOT=1 || echo "  Note: the Task Manager didn't finish. Try:  sudo bash \"$TM/install.sh\""
    else
      echo "  Skipped. Install it later with:  sudo bash \"$TM/install.sh\""
    fi
  elif [ "$OLD_V" != "$NEW_V" ]; then
    echo
    if ask "  Update the Task Manager ($OLD_V -> $NEW_V)? Your settings are kept. [Y/n] " y; then
      sudo bash "$TM/install.sh" || echo "  Note: the Task Manager update didn't finish. Try:  sudo bash \"$TM/install.sh\""
    fi
  fi
fi

# 3h3. Pip (offline voice chat). It lives in ~/Pip and keeps itself up to date from there.
PIPSRC="$DEST/apps/pip"
PIPDIR="$HOME/Pip"
if [ -f "$PIPSRC/server.py" ]; then
  if [ ! -f "$PIPDIR/server.py" ]; then
    cp -r "$PIPSRC" "$PIPDIR"
    rm -f "$PIPDIR/Open Pip.html"
  fi
  chmod +x "$PIPDIR"/*.sh 2>/dev/null || true
  if [ ! -x "$PIPDIR/.venv/bin/python" ]; then
    echo
    echo "  Pip (offline voice chat) needs a one-time setup: it downloads 1.5 to 3.5 GB and takes 10 to 20 minutes."
    if ask "  Set up Pip now? [y/N] " n; then
      (cd "$PIPDIR" && bash ./install.sh) || echo "  Note: Pip's setup didn't finish. Try again with:  bash ~/Pip/install.sh"
    else
      echo "  Skipped. Set it up later with:  bash ~/Pip/install.sh"
    fi
  else
    bash "$PIPDIR/shortcuts.sh" >/dev/null 2>&1 || true
  fi
fi

# 3i. Server lights for the menu (SSH, VNC, The Shelf, PiMesh, PodDeck, ... like Command Center on Windows).
#     A tiny Python helper that starts with the Pi and only listens on this Pi (127.0.0.1:8399).
ST="$DEST/status"
if [ -f "$ST/pi-status.py" ]; then
  chmod +x "$ST/pi-status.py"
  command -v ss >/dev/null 2>&1 || echo "  Note: for program names on the server lights install:  sudo apt install iproute2"
  # stop an older copy so the new one takes over
  systemctl --user stop myapps-status.service >/dev/null 2>&1 || true
  pkill -f '^python3 .*/status/pi-status\.py$' 2>/dev/null || true
  STARTED=""
  if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/myapps-status.service" <<EOS
[Unit]
Description=My Apps server lights (status helper for My Apps Menu)

[Service]
ExecStart=/usr/bin/env python3 "$ST/pi-status.py"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOS
    systemctl --user daemon-reload || true
    systemctl --user enable myapps-status.service >/dev/null 2>&1 || true
    systemctl --user restart myapps-status.service && STARTED=1
  fi
  if [ -z "$STARTED" ]; then
    # no systemd user session: start it with the desktop instead
    mkdir -p "$HOME/.config/autostart"
    printf '%s\n' "[Desktop Entry]
Type=Application
Name=My Apps server lights
Exec=python3 \"$ST/pi-status.py\"
NoDisplay=true
X-GNOME-Autostart-enabled=true" > "$HOME/.config/autostart/myapps-status.desktop"
    nohup python3 "$ST/pi-status.py" >/dev/null 2>&1 &
  else
    rm -f "$HOME/.config/autostart/myapps-status.desktop"
  fi
  sleep 1
fi

# Pi OS: don't ask "Execute?" every time a launcher is double-clicked
for f in "$HOME/.config/libfm/libfm.conf"; do
  if [ -f "$f" ] && ! grep -q '^quick_exec=1' "$f"; then
    if grep -q '^\[config\]' "$f"; then sed -i '/^\[config\]/a quick_exec=1' "$f"; fi
  fi
done

echo
echo "  All set! Double-click 'My Apps' on the Desktop,"
echo "  (RPi400 Performance, the Task Manager, Pip, NetPulse, NetSentry, Halo Studio, PiDirStat, PON Lab, PON Fleet, WiFi Share, Blueprint Wiki, PodDeck, PiMesh, Frostfall Cabins Server, RAID Forge and the Snipping Tool have their own icons too),"
echo "  or find it in the Raspberry Pi menu under Games."
echo "  The server lights (SSH, VNC, The Shelf, PiMesh and more) are at the top of the menu."
[ -n "$NEED_REBOOT" ] && echo "  Restart the Pi once so the Task Manager starts cleanly:  sudo reboot"
echo
"$DEST/.open-menu.sh" >/dev/null 2>&1 &
sleep 3
