#!/bin/bash
# RPi400 Task Manager installer
# Run from this folder:   sudo bash install.sh
#
# Optional settings, typed after sudo, e.g.:   sudo SKIP_AI=1 bash install.sh
#   WIFI_COUNTRY=US        Wi-Fi country used only if none is set yet (needed for the hotspot)
#   SKIP_AUTOLOGIN=1       don't switch the Pi to boot straight into the desktop
#   VNC_RESOLUTION=1920x1080   screen size VNC uses when no monitor is plugged in
#   SKIP_AI=1              don't install the local AI (Ollama)
#   AI_MODEL=gemma3:1b     which AI model to download (default: llama3.2:3b with 8 GB+ RAM, else qwen2.5:0.5b)
#   TAILSCALE_AUTHKEY=tskey-...   join your Tailscale network straight away (otherwise switch it on in the app)
#   WEB_PASSWORD=...       turn on the web page for phones and PCs on your network, with this control password
#   WEB_PORT=8090          port for that web page
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run with sudo:   sudo bash install.sh"
    exit 1
fi

USER_NAME="${SUDO_USER:-}"
if [ -z "$USER_NAME" ] || [ "$USER_NAME" = root ]; then
    USER_NAME="$(getent passwd 1000 | cut -d: -f1)"
fi
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
WIFI_COUNTRY="${WIFI_COUNTRY:-US}"
VNC_RESOLUTION="${VNC_RESOLUTION:-1920x1080}"
MEM_GB=$(awk '/^MemTotal:/{print int($2/1024/1024+0.5)}' /proc/meminfo)
if [ -z "${AI_MODEL:-}" ]; then
    if [ "${MEM_GB:-0}" -ge 7 ]; then AI_MODEL="llama3.2:3b"; else AI_MODEL="qwen2.5:0.5b"; fi
fi
MODEL_NAME="$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' || true)"

step() { echo; echo "==> $*"; }
warn() { echo "   ! $*"; }

echo "Installing RPi400 Task Manager for user: $USER_NAME"

step "Installing packages"
apt-get update -qq || warn "apt update failed (offline?) — continuing"
apt-get install -y python3-tk python3-psutil python3-qrcode iw rfkill curl \
    || apt-get install -y python3-tk python3-psutil iw rfkill curl \
    || warn "Some packages didn't install — the app needs python3-tk and python3-psutil"

if printf '%s' "$MODEL_NAME" | grep -q "Raspberry Pi 500"; then
    step "Pi 500 keyboard lighting tool"
    apt-get install -y rpi-keyboard-config >/dev/null 2>&1 \
        && echo "   rpi-keyboard-config installed (the keys glow while the hotspot is on, Pi 500+ only)" \
        || warn "Couldn't install rpi-keyboard-config, so the keyboard won't glow for the hotspot"
fi

step "Copying program files"
install -d /opt/rpi400-taskmgr
install -m 755 "$HERE/rpi400_taskmgr.py" /opt/rpi400-taskmgr/rpi400_taskmgr.py
install -m 644 "$HERE/VERSION" /opt/rpi400-taskmgr/VERSION
install -m 755 -o root -g root "$HERE/rpi400-helper" /usr/local/bin/rpi400-helper
install -m 755 -o root -g root "$HERE/rpi400-fallback-ap" /usr/local/sbin/rpi400-fallback-ap
cat > /usr/local/bin/rpi400-taskmgr <<'EOF'
#!/bin/sh
exec python3 /opt/rpi400-taskmgr/rpi400_taskmgr.py "$@"
EOF
chmod 755 /usr/local/bin/rpi400-taskmgr
install -d /opt/rpi400-taskmgr/web
install -m 755 "$HERE/rpi400_web.py" /opt/rpi400-taskmgr/rpi400_web.py
install -m 644 "$HERE/web/index.html" /opt/rpi400-taskmgr/web/index.html
cat > /usr/local/bin/rpi400-web <<'EOF'
#!/bin/sh
exec python3 /opt/rpi400-taskmgr/rpi400_web.py "$@"
EOF
chmod 755 /usr/local/bin/rpi400-web

step "Letting the app run its root helper without a password prompt"
TMP_SUDO="$(mktemp)"
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/local/bin/rpi400-helper" > "$TMP_SUDO"
if visudo -cf "$TMP_SUDO" >/dev/null; then
    install -m 440 -o root -g root "$TMP_SUDO" /etc/sudoers.d/rpi400-taskmgr
else
    warn "sudo rule failed validation — actions will ask for a password instead"
fi
rm -f "$TMP_SUDO"

step "Setting up the RPI400-OPEN hotspot"
if command -v nmcli >/dev/null; then
    if command -v raspi-config >/dev/null; then
        CUR_COUNTRY="$(raspi-config nonint get_wifi_country 2>/dev/null || true)"
        if [ -z "$CUR_COUNTRY" ]; then
            raspi-config nonint do_wifi_country "$WIFI_COUNTRY" && echo "   Wi-Fi country set to $WIFI_COUNTRY"
        else
            echo "   Wi-Fi country already set: $CUR_COUNTRY"
        fi
    fi
    /usr/local/bin/rpi400-helper ap-ensure
    install -m 644 "$HERE/rpi400-fallback-ap.service" /etc/systemd/system/rpi400-fallback-ap.service
    systemctl daemon-reload
    systemctl enable rpi400-fallback-ap.service >/dev/null 2>&1
    echo "   Auto-hotspot at boot: on (starts ~60 s after boot if there's no network)"
    install -d /etc/NetworkManager/dispatcher.d
    install -m 755 -o root -g root "$HERE/90-rpi400-led" /etc/NetworkManager/dispatcher.d/90-rpi400-led
    grep -qs '^LED_BLINK=' /etc/rpi400-taskmgr.conf || echo "LED_BLINK=1" >> /etc/rpi400-taskmgr.conf
    chmod 644 /etc/rpi400-taskmgr.conf
    echo "   Power light flashes while the hotspot is on: $(/usr/local/bin/rpi400-helper led status)"
else
    warn "NetworkManager isn't in use on this system, so the hotspot is skipped."
    warn "Raspberry Pi OS Bookworm or newer uses it by default."
fi

step "Turning on VNC (port 5900) for headless remote access"
if command -v raspi-config >/dev/null; then
    raspi-config nonint do_vnc 0 && echo "   VNC enabled"
    if [ "${SKIP_AUTOLOGIN:-0}" != 1 ]; then
        raspi-config nonint do_boot_behaviour B4 && echo "   Boots straight into the desktop (needed for VNC with no monitor)"
    fi
    raspi-config nonint do_vnc_resolution "$VNC_RESOLUTION" >/dev/null 2>&1 \
        && echo "   Headless screen size: $VNC_RESOLUTION" || true
else
    warn "raspi-config not found — enable VNC yourself"
fi

step "Local AI (Ollama + the $AI_MODEL model)"
if [ "${SKIP_AI:-0}" = 1 ]; then
    echo "   Skipped (SKIP_AI=1). You can install it later from the AI tab."
elif [ "$(dpkg --print-architecture)" != arm64 ]; then
    warn "The AI needs the 64-bit Raspberry Pi OS, so it was skipped."
elif ! curl -fsS --max-time 10 -o /dev/null https://ollama.com; then
    warn "No internet right now, so the AI was skipped. Install it later from the AI tab."
elif /usr/local/bin/rpi400-helper ai install; then
    for _ in $(seq 1 30); do curl -fs -o /dev/null http://127.0.0.1:11434/api/tags && break; sleep 1; done
    echo "   Downloading the $AI_MODEL model (this can take a few minutes)…"
    if ollama pull "$AI_MODEL" >/dev/null 2>&1; then
        echo "   AI ready: $AI_MODEL (works offline from now on)"
    else
        warn "Model download didn't finish. Press Download in the AI tab to try again."
    fi
else
    warn "AI engine install didn't finish. You can retry from the AI tab."
fi

step "Remote access from anywhere (Tailscale)"
if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
    if /usr/local/bin/rpi400-helper vpn install >/dev/null && /usr/local/bin/rpi400-helper vpn key "$TAILSCALE_AUTHKEY"; then
        :
    else
        warn "Tailscale setup didn't finish. Switch it on from the Tools tab instead."
    fi
else
    echo "   Switch on Tailscale in the Tools tab whenever you want to reach this Pi from anywhere."
fi

step "Web page for phones and PCs on your network (off until you switch it on)"
DESK="$(sudo -u "$USER_NAME" HOME="$USER_HOME" xdg-user-dir DESKTOP 2>/dev/null || true)"
[ -n "$DESK" ] && [ "$DESK" != "$USER_HOME" ] || DESK="$USER_HOME/Desktop"
WEB_PORT="${WEB_PORT:-8090}"
sed -e "s#@USER@#$USER_NAME#" -e "s#@DESKTOP@#$DESK#" -e "s#RZ_PORT=8090#RZ_PORT=$WEB_PORT#" \
    "$HERE/rpi400-web.service" > /etc/systemd/system/rpi400-web.service
systemctl daemon-reload
if [ -n "${WEB_PASSWORD:-}" ]; then
    if printf '%s\n' "$WEB_PASSWORD" | sudo -u "$USER_NAME" HOME="$USER_HOME" python3 /opt/rpi400-taskmgr/rpi400_web.py --set-password >/dev/null \
        && /usr/local/bin/rpi400-helper web on; then
        :
    else
        warn "Couldn't turn on the web page. Use the Web page switch in the Tools tab."
    fi
elif systemctl is-enabled rpi400-web.service >/dev/null 2>&1; then
    systemctl restart rpi400-web.service && echo "   Web page is on (restarted with the new version)"
else
    echo "   Flip the Web page switch in the Tools tab when you want to use the Task Manager from a phone or PC."
fi

step "Adding menu entry and starting with the desktop"
cat > /usr/share/applications/rpi400-taskmgr.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=RPi400 Task Manager
Comment=CPU, memory, network, power and handy tools
Exec=/usr/local/bin/rpi400-taskmgr
Icon=utilities-system-monitor
Categories=System;Monitor;
Terminal=false
EOF
AUTO_DIR="$USER_HOME/.config/autostart"
install -d -o "$USER_NAME" -g "$USER_NAME" "$USER_HOME/.config" "$AUTO_DIR"
cat > "$AUTO_DIR/rpi400-taskmgr.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=RPi400 Task Manager
Exec=/usr/local/bin/rpi400-taskmgr
Icon=utilities-system-monitor
X-GNOME-Autostart-enabled=true
EOF
chown "$USER_NAME:$USER_NAME" "$AUTO_DIR/rpi400-taskmgr.desktop"

step "Keyboard shortcuts (Ctrl+Shift+Esc = Task Manager, Ctrl+Alt+B = Bluetooth pairing)"
bash "$HERE/setup-shortcuts.sh" "$USER_NAME" || warn "Shortcut setup hit a problem. Run: sudo bash setup-shortcuts.sh"

IP_NOW="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$IP_NOW" ] || IP_NOW="this Pi's address"
cat <<EOF

────────────────────────────────────────────────────────
 Done!  RPi400 Task Manager is installed.

 • Open it:  Menu → System Tools → RPi400 Task Manager
             or press Ctrl+Shift+Esc  (or run: rpi400-taskmgr)
 • It opens by itself when the desktop starts.
 • AI tab (F5): chat with a small AI that runs on the Pi itself, even offline.
 • VNC: connect to ${IP_NOW}:5900 with your Pi login.
 • Web page: flip "Web page" on in the Tools tab, then open
     http://${IP_NOW}:${WEB_PORT} on any phone or PC on your network.
 • No network at boot? After ~60 s it creates Wi-Fi
     RPI400-OPEN   password fifty-seven57   Pi at 192.168.1.250
   and the power light flashes while that hotspot is on.

 Reboot once to make sure everything starts cleanly:  sudo reboot
────────────────────────────────────────────────────────
EOF
