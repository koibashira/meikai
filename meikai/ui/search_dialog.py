"""Fuzzy search dialog over tabs (title, latex, tags)."""
from typing import Callable, List, Dict, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QVBoxLayout, QWidget,
)

from ..database import get_db

try:
    from rapidfuzz import fuzz, process
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False


def _fuzzy_score(query: str, candidate: str) -> float:
    if not query:
        return 100.0
    if _HAS_RAPIDFUZZ:
        return fuzz.partial_ratio(query.lower(), candidate.lower())
    # Fallback: simple substring
    return 80.0 if query.lower() in candidate.lower() else 0.0


class SearchDialog(QDialog):
    tab_selected = pyqtSignal(str)

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setWindowTitle("🔍 Quick Search")
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setModal(True)
        self.setMinimumWidth(500)
        self._setup_ui()
        self._apply_style()
        self._all_tabs = get_db().get_all_tabs()
        self._populate("")

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Search bar
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search tabs by title, LaTeX, or tag…")
        self._search.setFont(QFont("sans-serif", 13))
        self._search.textChanged.connect(self._on_query_changed)
        layout.addWidget(self._search)

        # Results
        self._list = QListWidget()
        self._list.setMaximumHeight(360)
        self._list.itemActivated.connect(self._on_activated)
        self._list.itemClicked.connect(self._on_activated)
        layout.addWidget(self._list)

        # Hint
        hint = QLabel("Enter to open  ·  Esc to close")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

    def _populate(self, query: str):
        self._list.clear()
        tabs = self._all_tabs
        scored = []
        for tab in tabs:
            text = " ".join([
                tab.get("title", ""),
                tab.get("latex", "")[:200],
                " ".join(tab.get("tags", [])),
            ])
            score = _fuzzy_score(query, text)
            if score >= 40 or not query:
                scored.append((score, tab))

        scored.sort(key=lambda x: -x[0])
        for score, tab in scored[:30]:
            title = tab.get("title", "Untitled")
            tags = "  ".join(tab.get("tags", []))
            item = QListWidgetItem()
            item.setText(f"  {title}   {tags}")
            item.setData(Qt.ItemDataRole.UserRole, tab["id"])
            self._list.addItem(item)

        if self._list.count():
            self._list.setCurrentRow(0)

    def _on_query_changed(self, text: str):
        self._populate(text)

    def _on_activated(self, item: QListWidgetItem):
        tid = item.data(Qt.ItemDataRole.UserRole)
        if tid:
            self.tab_selected.emit(tid)
            self.accept()

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key.Key_Escape:
            self.reject()
        elif ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cur = self._list.currentItem()
            if cur:
                self._on_activated(cur)
        elif ev.key() == Qt.Key.Key_Down:
            row = self._list.currentRow()
            if row < self._list.count() - 1:
                self._list.setCurrentRow(row + 1)
        elif ev.key() == Qt.Key.Key_Up:
            row = self._list.currentRow()
            if row > 0:
                self._list.setCurrentRow(row - 1)
        else:
            super().keyPressEvent(ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        self._search.setFocus()

    def _apply_style(self):
        t = self._theme
        self.setStyleSheet(f"""
            QDialog {{
                background: {t.get('surface','#161b22')};
                border: 1px solid {t.get('accent','#58a6ff')};
                border-radius: 10px;
            }}
            QLineEdit {{
                background: {t.get('editor_bg','#0d1117')};
                color: {t.get('text','#e6edf3')};
                border: 1px solid {t.get('border','#30363d')};
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
            }}
            QListWidget {{
                background: {t.get('bg','#0d1117')};
                color: {t.get('text','#e6edf3')};
                border: 1px solid {t.get('border','#30363d')};
                border-radius: 6px;
                font-size: 12px;
                outline: none;
            }}
            QListWidget::item {{ padding: 8px; border-radius: 4px; }}
            QListWidget::item:selected {{
                background: {t.get('accent2','#1f6feb')}44;
                color: {t.get('text','#e6edf3')};
            }}
            QListWidget::item:hover {{
                background: {t.get('tab_hover','#1c2128')};
            }}
            QLabel {{
                color: {t.get('text_dim','#8b949e')};
                font-size: 11px;
            }}
        """)
