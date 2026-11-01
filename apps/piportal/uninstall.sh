#!/usr/bin/env bash
# Removes PiPortal (keeps installed apt packages).
set -e
sudo systemctl disable --now piportal.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/piportal.service /etc/sudoers.d/piportal
sudo systemctl daemon-reload
rm -rf "$HOME/.local/share/piportal" "$HOME/.local/share/applications/piportal.desktop"
read -rp "Also delete your PiPortal password and settings? [y/N] " a
[[ "$a" =~ ^[Yy] ]] && rm -rf "$HOME/.config/piportal"
echo "PiPortal removed."
