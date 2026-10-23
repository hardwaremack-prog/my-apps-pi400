#!/usr/bin/env bash
# PON Lab installer for Raspberry Pi OS (Bookworm or newer)
# Usage:  cd ponlab && sudo ./install.sh              (keeps config.json as is)
#         sudo ./install.sh waveshare-8ch             (uses config.waveshare-8ch.json)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-pi}"

if [ -n "$1" ]; then
  if [ ! -f "$DIR/config.$1.json" ]; then echo "No config.$1.json here"; exit 1; fi
  [ -f "$DIR/config.json" ] && cp "$DIR/config.json" "$DIR/config.json.bak"
  cp "$DIR/config.$1.json" "$DIR/config.json"
  echo "Using config.$1.json (old config saved as config.json.bak)"
fi

echo "Installing packages..."
apt-get update -qq
apt-get install -y -qq python3-flask python3-gpiozero python3-lgpio

echo "Keeping the clock in sync (PON Fleet lines up cuts by clock)..."
timedatectl set-ntp true || true

# Relay boards that are active-low would switch ON while the Pi boots (pins float low),
# which with NC wiring would cut the meters for a few seconds. Drive the relay pins
# HIGH from the very start of boot so meters stay powered while the Pi starts.
PINS=$(python3 -c "import json;c=json.load(open('$DIR/config.json'));print(','.join(str(x['gpio']) for x in c['channels']) if not c.get('relay_active_high',False) else '')")
BOOTCFG=/boot/firmware/config.txt; [ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
if [ -n "$PINS" ] && [ -f "$BOOTCFG" ]; then
  sed -i '/# ponlab-relay-pins/d' "$BOOTCFG"
  echo "gpio=${PINS}=op,dh # ponlab-relay-pins" >> "$BOOTCFG"
  echo "Relay pins ${PINS} will be held HIGH from boot (in $BOOTCFG)"
fi

echo "Creating service..."
cat > /etc/systemd/system/ponlab.service <<UNIT
[Unit]
Description=PON Lab outage simulator
After=network-online.target time-sync.target

[Service]
User=${USER_NAME}
WorkingDirectory=${DIR}
ExecStart=/usr/bin/python3 ${DIR}/app.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT

usermod -aG gpio "${USER_NAME}" || true
systemctl daemon-reload
systemctl enable --now ponlab.service
systemctl restart ponlab.service

IP=$(hostname -I | awk '{print $1}')
echo
echo "PON Lab is running:  http://${IP}:8080"
echo "Add this Pi to PON Fleet with the IP above."
echo "Logs:   journalctl -u ponlab -f"
echo "Config: ${DIR}/config.json  (then: sudo systemctl restart ponlab)"
echo "Tip: set \"api_token\" in config.json so only your fleet server can switch relays."
