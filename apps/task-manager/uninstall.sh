#!/bin/bash
# Removes RPi400 Task Manager.   Run:  sudo bash uninstall.sh
# Leaves VNC, desktop auto-login and any overclock alone (change those in raspi-config
# or with the Stock button in the app before uninstalling).
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo:  sudo bash uninstall.sh"; exit 1; }
USER_NAME="${SUDO_USER:-$(getent passwd 1000 | cut -d: -f1)}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"

/usr/local/bin/rpi400-helper led stop >/dev/null 2>&1
systemctl disable --now rpi400-web.service 2>/dev/null
rm -f /etc/systemd/system/rpi400-web.service /usr/local/bin/rpi400-web
rm -f "$USER_HOME/.config/rpi400-taskmgr/web-password.json"
rm -f /etc/NetworkManager/dispatcher.d/90-rpi400-led /etc/rpi400-taskmgr.conf
systemctl disable --now rpi400-fallback-ap.service 2>/dev/null
rm -f /etc/systemd/system/rpi400-fallback-ap.service
systemctl daemon-reload
nmcli con delete RPI400-OPEN 2>/dev/null
rm -rf /opt/rpi400-taskmgr
rm -f /usr/local/bin/rpi400-taskmgr /usr/local/bin/rpi400-helper /usr/local/sbin/rpi400-fallback-ap
rm -f /etc/sudoers.d/rpi400-taskmgr /usr/share/applications/rpi400-taskmgr.desktop
rm -f "$USER_HOME/.config/autostart/rpi400-taskmgr.desktop"
for f in "$USER_HOME/.config/labwc/rc.xml" "$USER_HOME/.config/openbox/lxde-pi-rc.xml" "$USER_HOME/.config/wayfire.ini"; do
    [ -f "$f" ] && sed -i '/rpi400/d' "$f"
done
pkill -HUP -x labwc 2>/dev/null
sudo -u "$USER_NAME" DISPLAY=:0 openbox --reconfigure >/dev/null 2>&1
echo "RPi400 Task Manager removed."
if command -v tailscale >/dev/null; then
    echo "Tailscale was left installed (other programs may use it). To remove it:  sudo apt remove tailscale"
fi
if command -v ollama >/dev/null; then
    echo "The local AI engine (Ollama) and its models were left installed."
    echo "To remove them too:  sudo systemctl disable --now ollama && sudo rm -rf /usr/local/bin/ollama /usr/local/lib/ollama /usr/share/ollama /etc/systemd/system/ollama.service"
fi
