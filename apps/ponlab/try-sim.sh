#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 -c "import flask" 2>/dev/null || sudo apt-get install -y python3-flask || pip install flask --break-system-packages -q
PONLAB_SIM=1 exec python3 app.py --open
