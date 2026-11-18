"""
Main Studio window — 2-column responsive workspace.

Background behaviour:
  - When closed, the window hides (not quits). The app keeps running
    with only the Dynamic Island visible.
  - Window uses Qt.WindowType.Window (normal) so it shows in the task
    manager ONLY when visible. When hidden, it disappears from the pager.
  - Quit via File→Quit or Ctrl+Q.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import (
    QCloseEvent, QColor, QFont, QIcon, QImage, QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSizePolicy, QSplitter,
    QStatusBar, QVBoxLayout, QWidget,
)

from ..database import get_db
from ..gemini import get_client
from ..image_proc import (
    prepare_for_gemini, save_original,
    rotate_image, enhance_contrast, invert_image,
)
from ..settings import get_settings

from .tab_bar import TabBarWidget
from .crop_widget import CropWidget
from .code_editor import CodeEditor
from .math_preview import MathPreviewWidget
from .settings_dialog import SettingsDialog
from .search_dialog import SearchDialog


# ── Worker ─────────────────────────────────────────────────────────────────────
class _TranscribeWorker(QObject):
    # latex, tags, model, elapsed_ms, topic
    result_ready  = pyqtSignal(str, list, str, float, str)
    error_signal  = pyqtSignal(str)
    status_update = pyqtSignal(str)
    finished      = pyqtSignal()

    def __init__(self, image_bytes: bytes, crop_corners: Optional[list]):
        super().__init__()
        self._bytes = image_bytes
        self._corners = crop_corners

    def run(self):
        import time as _time
        from ..image_proc import prepare_for_gemini, save_original
        from ..gemini import get_client
        try:
            path = save_original(self._bytes)
            data = prepare_for_gemini(path, self._corners)
            client = get_client()
            t0 = _time.monotonic()
            result = client.transcribe(data, on_status=self.status_update.emit)
            elapsed_ms = (_time.monotonic() - t0) * 1000
            if result.get("error"):
                self.error_signal.emit(result["error"])
            else:
                self.result_ready.emit(
                    result.get("latex", ""),
                    result.get("tags", []),
                    result.get("model", ""),
                    elapsed_ms,
                    result.get("topic", ""),
                )
        except Exception as e:
            self.error_signal.emit(str(e))
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self, app_icon: Optional[QIcon] = None):
        super().__init__()
        self._settings = get_settings()
        self._theme = self._settings.theme
        self._db = get_db()
        self._active_tab_id: Optional[str] = None
        self._tab_data: Dict[str, dict] = {}
        self._is_busy = False
        self._worker: Optional[_TranscribeWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._island = None
        self._inhibit_island_sync = False

        self.setWindowTitle("Meikai — Mathematical LaTeX Studio")
        if app_icon:
            self.setWindowIcon(app_icon)
        self.setMinimumSize(880, 580)
        self.setAcceptDrops(True)

        self._setup_menu()
        self._setup_ui()
        self._setup_shortcuts()
        self._apply_style()
        self._load_tabs()
        self._setup_clipboard_watcher()
        self._restore_geometry()
        # Intercept Ctrl+V globally within our window tree so paste works
        # even when focus is in a child widget (e.g. the code editor)
        QApplication.instance().installEventFilter(self)

    # ── Menu ───────────────────────────────────────────────────────────────────
    def _setup_menu(self):
        mb = self.menuBar()

        fm = mb.addMenu("&File")
        fm.addAction("Open File…\tCtrl+O",      self._open_file_dialog)
        fm.addSeparator()
        fm.addAction("New Tab\tCtrl+T",         self._new_tab)
        fm.addAction("Close Tab\tCtrl+W",        self._close_active_tab)
        fm.addAction("Reopen Last\tCtrl+Shift+T", self._reopen_last_tab)
        fm.addSeparator()
        fm.addAction("Next Tab\tCtrl+Tab",       self._next_tab)
        fm.addAction("Previous Tab\tCtrl+Shift+Tab", self._prev_tab)
        fm.addSeparator()
        fm.addAction("Settings\tCtrl+,",         self._open_settings)
        fm.addSeparator()
        fm.addAction("Hide to Island\tCtrl+H",   self.hide)
        fm.addAction("Quit\tCtrl+Q",             self._quit_app)

        em = mb.addMenu("&Edit")
        em.addAction("Transcribe\tEnter",        self._transcribe)
        em.addAction("Re-transcribe\tAlt+Enter", self._transcribe)
        em.addSeparator()
        em.addAction("Search Tabs\tCtrl+P",      self._open_search)
        em.addSeparator()
        em.addAction("Copy LaTeX\tCtrl+Shift+C", self._copy_latex)
        em.addAction("Copy for AI\tCtrl+Alt+A",  self._copy_for_ai)
        em.addAction("Copy PNG\tCtrl+Alt+C",     self._copy_png)

        vm = mb.addMenu("&View")
        vm.addAction("Show/Hide Island",         self._toggle_island)

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Tab bar row ────────────────────────────────────────────────────────
        top = QHBoxLayout()
        top.setContentsMargins(8, 4, 8, 0)
        top.setSpacing(4)

        self._tab_bar = TabBarWidget(self._theme, overflow_mode=self._settings.tab_overflow_mode)
        self._tab_bar.tab_selected.connect(self._select_tab)
        self._tab_bar.tab_close_requested.connect(self._close_tab)
        self._tab_bar.tab_renamed.connect(self._rename_tab)
        self._tab_bar.tab_pin_toggled.connect(self._toggle_pin)
        self._tab_bar.new_tab_requested.connect(self._new_tab)
        top.addWidget(self._tab_bar)

        for icon, tip, fn in [
            ("🔍", "Quick Search (Ctrl+P)", self._open_search),
            ("⚙",  "Settings (Ctrl+,)",    self._open_settings),
        ]:
            b = QPushButton(icon)
            b.setFixedSize(32, 32)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            top.addWidget(b)
        v.addLayout(top)

        # ── Image toolbar ──────────────────────────────────────────────────────
        tb = QHBoxLayout()
        tb.setContentsMargins(8, 4, 8, 4)
        tb.setSpacing(4)

        for icon, tip, fn in [
            ("↺",  "Rotate Left",       self._rotate_left),
            ("↻",  "Rotate Right",      self._rotate_right),
            ("⚡",  "Enhance Contrast",  self._enhance_contrast),
            ("🌓",  "Invert",            self._invert),
            ("↩",  "Reset Crop",        self._reset_crop),
        ]:
            b = QPushButton(icon)
            b.setFixedSize(32, 32)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            tb.addWidget(b)

        tb.addStretch()

        self._btn_transcribe = QPushButton("▶  Transcribe  (Enter)")
        self._btn_transcribe.setFixedHeight(32)
        self._btn_transcribe.clicked.connect(self._transcribe)
        tb.addWidget(self._btn_transcribe)
        v.addLayout(tb)

        # ── Main splitter: crop | editor+preview ───────────────────────────────
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)

        self._crop = CropWidget(self._theme)
        self._crop.setMinimumWidth(280)
        self._crop.corners_changed.connect(self._on_crop_changed)
        self._crop.image_dropped.connect(self._stage_image)
        self._main_splitter.addWidget(self._crop)

        rp = QWidget()
        rl = QVBoxLayout(rp)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        self._right_splitter = QSplitter(Qt.Orientation.Vertical)

        self._editor = CodeEditor(self._theme)
        self._editor.latex_changed.connect(self._on_latex_changed)
        self._right_splitter.addWidget(self._editor)

        self._preview = MathPreviewWidget(self._theme)
        self._right_splitter.addWidget(self._preview)
        self._right_splitter.setSizes([260, 260])

        rl.addWidget(self._right_splitter)

        # Copy row
        cr = QHBoxLayout()
        cr.setContentsMargins(8, 4, 8, 4)
        cr.addStretch()
        for lbl, tip, fn in [
            ("⎘ Copy LaTeX",  "Ctrl+Shift+C", self._copy_latex),
            ("🤖 Copy for AI", "Ctrl+Alt+A",   self._copy_for_ai),
            ("⎙ Copy PNG",    "Ctrl+Alt+C",   self._copy_png),
        ]:
            b = QPushButton(lbl)
            b.setFixedHeight(28)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            cr.addWidget(b)
        rl.addLayout(cr)

        self._main_splitter.addWidget(rp)
        self._main_splitter.setSizes([440, 500])
        v.addWidget(self._main_splitter)

        # ── Status bar ────────────────────────────────────────────────────────
        sb = QStatusBar()
        sb.setFixedHeight(24)
        self.setStatusBar(sb)
        self._status_lbl = QLabel("Ready")
        sb.addWidget(self._status_lbl)
        self._tags_lbl = QLabel("")
        self._tags_lbl.setStyleSheet(f"color: {self._theme.get('accent', '#58a6ff')}; font-size: 11px;")
        sb.addPermanentWidget(self._tags_lbl)
        self._model_lbl = QLabel("")
        sb.addPermanentWidget(self._model_lbl)

        # Restore splits
        for key, splitter in [
            ("ui/split_main", self._main_splitter),
            ("ui/split_right", self._right_splitter),
        ]:
            saved = self._settings.get(key)
            if saved:
                try:
                    splitter.restoreState(bytes(saved))
                except Exception:
                    pass

    def _setup_shortcuts(self):
        def sc(key, fn):
            QShortcut(QKeySequence(key), self).activated.connect(fn)

        sc("Ctrl+O",       self._open_file_dialog)
        sc("Ctrl+T",       self._new_tab)
        sc("Ctrl+W",       self._close_active_tab)
        sc("Ctrl+Shift+T", self._reopen_last_tab)
        sc("Ctrl+Tab",       self._next_tab)
        sc("Ctrl+Shift+Tab", self._prev_tab)
        sc("Ctrl+Page_Down", self._next_tab)
        sc("Ctrl+Page_Up",   self._prev_tab)
        sc("Ctrl+P",       self._open_search)
        sc("Ctrl+K",       self._open_search)
        sc("Ctrl+,",       self._open_settings)
        sc("Ctrl+Shift+C", self._copy_latex)
        sc("Ctrl+Alt+A",   self._copy_for_ai)
        sc("Ctrl+Alt+C",   self._copy_png)
        sc("Return",       self._transcribe)
        sc("Ctrl+Return",  self._transcribe)
        sc("Alt+Return",   self._transcribe)
        sc("Ctrl+H",       self.hide)

    # ── Tab management ─────────────────────────────────────────────────────────
    def _load_tabs(self):
        tabs = self._db.get_all_tabs()
        if not tabs:
            tab = self._db.create_tab("New Problem")
            tabs = [tab]
        for t in tabs:
            img_p = t.get("orig_path")
            self._tab_data[t["id"]] = {
                "image_bytes": None,  # Lazy-loaded on demand when selected
                "img_path": img_p,
                "crop_corners": t.get("crop_points"),
            }
        self._refresh_tab_bar(tabs)
        saved_active_id = self._settings.get("ui/active_tab_id")
        tab_ids = [t["id"] for t in tabs]
        if saved_active_id and saved_active_id in tab_ids:
            self._select_tab(saved_active_id)
        else:
            self._select_tab(tabs[0]["id"])

    def _refresh_tab_bar(self, tabs=None):
        if tabs is None:
            tabs = self._db.get_all_tabs()
        self._tab_bar.set_tabs([
            {"id": t["id"], "title": t["title"], "pinned": t.get("pinned", False)}
            for t in tabs
        ])
        self._tab_bar.set_active(self._active_tab_id)

    def _new_tab(self):
        tab = self._db.create_tab("New Problem")
        self._tab_data[tab["id"]] = {"image_bytes": None, "img_path": None, "crop_corners": None}
        self._refresh_tab_bar()
        self._select_tab(tab["id"])

    def _select_tab(self, tab_id: str):
        # Save current latex
        if self._active_tab_id and self._active_tab_id != tab_id:
            self._db.update_tab(self._active_tab_id, latex=self._editor.get_latex())

        self._active_tab_id = tab_id
        self._settings.set("ui/active_tab_id", tab_id)
        self._tab_bar.set_active(tab_id)

        tab = self._db.get_tab(tab_id)
        if not tab:
            return

        # Populate editor+preview
        latex = tab.get("latex", "")
        self._editor.set_latex(latex)
        self._preview.set_latex(latex)

        # Populate crop without wiping corners
        td = self._tab_data.setdefault(tab_id, {})
        img_path = td.get("img_path") or tab.get("orig_path") or ""
        corners = td.get("crop_corners") or tab.get("crop_points")
        if img_path and os.path.isfile(img_path):
            if not td.get("image_bytes"):
                try:
                    with open(img_path, "rb") as f:
                        td["image_bytes"] = f.read()
                except Exception:
                    pass
            self._crop.set_image_path(img_path, reset_corners=False)
        else:
            self._crop.set_image_path("", reset_corners=False)

        if corners:
            self._crop.set_corners(corners)
        else:
            self._crop.reset_corners()

        # Update tags in status bar
        tags_raw = tab.get("tags", "[]")
        try:
            tags = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
        except Exception:
            tags = []
        tag_str = " ".join(tags[:4]) if tags else ""
        self._tags_lbl.setText(f" {tag_str} " if tag_str else "")
        title = tab.get("title", "")
        if title:
            self.setWindowTitle(f"Meikai — {title}")
        else:
            self.setWindowTitle("Meikai — Mathematical LaTeX Studio")

        self._sync_island()

    def _close_tab(self, tab_id: str):
        tab = self._db.get_tab(tab_id)
        if not tab:
            return
        if tab.get("pinned"):
            self._status_lbl.setText("Tab is pinned — right-click to unpin first")
            return
        self._db.soft_delete_tab(tab_id)
        self._tab_data.pop(tab_id, None)
        tabs = self._db.get_all_tabs()
        if not tabs:
            new_tab = self._db.create_tab("New Problem")
            self._tab_data[new_tab["id"]] = {"image_bytes": None, "img_path": None, "crop_corners": None}
            tabs = [new_tab]
        self._refresh_tab_bar(tabs)
        if self._active_tab_id == tab_id:
            self._select_tab(tabs[-1]["id"])

    def _close_active_tab(self):
        if self._active_tab_id:
            self._close_tab(self._active_tab_id)

    def _reopen_last_tab(self):
        tid = self._db.restore_last_deleted()
        if tid:
            tabs = self._db.get_all_tabs()
            self._refresh_tab_bar(tabs)
            self._select_tab(tid)
        else:
            self._status_lbl.setText("No recently closed tabs")

    def _next_tab(self):
        """Navigate to the next tab (wraps around)."""
        tabs = self._tab_bar.get_tabs()
        if not tabs or len(tabs) <= 1:
            return
        ids = [t["id"] for t in tabs]
        try:
            idx = ids.index(self._active_tab_id) if self._active_tab_id in ids else -1
            next_idx = (idx + 1) % len(ids)
        except Exception:
            next_idx = 0
        self._select_tab(ids[next_idx])

    def _prev_tab(self):
        """Navigate to the previous tab (wraps around)."""
        tabs = self._tab_bar.get_tabs()
        if not tabs or len(tabs) <= 1:
            return
        ids = [t["id"] for t in tabs]
        try:
            idx = ids.index(self._active_tab_id) if self._active_tab_id in ids else 0
            prev_idx = (idx - 1) % len(ids)
        except Exception:
            prev_idx = len(ids) - 1
        self._select_tab(ids[prev_idx])

    def _rename_tab(self, tab_id: str, title: str):
        self._db.update_tab(tab_id, title=title)
        self._refresh_tab_bar()

    def _toggle_pin(self, tab_id: str):
        tab = self._db.get_tab(tab_id)
        if tab:
            self._db.update_tab(tab_id, pinned=not tab.get("pinned", False))
        self._refresh_tab_bar()

    # ── Crop events ────────────────────────────────────────────────────────────
    def _on_crop_changed(self, corners: list):
        if not self._active_tab_id:
            return
        td = self._tab_data.setdefault(self._active_tab_id, {})
        td["crop_corners"] = corners
        self._db.update_tab(self._active_tab_id, crop_points=json.dumps(corners))
        self._sync_island()

    def _reset_crop(self):
        self._crop.reset_corners()

    # ── Image ops ─────────────────────────────────────────────────────────────
    def _rotate_left(self):   self._do_image_op(rotate_image, -90)
    def _rotate_right(self):  self._do_image_op(rotate_image,  90)
    def _enhance_contrast(self): self._do_image_op(enhance_contrast)
    def _invert(self):        self._do_image_op(invert_image)

    def _do_image_op(self, fn, *args):
        if not self._active_tab_id:
            return
        td = self._tab_data.get(self._active_tab_id, {})
        path = td.get("img_path")
        if not path or not os.path.isfile(path):
            self._status_lbl.setText("No image loaded")
            return
        try:
            new_path, _ = fn(path, *args)
            td["img_path"] = new_path
            with open(new_path, "rb") as f:
                td["image_bytes"] = f.read()
            # Only reset corners if rotating geometry, not for contrast or invert!
            reset_corners = (fn == rotate_image)
            self._crop.set_image_path(new_path, reset_corners=reset_corners)
            self._db.update_tab(self._active_tab_id, orig_path=new_path)
            self._sync_island()
        except Exception as e:
            self._status_lbl.setText(f"Image error: {e}")

    # ── LaTeX sync ─────────────────────────────────────────────────────────────
    def _on_latex_changed(self, latex: str):
        self._preview.set_latex(latex)
        if self._active_tab_id:
            self._db.update_tab(self._active_tab_id, latex=latex)
        self._sync_island()

    # ── Island → Studio ────────────────────────────────────────────────────────
    def receive_latex_from_island(self, latex: str):
        """Island editor changed — update studio without re-emitting to island."""
        self._inhibit_island_sync = True
        self._editor.set_latex(latex)
        self._preview.set_latex(latex)
        if self._active_tab_id:
            self._db.update_tab(self._active_tab_id, latex=latex)
        self._inhibit_island_sync = False

    def receive_crop_from_island(self, corners: list):
        """Island crop changed — update studio crop widget."""
        self._inhibit_island_sync = True
        self._crop.set_corners(corners)
        if self._active_tab_id:
            td = self._tab_data.setdefault(self._active_tab_id, {})
            td["crop_corners"] = corners
            self._db.update_tab(self._active_tab_id, crop_points=json.dumps(corners))
        self._inhibit_island_sync = False

    # ── Studio → Island sync ───────────────────────────────────────────────────
    def _sync_island(self):
        if self._inhibit_island_sync or not self._island or not self._active_tab_id:
            return
        td = self._tab_data.get(self._active_tab_id, {})
        tab = self._db.get_tab(self._active_tab_id)
        title = tab.get("title", "") if tab else ""
        img_bytes = td.get("image_bytes")
        img_path = td.get("img_path") or (tab.get("orig_path") if tab else None)
        if not img_bytes and img_path and os.path.isfile(img_path):
            try:
                with open(img_path, "rb") as f:
                    img_bytes = f.read()
                    td["image_bytes"] = img_bytes
            except Exception:
                pass
        self._island.sync_from_studio(
            self._active_tab_id,
            self._editor.get_latex(),
            img_bytes,
            img_path,
            td.get("crop_corners") or (tab.get("crop_points") if tab else None),
            tab_title=title,
        )

    def set_island(self, island):
        self._island = island
        island.latex_changed.connect(self.receive_latex_from_island)
        island.crop_corners_changed.connect(self.receive_crop_from_island)
        island.image_staged.connect(self._on_island_image)
        island.transcribe_requested.connect(self._on_island_transcribe_request)
        island.open_studio_requested.connect(self.show_and_raise)
        # Push current tab state to island immediately and once again after event loop turns
        self._sync_island()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, self._sync_island)

    def _on_island_transcribe_request(self, image_bytes: bytes, crop_corners=None):
        if self._is_busy:
            return
        self._start_transcribe(image_bytes, crop_corners)

    def _on_island_image(self, image_bytes: bytes):
        """Image dropped on island → open in a new tab in studio."""
        tab = self._db.create_tab("New Problem")
        from ..image_proc import save_original
        img_path = save_original(image_bytes)
        self._tab_data[tab["id"]] = {
            "image_bytes": image_bytes,
            "img_path": img_path,
            "crop_corners": None,
        }
        self._db.update_tab(tab["id"], orig_path=img_path)
        self._refresh_tab_bar()
        self._select_tab(tab["id"])

    # ── Transcription ──────────────────────────────────────────────────────────
    def _transcribe(self):
        if self._is_busy or not self._active_tab_id:
            return
        td = self._tab_data.get(self._active_tab_id, {})
        image_bytes = td.get("image_bytes")
        img_path = td.get("img_path")
        if not image_bytes and img_path and os.path.isfile(img_path):
            try:
                with open(img_path, "rb") as f:
                    image_bytes = f.read()
                    td["image_bytes"] = image_bytes
            except Exception:
                pass
        if not image_bytes:
            self._status_lbl.setText("No image — drag/drop or paste an image first")
            return
        self._start_transcribe(image_bytes, td.get("crop_corners"))

    def _set_busy(self, busy: bool):
        self._is_busy = busy
        self._btn_transcribe.setEnabled(not busy)
        if busy:
            self._btn_transcribe.setText("⏳ Transcribing…")
            self._status_lbl.setText("⏳ Transcribing…")
        else:
            self._btn_transcribe.setText("▶  Transcribe  (Enter)")
        if self._island:
            self._island.set_busy(busy)

    def _start_transcribe(self, image_bytes: bytes, crop_corners=None):
        if self._is_busy:
            return
        self._set_busy(True)

        self._worker = _TranscribeWorker(image_bytes, crop_corners)
        self._worker_thread = QThread(self)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.result_ready.connect(self._on_result)
        self._worker.error_signal.connect(self._on_error)
        self._worker.status_update.connect(self._on_status_update)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)
        self._worker_thread.finished.connect(self._on_worker_finished)
        self._worker_thread.start()

    def _on_worker_finished(self):
        self._worker = None
        self._worker_thread = None

    def _on_status_update(self, msg: str):
        self._status_lbl.setText(msg)
        if self._island:
            self._island.set_status(msg)

    def _on_result(self, latex: str, tags: list, model: str, elapsed_ms: float, topic: str = ""):
        import re
        self._set_busy(False)
        elapsed_s = elapsed_ms / 1000
        self._model_lbl.setText(f"  {model}  ")
        tag_str = " ".join(tags[:4]) if tags else ""
        self._tags_lbl.setText(f" {tag_str} " if tag_str else "")

        # 1. Update tab title immediately from Gemini topic
        clean_topic = re.sub(r'[*_`"]', '', topic).strip()[:50] if topic else ""
        if clean_topic and self._active_tab_id:
            self._apply_gemini_title(self._active_tab_id, clean_topic)
        elif self._active_tab_id and latex:
            # Fallback title request if topic wasn't provided
            self._request_gemini_title(self._active_tab_id, latex)

        # 2. Update editor & preview: handle empty latex gracefully
        if not latex:
            self._editor.set_latex("")
            self._preview.set_latex("")
            self._status_lbl.setText(f"✓ {model}  ({elapsed_s:.1f}s) — No math/text found in image")
        else:
            self._editor.set_latex(latex)
            self._preview.set_latex(latex)
            self._status_lbl.setText(f"✓ {model}  ({elapsed_s:.1f}s)")

        if self._active_tab_id:
            self._db.update_tab(self._active_tab_id, latex=latex, tags=json.dumps(tags))

        # Sync to island
        if self._island:
            self._island.apply_transcription_result(latex, tags, model, elapsed_ms, clean_topic)

    def _on_error(self, err: str):
        self._set_busy(False)
        self._status_lbl.setText(f"✗ {err[:100]}")
        if self._island:
            self._island.set_error(err)
        if "No API keys configured" in err:
            self._open_settings()

    def _request_gemini_title(self, tab_id: str, latex: str):
        """Ask Gemini for a short, descriptive tab title in a background thread."""
        import threading

        def _worker():
            try:
                from ..gemini import get_client
                client = get_client()
                # Use a text-only prompt — no image needed for titling
                result = client.generate_title(latex)
                if result:
                    from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
                    # Schedule UI update on main thread
                    QTimer.singleShot(0, lambda: self._apply_gemini_title(tab_id, result))
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name="title-gen").start()

    def _apply_gemini_title(self, tab_id: str, title: str):
        """Apply the Gemini-generated title to the tab (runs on main thread)."""
        if tab_id and title:
            self._db.update_tab(tab_id, title=title)
            self._refresh_tab_bar()
            self._sync_island()
            if tab_id == self._active_tab_id:
                self.setWindowTitle(f"Meikai — {title}")

    def _cleanup_worker(self):
        if self._worker_thread and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(1000)
        self._worker_thread = None
        self._worker = None

    # ── Clipboard ─────────────────────────────────────────────────────────────
    def _copy_latex(self):
        latex = self._editor.get_latex()
        if latex:
            QApplication.clipboard().setText(latex)
            self._status_lbl.setText("LaTeX copied to clipboard")

    def _copy_for_ai(self):
        latex = self._editor.get_latex()
        if not latex:
            self._status_lbl.setText("No LaTeX to copy")
            return
        tmpl = self._settings.ai_prompt_template
        prompt = tmpl.replace("{latex}", latex) if "{latex}" in tmpl else f"{tmpl}\n\n{latex}"
        QApplication.clipboard().setText(prompt)
        self._status_lbl.setText("🤖 Formatted prompt copied for AI to clipboard")

    def _copy_png(self):
        png = self._preview.render_to_png()
        if png:
            QApplication.clipboard().setImage(QImage.fromData(png))
            self._status_lbl.setText("PNG copied to clipboard")

    # ── Drag & drop ───────────────────────────────────────────────────────────
    def dragEnterEvent(self, ev):
        from ..image_proc import can_accept_mime
        if can_accept_mime(ev.mimeData()):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        from ..image_proc import can_accept_mime
        if can_accept_mime(ev.mimeData()):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        from ..image_proc import extract_image_bytes, extract_pdf_path
        pdf_path = extract_pdf_path(ev.mimeData())
        if pdf_path:
            self.open_pdf_path(pdf_path)
            ev.acceptProposedAction()
            return
        data = extract_image_bytes(ev.mimeData())
        if data:
            self._stage_image(data)
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def _stage_image(self, image_bytes: bytes):
        from ..image_proc import save_original
        img_path = save_original(image_bytes)
        if self._active_tab_id:
            td = self._tab_data.setdefault(self._active_tab_id, {})
            td["image_bytes"] = image_bytes
            td["img_path"] = img_path
            self._crop.set_image_path(img_path)
            self._db.update_tab(self._active_tab_id, orig_path=img_path)
        self._sync_island()

    def open_image_path(self, path: str):
        """Open an image or PDF file. If the current tab is already occupied, opens in a new tab."""
        if not path or not os.path.isfile(path):
            return
        if path.lower().endswith(".pdf"):
            self.open_pdf_path(path)
            return
        td = self._tab_data.get(self._active_tab_id, {})
        if td.get("image_bytes") or td.get("latex"):
            self._new_tab()
        try:
            with open(path, "rb") as f:
                self._stage_image(f.read())
            self._status_lbl.setText(f"Opened: {os.path.basename(path)}")
        except Exception as e:
            self._status_lbl.setText(f"Error opening image: {e}")
        self.show_and_raise()

    # ── PDF support ────────────────────────────────────────────────────────────
    def open_pdf_path(self, path: str):
        """
        Open a PDF file.
        • Single-page PDF  → load directly (no dialog).
        • Multi-page PDF   → show PdfPagePickerDialog, then open each selected
                             page as a rasterised image in its own tab.
        """
        from ..image_proc import get_pdf_page_count
        self.show_and_raise()
        try:
            count = get_pdf_page_count(path)
        except ImportError as e:
            self._status_lbl.setText(str(e))
            return
        if count == 0:
            self._status_lbl.setText(f"Could not read PDF: {os.path.basename(path)}")
            return
        if count == 1:
            self._open_pdf_pages(path, [0])
        else:
            from .pdf_page_picker import PdfPagePickerDialog
            dlg = PdfPagePickerDialog(path, count, self._theme, self)
            if dlg.exec():
                pages = dlg.selected_pages()
                if pages:
                    self._open_pdf_pages(path, pages)

    def _open_pdf_pages(self, pdf_path: str, pages: List[int]):
        """Rasterise each selected PDF page and open it in a tab."""
        from ..image_proc import pdf_page_to_image_bytes
        fname = os.path.basename(pdf_path)
        for i, page_num in enumerate(pages):
            try:
                img_bytes = pdf_page_to_image_bytes(pdf_path, page_num, dpi=200)
            except Exception as e:
                self._status_lbl.setText(f"PDF error (p.{page_num + 1}): {e}")
                continue
            # First page: reuse current empty tab; subsequent pages: new tab
            if i == 0:
                td = self._tab_data.get(self._active_tab_id, {})
                if td.get("image_bytes") or td.get("latex"):
                    self._new_tab()
            else:
                self._new_tab()
            self._stage_image(img_bytes)
            suffix = f"p.{page_num + 1}" if len(pages) > 1 else f"p.{page_num + 1}/{len(pages)}"
            self._status_lbl.setText(f"Opened: {fname}  {suffix}")

    def _open_file_dialog(self):
        """File → Open: pick one image or PDF via native file dialog."""
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Image or PDF",
            "",
            "Images & PDFs (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff *.pdf);;"
            "PDF Files (*.pdf);;"
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff);;"
            "All Files (*)",
        )
        if path:
            self.open_image_path(path)

    def _setup_clipboard_watcher(self):
        """Set up clipboard monitoring — but only paste on explicit Ctrl+V, never automatically."""
        # We deliberately do NOT connect dataChanged here.
        # Auto-pasting when the user copies anything to the clipboard would be very annoying
        # (e.g. copying text from another app would suddenly load it as an image).
        # Paste only happens on explicit Ctrl+V via keyPressEvent / eventFilter.
        pass

    def _on_clipboard_changed(self):
        """Intentionally disabled — auto-paste removed."""
        pass

    # ── Paste ─────────────────────────────────────────────────────────────────
    def keyPressEvent(self, ev):
        if ev.matches(QKeySequence.StandardKey.Paste):
            if self._try_paste_image():
                return
        super().keyPressEvent(ev)

    def _try_paste_image(self) -> bool:
        """Try to paste an image or PDF from clipboard. Returns True if handled."""
        from ..image_proc import extract_image_bytes, extract_pdf_path
        cb = QApplication.clipboard()
        md = cb.mimeData()
        data = extract_image_bytes(md)
        if data:
            self._stage_image(data)
            self._status_lbl.setText("📋 Image pasted from clipboard")
            return True
        pdf_path = extract_pdf_path(md)
        if pdf_path:
            self.open_pdf_path(pdf_path)
            return True
        return False

    def eventFilter(self, obj, event):
        """App-level event filter: catch tab navigation, Ctrl+V paste, and drag-and-drop anywhere in the window."""
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress:
            from PyQt6.QtGui import QKeyEvent
            ke = event  # type: QKeyEvent
            mods = ke.modifiers()

            # Global tab cycling (works even when CodeEditor or child widgets have focus)
            if (self.isActiveWindow() or self.isVisible()) and (mods & Qt.KeyboardModifier.ControlModifier):
                if ke.key() == Qt.Key.Key_Tab:
                    if mods & Qt.KeyboardModifier.ShiftModifier:
                        self._prev_tab()
                    else:
                        self._next_tab()
                    return True
                elif ke.key() == Qt.Key.Key_Backtab:
                    self._prev_tab()
                    return True

            if (ke.key() == Qt.Key.Key_V and
                    mods & Qt.KeyboardModifier.ControlModifier and
                    not mods & Qt.KeyboardModifier.ShiftModifier):
                # Only intercept if there's an image — let text paste pass through
                if self._try_paste_image():
                    return True  # Handled

        # Intercept Drag & Drop anywhere inside the studio window hierarchy
        elif event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if obj == self or self.isAncestorOf(obj):
                from ..image_proc import can_accept_mime
                if can_accept_mime(event.mimeData()):
                    event.acceptProposedAction()
                    return True

        elif event.type() == QEvent.Type.Drop:
            if obj == self or self.isAncestorOf(obj):
                from ..image_proc import extract_image_bytes, extract_pdf_path
                pdf_path = extract_pdf_path(event.mimeData())
                if pdf_path:
                    self.open_pdf_path(pdf_path)
                    event.acceptProposedAction()
                    return True
                data = extract_image_bytes(event.mimeData())
                if data:
                    self._stage_image(data)
                    event.acceptProposedAction()
                    return True

        return False

    # ── Settings & search ─────────────────────────────────────────────────────
    def _open_settings(self):
        dlg = SettingsDialog(self._theme, self)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.exec()

    def _on_settings_changed(self):
        self._theme = self._settings.theme
        self._apply_style()
        self._tab_bar.set_overflow_mode(self._settings.tab_overflow_mode)
        for w in [self._crop, self._editor, self._preview, self._tab_bar]:
            w.apply_theme(self._theme)
        if self._island:
            self._island.apply_theme(self._theme)

    def _open_search(self):
        dlg = SearchDialog(self._theme, self)
        dlg.tab_selected.connect(self._select_tab)
        dlg.exec()

    def _toggle_island(self):
        if self._island:
            self._island.setVisible(not self._island.isVisible())

    # ── Style ─────────────────────────────────────────────────────────────────
    def _apply_style(self):
        t = self._theme
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background:{t.get('bg','#0d1117')};
                color:{t.get('text','#e6edf3')};
            }}
            QMenuBar {{
                background:{t.get('surface','#161b22')};
                color:{t.get('text','#e6edf3')};
                border-bottom:1px solid {t.get('border','#30363d')};
                padding:2px; font-size:12px;
            }}
            QMenuBar::item:selected {{ background:{t.get('accent2','#1f6feb')}44; border-radius:4px; }}
            QMenu {{
                background:{t.get('surface','#161b22')};
                color:{t.get('text','#e6edf3')};
                border:1px solid {t.get('border','#30363d')};
                border-radius:6px; padding:4px;
            }}
            QMenu::item {{ padding:6px 20px; border-radius:4px; }}
            QMenu::item:selected {{ background:{t.get('accent2','#1f6feb')}44; }}
            QStatusBar {{
                background:{t.get('surface','#161b22')};
                color:{t.get('text_dim','#8b949e')};
                border-top:1px solid {t.get('border','#30363d')};
                font-size:11px;
            }}
            QSplitter::handle {{
                background:{t.get('border','#30363d')};
                width:2px; height:2px;
            }}
            QPushButton {{
                background:{t.get('button_bg','#21262d')};
                color:{t.get('text','#e6edf3')};
                border:1px solid {t.get('border','#30363d')};
                border-radius:6px; padding:4px 10px; font-size:12px;
            }}
            QPushButton:hover {{
                background:{t.get('button_hover','#30363d')};
                border-color:{t.get('accent','#58a6ff')};
            }}
            QPushButton:disabled {{
                color:{t.get('text_dim','#8b949e')};
                background:{t.get('surface','#161b22')};
            }}
            QLabel {{ color:{t.get('text','#e6edf3')}; }}
        """)
        self._btn_transcribe.setStyleSheet(f"""
            QPushButton {{
                background:{t.get('accent2','#1f6feb')};
                color:white; border:none; border-radius:6px;
                padding:4px 16px; font-size:12px; font-weight:bold;
            }}
            QPushButton:hover {{ background:{t.get('accent','#58a6ff')}; }}
            QPushButton:disabled {{
                background:{t.get('button_bg','#21262d')};
                color:{t.get('text_dim','#8b949e')};
            }}
        """)

    # ── Window lifecycle ───────────────────────────────────────────────────────
    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _restore_geometry(self):
        """Restore window position/size/maximized state from settings."""
        geo = self._settings.get("ui/window_geometry")
        maximized = self._settings.get("ui/window_maximized")
        if geo:
            try:
                from PyQt6.QtCore import QByteArray
                self.restoreGeometry(QByteArray(bytes(geo)))
            except Exception:
                pass
        if maximized:
            self.showMaximized()

    def closeEvent(self, ev: QCloseEvent):
        """
        Closing the studio window HIDES it (background mode).
        The app keeps running with the Dynamic Island.
        Only File→Quit terminates the process.
        """
        self._save_state()
        ev.ignore()   # don't quit — just hide
        self.hide()

    def _save_state(self):
        self._settings.set("ui/split_main", list(self._main_splitter.saveState()))
        self._settings.set("ui/split_right", list(self._right_splitter.saveState()))
        # Save window geometry and maximized state
        self._settings.set("ui/window_geometry", list(self.saveGeometry()))
        self._settings.set("ui/window_maximized", self.isMaximized())
        if self._active_tab_id:
            self._settings.set("ui/active_tab_id", self._active_tab_id)
            self._db.update_tab(self._active_tab_id, latex=self._editor.get_latex())
        self._settings.sync()

    def _quit_app(self):
        self._save_state()
        self._cleanup_worker()
        if hasattr(self, "_preview"):
            self._preview.cleanup()
        if self._island and hasattr(self._island, "_preview"):
            self._island._preview.cleanup()
        QApplication.quit()
