#!/usr/bin/env bash
# Removes the PodDeck service, its password and its own shortcut.
# Your containers and images are NOT touched.
systemctl --user disable --now poddeck.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/poddeck.service"
rm -rf "$HOME/.config/poddeck" "$HOME/.local/state/poddeck"
# Only remove the shortcut install.sh made (My Apps' own icon stays)
grep -q "^Exec=xdg-open http://localhost" "$HOME/Desktop/PodDeck.desktop" 2>/dev/null && rm -f "$HOME/Desktop/PodDeck.desktop"
systemctl --user daemon-reload 2>/dev/null || true
echo "✓ PodDeck removed. Your containers are still there (podman ps -a)."
