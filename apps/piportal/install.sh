#!/usr/bin/env bash
# PiPortal installer — Raspberry Pi 5 / 500 / 4 / 400 (Raspberry Pi OS Bookworm or Trixie)
# Usage:  ./install.sh            (asks for a password)
#         ./install.sh --https    (also turn on self-signed HTTPS)
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Run this as your normal desktop user (not with sudo). It will ask for sudo when needed."
  exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/piportal"
CONF="$HOME/.config/piportal"
PORT=6090
HTTPS=0
for a in "$@"; do [[ "$a" == "--https" ]] && HTTPS=1; done

MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo 'Linux')"
echo "== PiPortal installer on: $MODEL"

echo "== Installing packages (sound capture, VNC engines, web server)…"
sudo apt-get update -qq
PKGS="python3-aiohttp pulseaudio-utils openssl x11vnc"
apt-cache show wayvnc >/dev/null 2>&1 && PKGS="$PKGS wayvnc"
# shellcheck disable=SC2086
sudo apt-get install -y $PKGS

echo "== Copying app to $DEST"
mkdir -p "$DEST" "$CONF"
rm -rf "$DEST/web"
cp -r "$SRC/server.py" "$SRC/web" "$DEST/"
cp "$SRC/uninstall.sh" "$DEST/" 2>/dev/null || true
chmod +x "$DEST/server.py"

if [[ ! -f "$CONF/config.json" ]] || ! grep -q pbkdf2 "$CONF/config.json"; then
  echo
  echo "== Pick a password for the PiPortal web page"
  python3 "$DEST/server.py" --set-password
fi
if [[ $HTTPS -eq 1 ]]; then
  python3 - "$CONF/config.json" <<'PY'
import json, sys
p = sys.argv[1]; c = json.load(open(p)); c["tls"] = True; json.dump(c, open(p, "w"), indent=2)
PY
fi

echo "== Allowing the web page to restart services, read logs and power off"
SUDOERS="/etc/sudoers.d/piportal"
echo "$USER ALL=(root) NOPASSWD: /usr/bin/systemctl, /usr/bin/journalctl, /usr/bin/kill" | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"
sudo visudo -cf "$SUDOERS" >/dev/null

echo "== Setting up the background service"
sudo tee /etc/systemd/system/piportal.service >/dev/null <<EOF
[Unit]
Description=PiPortal — HTML5 desktop with sound and Pi management
After=network-online.target sound.target
Wants=network-online.target

[Service]
User=$USER
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u)
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $DEST/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now piportal.service
sleep 1
sudo systemctl restart piportal.service

# Menu entry so it can be opened from the Pi itself
mkdir -p "$HOME/.local/share/applications"
SCHEME=http; [[ $HTTPS -eq 1 ]] && SCHEME=https
cat > "$HOME/.local/share/applications/piportal.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PiPortal
Comment=Web desktop with sound and Pi management
Exec=xdg-open $SCHEME://localhost:$PORT
Icon=$DEST/web/favicon.svg
Categories=System;Network;
EOF

echo
echo "== Done!  Open one of these on your PC, phone or tablet:"
for ip in $(hostname -I); do
  [[ "$ip" == *:* ]] && continue
  echo "     $SCHEME://$ip:$PORT"
done
echo "     $SCHEME://$(hostname).local:$PORT"
echo
echo "Tip: the Pi must be booted to its desktop (auto-login on) for the screen and sound to stream."
