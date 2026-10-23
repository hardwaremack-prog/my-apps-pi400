#!/bin/bash
# WiFi Share on Raspberry Pi - shares the Pi's Wi-Fi to its Ethernet port.
cd "$(dirname "$0")"
python3 wifi_share.py "$@"
