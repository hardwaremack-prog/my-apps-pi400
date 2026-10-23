#!/bin/bash
# NetSentry launcher for Raspberry Pi / Linux / macOS
cd "$(dirname "$0")"
exec python3 netsentry.py "$@"
