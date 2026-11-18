#!/usr/bin/env python3
"""
Meikai — Mathematical LaTeX Transcription Studio
Entry point.

Background model:
  - App starts with the Dynamic Island visible (Tool window, never in taskbar)
  - Main Studio window is a normal Window (shows in taskbar when open)
  - Closing the studio hides it; only File→Quit exits the process
  - Single-instance via UNIX socket IPC
"""
import argparse
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

from meikai.config import CACHE_DIR, SHARED_KEYS_DIR, SHARED_KEYS_FILE, save_api_key
from meikai.ipc import IpcServer, send_to_running_instance
from meikai.database import get_db


def add_api_key(key: str, label: str = "") -> int:
    key = key.strip()
    if not key:
        print("[Meikai] Error: API key cannot be empty.")
        return 1
    if save_api_key(key, label):
        print(f"[Meikai] API key saved successfully to {SHARED_KEYS_FILE}")
        return 0
    else:
        print(f"[Meikai] Error saving API key to {SHARED_KEYS_FILE}")
        return 1


def list_api_keys() -> int:
    from meikai.gemini import load_all_keys
    keys = load_all_keys()
    if not keys:
        print("[Meikai] No API keys configured in ~/.config/gemini-shared/config.json or environment.")
        return 0
    print(f"[Meikai] Configured API keys ({len(keys)} total):")
    for i, k in enumerate(keys, 1):
        key = k.get("key", "")
        masked = f"{key[:6]}...{key[-4:]}" if len(key) > 12 else key
        lbl = k.get("label", "(no label)")
        print(f"  {i}. {lbl}: {masked}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="meikai",
        description="Meikai — Mathematical LaTeX Transcription Studio"
    )
    parser.add_argument("files", nargs="*", help="Image file(s) to open")
    parser.add_argument("--add-key", metavar="KEY", help="Add a Gemini API key to shared config")
    parser.add_argument("--label", default="", help="Optional label for the API key (used with --add-key)")
    parser.add_argument("--list-keys", action="store_true", help="List configured API keys")
    args = parser.parse_args()

    if args.add_key:
        return add_api_key(args.add_key, args.label)

    if args.list_keys:
        return list_api_keys()

    # ── Single-instance guard ─────────────────────────────────────────────────
    if args.files:
        # Check if another instance is already running
        forwarded = False
        for fpath in args.files:
            abs_p = os.path.abspath(fpath)
            if send_to_running_instance(f"open:{abs_p}"):
                print(f"[Meikai] Forwarded '{fpath}' to running instance.")
                forwarded = True
            else:
                break
        if forwarded:
            return 0
    else:
        # No files provided: if an instance is already running, focus it and exit
        if send_to_running_instance("focus"):
            print("[Meikai] Focused running instance.")
            return 0

    # ── Qt app setup ──────────────────────────────────────────────────────────
    # Force XCB for Wayland compositors (Dynamic Island needs x=0,y=0 positioning)
    if "WAYLAND_DISPLAY" in os.environ and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    # Critical: tell Qt not to quit when the last *visible* window is closed
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Meikai")
    app.setApplicationDisplayName("Meikai")
    app.setOrganizationName("Meikai")
    app.setApplicationVersion("1.0.0")

    # ── App icon ──────────────────────────────────────────────────────────────
    icon_path = os.path.join(_here, "meikai.png")
    app_icon = QIcon(icon_path) if os.path.isfile(icon_path) else QIcon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    # ── Database ─────────────────────────────────────────────────────────────
    os.makedirs(CACHE_DIR, exist_ok=True)
    get_db()

    # ── Main window ───────────────────────────────────────────────────────────
    from meikai.ui.main_window import MainWindow
    window = MainWindow(app_icon if not app_icon.isNull() else None)

    # ── Dynamic Island ────────────────────────────────────────────────────────
    from meikai.ui.dynamic_island import DynamicIsland
    island = DynamicIsland()

    # Wire bidirectional sync
    window.set_island(island)

    # ── System tray — ONE icon only, closing quits both ───────────────────────
    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable() and not app_icon.isNull():
        tray = QSystemTrayIcon(app_icon, app)
        tray_menu = QMenu()
        tray_menu.addAction("Open Studio", window.show_and_raise)
        tray_menu.addAction("New Tab", lambda: (window.show_and_raise(), window._new_tab()))
        tray_menu.addSeparator()
        tray_menu.addAction("Show/Hide Island",
                            lambda: island.setVisible(not island.isVisible()))
        tray_menu.addSeparator()
        tray_menu.addAction("Quit Meikai", window._quit_app)
        tray.setContextMenu(tray_menu)
        tray.setToolTip("Meikai — LaTeX Transcription Studio")
        tray.activated.connect(lambda reason: (
            window.show_and_raise()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        ))
        tray.show()

    # ── IPC server ────────────────────────────────────────────────────
    def _on_ipc(msg: str):
        if msg == "ping":
            return
        if msg.startswith("open:"):
            path = msg[5:].strip()
            if os.path.isfile(path):
                QTimer.singleShot(0, lambda p=path: window.open_image_path(p))
                return
        QTimer.singleShot(0, window.show_and_raise)

    ipc = IpcServer(_on_ipc)
    ipc.start()

    # ── Handle CLI path ───────────────────────────────────────────────────────
    if args.files:
        def _open_all(paths):
            for p in paths:
                abs_p = os.path.abspath(p)
                if os.path.isfile(abs_p):
                    window.open_image_path(abs_p)
        QTimer.singleShot(300, lambda: _open_all(args.files))

    # ── Show ─────────────────────────────────────────────────────────────────
    island.show()
    window.show()

    ret = app.exec()
    if hasattr(window, "_preview"):
        window._preview.cleanup()
    if hasattr(island, "_preview"):
        island._preview.cleanup()
    window._cleanup_worker()
    island._cleanup_worker() if hasattr(island, "_cleanup_worker") else None
    ipc.stop()
    return ret


if __name__ == "__main__":
    sys.exit(main())
