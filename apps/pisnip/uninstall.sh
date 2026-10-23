#!/bin/bash
# Removes PiSnip. Your screenshots and recordings are left alone.
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
rm -f "$HOME/.local/bin/pisnip" "$HOME/.local/share/applications/pisnip.desktop" "$DESK/pisnip.desktop" \
      "$HOME"/.local/share/icons/hicolor/*/apps/pisnip.png
rm -rf "$HOME/.local/share/pisnip"
for f in "$HOME/.config/labwc/rc.xml" "$HOME/.config/openbox/lxde-pi-rc.xml" "$HOME/.config/wayfire.ini" \
         "$HOME/.config/wf-panel-pi/wf-panel-pi.ini" "$HOME/.config/wf-panel-pi.ini" "$HOME/.config/lxpanel-pi/panels/panel" \
         "$HOME/.config/lxpanel/LXDE-pi/panels/panel" "$HOME/.config/libfm/libfm.conf"; do
    if [ -f "$f.pisnip-backup" ]; then
        mv "$f.pisnip-backup" "$f"
        echo "Restored $f"
    fi
done
# take the taskbar button out of panel files that had no backup
python3 - <<'PY'
import os, re
h = os.path.expanduser("~/.config")
for p in ["wf-panel-pi/wf-panel-pi.ini", "wf-panel-pi.ini", "lxpanel-pi/panels/panel", "lxpanel/LXDE-pi/panels/panel"]:
    p = os.path.join(h, p)
    if os.path.exists(p):
        t = open(p).read()
        n = re.sub(r"^\s*launcher_\d+\s*=\s*pisnip\.desktop\s*\n", "", t, flags=re.M)
        n = re.sub(r"[ \t]*Button\s*\{\s*id=pisnip\.desktop\s*\}\s*\n", "", n)
        if n != t:
            open(p, "w").write(n)
PY
command -v labwc >/dev/null && labwc --reconfigure >/dev/null 2>&1
command -v openbox >/dev/null && openbox --reconfigure >/dev/null 2>&1
echo "PiSnip removed. (Settings are in ~/.config/pisnip if you want to delete them too.)"
