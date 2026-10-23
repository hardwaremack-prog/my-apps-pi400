#!/usr/bin/env bash
# PiMesh installer - run on every Raspberry Pi you want in the mesh:
#   sudo ./install.sh
set -e
if [ "$EUID" -ne 0 ]; then echo "Please run with sudo:  sudo ./install.sh"; exit 1; fi
command -v python3 >/dev/null || { echo "python3 is required (sudo apt install python3)"; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
DIR=/opt/pimesh
CFG=/etc/pimesh.json

echo "== Installing PiMesh to $DIR"
mkdir -p "$DIR"
cp "$SRC/pimesh.py" "$SRC/dashboard.html" "$SRC/icon-256.png" "$DIR/"
chmod +x "$DIR/pimesh.py"

if [ ! -f "$CFG" ]; then
  MESH="home-mesh"; SECRET=""
  if [ -t 0 ]; then
    echo
    echo "Every Pi that should join the same mesh needs the SAME mesh name and key."
    read -r -p "Mesh name [home-mesh]: " IN; MESH="${IN:-home-mesh}"
    read -r -p "Mesh key (blank = open to any PiMesh on your network): " SECRET
  fi
  cat > "$CFG" <<JSON
{
  "mesh_name": "$MESH",
  "secret": "$SECRET",
  "node_name": "",
  "peers": [],
  "mesh_port": 47474,
  "web_port": 8470
}
JSON
  chmod 600 "$CFG"
  echo "== Wrote $CFG"
else
  echo "== Keeping existing $CFG"
fi

cat > /etc/systemd/system/pimesh.service <<UNIT
[Unit]
Description=PiMesh node (mesh + dashboard)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 $DIR/pimesh.py --config $CFG
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 47474/udp >/dev/null; ufw allow 8470/tcp >/dev/null; echo "== Opened firewall ports"
fi

systemctl daemon-reload
systemctl enable pimesh >/dev/null 2>&1
systemctl restart pimesh

# Clickable desktop icon for the desktop user
USR="${SUDO_USER:-}"
if [ -n "$USR" ] && [ -d "/home/$USR/Desktop" ]; then
  cat > "/home/$USR/Desktop/PiMesh.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=PiMesh Dashboard
Comment=Live dashboard of every Pi in the mesh
Exec=xdg-open http://localhost:8470
Icon=$DIR/icon-256.png
Terminal=false
DESK
  chown "$USR:$USR" "/home/$USR/Desktop/PiMesh.desktop"
  chmod +x "/home/$USR/Desktop/PiMesh.desktop"
  echo "== Added PiMesh icon to $USR's Desktop"
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "PiMesh is running.  Dashboard:  http://${IP:-localhost}:8470"
echo "Logs:  journalctl -u pimesh -f     Config:  $CFG"
