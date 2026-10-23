#!/bin/bash
# Starts RPi400 Performance and opens its window.
cd "$(dirname "$0")"
exec python3 rpi400perf.py "$@"
