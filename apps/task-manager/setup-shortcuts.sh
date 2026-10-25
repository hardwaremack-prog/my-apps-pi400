#!/bin/bash
# Adds the global keyboard shortcuts for RPi400 Task Manager:
#   Ctrl+Shift+Esc  -> open the Task Manager
#   Ctrl+Alt+B      -> Bluetooth pairing window (keyboard only, for when the mouse is gone)
# Raspberry Pi OS has used three different desktops over the years, so this sets up
# every one that's installed: labwc (current), Wayfire (2023-24) and Openbox (X11).
#
# Run by install.sh, or by hand:   sudo bash setup-shortcuts.sh
set -u
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo:  sudo bash setup-shortcuts.sh"; exit 1; }

U="${1:-${SUDO_USER:-}}"
[ -n "$U" ] && [ "$U" != root ] || U="$(getent passwd 1000 | cut -d: -f1)"
H="$(getent passwd "$U" | cut -d: -f6)"
SYS="${RPI400_SYSROOT:-}"          # test hook: prefix for /etc paths
TM=/usr/local/bin/rpi400-taskmgr
BT="/usr/local/bin/rpi400-taskmgr --pair"
added=0

say() { echo "   $*"; }

as_user_dir() { install -d -o "$U" -g "$U" "$1"; }

# Insert our keybinds into an Openbox/labwc-style XML file (labwc|openbox).
add_xml() {
    python3 - "$1" "$2" "$TM" "$BT" <<'PY'
import sys, re
path, style, tm, bt = sys.argv[1:]
s = open(path, encoding="utf-8").read()
had = "rpi400" in s
# drop any earlier version of our shortcuts, then add the current ones
s = "".join(l for l in s.splitlines(True) if "rpi400" not in l)
if style == "labwc":
    binds = (f'<keybind key="C-S-Escape"><action name="Execute" command="{tm}"/></keybind>\n'
             f'    <keybind key="C-A-b"><action name="Execute" command="{bt}"/></keybind>')
else:
    binds = (f'<keybind key="C-S-Escape"><action name="Execute"><command>{tm}</command></action></keybind>\n'
             f'    <keybind key="C-A-b"><action name="Execute"><command>{bt}</command></action></keybind>')
block = "    <!-- rpi400-taskmgr -->\n    " + binds + "\n  "
i = s.rfind("</keyboard>")
if i >= 0:
    s = s[:i] + block + s[i:]
else:
    m = re.search(r"</(labwc_config|openbox_config)>\s*$", s)
    if not m:
        print("unrecognised"); sys.exit(1)
    # labwc only keeps its built-in shortcuts when <default/> is present
    keep = "    <default />\n" if style == "labwc" else ""
    s = s[:m.start()] + "  <keyboard>\n" + keep + block + "</keyboard>\n" + s[m.start():]
open(path, "w", encoding="utf-8").write(s)
print("updated" if had else "added")
PY
}

# ---------- labwc (Raspberry Pi OS since late 2024)
if command -v labwc >/dev/null; then
    RC="$H/.config/labwc/rc.xml"
    as_user_dir "$H/.config"; as_user_dir "$H/.config/labwc"
    if [ ! -f "$RC" ]; then
        if [ -f "$SYS/etc/xdg/labwc/rc.xml" ]; then
            cp "$SYS/etc/xdg/labwc/rc.xml" "$RC"          # start from the Pi's own settings
        else
            printf '<?xml version="1.0"?>\n<labwc_config>\n</labwc_config>\n' > "$RC"
        fi
        chown "$U:$U" "$RC"
    else
        grep -q rpi400 "$RC" || { cp "$RC" "$RC.bak-rpi400"; chown "$U:$U" "$RC.bak-rpi400"; }
    fi
    r=$(add_xml "$RC" labwc)
    chown "$U:$U" "$RC"
    case "$r" in added|updated) say "labwc: shortcuts $r"; added=1 ;;
                 *) say "labwc: couldn't read $RC" ;; esac
    pkill -HUP -x labwc 2>/dev/null || true             # labwc reloads its settings on HUP
fi

# ---------- Wayfire (Raspberry Pi OS 2023-2024)
if command -v wayfire >/dev/null; then
    INI="$H/.config/wayfire.ini"
    if [ ! -f "$INI" ] && [ -f "$SYS/etc/wayfire/template.ini" ]; then
        cp "$SYS/etc/wayfire/template.ini" "$INI"; chown "$U:$U" "$INI"
    fi
    [ -f "$INI" ] || { printf '' > "$INI"; chown "$U:$U" "$INI"; }
    if true; then
        grep -q rpi400 "$INI" || { cp "$INI" "$INI.bak-rpi400"; chown "$U:$U" "$INI.bak-rpi400"; }
        python3 - "$INI" "$TM" "$BT" <<'PY'
import sys, re
p, tm, bt = sys.argv[1:]
s = open(p, encoding="utf-8").read()
s = "".join(l for l in s.splitlines(True) if "rpi400" not in l)   # replace any earlier version
lines = ("binding_rpi400tm = <ctrl> <shift> KEY_ESC\n"
         f"command_rpi400tm = {tm}\n"
         "binding_rpi400bt = <ctrl> <alt> KEY_B\n"
         f"command_rpi400bt = {bt}\n")
m = re.search(r"^\[command\][ \t]*\n", s, re.M)
if m:
    s = s[:m.end()] + lines + s[m.end():]
else:
    s = s.rstrip("\n") + ("\n\n" if s.strip() else "") + "[command]\n" + lines
open(p, "w", encoding="utf-8").write(s)
PY
        chown "$U:$U" "$INI"
        say "Wayfire: shortcuts set"; added=1          # Wayfire picks up changes by itself
    fi
fi

# ---------- Openbox (the older X11 desktop)
if command -v openbox >/dev/null; then
    OB="$H/.config/openbox/lxde-pi-rc.xml"
    as_user_dir "$H/.config"; as_user_dir "$H/.config/openbox"
    if [ ! -f "$OB" ]; then
        for src in "$SYS/etc/xdg/openbox/lxde-pi-rc.xml" "$SYS/etc/xdg/openbox/rc.xml"; do
            [ -f "$src" ] && { cp "$src" "$OB"; break; }
        done
    else
        grep -q rpi400 "$OB" || { cp "$OB" "$OB.bak-rpi400"; chown "$U:$U" "$OB.bak-rpi400"; }
    fi
    if [ -f "$OB" ]; then
        chown "$U:$U" "$OB"
        r=$(add_xml "$OB" openbox)
        chown "$U:$U" "$OB"
        case "$r" in added|updated) say "Openbox (X11): shortcuts $r"; added=1 ;;
                     *) say "Openbox (X11): couldn't read $OB" ;; esac
        sudo -u "$U" DISPLAY=:0 openbox --reconfigure >/dev/null 2>&1 || true
    fi
fi

if [ "$added" = 1 ]; then
    say "Ctrl+Shift+Esc opens the Task Manager, Ctrl+Alt+B opens Bluetooth pairing."
    say "If they don't work straight away, log out and back in (or reboot)."
else
    say "No supported desktop found (labwc, Wayfire or Openbox), so no global shortcuts were added."
    say "Inside the app, Ctrl+B / Ctrl+M / Ctrl+H still work."
fi
exit 0
