#!/usr/bin/env bash
# PodDeck installer for Raspberry Pi OS (Bookworm or newer)
# Run from this folder:   bash install.sh
# Safe to run again: it updates the service and lets you keep or change the password.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PODDECK_PORT:-8070}"
CONF="$HOME/.config/poddeck"
PWFILE="$CONF/password"
UNIT="$HOME/.config/systemd/user/poddeck.service"

echo "⬢ PodDeck installer"

if [ "$(id -u)" = 0 ]; then
  echo "✗ Run this as your normal user, not with sudo:  bash install.sh"
  exit 1
fi

if ! command -v podman >/dev/null 2>&1; then
  echo "→ Installing Podman (needs your password once)…"
  sudo apt update && sudo apt install -y podman
fi
echo "✓ $(podman --version)"

# Podman without root needs a block of spare user IDs. A renamed account can lose its entry.
if ! grep -qE "^($USER|$(id -u)):" /etc/subuid 2>/dev/null || ! grep -qE "^($USER|$(id -u)):" /etc/subgid 2>/dev/null; then
  echo "→ Giving $USER the user IDs Podman needs…"
  START=$(awk -F: '{e=$2+$3; if (e>m) m=e} END {print (m>100000 ? m : 100000)}' /etc/subuid /etc/subgid 2>/dev/null || echo 100000)
  sudo usermod --add-subuids "$START-$((START + 65535))" --add-subgids "$START-$((START + 65535))" "$USER"
  podman system migrate >/dev/null 2>&1 || true
fi

# Password: kept in its own private file so spaces, quotes, % and $ all work.
mkdir -p "$CONF"; chmod 700 "$CONF"
# Carry over a password from an older PodDeck install (it lived inside the service file)
if [ ! -f "$PWFILE" ] && [ -f "$UNIT" ]; then
  OLDPW="$(sed -n 's/^Environment=PODDECK_PASSWORD=//p' "$UNIT" | head -1)"
  if [ -n "$OLDPW" ]; then (umask 077; printf '%s' "$OLDPW" > "$PWFILE"); fi
  unset OLDPW
fi
if [ -t 0 ]; then
  if [ -f "$PWFILE" ]; then
    read -r -s -p "PodDeck password (Enter = keep current, type - to remove it): " PW; echo
    if [ "$PW" = "-" ]; then rm -f "$PWFILE"; echo "✓ Password removed"
    elif [ -n "$PW" ]; then (umask 077; printf '%s' "$PW" > "$PWFILE"); echo "✓ Password changed"
    else echo "✓ Keeping your current password"; fi
  else
    read -r -s -p "Choose a PodDeck password (leave blank for none): " PW; echo
    if [ -n "$PW" ]; then (umask 077; printf '%s' "$PW" > "$PWFILE"); fi
  fi
fi
unset PW

# Let containers set to 'restart' come back after a reboot
systemctl --user enable --now podman-restart.service 2>/dev/null || true
# Keep your user services running even when you're not logged in
sudo loginctl enable-linger "$USER" || true

# systemd needs the path quoted (My Apps lives in a folder with a space) and % / $ / \ / " escaped
q() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//%/%%}"; s="${s//\$/\$\$}"; printf '"%s"' "$s"; }
PWLINE=""
[ -f "$PWFILE" ] && PWLINE="Environment=$(q "PODDECK_PASSWORD_FILE=$PWFILE")"

mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<EOF
[Unit]
Description=PodDeck - Podman dashboard

[Service]
Environment=PODDECK_PORT=$PORT
$PWLINE
ExecStart=/usr/bin/python3 $(q "$DIR/poddeck.py")
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
chmod 600 "$UNIT"

systemctl --user daemon-reload
systemctl --user enable poddeck.service >/dev/null 2>&1
systemctl --user restart poddeck.service

# Desktop shortcut (left alone if My Apps already made one)
if [ -d "$HOME/Desktop" ] && [ ! -e "$HOME/Desktop/PodDeck.desktop" ]; then
  cat > "$HOME/Desktop/PodDeck.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PodDeck
Comment=Manage your Podman containers
Exec=xdg-open http://localhost:$PORT
Icon=$DIR/poddeck-icon.png
Terminal=false
Categories=System;Utility;
EOF
  chmod +x "$HOME/Desktop/PodDeck.desktop"
  gio set "$HOME/Desktop/PodDeck.desktop" metadata::trusted true 2>/dev/null || true
fi

# Make sure it actually came up
OK=""
for _ in $(seq 1 20); do
  if python3 -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:$PORT/poddeck.js', timeout=1)" 2>/dev/null; then OK=1; break; fi
  sleep 0.5
done

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
if [ -n "$OK" ]; then
  echo "✓ PodDeck is running and will start on every boot."
  echo "  On this Pi:          http://localhost:$PORT"
  [ -n "$IP" ] && echo "  From other devices:  http://$IP:$PORT"
  [ -f "$PWFILE" ] || echo "  (No password set: anyone on your network can open it. Run install.sh again to add one.)"
else
  echo "✗ PodDeck didn't start. Here's what it said:"
  journalctl --user -u poddeck -n 15 --no-pager 2>/dev/null || systemctl --user status poddeck --no-pager
  exit 1
fi
echo
echo "  Stop it:     systemctl --user stop poddeck"
echo "  Uninstall:   bash \"$DIR/uninstall.sh\""
