#!/usr/bin/env bash
# Removes the PodDeck service and shortcut. Your containers and images are NOT touched.
systemctl --user disable --now poddeck.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/poddeck.service" "$HOME/Desktop/PodDeck.desktop"
systemctl --user daemon-reload
echo "✓ PodDeck removed. Your containers are still there (podman ps -a)."
