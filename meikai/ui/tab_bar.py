"""Browser-style tab bar with pin, close, double-click rename, horizontal scrolling and overflow dropdown."""
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QDrag, QFont, QFontMetrics, QMouseEvent, QPainter,
    QPainterPath, QPixmap, QWheelEvent,
)
from PyQt6.QtWidgets import QWidget, QSizePolicy, QToolTip, QMenu, QLineEdit


TAB_H = 32
TAB_MIN_W = 110
TAB_MAX_W = 220
CLOSE_SIZE = 16
PIN_ICON = "★"
CLOSE_ICON = "×"


class TabBarWidget(QWidget):
    """
    Chrome/Firefox-style tab bar with:
      - Double-click rename
      - Pin toggling
      - Horizontal scroll arrows (◀ ▶)
      - Tab overflow dropdown (▼)
      - Mouse-wheel horizontal scrolling
      - Automatic scroll tracking on active tab change

    Signals:
      tab_selected(tab_id)
      tab_close_requested(tab_id)
      tab_renamed(tab_id, new_title)
      tab_pin_toggled(tab_id)
      new_tab_requested()
    """
    tab_selected        = pyqtSignal(str)
    tab_close_requested = pyqtSignal(str)
    tab_renamed         = pyqtSignal(str, str)
    tab_pin_toggled     = pyqtSignal(str)
    new_tab_requested   = pyqtSignal()

    def __init__(self, theme: dict, overflow_mode: str = "dropdown", parent=None):
        super().__init__(parent)
        self._theme = theme
        self._overflow_mode = overflow_mode  # "dropdown" | "scroll" | "both"
        self._tabs: List[Dict] = []          # {id, title, pinned}
        self._active_id: Optional[str] = None
        self._scroll_offset: int = 0
        self._hover_idx: Optional[int] = None
        self._hover_close: Optional[int] = None
        self._hover_btn: Optional[str] = None  # "new_tab", "scroll_left", "scroll_right", "overflow"
        self._drag_start: Optional[QPoint] = None
        self._drag_tab_idx: Optional[int] = None
        self._rename_idx: Optional[int] = None

        self.setFixedHeight(TAB_H + 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

        # Inline rename editor
        self._rename_edit = QLineEdit(self)
        self._rename_edit.setVisible(False)
        self._rename_edit.returnPressed.connect(self._finish_rename)
        self._rename_edit.editingFinished.connect(self._finish_rename)

    # ── Public API ─────────────────────────────────────────────────────────────
    def get_tabs(self) -> List[Dict]:
        """Return a copy of the current tab list."""
        return list(self._tabs)

    def set_tabs(self, tabs: List[Dict]):
        """tabs: list of {id, title, pinned}"""
        self._tabs = [dict(t) for t in tabs]
        self._clamp_scroll()
        self.update()

    def set_active(self, tab_id: Optional[str]):
        self._active_id = tab_id
        if tab_id:
            for i, t in enumerate(self._tabs):
                if t["id"] == tab_id:
                    self._ensure_tab_visible(i)
                    break
        self.update()

    def set_overflow_mode(self, mode: str):
        """Change tab strip overflow mode: 'dropdown' | 'scroll' | 'both'."""
        if mode in ("dropdown", "scroll", "both"):
            self._overflow_mode = mode
            self._clamp_scroll()
            self.update()

    def get_overflow_mode(self) -> str:
        return self._overflow_mode

    def update_tab(self, tab_id: str, **kwargs):
        for t in self._tabs:
            if t["id"] == tab_id:
                t.update(kwargs)
        self.update()

    def apply_theme(self, theme: dict):
        self._theme = theme
        self.update()

    # ── Controls & Layout ──────────────────────────────────────────────────────
    def _controls_rects(self) -> Tuple[Dict[str, QRect], int]:
        """
        Calculate rectangles for right-aligned control buttons and return
        the (rect_dict, avail_tab_width) tuple.
        """
        rects: Dict[str, QRect] = {}
        x = self.width() - 4
        btn_y = (TAB_H - 22) // 2

        # 1. Overflow Dropdown button (if dropdown or both)
        if self._overflow_mode in ("dropdown", "both"):
            x -= 24
            rects["overflow"] = QRect(x, btn_y, 24, 22)
            x -= 3

        # 2. Scroll Right & Left buttons (if scroll or both)
        if self._overflow_mode in ("scroll", "both"):
            x -= 22
            rects["scroll_right"] = QRect(x, btn_y, 22, 22)
            x -= 2
            x -= 22
            rects["scroll_left"] = QRect(x, btn_y, 22, 22)
            x -= 3

        # 3. New Tab button (+)
        x -= 26
        rects["new_tab"] = QRect(x, btn_y, 26, 22)

        avail_w = max(80, x - 4)
        return rects, avail_w

    def _avail_width(self) -> int:
        _, avail = self._controls_rects()
        return avail

    def _tab_width(self) -> int:
        n = len(self._tabs)
        if n == 0:
            return TAB_MAX_W
        avail = self._avail_width()
        ideal = avail // n
        if ideal >= TAB_MIN_W:
            return min(TAB_MAX_W, ideal)
        return TAB_MIN_W

    def _max_scroll(self) -> int:
        total_w = len(self._tabs) * self._tab_width()
        return max(0, total_w - self._avail_width())

    def _clamp_scroll(self):
        ms = self._max_scroll()
        self._scroll_offset = max(0, min(self._scroll_offset, ms))

    def _scroll_by(self, delta: int):
        ms = self._max_scroll()
        new_off = max(0, min(self._scroll_offset + delta, ms))
        if new_off != self._scroll_offset:
            self._scroll_offset = new_off
            self.update()

    def _ensure_tab_visible(self, idx: int):
        if idx < 0 or idx >= len(self._tabs):
            return
        tw = self._tab_width()
        left = idx * tw
        right = left + tw
        avail = self._avail_width()
        if left < self._scroll_offset:
            self._scroll_offset = left
            self.update()
        elif right > self._scroll_offset + avail:
            self._scroll_offset = max(0, right - avail)
            self.update()

    def _tab_rect(self, idx: int) -> QRect:
        tw = self._tab_width()
        return QRect(idx * tw - self._scroll_offset, 0, tw, TAB_H)

    def _close_rect(self, idx: int) -> QRect:
        tr = self._tab_rect(idx)
        x = tr.right() - CLOSE_SIZE - 6
        y = (TAB_H - CLOSE_SIZE) // 2
        return QRect(x, y, CLOSE_SIZE, CLOSE_SIZE)

    def _idx_at(self, pos: QPoint) -> Optional[int]:
        if pos.x() > self._avail_width() or pos.y() > TAB_H:
            return None
        tw = self._tab_width()
        local_x = pos.x() + self._scroll_offset
        idx = local_x // tw
        if 0 <= idx < len(self._tabs):
            return idx
        return None

    # ── Dropdown Menu ──────────────────────────────────────────────────────────
    def _open_overflow_menu(self, global_pos: QPoint):
        if not self._tabs:
            return
        menu = QMenu(self)
        t = self._theme
        menu.setStyleSheet(f"""
            QMenu {{
                background: {t.get('surface','#161b22')};
                color: {t.get('text','#e6edf3')};
                border: 1px solid {t.get('border','#30363d')};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 20px 6px 12px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background: {t.get('accent2','#1f6feb')}44;
            }}
        """)

        # Add header showing total count
        header = menu.addAction(f"Open Tabs ({len(self._tabs)})")
        header.setEnabled(False)
        menu.addSeparator()

        for idx, tab in enumerate(self._tabs):
            is_active = (tab["id"] == self._active_id)
            title = tab.get("title", "Untitled")
            pin = "★ " if tab.get("pinned") else ""
            mark = "● " if is_active else "   "
            action = menu.addAction(f"{mark}{pin}{title}")
            action.triggered.connect(
                lambda checked, tid=tab["id"], i=idx: self._select_and_scroll(tid, i)
            )

        menu.exec(global_pos)

    def _select_and_scroll(self, tab_id: str, idx: int):
        self.tab_selected.emit(tab_id)
        self._ensure_tab_visible(idx)

    # ── Paint ──────────────────────────────────────────────────────────────────
    def paintEvent(self, ev):
        t = self._theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Background
        painter.fillRect(self.rect(), QColor(t.get("bg", "#0d1117")))

        rects, avail_w = self._controls_rects()
        ms = self._max_scroll()
        tw = self._tab_width()

        # 2. Draw tabs with clipping to tab strip area
        painter.save()
        painter.setClipRect(0, 0, avail_w, TAB_H)
        for i, tab in enumerate(self._tabs):
            tr = self._tab_rect(i)
            if tr.right() >= 0 and tr.left() <= avail_w:
                self._draw_tab(painter, i, tab, tr)
        painter.restore()

        # 3. Bottom separator line
        painter.setPen(QColor(t.get("border", "#30363d")))
        painter.drawLine(0, TAB_H, self.width(), TAB_H)

        # 4. Draw Right Control Strip Background
        ctrl_strip_rect = QRect(avail_w, 0, self.width() - avail_w, TAB_H)
        painter.fillRect(ctrl_strip_rect, QColor(t.get("bg", "#0d1117")))

        btn_bg = QColor(t.get("button_bg", "#21262d"))
        btn_hover = QColor(t.get("button_hover", "#30363d"))
        text_dim = QColor(t.get("text_dim", "#8b949e"))
        text_norm = QColor(t.get("text", "#e6edf3"))
        accent = QColor(t.get("accent", "#58a6ff"))

        # Helper to draw a rounded button
        def _draw_btn(rect: QRect, label: str, name: str, enabled: bool = True, highlight: bool = False):
            is_hover = (self._hover_btn == name) and enabled
            bg = btn_hover if is_hover else btn_bg
            path = QPainterPath()
            path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), 4, 4)
            painter.fillPath(path, bg)
            painter.setPen(QColor(t.get("border", "#30363d")))
            painter.drawPath(path)

            if not enabled:
                painter.setPen(QColor(text_dim.red(), text_dim.green(), text_dim.blue(), 90))
            elif highlight:
                painter.setPen(accent)
            else:
                painter.setPen(text_norm if is_hover else text_dim)

            painter.setFont(QFont("sans-serif", 10, QFont.Weight.Bold))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        # Draw New Tab (+)
        if "new_tab" in rects:
            _draw_btn(rects["new_tab"], "+", "new_tab", enabled=True)

        # Draw Scroll Arrows (◀ ▶)
        if "scroll_left" in rects and "scroll_right" in rects:
            can_scroll_left = (self._scroll_offset > 0)
            can_scroll_right = (self._scroll_offset < ms)
            _draw_btn(rects["scroll_left"], "◀", "scroll_left", enabled=can_scroll_left)
            _draw_btn(rects["scroll_right"], "▶", "scroll_right", enabled=can_scroll_right)

        # Draw Overflow Dropdown (▼)
        if "overflow" in rects:
            is_overflowing = (ms > 0)
            _draw_btn(rects["overflow"], "▼", "overflow", enabled=True, highlight=is_overflowing)

        painter.end()

    def _draw_tab(self, painter: QPainter, idx: int, tab: Dict, tr: QRect):
        t = self._theme
        is_active = (tab["id"] == self._active_id)
        is_hover  = (idx == self._hover_idx)

        # Background
        if is_active:
            bg = QColor(t.get("tab_active", "#161b22"))
        elif is_hover:
            bg = QColor(t.get("tab_hover", "#1c2128"))
        else:
            bg = QColor(t.get("bg", "#0d1117"))

        path = QPainterPath()
        path.addRoundedRect(float(tr.x()), 0, float(tr.width()), float(TAB_H), 6, 6)
        painter.fillPath(path, bg)

        if is_active:
            # Accent bottom line
            painter.setPen(QColor(t.get("accent", "#58a6ff")))
            painter.drawLine(tr.x() + 4, TAB_H - 1, tr.right() - 4, TAB_H - 1)

        # Pin icon
        x_start = tr.x() + 8
        if tab.get("pinned"):
            painter.setPen(QColor(t.get("warning", "#d29922")))
            painter.setFont(QFont("sans-serif", 10))
            painter.drawText(QRect(x_start, 0, 16, TAB_H), Qt.AlignmentFlag.AlignVCenter, PIN_ICON)
            x_start += 18

        # Title & Close button layout
        show_close = is_active or is_hover or (tr.width() >= 120)
        close_w = (CLOSE_SIZE + 8) if show_close else 4
        title_w = max(0, tr.right() - x_start - close_w)
        title_rect = QRect(x_start, 0, title_w, TAB_H)
        text_col = QColor(t.get("text", "#e6edf3") if is_active else t.get("text_dim", "#8b949e"))
        painter.setPen(text_col)
        painter.setFont(QFont("sans-serif", 10 if tr.width() < 100 else 11))
        fm = QFontMetrics(painter.font())
        title = fm.elidedText(tab.get("title", "Untitled"), Qt.TextElideMode.ElideRight, title_rect.width())
        if title_rect.width() > 10:
            painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, title)

        # Close button
        cr = self._close_rect(idx)
        if show_close:
            hover_close = (idx == self._hover_close)
            if hover_close:
                painter.setPen(QColor(t.get("error", "#f85149")))
            else:
                painter.setPen(QColor(t.get("text_dim", "#8b949e")))
            painter.setFont(QFont("sans-serif", 13, QFont.Weight.Bold))
            painter.drawText(cr, Qt.AlignmentFlag.AlignCenter, CLOSE_ICON)

    # ── Mouse & Wheel events ───────────────────────────────────────────────────
    def wheelEvent(self, ev: QWheelEvent):
        delta = ev.angleDelta().y() or ev.angleDelta().x()
        if delta != 0:
            # Invert so scrolling down scrolls right (standard browser behavior)
            self._scroll_by(-delta)
            ev.accept()
        else:
            super().wheelEvent(ev)

    def mousePressEvent(self, ev: QMouseEvent):
        pos = ev.position().toPoint()
        rects, _ = self._controls_rects()

        if ev.button() == Qt.MouseButton.LeftButton:
            # Check control buttons
            if "new_tab" in rects and rects["new_tab"].contains(pos):
                self.new_tab_requested.emit()
                return
            if "scroll_left" in rects and rects["scroll_left"].contains(pos):
                self._scroll_by(-120)
                return
            if "scroll_right" in rects and rects["scroll_right"].contains(pos):
                self._scroll_by(120)
                return
            if "overflow" in rects and rects["overflow"].contains(pos):
                self._open_overflow_menu(self.mapToGlobal(rects["overflow"].bottomLeft()))
                return

            idx = self._idx_at(pos)
            if idx is not None:
                # Check close button
                if self._close_rect(idx).contains(pos):
                    self.tab_close_requested.emit(self._tabs[idx]["id"])
                    return
                # Select tab & ensure visible
                self.tab_selected.emit(self._tabs[idx]["id"])
                self._ensure_tab_visible(idx)
                self._drag_start = pos
                self._drag_tab_idx = idx

        elif ev.button() == Qt.MouseButton.MiddleButton:
            idx = self._idx_at(pos)
            if idx is not None:
                self.tab_close_requested.emit(self._tabs[idx]["id"])

    def mouseDoubleClickEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            pos = ev.position().toPoint()
            idx = self._idx_at(pos)
            if idx is not None:
                self._start_rename(idx)

    def contextMenuEvent(self, ev):
        pos = ev.pos()
        idx = self._idx_at(pos)
        if idx is None:
            return
        tab = self._tabs[idx]
        menu = QMenu(self)
        t = self._theme

        pin_action = menu.addAction(("★ Unpin" if tab.get("pinned") else "☆ Pin") + " tab")
        rename_action = menu.addAction("✏ Rename tab")
        menu.addSeparator()
        close_action = menu.addAction("✕ Close tab")

        menu.setStyleSheet(f"""
            QMenu {{
                background: {t.get('surface','#161b22')};
                color: {t.get('text','#e6edf3')};
                border: 1px solid {t.get('border','#30363d')};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{ padding: 6px 16px; border-radius: 4px; }}
            QMenu::item:selected {{ background: {t.get('accent2','#1f6feb')}44; }}
        """)
        action = menu.exec(ev.globalPos())
        if action == pin_action:
            self.tab_pin_toggled.emit(tab["id"])
        elif action == rename_action:
            self._start_rename(idx)
        elif action == close_action:
            self.tab_close_requested.emit(tab["id"])

    def mouseMoveEvent(self, ev: QMouseEvent):
        pos = ev.position().toPoint()
        rects, avail_w = self._controls_rects()

        # Check button hover
        old_hover_btn = self._hover_btn
        self._hover_btn = None
        for name, r in rects.items():
            if r.contains(pos):
                self._hover_btn = name
                break

        # Check tab hover
        idx = self._idx_at(pos)
        old_hover = self._hover_idx
        old_close = self._hover_close
        self._hover_idx = idx
        self._hover_close = idx if (idx is not None and self._close_rect(idx).contains(pos)) else None

        if self._hover_btn != old_hover_btn or self._hover_idx != old_hover or self._hover_close != old_close:
            self.update()

        # Tooltip
        if self._hover_btn == "new_tab":
            QToolTip.showText(ev.globalPosition().toPoint(), "New Tab (Ctrl+T)", self)
        elif self._hover_btn == "scroll_left":
            QToolTip.showText(ev.globalPosition().toPoint(), "Scroll Tabs Left", self)
        elif self._hover_btn == "scroll_right":
            QToolTip.showText(ev.globalPosition().toPoint(), "Scroll Tabs Right", self)
        elif self._hover_btn == "overflow":
            QToolTip.showText(ev.globalPosition().toPoint(), f"All Tabs ({len(self._tabs)})", self)
        elif idx is not None and idx < len(self._tabs):
            title = self._tabs[idx].get("title", "")
            if title:
                QToolTip.showText(ev.globalPosition().toPoint(), title, self)

    def leaveEvent(self, ev):
        self._hover_idx = None
        self._hover_close = None
        self._hover_btn = None
        self.update()

    # ── Rename ─────────────────────────────────────────────────────────────────
    def _start_rename(self, idx: int):
        self._ensure_tab_visible(idx)
        self._rename_idx = idx
        tr = self._tab_rect(idx)
        self._rename_edit.setGeometry(tr.x() + 4, 4, tr.width() - 8, TAB_H - 8)
        self._rename_edit.setText(self._tabs[idx].get("title", ""))
        self._rename_edit.selectAll()
        self._rename_edit.setVisible(True)
        self._rename_edit.setFocus()

        t = self._theme
        self._rename_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {t.get('editor_bg','#0d1117')};
                color: {t.get('text','#e6edf3')};
                border: 1px solid {t.get('accent','#58a6ff')};
                border-radius: 3px;
                font-size: 11px;
                padding: 2px 4px;
            }}
        """)

    def _finish_rename(self):
        if self._rename_idx is not None and self._rename_edit.isVisible():
            new_title = self._rename_edit.text().strip()
            if new_title and self._rename_idx < len(self._tabs):
                tab_id = self._tabs[self._rename_idx]["id"]
                self.tab_renamed.emit(tab_id, new_title)
        self._rename_edit.setVisible(False)
        self._rename_idx = None
