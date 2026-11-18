"""
Dynamic Island — top-bezel companion widget.

States:
  notch   → 150×14px pill at y=0, camera glow, NO taskbar entry
  capsule → 340×42px hover expansion
  card    → 740×520px full HUD with crop + editor + preview

Fixes in this version:
- Smooth spring animation (QPropertyAnimation on geometry, 220ms OutCubic)
- Instant collapse on mouse-leave (100ms debounce, not 1.4s)
- Click outside card → collapse immediately
- Esc inside card → collapse
- Skip-taskbar enforced after show (xprop _NET_WM_STATE_SKIP_TASKBAR)
- Drag-to-notch: window is wider in notch state to give a bigger drop target
- Ctrl+V paste works in card state
- Full bidirectional sync with MainWindow
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from PyQt6.QtCore import (
    QAbstractAnimation, QByteArray, QEasingCurve, QEvent,
    QPropertyAnimation, QRect, Qt, QTimer, QThread, QObject,
    pyqtSignal, QPointF,
)
from PyQt6.QtGui import (
    QColor, QFont, QImage, QKeyEvent, QKeySequence,
    QMouseEvent, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QBrush, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from ..settings import get_settings
from ..image_proc import can_accept_mime, extract_image_bytes
from .crop_widget import CropWidget
from .code_editor import CodeEditor
from .math_preview import MathPreviewWidget

# ── Geometry (matching C++ Dynamic Island architecture) ──────────────────────
NOTCH_W,   NOTCH_H   = 150,  14
CAPSULE_W, CAPSULE_H = 320,  38
CARD_W,    CARD_H    = 740, 520
ANIM_MS = 260    # 260ms buttery smooth OutCubic animation from C++


# ── Background worker ──────────────────────────────────────────────────────────
class _TranscribeWorker(QObject):
    # latex, tags, model, elapsed_ms, topic
    result_ready  = pyqtSignal(str, list, str, float, str)
    error_signal  = pyqtSignal(str)
    status_update = pyqtSignal(str)
    finished      = pyqtSignal()

    def __init__(self, image_bytes: bytes, crop_corners):
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


class DynamicIsland(QWidget):
    """
    Apple-inspired Dynamic Island widget.

    Key design decisions:
    - Window flags: FramelessWindowHint + Tool + X11BypassWindowManagerHint
      → Never shows in KDE task manager or Alt+Tab
    - In notch state the WINDOW is NOTCH_HIT_W × NOTCH_HIT_H (wider transparent)
      so drag-and-drop has a decent hit area.
    - Smooth geometry animation (220ms OutCubic spring).
    - Global event filter while in card state → click outside collapses.
    - All changes emit signals → bidirectional sync with MainWindow.
    """

    # ── Signals to MainWindow ──────────────────────────────────────────────────
    latex_changed           = pyqtSignal(str)
    crop_corners_changed    = pyqtSignal(list)
    image_staged            = pyqtSignal(bytes)
    open_studio_requested   = pyqtSignal()
    transcribe_requested    = pyqtSignal(bytes, object)  # image_bytes, crop_corners
    transcription_completed = pyqtSignal(str, list, str, float, str)  # latex, tags, model, elapsed_ms, topic

    def __init__(self):
        super().__init__(None)
        self._settings = get_settings()
        self._theme = self._settings.theme

        self._state = "notch"
        self._status_text = "Meikai"
        self._tab_id: Optional[str] = None
        self._tab_title: str = ""
        self._image_bytes: Optional[bytes] = None
        self._img_path: Optional[str] = None
        self._latex = ""
        self._crop_corners: Optional[list] = None
        self._is_busy = False
        self._worker: Optional[_TranscribeWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._inhibit_sync = False
        self._filter_installed = False

        self._setup_flags()
        self._build_card()

        # ── Geometry animation (260ms OutCubic) ────────────────────────────────
        self._geo_anim = QPropertyAnimation(self, b"geometry")
        self._geo_anim.setDuration(ANIM_MS)
        self._geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setMouseTracking(True)
        self.setAcceptDrops(True)

        # Start in compact notch geometry
        self._move_to(NOTCH_W, NOTCH_H)

    # ── Window flags ───────────────────────────────────────────────────────────
    def _setup_flags(self):
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowRole("meikai_island")
        self.setWindowTitle("")

    def showEvent(self, ev):
        super().showEvent(ev)
        self._enforce_skip_taskbar()
        QTimer.singleShot(50, self._enforce_skip_taskbar)
        QTimer.singleShot(200, self._enforce_skip_taskbar)

    def _enforce_skip_taskbar(self):
        """Ensure the island never appears as a taskbar entry or app placeholder on KDE Plasma."""
        try:
            wid = int(self.winId())
            # 1. Change WM_CLASS to 'meikai-island' so KDE Plasma never matches it with meikai.desktop
            subprocess.run(
                ['xprop', '-id', str(wid),
                 '-f', 'WM_CLASS', '8s',
                 '-set', 'WM_CLASS', 'meikai-island'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            # 2. Strip KDE desktop file association from this window
            subprocess.run(
                ['xprop', '-id', str(wid), '-remove', '_KDE_NET_WM_DESKTOP_FILE'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            # 3. Set window type to UTILITY (activatable Tool window)
            subprocess.run(
                ['xprop', '-id', str(wid),
                 '-f', '_NET_WM_WINDOW_TYPE', '32a',
                 '-set', '_NET_WM_WINDOW_TYPE', '_NET_WM_WINDOW_TYPE_UTILITY'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            # 4. Set SKIP_TASKBAR, SKIP_PAGER, STAYS_ON_TOP in _NET_WM_STATE
            subprocess.run(
                ['xprop', '-id', str(wid),
                 '-f', '_NET_WM_STATE', '32a',
                 '-set', '_NET_WM_STATE',
                 '_NET_WM_STATE_SKIP_TASKBAR, _NET_WM_STATE_SKIP_PAGER, _NET_WM_STATE_STAYS_ON_TOP'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            # 5. Send EWMH client message to root window
            self._send_x11_skip_taskbar(wid)
        except Exception:
            pass

    def _send_x11_skip_taskbar(self, wid: int):
        try:
            import ctypes
            from ctypes import c_ulong, c_long, c_int, Structure, byref
            x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
            disp = x11.XOpenDisplay(None)
            if not disp:
                return

            def intern(name: str):
                return x11.XInternAtom(disp, name.encode("ascii"), False)

            net_wm_state = intern("_NET_WM_STATE")
            skip_taskbar = intern("_NET_WM_STATE_SKIP_TASKBAR")
            skip_pager   = intern("_NET_WM_STATE_SKIP_PAGER")
            root = x11.XDefaultRootWindow(disp)

            class XClientMessageEvent(Structure):
                _fields_ = [
                    ("type", c_int),
                    ("serial", c_ulong),
                    ("send_event", c_int),
                    ("display", ctypes.c_void_p),
                    ("window", c_ulong),
                    ("message_type", c_ulong),
                    ("format", c_int),
                    ("data", c_long * 5)
                ]

            ev = XClientMessageEvent()
            ev.type = 33  # ClientMessage
            ev.serial = 0
            ev.send_event = 1
            ev.display = disp
            ev.window = wid
            ev.message_type = net_wm_state
            ev.format = 32
            ev.data[0] = 1  # _NET_WM_STATE_ADD
            ev.data[1] = skip_taskbar
            ev.data[2] = skip_pager
            ev.data[3] = 1  # normal client application
            ev.data[4] = 0

            mask = (1 << 20) | (1 << 19)
            x11.XSendEvent(disp, root, False, c_long(mask), byref(ev))
            x11.XFlush(disp)
            x11.XCloseDisplay(disp)
        except Exception:
            pass

    # ── UI construction ────────────────────────────────────────────────────────
    def _build_card(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._card = QWidget(self)
        self._card.setVisible(False)
        self._card.setAcceptDrops(True)
        layout.addWidget(self._card)

        cl = QVBoxLayout(self._card)
        cl.setContentsMargins(14, 10, 14, 14)
        cl.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        self._lbl_title = QLabel("✦ Meikai")
        self._lbl_title.setFont(QFont("sans-serif", 12, QFont.Weight.Bold))
        hdr.addWidget(self._lbl_title)
        hdr.addStretch()
        self._lbl_status = QLabel("Ready")
        self._lbl_status.setFont(QFont("sans-serif", 10))
        hdr.addWidget(self._lbl_status)

        # Dedicated Close button
        self._btn_close = QPushButton("✕")
        self._btn_close.setFixedSize(26, 26)
        self._btn_close.setToolTip("Collapse (Esc / Ctrl+X)")
        self._btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_close.clicked.connect(self._to_notch)
        hdr.addWidget(self._btn_close)
        cl.addLayout(hdr)

        # Keyboard shortcuts that work anywhere inside the card HUD
        self._sc_close_x = QShortcut(QKeySequence("Ctrl+X"), self)
        self._sc_close_x.setContext(Qt.ShortcutContext.WindowShortcut)
        self._sc_close_x.activated.connect(self._on_ctrl_x)

        self._sc_close_esc = QShortcut(QKeySequence("Escape"), self)
        self._sc_close_esc.setContext(Qt.ShortcutContext.WindowShortcut)
        self._sc_close_esc.activated.connect(self._to_notch)

        # Splitter: crop | editor+preview
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._crop = CropWidget(self._theme)
        self._crop.setMinimumWidth(240)
        self._crop.corners_changed.connect(self._on_crop_changed)
        self._crop.image_dropped.connect(self._handle_image_bytes)
        self._splitter.addWidget(self._crop)

        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        self._editor = CodeEditor(self._theme)
        self._editor.setMaximumHeight(165)
        self._editor.latex_changed.connect(self._on_editor_changed)
        rl.addWidget(self._editor)

        self._preview = MathPreviewWidget(self._theme)
        rl.addWidget(self._preview)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._btn_trans = QPushButton("▶ Transcribe")
        self._btn_trans.setFixedHeight(28)
        self._btn_trans.clicked.connect(self._do_transcribe)
        btn_row.addWidget(self._btn_trans)

        self._btn_latex = QPushButton("📋 Copy LaTeX")
        self._btn_latex.setToolTip("Copy LaTeX code (Ctrl+C / Ctrl+Shift+C)")
        self._btn_latex.setFixedHeight(28)
        self._btn_latex.clicked.connect(self._copy_latex)
        btn_row.addWidget(self._btn_latex)

        self._btn_ai = QPushButton("🤖 Copy for AI")
        self._btn_ai.setToolTip("Copy prompt formatted for ChatGPT / Claude / Gemini (Ctrl+Alt+A)")
        self._btn_ai.setFixedHeight(28)
        self._btn_ai.clicked.connect(self._copy_for_ai)
        btn_row.addWidget(self._btn_ai)

        self._btn_png = QPushButton("🖼 Copy PNG")
        self._btn_png.setToolTip("Copy rendered equation as PNG")
        self._btn_png.setFixedHeight(28)
        self._btn_png.clicked.connect(self._copy_png)
        btn_row.addWidget(self._btn_png)

        self._btn_studio = QPushButton("⤢ Open Studio")
        self._btn_studio.setToolTip("Open in full Studio window")
        self._btn_studio.setFixedHeight(28)
        self._btn_studio.clicked.connect(self._open_studio_and_collapse)
        btn_row.addWidget(self._btn_studio)

        rl.addLayout(btn_row)
        self._splitter.addWidget(rw)
        self._splitter.setSizes([270, 420])
        cl.addWidget(self._splitter)

        self._apply_card_style()

    def _on_ctrl_x(self):
        """Handler for Ctrl+X shortcut: collapse to notch unless text is selected in code editor."""
        if self._state == "card":
            if self._editor.hasFocus() or self._editor.viewport().hasFocus():
                cursor = self._editor.textCursor()
                if cursor and cursor.hasSelection():
                    self._editor.cut()
                    return
            self._to_notch()

    # ── Geometry helpers ───────────────────────────────────────────────────────
    def _screen(self):
        from PyQt6.QtGui import QCursor
        screen = QApplication.screenAt(QCursor.pos())
        if not screen:
            screen = QApplication.primaryScreen()
        return screen

    def _screen_cx(self) -> int:
        screen = self._screen()
        return screen.geometry().center().x() if screen else 960

    def _screen_top(self) -> int:
        screen = self._screen()
        return screen.geometry().top() if screen else 0

    def _move_to(self, w: int, h: int):
        """Instant move (no animation) — used only during init."""
        cx = self._screen_cx()
        top = self._screen_top()
        self.setGeometry(cx - w // 2, top, w, h)

    def _animate_to(self, w: int, h: int, to_card: bool):
        """Fluid spring transition matching the C++ Dynamic Island architecture."""
        cx = self._screen_cx()
        top = self._screen_top()
        target = QRect(cx - w // 2, top, w, h)

        if self._geo_anim.state() == QAbstractAnimation.State.Running:
            self._geo_anim.stop()

        # If transitioning away from card, hide card container immediately
        # so geometry contracts smoothly as a pure OLED pill
        if not to_card and self._card.isVisible():
            self._card.setVisible(False)

        self._geo_anim.setStartValue(self.geometry())
        self._geo_anim.setEndValue(target)

        try:
            self._geo_anim.finished.disconnect()
        except Exception:
            pass

        def _on_done():
            if to_card:
                self._card.setVisible(True)
                self.activateWindow()
                self._editor.setFocus()
            self._enforce_skip_taskbar()

        self._geo_anim.finished.connect(_on_done)
        self._geo_anim.start()
        self.update()

    # ── State transitions ──────────────────────────────────────────────────────
    def _to_notch(self):
        self._state = "notch"
        self._remove_global_filter()
        self._animate_to(NOTCH_W, NOTCH_H, False)
        QTimer.singleShot(ANIM_MS + 20, self._enforce_skip_taskbar)

    def _to_capsule(self):
        if self._state == "card":
            return
        self._state = "capsule"
        self._animate_to(CAPSULE_W, CAPSULE_H, False)
        QTimer.singleShot(ANIM_MS + 20, self._enforce_skip_taskbar)

    def _to_card(self):
        self._state = "card"
        self._apply_card_style()
        self._inhibit_sync = True
        self._editor.set_latex(self._latex)
        self._preview.set_latex(self._latex)
        if self._img_path and os.path.isfile(self._img_path):
            self._crop.set_image_path(self._img_path, reset_corners=False)
            if not self._image_bytes:
                try:
                    with open(self._img_path, "rb") as f:
                        self._image_bytes = f.read()
                except Exception:
                    pass
        if self._crop_corners:
            self._crop.set_corners(self._crop_corners)
        self._inhibit_sync = False
        self._install_global_filter()
        self.show()
        self.raise_()
        self._animate_to(CARD_W, CARD_H, True)

    # ── Global event filter (card state only) ─────────────────────────────────
    def _install_global_filter(self):
        if not self._filter_installed:
            QApplication.instance().installEventFilter(self)
            self._filter_installed = True

    def _remove_global_filter(self):
        if self._filter_installed:
            QApplication.instance().removeEventFilter(self)
            self._filter_installed = False

    def eventFilter(self, obj, event):
        """Collapse card on outside click or Esc/Ctrl+X; handle drag&drop and Ctrl+V paste."""
        if self._state == "card":
            # 1. Outside click collapse
            if event.type() == QEvent.Type.MouseButtonPress:
                try:
                    gpos = event.globalPosition().toPoint()
                except AttributeError:
                    try:
                        gpos = event.globalPos()
                    except Exception:
                        return False
                if not self.geometry().contains(gpos):
                    self._to_notch()

            # 2. Key events anywhere inside the card
            elif event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
                ke = event
                # Escape: collapse to notch
                if ke.key() == Qt.Key.Key_Escape:
                    self._to_notch()
                    return True

                # Ctrl+X: collapse to notch (unless user has selected text in code editor)
                if (ke.key() == Qt.Key.Key_X and
                        (ke.modifiers() & Qt.KeyboardModifier.ControlModifier) and
                        not (ke.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
                    if obj == self._editor or self._editor.isAncestorOf(obj):
                        cursor = self._editor.textCursor()
                        if cursor and cursor.hasSelection():
                            return False  # standard editor cut
                    self._to_notch()
                    return True

                # Ctrl+V: paste image or PDF if clipboard contains one
                if (ke.key() == Qt.Key.Key_V and
                        ke.modifiers() & Qt.KeyboardModifier.ControlModifier and
                        not ke.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                    if self._paste_image():
                        return True  # Consumed

                # Ctrl+Alt+A: copy for AI prompt
                if (ke.key() == Qt.Key.Key_A and
                        (ke.modifiers() & Qt.KeyboardModifier.ControlModifier) and
                        (ke.modifiers() & Qt.KeyboardModifier.AltModifier)):
                    self._copy_for_ai()
                    return True

                # Ctrl+C or Ctrl+Shift+C: copy LaTeX code
                if (ke.key() == Qt.Key.Key_C and
                        (ke.modifiers() & Qt.KeyboardModifier.ControlModifier) and
                        not (ke.modifiers() & Qt.KeyboardModifier.AltModifier)):
                    if obj == self._editor or self._editor.isAncestorOf(obj):
                        cursor = self._editor.textCursor()
                        if cursor and cursor.hasSelection():
                            return False  # Standard editor selection copy
                    self._copy_latex()
                    return True

            # 3. Drag and Drop events over any child widget of the card HUD
            elif event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if obj == self or self.isAncestorOf(obj):
                    if can_accept_mime(event.mimeData()):
                        event.acceptProposedAction()
                        return True
            elif event.type() == QEvent.Type.Drop:
                if obj == self or self.isAncestorOf(obj):
                    from ..image_proc import extract_image_bytes, extract_pdf_path
                    pdf_path = extract_pdf_path(event.mimeData())
                    if pdf_path:
                        self._handle_pdf_drop(pdf_path)
                        event.acceptProposedAction()
                        return True
                    data = extract_image_bytes(event.mimeData())
                    if data:
                        self._handle_image_bytes(data)
                        event.acceptProposedAction()
                        return True

        return False  # never consume other events — just observe

    # ── Bidirectional sync: Studio → Island ────────────────────────────────────
    def sync_from_studio(
        self,
        tab_id: str,
        latex: str,
        image_bytes,
        img_path,
        crop_corners,
        tab_title: str = "",
    ):
        self._tab_id = tab_id
        if tab_title:
            self._tab_title = tab_title
            self._lbl_title.setText(f"✦ {tab_title[:28]}")
            self._lbl_title.setToolTip(tab_title)
        self._inhibit_sync = True
        if latex != self._latex:
            self._latex = latex
            if self._state == "card":
                self._editor.set_latex(latex)
                self._preview.set_latex(latex)
        if image_bytes:
            self._image_bytes = image_bytes
        elif img_path and os.path.isfile(img_path):
            try:
                with open(img_path, "rb") as f:
                    self._image_bytes = f.read()
            except Exception:
                pass
        if img_path and img_path != self._img_path:
            self._img_path = img_path
            if self._state == "card":
                self._crop.set_image_path(img_path, reset_corners=False)
        if crop_corners and crop_corners != self._crop_corners:
            self._crop_corners = crop_corners
            if self._state == "card":
                self._crop.set_corners(crop_corners)
        self._inhibit_sync = False
        self.update()

    def set_status(self, text: str):
        self._status_text = text
        self._lbl_status.setText(text)
        self.update()

    def apply_theme(self, theme: dict):
        self._theme = theme
        self._crop.apply_theme(theme)
        self._editor.apply_theme(theme)
        self._preview.apply_theme(theme)
        self._apply_card_style()
        self.update()

    def _apply_card_style(self):
        t = self._theme
        self._card.setStyleSheet(f"""
            QWidget {{
                background: {t.get('island_bg','#010409')};
                color: {t.get('text','#e6edf3')};
            }}
            QSplitter::handle {{ background: {t.get('border','#30363d')}; width:1px; }}
        """)
        self._lbl_title.setStyleSheet(f"color:{t.get('island_text','#58a6ff')};")
        self._lbl_status.setStyleSheet(f"color:{t.get('text_dim','#8b949e')};font-size:10px;")
        btn_style = f"""
            QPushButton {{
                background:{t.get('button_bg','#21262d')};
                color:{t.get('text','#e6edf3')};
                border:1px solid {t.get('border','#30363d')};
                border-radius:5px; padding:3px 8px; font-size:11px;
            }}
            QPushButton:hover {{
                background:{t.get('button_hover','#30363d')};
                border-color:{t.get('accent','#58a6ff')};
            }}
        """
        for btn in self._card.findChildren(QPushButton):
            if btn is not self._btn_close:
                btn.setStyleSheet(btn_style)

        # Style close button distinctly
        self._btn_close.setStyleSheet(f"""
            QPushButton {{
                background: {t.get('button_bg','#21262d')};
                color: {t.get('text_dim','#8b949e')};
                border: 1px solid {t.get('border','#30363d')};
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
                padding: 0px;
            }}
            QPushButton:hover {{
                background: {t.get('error','#f85149')};
                color: #ffffff;
                border-color: {t.get('error','#f85149')};
            }}
        """)

        self._btn_trans.setStyleSheet(f"""
            QPushButton {{
                background:{t.get('accent2','#1f6feb')};
                color:white; border:none;
                border-radius:5px; padding:3px 10px; font-size:11px; font-weight:bold;
            }}
            QPushButton:hover {{ background:{t.get('accent','#58a6ff')}; }}
            QPushButton:disabled {{ background:{t.get('button_bg','#21262d')};color:{t.get('text_dim','#8b949e')}; }}
        """)

    # ── Card slots (Island → Studio sync) ─────────────────────────────────────
    def _on_editor_changed(self, latex: str):
        if self._inhibit_sync:
            return
        self._latex = latex
        self._preview.set_latex(latex)
        self.latex_changed.emit(latex)

    def _on_crop_changed(self, corners: list):
        if self._inhibit_sync:
            return
        self._crop_corners = corners
        self.crop_corners_changed.emit(corners)

    def _handle_image_bytes(self, data: bytes):
        if not data:
            return
        self._image_bytes = data
        from ..image_proc import save_original
        self._img_path = save_original(data)
        if self._state == "card":
            self._crop.set_image_path(self._img_path, reset_corners=True)
        self.image_staged.emit(data)
        self.set_status("📋 Image loaded")
        if self._state != "card":
            self._to_card()

    # ── Transcription ──────────────────────────────────────────────────────────
    def set_busy(self, busy: bool):
        self._is_busy = busy
        if hasattr(self, "_btn_trans"):
            self._btn_trans.setEnabled(not busy)
            if busy:
                self._btn_trans.setText("⏳ …")
                self.set_status("⏳ Transcribing…")
            else:
                self._btn_trans.setText("▶ Transcribe")

    def _do_transcribe(self):
        if not self._image_bytes and self._img_path and os.path.isfile(self._img_path):
            try:
                with open(self._img_path, "rb") as f:
                    self._image_bytes = f.read()
            except Exception:
                pass

        if self._is_busy or not self._image_bytes:
            if not self._image_bytes:
                self.set_status("No image — drop one here")
            return
        # Delegate to MainWindow to maintain a single unified worker pipeline
        self.set_busy(True)
        self.transcribe_requested.emit(self._image_bytes, self._crop_corners)

    def apply_transcription_result(self, latex: str, tags: list, model: str, elapsed_ms: float, topic: str = ""):
        import re
        self.set_busy(False)
        self._latex = latex

        if not latex:
            self._editor.set_latex("")
            self._preview.set_latex("")
            self.set_status("No text/math found")
        else:
            self._editor.set_latex(latex)
            self._preview.set_latex(latex)
            self.set_status(f"✓ {elapsed_ms/1000:.1f}s")

        clean_topic = re.sub(r'[*_`"]', '', topic).strip()[:50] if topic else ""
        if clean_topic:
            self._lbl_title.setText(f"✦ {clean_topic[:24]}")

        self.latex_changed.emit(latex)

    def set_error(self, err: str):
        self.set_busy(False)
        self.set_status(f"✗ {err[:45]}")

    def _cleanup_worker(self):
        if hasattr(self, "_preview"):
            self._preview.cleanup()
        self._worker_thread = None
        self._worker = None

    def _copy_latex(self):
        latex = self._latex or self._editor.get_latex()
        if latex:
            QApplication.clipboard().setText(latex)
            self.set_status("✓ LaTeX copied!")
            if hasattr(self, "_btn_latex"):
                self._btn_latex.setText("✓ Copied!")
                QTimer.singleShot(1400, lambda: self._btn_latex.setText("📋 Copy LaTeX"))

    def _copy_for_ai(self):
        latex = self._latex or self._editor.get_latex()
        if not latex:
            self.set_status("No LaTeX to copy")
            return
        tmpl = self._settings.ai_prompt_template
        prompt = tmpl.replace("{latex}", latex) if "{latex}" in tmpl else f"{tmpl}\n\n{latex}"
        QApplication.clipboard().setText(prompt)
        self.set_status("🤖 Copied for AI!")
        if hasattr(self, "_btn_ai"):
            self._btn_ai.setText("✓ Copied!")
            QTimer.singleShot(1400, lambda: self._btn_ai.setText("🤖 Copy for AI"))

    def _open_studio_and_collapse(self):
        """Open the studio window and collapse the island card."""
        self.open_studio_requested.emit()
        self._to_notch()

    def _copy_png(self):
        png = self._preview.render_to_png()
        if png:
            img = QImage.fromData(png)
            QApplication.clipboard().setImage(img)
            self.set_status("✓ PNG copied!")
            if hasattr(self, "_btn_png"):
                self._btn_png.setText("✓ Copied!")
                QTimer.singleShot(1400, lambda: self._btn_png.setText("🖼 Copy PNG"))

    def _paste_image(self) -> bool:
        """Paste an image or PDF from clipboard into the island. Returns True if handled."""
        from ..image_proc import extract_image_bytes, extract_pdf_path
        cb = QApplication.clipboard()
        md = cb.mimeData()
        data = extract_image_bytes(md)
        if data:
            self._handle_image_bytes(data)
            self.set_status("📋 Image pasted")
            return True
        pdf_path = extract_pdf_path(md)
        if pdf_path:
            self._handle_pdf_drop(pdf_path)
            return True
        return False

    # ── Keyboard ───────────────────────────────────────────────────────────────
    def keyPressEvent(self, ev: QKeyEvent):
        k = ev.key()
        mod = ev.modifiers()

        if k == Qt.Key.Key_Escape:
            if self._state == "card":
                self._to_notch()
            return

        # Ctrl+X — explicit collapse (same as ✕ button)
        if k == Qt.Key.Key_X and (mod & Qt.KeyboardModifier.ControlModifier):
            if self._state == "card":
                self._to_notch()
            return

        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not (mod & Qt.KeyboardModifier.ShiftModifier):
                self._do_transcribe()
            return

        if k == Qt.Key.Key_V and (mod & Qt.KeyboardModifier.ControlModifier):
            if not (mod & Qt.KeyboardModifier.ShiftModifier):
                self._paste_image()
            return

        if k == Qt.Key.Key_A and (mod & Qt.KeyboardModifier.ControlModifier) and (mod & Qt.KeyboardModifier.AltModifier):
            self._copy_for_ai()
            return

        if k == Qt.Key.Key_C and (mod & Qt.KeyboardModifier.ControlModifier):
            if mod & Qt.KeyboardModifier.AltModifier:
                self._copy_png()
            else:
                self._copy_latex()
            return

        super().keyPressEvent(ev)

    # ── Mouse ──────────────────────────────────────────────────────────────────
    def enterEvent(self, ev):
        if self._state == "notch":
            self.set_status("Drop image or click")
            self._to_capsule()

    def leaveEvent(self, ev):
        if self._state == "capsule":
            self._to_notch()

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            if self._state != "card":
                self._to_card()

    # ── Drag & drop ────────────────────────────────────────────────────────────
    def dragEnterEvent(self, ev):
        if can_accept_mime(ev.mimeData()):
            ev.acceptProposedAction()
            self.set_status("Drop image to transcribe")
            if self._state == "notch":
                self._to_capsule()

    def dragLeaveEvent(self, ev):
        if self._state == "capsule":
            self._to_notch()

    def dragMoveEvent(self, ev):
        if can_accept_mime(ev.mimeData()):
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        from ..image_proc import extract_image_bytes, extract_pdf_path
        pdf_path = extract_pdf_path(ev.mimeData())
        if pdf_path:
            self._handle_pdf_drop(pdf_path)
            ev.acceptProposedAction()
            return
        data = extract_image_bytes(ev.mimeData())
        if data:
            self._handle_image_bytes(data)
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def _handle_pdf_drop(self, pdf_path: str):
        """Load page 1 of a dropped PDF; show a hint if the PDF has multiple pages."""
        from ..image_proc import pdf_page_to_image_bytes, get_pdf_page_count
        fname = os.path.basename(pdf_path)
        try:
            count = get_pdf_page_count(pdf_path)
            img_bytes = pdf_page_to_image_bytes(pdf_path, 0, dpi=200)
            self._handle_image_bytes(img_bytes)
            if count > 1:
                self.set_status(f"PDF p.1/{count} — Open Studio for more")
            else:
                self.set_status(f"PDF loaded: {fname}")
        except ImportError:
            self.set_status("⚠ pip install pymupdf  for PDF support")
        except Exception as e:
            self.set_status(f"✗ PDF error: {str(e)[:45]}")

    # ── Paint ──────────────────────────────────────────────────────────────────
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._theme
        isl_bg     = QColor(t.get("island_bg", "#010409"))
        isl_border = QColor(t.get("island_border", "#30363d"))
        isl_text   = QColor(t.get("island_text", "#58a6ff"))

        b = self.rect()
        path = QPainterPath()
        r = 16 if self._state == "card" else self.height() // 2

        # Flush top border with organic rounded bottom (C++ Dynamic Island OLED curve)
        path.moveTo(b.left(), b.top())
        path.lineTo(b.right(), b.top())
        path.lineTo(b.right(), b.bottom() - r)
        path.quadTo(b.right(), b.bottom(), b.right() - r, b.bottom())
        path.lineTo(b.left() + r, b.bottom())
        path.quadTo(b.left(), b.bottom(), b.left(), b.bottom() - r)
        path.closeSubpath()

        p.fillPath(path, isl_bg)
        p.setPen(QPen(isl_border, 1.2))
        p.drawPath(path)

        if self.width() <= 180 and self._state == "notch":
            # Camera optic indicator in notch center
            p.setBrush(QColor(56, 189, 248, 200))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(b.center().x(), 5.0), 3.0, 3.0)

        elif self.width() > 180 and self._state != "card":
            # Status text
            p.setPen(isl_text)
            p.setFont(QFont("sans-serif", 10, QFont.Weight.Medium))
            p.drawText(
                self.rect().adjusted(16, 0, -16, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                f"✦ {self._status_text}",
            )
            # Busy dot
            if self._is_busy:
                p.setBrush(QColor(t.get("warning", "#d29922")))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(self.width() - 16, self.height() / 2), 4, 4)

        p.end()
