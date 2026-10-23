#!/bin/bash
# Opens PodDeck. If it's installed as a service it's already running;
# otherwise this starts it for now (close the window to stop it).
cd "$(dirname "$0")"
PORT="${PODDECK_PORT:-8070}"
open_browser(){ (sleep 2; xdg-open "http://localhost:$PORT" >/dev/null 2>&1) & }
if ! command -v podman >/dev/null 2>&1; then
  echo "Podman isn't installed yet. Setting it up now (bash install.sh)..."
  bash install.sh && open_browser && sleep 3; exit
fi
if systemctl --user is-active --quiet poddeck 2>/dev/null; then
  xdg-open "http://localhost:$PORT" >/dev/null 2>&1; exit
fi
open_browser
exec python3 poddeck.py
