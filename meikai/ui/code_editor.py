"""LaTeX code editor with syntax highlighting, line numbers, and undo/redo."""
from PyQt6.QtCore import Qt, QRect, QSize, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QSyntaxHighlighter,
    QTextCharFormat, QTextCursor, QKeySequence,
)
from PyQt6.QtWidgets import QPlainTextEdit, QWidget, QTextEdit


class _LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor._line_num_width(), 0)

    def paintEvent(self, ev):
        self._editor._paint_line_numbers(ev)


class _LaTeXHighlighter(QSyntaxHighlighter):
    """Simple LaTeX syntax highlighter."""

    def __init__(self, doc, theme: dict):
        super().__init__(doc)
        self._theme = theme
        self._build_rules()

    def _fmt(self, color: str, bold=False, italic=False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:
            f.setFontWeight(700)
        if italic:
            f.setFontItalic(True)
        return f

    def _build_rules(self):
        t = self._theme
        acc = t.get("accent", "#58a6ff")
        acc2 = t.get("accent2", "#1f6feb")
        success = t.get("success", "#3fb950")
        warning = t.get("warning", "#d29922")
        error = t.get("error", "#f85149")
        text = t.get("text", "#e6edf3")

        import re
        self._rules = [
            # Display math \[...\]
            (re.compile(r'\\\[.*?\\\]', re.DOTALL), self._fmt("#bd93f9", bold=True)),
            # Display math $$...$$
            (re.compile(r'\$\$.*?\$\$', re.DOTALL), self._fmt("#bd93f9", bold=True)),
            # Inline math \(...\)
            (re.compile(r'\\\(.*?\\\)', re.DOTALL), self._fmt(acc, bold=True)),
            # Inline math $...$
            (re.compile(r'\$[^$\n]+?\$'), self._fmt(acc, bold=True)),
            # LaTeX commands \cmd
            (re.compile(r'\\[a-zA-Z@]+\*?'), self._fmt("#ff79c6")),
            # Curly braces
            (re.compile(r'[{}]'), self._fmt(acc2, bold=True)),
            # Square brackets
            (re.compile(r'[\[\]]'), self._fmt(warning)),
            # Comments
            (re.compile(r'%[^\n]*'), self._fmt(t.get("text_dim", "#8b949e"), italic=True)),
            # Numbers
            (re.compile(r'\b\d+(\.\d+)?\b'), self._fmt(success)),
        ]

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)

    def rehighlight_with_theme(self, theme: dict):
        self._theme = theme
        self._build_rules()
        self.rehighlight()


class CodeEditor(QPlainTextEdit):
    """LaTeX code editor with line numbers and syntax highlighting."""

    latex_changed = pyqtSignal(str)

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._line_num_area = _LineNumberArea(self)
        self._highlighter = _LaTeXHighlighter(self.document(), theme)

        self.setFont(QFont("JetBrains Mono, Fira Code, Courier New", 13))
        self.setTabStopDistance(QFontMetrics(self.font()).horizontalAdvance(" ") * 4)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self._apply_style()

        self.blockCountChanged.connect(self._update_line_num_width)
        self.updateRequest.connect(self._update_line_num_area)
        self.textChanged.connect(self._on_text_changed)

        self._update_line_num_width()

    # ── Public API ─────────────────────────────────────────────────────────────
    def set_latex(self, text: str):
        if self.toPlainText() != text:
            cursor = self.textCursor()
            pos = cursor.position()
            self.setPlainText(text)
            cursor = self.textCursor()
            cursor.setPosition(min(pos, len(text)))
            self.setTextCursor(cursor)

    def get_latex(self) -> str:
        return self.toPlainText()

    def apply_theme(self, theme: dict):
        self._theme = theme
        self._apply_style()
        self._highlighter.rehighlight_with_theme(theme)

    # ── Style ──────────────────────────────────────────────────────────────────
    def _apply_style(self):
        t = self._theme
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {t.get('editor_bg', '#0d1117')};
                color: {t.get('text', '#e6edf3')};
                border: none;
                font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
                font-size: 13px;
                selection-background-color: {t.get('accent2', '#1f6feb')}55;
            }}
            QScrollBar:vertical {{
                background: {t.get('surface','#161b22')};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {t.get('border','#30363d')};
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar:horizontal {{
                background: {t.get('surface','#161b22')};
                height: 8px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {t.get('border','#30363d')};
                border-radius: 4px;
                min-width: 24px;
            }}
        """)

    # ── Line numbers ───────────────────────────────────────────────────────────
    def _line_num_width(self) -> int:
        digits = max(1, len(str(self.blockCount())))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_num_width(self):
        self.setViewportMargins(self._line_num_width(), 0, 0, 0)

    def _update_line_num_area(self, rect, dy):
        if dy:
            self._line_num_area.scroll(0, dy)
        else:
            self._line_num_area.update(0, rect.y(), self._line_num_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_num_width()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        cr = self.contentsRect()
        self._line_num_area.setGeometry(QRect(cr.left(), cr.top(), self._line_num_width(), cr.height()))

    def _paint_line_numbers(self, ev):
        t = self._theme
        painter = QPainter(self._line_num_area)
        painter.fillRect(ev.rect(), QColor(t.get("surface", "#161b22")))

        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= ev.rect().bottom():
            if block.isVisible() and bottom >= ev.rect().top():
                painter.setPen(QColor(t.get("text_dim", "#8b949e")))
                painter.setFont(QFont("monospace", 10))
                painter.drawText(
                    0, top, self._line_num_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, str(block_num + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_num += 1

    def _on_text_changed(self):
        self.latex_changed.emit(self.toPlainText())

    def keyPressEvent(self, ev):
        if (ev.key() == Qt.Key.Key_X and
                (ev.modifiers() & Qt.KeyboardModifier.ControlModifier) and
                not (ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
            cursor = self.textCursor()
            if not cursor.hasSelection():
                # No text selected -> let parent / shortcut handle collapse
                ev.ignore()
                return
        super().keyPressEvent(ev)
