#!/usr/bin/env bash
# Meikai launcher script
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Use XCB backend for Wayland compatibility
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export QT_AUTO_SCREEN_SCALE_FACTOR=1

exec python3 "$SCRIPT_DIR/main.py" "$@"
