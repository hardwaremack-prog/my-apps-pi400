#!/usr/bin/env bash
# Try PiMesh with 6 simulated Pis (no install needed)
cd "$(dirname "$0")"
exec python3 pimesh.py --demo --open --config pimesh.example.json --mesh demo
