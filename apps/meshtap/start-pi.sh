#!/usr/bin/env bash
# MeshTap launcher for Raspberry Pi / Linux. If MeshTap is already running, just opens it.
cd "$(dirname "$0")"
PORT="${MESHTAP_PORT:-8742}"
up(){ python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/meshtap.js', timeout=1)" 2>/dev/null; }
if up; then xdg-open "http://localhost:$PORT" >/dev/null 2>&1; exit 0; fi
if [ ! -x .venv/bin/python ]; then
  echo "Setting up MeshTap for the first time (needs internet once)..."
  python3 -m venv .venv || { echo "Run: sudo apt install python3-venv"; read -r -p "Press Enter to close." _; exit 1; }
fi
if [ ! -f .venv/installed.txt ]; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt && echo ok > .venv/installed.txt
fi
if ! groups | grep -qw dialout; then
  echo "Note: to use a USB radio, run once:  sudo usermod -aG dialout $USER   then log out and back in."
fi
exec .venv/bin/python meshtap.py --web-port "$PORT" "$@"
