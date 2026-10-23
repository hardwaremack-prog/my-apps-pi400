#!/usr/bin/env bash
# Halo Studio launcher for Linux / Raspberry Pi / macOS
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up Halo Studio..."
  python3 -m venv .venv || { echo "Install python3-venv first:  sudo apt install python3-venv"; exit 1; }
fi
. .venv/bin/activate
if ! cmp -s requirements.txt .venv/installed.txt; then
  python -m pip install -q --upgrade pip
  python -m pip install -q -r requirements.txt || { echo "Could not install what Halo Studio needs - check the internet."; read -r -p "Press Enter to close" _; exit 1; }
  while read -r pkg; do [ -n "$pkg" ] && python -m pip install -q "$pkg" >/dev/null 2>&1 || true; done < requirements-extras.txt
  cp requirements.txt .venv/installed.txt
fi
if [ "$1" = "--ai" ]; then python -m pip install "rembg[cpu]"; fi
exec python server.py "$@"
