#!/usr/bin/env bash
# Raspberry Pi / Linux desktop setup for Blueprint:
#   - puts a "Blueprint Wiki" icon on the Desktop and in the app menu
#   - starts the wiki server automatically when you log in
# Run once:  bash install-pi.sh        Undo:  bash install-pi.sh --remove
DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$DIR/start.sh" "$DIR/server.py"
APPS="$HOME/.local/share/applications"
AUTO="$HOME/.config/autostart"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

if [ "$1" = "--remove" ]; then
  rm -f "$APPS/blueprint-wiki.desktop" "$AUTO/blueprint-wiki-server.desktop" "$DESK/blueprint-wiki.desktop"
  echo "Removed Blueprint shortcuts and autostart. Your wiki data is untouched in $DIR/data"
  exit 0
fi

mkdir -p "$APPS" "$AUTO" "$DESK"
cat > "$APPS/blueprint-wiki.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Blueprint Wiki
Comment=Your local technical wiki
Exec=bash "$DIR/start.sh"
Icon=$DIR/static/icon.svg
Terminal=false
Categories=Office;Development;
EOF
cp "$APPS/blueprint-wiki.desktop" "$DESK/blueprint-wiki.desktop"
chmod +x "$DESK/blueprint-wiki.desktop" "$APPS/blueprint-wiki.desktop"
gio set "$DESK/blueprint-wiki.desktop" metadata::trusted true 2>/dev/null || true

cat > "$AUTO/blueprint-wiki-server.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Blueprint Wiki server
Exec=bash "$DIR/start.sh" --no-browser
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

bash "$DIR/start.sh"
echo
echo "Done. Blueprint now starts when you log in. Double-click 'Blueprint Wiki' on the Desktop to open it."
echo "(If the Desktop asks how to open it, choose 'Execute'.)"
