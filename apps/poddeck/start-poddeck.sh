#!/bin/bash
# Opens PodDeck. The first time it runs the installer (Podman + start-on-boot);
# after that PodDeck is already running in the background and this just opens it.
cd "$(dirname "$0")"
PORT="${PODDECK_PORT:-8070}"
UNIT="$HOME/.config/systemd/user/poddeck.service"
up(){ python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/poddeck.js', timeout=1)" 2>/dev/null; }
open_it(){ xdg-open "http://localhost:$PORT" >/dev/null 2>&1 & sleep 2; }

if up; then open_it; exit; fi

if ! command -v podman >/dev/null 2>&1 || [ ! -f "$UNIT" ] || ! grep -qF "\"$PWD/poddeck.py\"" "$UNIT"; then
  echo "Setting up PodDeck (one time)..."
  if bash install.sh; then open_it; sleep 2; exit; fi
  echo; read -r -p "Setup didn't finish. Press Enter to close." _; exit 1
fi

systemctl --user start poddeck 2>/dev/null
for _ in $(seq 1 16); do up && { open_it; exit; }; sleep 0.5; done

# Service won't start: run it here for now so you can still use it
echo "PodDeck's background service didn't start; running it in this window instead (close it to stop)."
(sleep 2; xdg-open "http://localhost:$PORT" >/dev/null 2>&1) &
exec python3 poddeck.py
