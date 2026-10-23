#!/usr/bin/env bash
# PodDeck installer for Raspberry Pi OS (Bookworm or newer)
# Run from this folder:   bash install.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PODDECK_PORT:-8070}"

echo "⬢ PodDeck installer"

if ! command -v podman >/dev/null 2>&1; then
  echo "→ Installing Podman (needs your password once)…"
  sudo apt update && sudo apt install -y podman
fi
echo "✓ $(podman --version)"

read -r -s -p "Choose a PodDeck password (leave blank for none): " PW; echo

# Let containers set to 'restart' come back after a reboot
systemctl --user enable --now podman-restart.service 2>/dev/null || true
# Keep your user services running even when you're not logged in
sudo loginctl enable-linger "$USER" || true

mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/poddeck.service" <<EOF
[Unit]
Description=PodDeck - Podman dashboard
After=network-online.target

[Service]
Environment=PODDECK_PORT=$PORT
Environment=PODDECK_PASSWORD=$PW
ExecStart=/usr/bin/python3 $DIR/poddeck.py
Restart=on-failure

[Install]
WantedBy=default.target
EOF
chmod 600 "$HOME/.config/systemd/user/poddeck.service"

systemctl --user daemon-reload
systemctl --user enable --now poddeck.service

# Desktop shortcut
if [ -d "$HOME/Desktop" ]; then
  cat > "$HOME/Desktop/PodDeck.desktop" <<EOF
[Desktop Entry]
Type=Link
Name=PodDeck
Comment=Manage your Podman containers
Icon=$DIR/poddeck-icon.png
URL=http://localhost:$PORT
EOF
  chmod +x "$HOME/Desktop/PodDeck.desktop"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "✓ PodDeck is running and will start on every boot."
echo "  On this Pi:          http://localhost:$PORT"
[ -n "$IP" ] && echo "  From other devices:  http://$IP:$PORT"
echo
echo "  Stop it:     systemctl --user stop poddeck"
echo "  Uninstall:   bash $DIR/uninstall.sh"
