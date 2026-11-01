#!/bin/bash
# Opens PiPortal. The first time it runs the installer (packages, password, start-on-boot);
# after that PiPortal is already running in the background and this just opens it.
cd "$(dirname "$0")"
PORT="${PIPORTAL_PORT:-6090}"
CFG="$HOME/.config/piportal/config.json"
SCHEME=http
grep -q '"tls": true' "$CFG" 2>/dev/null && SCHEME=https
up(){ python3 -c "import urllib.request,ssl; urllib.request.urlopen('$SCHEME://127.0.0.1:$PORT/piportal.js', timeout=1, context=ssl._create_unverified_context())" 2>/dev/null; }
open_it(){ xdg-open "$SCHEME://localhost:$PORT" >/dev/null 2>&1 & sleep 2; }

if up; then open_it; exit; fi

if [ ! -f /etc/systemd/system/piportal.service ] || ! grep -q pbkdf2 "$CFG" 2>/dev/null; then
  echo "Setting up PiPortal (one time)..."
  if bash install.sh; then open_it; sleep 2; exit; fi
  echo; read -r -p "Setup didn't finish. Press Enter to close." _; exit 1
fi

sudo -n systemctl start piportal 2>/dev/null || sudo systemctl start piportal
for _ in $(seq 1 16); do up && { open_it; exit; }; sleep 0.5; done

echo "PiPortal's background service didn't start; running it in this window instead (close it to stop)."
(sleep 2; xdg-open "$SCHEME://localhost:$PORT" >/dev/null 2>&1) &
exec python3 "$HOME/.local/share/piportal/server.py"
