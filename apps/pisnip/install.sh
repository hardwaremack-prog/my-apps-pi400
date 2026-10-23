#!/bin/bash
# PiSnip installer for Raspberry Pi OS (Bookworm / Trixie, Wayland or X11)
# Run from the unzipped folder:   bash install.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APPDIR="$HOME/.local/share/pisnip"
BIN="$HOME/.local/bin"

echo "==> Installing packages PiSnip uses (you may be asked for your password)"
PKGS="python3-tk python3-pil python3-pil.imagetk grim wl-clipboard xclip scrot x11-utils tesseract-ocr ffmpeg wf-recorder xdg-user-dirs"
sudo apt-get update -qq || true
if ! sudo apt-get install -y $PKGS; then
    echo "   Some packages failed together, trying one at a time..."
    for p in $PKGS; do sudo apt-get install -y "$p" || echo "   (skipped $p)"; done
fi

echo "==> Copying PiSnip"
mkdir -p "$APPDIR" "$BIN"
install -m 755 "$DIR/pisnip.py" "$APPDIR/pisnip.py"
ln -sf "$APPDIR/pisnip.py" "$BIN/pisnip"

echo "==> Adding Snipping Tool to the menu, desktop and taskbar"
"$APPDIR/pisnip.py" --setup-shortcuts || echo "   (couldn't add all icons - see README)"

PICS="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Pictures")"
[ "$PICS" = "$HOME" ] && PICS="$HOME/Pictures"
mkdir -p "$PICS/Screenshots"
echo "==> Screenshots will be saved in $PICS/Screenshots"

echo "==> Setting up keyboard shortcuts"
"$APPDIR/pisnip.py" --setup-hotkeys || echo "   (couldn't set shortcuts automatically - see README)"

echo
echo "Done! Click the Snipping Tool icon on your desktop or taskbar, or press:"
echo "   Print Screen            -> snip"
echo "   Raspberry + Shift + S   -> snip"
echo "   Raspberry + Shift + R   -> record the screen"
echo "If a shortcut doesn't respond yet, log out and back in (or reboot)."
