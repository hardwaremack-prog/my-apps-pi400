#!/usr/bin/env bash
# Start Blueprint Wiki (if it isn't already running) and open it in the browser.
#   ./start.sh               start + open browser
#   ./start.sh --no-browser  start only (used by autostart)
cd "$(dirname "$0")" || exit 1

# Finds a running Blueprint by asking for /bpw.js, so another program on the same port is never mistaken for it.
find_port() {
  python3 - <<'PY'
import urllib.request
for p in (8080, 8081, 8082, 8083):
    try:
        if b"__blueprint" in urllib.request.urlopen("http://127.0.0.1:%d/bpw.js" % p, timeout=0.6).read():
            print(p); break
    except Exception:
        pass
PY
}

PORT="$(find_port)"
if [ -z "$PORT" ]; then
  nohup python3 server.py > blueprint.log 2>&1 &
  for _ in $(seq 1 40); do PORT="$(find_port)"; [ -n "$PORT" ] && break; sleep 0.25; done
fi
if [ -z "$PORT" ]; then echo "Blueprint did not start - see blueprint.log"; exit 1; fi

URL="http://localhost:$PORT"
echo "Blueprint Wiki is running at $URL"
if [ "$1" != "--no-browser" ]; then
  (xdg-open "$URL" || python3 -m webbrowser "$URL") >/dev/null 2>&1 &
fi
