#!/usr/bin/env bash
set -e
if [ "$EUID" -ne 0 ]; then echo "Please run with sudo:  sudo ./uninstall.sh"; exit 1; fi
systemctl disable --now pimesh 2>/dev/null || true
rm -f /etc/systemd/system/pimesh.service
systemctl daemon-reload
rm -rf /opt/pimesh
[ -n "$SUDO_USER" ] && rm -f "/home/$SUDO_USER/Desktop/PiMesh.desktop"
echo "PiMesh removed. (Config kept at /etc/pimesh.json - delete it if you like.)"
