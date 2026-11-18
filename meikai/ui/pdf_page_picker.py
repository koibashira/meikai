"""PDF page picker dialog — scrollable thumbnail grid for selecting pages to open."""
from __future__ import annotations

import threading
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)


class _PageThumb(QWidget):
    """Single page thumbnail cell — click to select."""

    clicked = pyqtSignal(int, bool, bool)   # page_num, ctrl, shift

    def __init__(self, page_num: int, theme: dict, thumb_w: int = 120, parent=None):
        super().__init__(parent)
        self._page_num = page_num
        self._theme = theme
        self._thumb_w = thumb_w
        self._thumb_h = int(thumb_w * 1.414)   # A4 portrait ratio
        self._selected = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 5)
        layout.setSpacing(4)

        self._img_lbl = QLabel("⏳")
        self._img_lbl.setFixedSize(thumb_w, self._thumb_h)
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet(
            f"background:{theme.get('surface','#161b22')};"
            f"color:{theme.get('text_dim','#8b949e')};"
            "border-radius:3px; font-size:16px;"
        )
        layout.addWidget(self._img_lbl)

        self._num_lbl = QLabel(f"Page {page_num + 1}")
        self._num_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._num_lbl.setStyleSheet(
            f"color:{theme.get('text_dim','#8b949e')}; font-size:11px; border:none; background:transparent;"
        )
        layout.addWidget(self._num_lbl)

        self.setFixedWidth(thumb_w + 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._repaint()

    # ── Public ─────────────────────────────────────────────────────────────────
    def set_thumbnail(self, png_bytes: bytes) -> None:
        img = QImage.fromData(png_bytes)
        if not img.isNull():
            px = QPixmap.fromImage(img).scaled(
                self._thumb_w, self._thumb_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(px)
            self._img_lbl.setText("")

    def set_selected(self, selected: bool) -> None:
        if self._selected != selected:
            self._selected = selected
            self._repaint()

    # ── Internal ───────────────────────────────────────────────────────────────
    def _repaint(self) -> None:
        t = self._theme
        if self._selected:
            border = t.get("accent", "#58a6ff")
            bg = t.get("accent2", "#1f6feb") + "22"
        else:
            border = t.get("border", "#30363d")
            bg = "transparent"
        self.setStyleSheet(
            f"QWidget {{ background:{bg}; border:2px solid {border}; border-radius:7px; }}"
        )

    def mousePressEvent(self, ev):
        mods = ev.modifiers()
        self.clicked.emit(
            self._page_num,
            bool(mods & Qt.KeyboardModifier.ControlModifier),
            bool(mods & Qt.KeyboardModifier.ShiftModifier),
        )


class PdfPagePickerDialog(QDialog):
    """
    Modal dialog showing a scrollable grid of PDF page thumbnails.

    Usage::
        dlg = PdfPagePickerDialog(path, page_count, theme, parent)
        if dlg.exec():
            for page_num in dlg.selected_pages():
                ...
    """

    # Emitted from background thread → queued delivery to main thread (thread-safe)
    _thumb_ready = pyqtSignal(int, bytes)

    COLS = 5          # thumbnail columns
    THUMB_W = 120     # thumbnail width in px

    def __init__(self, pdf_path: str, page_count: int, theme: dict, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._page_count = page_count
        self._theme = theme
        self._selected: set[int] = {0}      # default: first page
        self._last_clicked: Optional[int] = 0
        self._thumbs: List[_PageThumb] = []
        self._loading_cancelled = False

        self.setWindowTitle("Select PDF Pages")
        self.setMinimumSize(720, 540)
        self._build_ui()
        self._apply_style()
        self._thumb_ready.connect(self._on_thumb_ready)
        self._update_selection_visuals()
        self._update_btn()
        self._start_loading()

    # ── UI construction ────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        t = self._theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # Header
        hdr = QLabel(f"Select page(s) to open  —  {self._page_count} pages total")
        hdr.setStyleSheet(f"color:{t.get('text','#e6edf3')}; font-size:13px; font-weight:bold; border:none;")
        layout.addWidget(hdr)

        hint = QLabel("Click to select  ·  Ctrl+Click for multiple  ·  Shift+Click for range")
        hint.setStyleSheet(f"color:{t.get('text_dim','#8b949e')}; font-size:11px; border:none;")
        layout.addWidget(hint)

        # Scrollable grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self._grid = QGridLayout(container)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setSpacing(10)

        for i in range(self._page_count):
            thumb = _PageThumb(i, t, self.THUMB_W)
            thumb.clicked.connect(self._on_thumb_clicked)
            self._thumbs.append(thumb)
            self._grid.addWidget(thumb, i // self.COLS, i % self.COLS)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._all_btn = QPushButton("Select All")
        self._all_btn.clicked.connect(self._select_all)
        btn_row.addWidget(self._all_btn)

        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._open_btn = QPushButton("▶ Open Page 1")
        self._open_btn.setDefault(True)
        self._open_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._open_btn)

        layout.addLayout(btn_row)

    def _apply_style(self) -> None:
        t = self._theme
        self.setStyleSheet(f"""
            QDialog {{
                background: {t.get('bg','#0d1117')};
                color: {t.get('text','#e6edf3')};
            }}
            QScrollArea {{
                background: {t.get('bg','#0d1117')};
                border: 1px solid {t.get('border','#30363d')};
                border-radius: 6px;
            }}
            QScrollBar:vertical {{
                background: {t.get('surface','#161b22')};
                width: 8px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {t.get('border','#30363d')};
                border-radius: 4px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QPushButton {{
                background: {t.get('button_bg','#21262d')};
                color: {t.get('text','#e6edf3')};
                border: 1px solid {t.get('border','#30363d')};
                border-radius: 6px; padding: 6px 16px; font-size: 12px;
            }}
            QPushButton:hover {{
                background: {t.get('button_hover','#30363d')};
                border-color: {t.get('accent','#58a6ff')};
            }}
        """)
        self._open_btn.setStyleSheet(f"""
            QPushButton {{
                background: {t.get('accent2','#1f6feb')};
                color: white; border: none;
                border-radius: 6px; padding: 6px 18px;
                font-size: 12px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {t.get('accent','#58a6ff')}; }}
            QPushButton:disabled {{
                background: {t.get('button_bg','#21262d')};
                color: {t.get('text_dim','#8b949e')};
            }}
        """)

    # ── Selection logic ────────────────────────────────────────────────────────
    def _on_thumb_clicked(self, page_num: int, ctrl: bool, shift: bool) -> None:
        if shift and self._last_clicked is not None:
            lo, hi = sorted([self._last_clicked, page_num])
            self._selected = set(range(lo, hi + 1))
        elif ctrl:
            if page_num in self._selected:
                self._selected.discard(page_num)
            else:
                self._selected.add(page_num)
        else:
            self._selected = {page_num}
        self._last_clicked = page_num
        self._update_selection_visuals()
        self._update_btn()

    def _select_all(self) -> None:
        self._selected = set(range(self._page_count))
        self._update_selection_visuals()
        self._update_btn()

    def _update_selection_visuals(self) -> None:
        for thumb in self._thumbs:
            thumb.set_selected(thumb._page_num in self._selected)

    def _update_btn(self) -> None:
        n = len(self._selected)
        if n == 0:
            self._open_btn.setText("Open Page 1")
            self._open_btn.setEnabled(False)
        elif n == 1:
            pg = next(iter(self._selected)) + 1
            self._open_btn.setText(f"▶ Open Page {pg}")
            self._open_btn.setEnabled(True)
        else:
            self._open_btn.setText(f"▶ Open {n} Pages as Tabs")
            self._open_btn.setEnabled(True)

    # ── Thumbnail loading (background thread) ──────────────────────────────────
    def _start_loading(self) -> None:
        def _worker():
            try:
                from ..image_proc import pdf_page_to_thumbnail
            except Exception:
                return
            for i in range(self._page_count):
                if self._loading_cancelled:
                    break
                try:
                    png = pdf_page_to_thumbnail(self._pdf_path, i, max_dim=self.THUMB_W)
                    if png and not self._loading_cancelled:
                        self._thumb_ready.emit(i, png)
                except Exception:
                    pass

        t = threading.Thread(target=_worker, daemon=True, name="pdf-thumb-loader")
        t.start()

    def _on_thumb_ready(self, page_num: int, png_bytes: bytes) -> None:
        if 0 <= page_num < len(self._thumbs):
            self._thumbs[page_num].set_thumbnail(png_bytes)

    # ── Public API ─────────────────────────────────────────────────────────────
    def selected_pages(self) -> List[int]:
        return sorted(self._selected)

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    def closeEvent(self, ev) -> None:
        self._loading_cancelled = True
        super().closeEvent(ev)
