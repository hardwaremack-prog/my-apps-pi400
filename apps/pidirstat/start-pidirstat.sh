#!/bin/bash
# PiDirStat on Raspberry Pi / Linux - scans the whole SD card and opens the viewer.
# Use "sudo ./start-pidirstat.sh" to include folders only root can read.
cd "$(dirname "$0")"
python3 pidirstat.py "${@:-/}" --open
