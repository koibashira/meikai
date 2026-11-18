#!/usr/bin/env bash
# install_desktop.sh — Install Meikai desktop integration on KDE/Linux
# Automatically adapts to the directory where it is run from.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ICON_SRC="$SCRIPT_DIR/meikai.png"
APP_DESKTOP="$SCRIPT_DIR/meikai.desktop"
DOLPHIN_DESKTOP="$SCRIPT_DIR/meikai-dolphin.desktop"
LAUNCHER="$SCRIPT_DIR/meikai.sh"

chmod +x "$LAUNCHER" "$SCRIPT_DIR/install_desktop.sh" "$SCRIPT_DIR/main.py" "$APP_DESKTOP" "$DOLPHIN_DESKTOP" 2>/dev/null || true

echo "→ Configuring Meikai for directory: $SCRIPT_DIR"

# Update local desktop files to point to the current location
sed -i "s|^Exec=.*|Exec=$LAUNCHER %F|" "$APP_DESKTOP"
sed -i "s|^Exec=.*|Exec=$LAUNCHER %F|" "$DOLPHIN_DESKTOP"

echo "→ Installing Meikai desktop integration..."

# Desktop launcher
mkdir -p ~/.local/share/applications
cp "$APP_DESKTOP" ~/.local/share/applications/meikai.desktop
chmod +x ~/.local/share/applications/meikai.desktop
echo "  ✔ Installed launcher to ~/.local/share/applications/meikai.desktop"

# Dolphin service menu
mkdir -p ~/.local/share/kio/servicemenus
cp "$DOLPHIN_DESKTOP" ~/.local/share/kio/servicemenus/meikai.desktop
chmod +x ~/.local/share/kio/servicemenus/meikai.desktop
echo "  ✔ Installed Dolphin service menu to ~/.local/share/kio/servicemenus/meikai.desktop"

# Icons
if [ -f "$ICON_SRC" ]; then
    for SIZE in 16 24 32 48 64 96 128 256; do
        ICON_DIR="$HOME/.local/share/icons/hicolor/${SIZE}x${SIZE}/apps"
        mkdir -p "$ICON_DIR"
        if command -v convert &>/dev/null; then
            convert "$ICON_SRC" -resize "${SIZE}x${SIZE}" "$ICON_DIR/meikai.png" 2>/dev/null || cp "$ICON_SRC" "$ICON_DIR/meikai.png"
        else
            cp "$ICON_SRC" "$ICON_DIR/meikai.png"
        fi
    done
    echo "  ✔ Installed icons to ~/.local/share/icons/hicolor/"
fi

# Update desktop DB and icon cache
command -v update-desktop-database &>/dev/null && update-desktop-database ~/.local/share/applications/ 2>/dev/null || true
command -v gtk-update-icon-cache &>/dev/null && gtk-update-icon-cache -f ~/.local/share/icons/hicolor/ 2>/dev/null || true
command -v kbuildsycoca6 &>/dev/null && kbuildsycoca6 --noincremental 2>/dev/null || true
command -v kbuildsycoca5 &>/dev/null && kbuildsycoca5 --noincremental 2>/dev/null || true

# Register KDE Plasma KWin rule to permanently exclude Dynamic Island from task manager
if [ -f "$HOME/.config/kwinrulesrc" ] || command -v qdbus6 &>/dev/null; then
    python3 -c '
import configparser, os, subprocess
path = os.path.expanduser("~/.config/kwinrulesrc")
if os.path.isfile(path):
    config = configparser.ConfigParser(interpolation=None)
    config.read(path)
    if "meikai-island" not in config.sections():
        config["meikai-island"] = {
            "Description": "Meikai Dynamic Island",
            "skiptaskbar": "true",
            "skiptaskbarrule": "2",
            "skippager": "true",
            "skippagerrule": "2",
            "wmclass": "meikai-island",
            "wmclassmatch": "1",
        }
        if "General" in config and "Order" in config["General"]:
            order = config["General"]["Order"].split(",")
            if "meikai-island" not in order:
                order.append("meikai-island")
                config["General"]["Order"] = ",".join(order)
        with open(path, "w") as f:
            config.write(f, space_around_delimiters=False)
        subprocess.run(["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
' 2>/dev/null || true
fi

echo ""
echo "✔ Installation complete! Configured for: $SCRIPT_DIR"
echo "   • Search 'Meikai' in KDE launcher"
echo "   • Right-click images in Dolphin → 'Open with Meikai'"
echo "   • Run: $LAUNCHER [image_path]"
