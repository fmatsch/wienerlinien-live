#!/usr/bin/env bash
# Installiert "Wiener Linien Live" auf dem Hackberry Pi (oder jedem Linux-Desktop mit Chromium).
# Läuft ohne sudo. Kann jederzeit erneut ausgeführt werden (aktualisiert die Installation).
#
#   ./install.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/wienerlinien-live/app"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"

echo "=== Wiener Linien Live: Installation ==="
command -v python3 >/dev/null || { echo "python3 fehlt"; exit 1; }
if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null && ! command -v firefox >/dev/null; then
    echo "Hinweis: weder Chromium noch Firefox gefunden. Installieren mit:  sudo apt-get install -y chromium"
fi

mkdir -p "$APP_DIR" "$BIN_DIR" "$APPS_DIR"
# Dateien kopieren (alte Version komplett ersetzen, die Konfiguration liegt ausserhalb in ~/.config)
rm -rf "$APP_DIR.new" && mkdir -p "$APP_DIR.new"
cp -R "$SRC/wl_live.py" "$SRC/web" "$SRC/VERSION" "$SRC/icon.png" "$SRC/install.sh" "$APP_DIR.new/"
rm -rf "$APP_DIR" && mv "$APP_DIR.new" "$APP_DIR"

cat > "$BIN_DIR/wienerlinien-live" <<LAUNCH
#!/usr/bin/env bash
exec python3 "$APP_DIR/wl_live.py" "\$@"
LAUNCH
chmod +x "$BIN_DIR/wienerlinien-live"

ENTRY="[Desktop Entry]
Type=Application
Name=Wiener Linien Live
GenericName=U-Bahn und Straßenbahn live
Comment=Live-Positionen der Wiener U-Bahn und Straßenbahn
Exec=$BIN_DIR/wienerlinien-live
Icon=$APP_DIR/icon.png
Terminal=false
Categories=Utility;Network;
StartupWMClass=wienerlinien-live"
printf '%s\n' "$ENTRY" > "$APPS_DIR/wienerlinien-live.desktop"
if [ -d "$DESKTOP_DIR" ]; then
    printf '%s\n' "$ENTRY" > "$DESKTOP_DIR/wienerlinien-live.desktop"
    chmod +x "$DESKTOP_DIR/wienerlinien-live.desktop"
    gio set "$DESKTOP_DIR/wienerlinien-live.desktop" metadata::trusted true 2>/dev/null || true
    echo "Desktop-Verknüpfung: $DESKTOP_DIR/wienerlinien-live.desktop"
fi
chmod +x "$APPS_DIR/wienerlinien-live.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" 2>/dev/null || true

echo "Version $(cat "$APP_DIR/VERSION") installiert."
echo "Start:  wienerlinien-live   (oder per Desktop-Symbol / Anwendungsmenü)"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "Hinweis: $BIN_DIR ist nicht im PATH (Neuanmeldung oder: export PATH=\"\$HOME/.local/bin:\$PATH\")";; esac
