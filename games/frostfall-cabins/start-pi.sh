#!/bin/sh
# Frostfall Cabins - start the LAN server (Raspberry Pi / Linux / Mac)
cd "$(dirname "$0")"
python3 server.py "$@"
echo
printf "Press Enter to close..."
read _
