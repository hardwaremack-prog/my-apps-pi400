#!/usr/bin/env bash
# Run PiMesh in the foreground (for a permanent setup use: sudo ./install.sh)
cd "$(dirname "$0")"
CFG=/etc/pimesh.json; [ -f "$CFG" ] || CFG=pimesh.example.json
exec python3 pimesh.py --config "$CFG" --open
