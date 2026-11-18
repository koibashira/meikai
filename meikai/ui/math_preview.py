"""
Math preview widget — renders LaTeX via MathJax (Node.js daemon) → SVG-in-HTML.

Architecture:
  - A single persistent Node.js child process (render_math.js) is shared
    across all MathPreviewWidget instances in the process.
  - JSON-RPC over stdin/stdout: send one line, receive one line.
  - Rendering is async (QThread worker) so the UI never blocks.
  - Falls back to syntax-highlighted raw LaTeX if the daemon is unavailable.
  - Font scaling: each widget computes its own base font size from its height,
    so the Island card and Studio preview scale independently.
"""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import threading
import time
from typing import Optional

from PyQt6.QtCore import Qt, QSize, QTimer, QThread, QObject, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextBrowser, QSizePolicy,
)


# ── MathJax daemon (singleton per process) ────────────────────────────────────
_daemon_lock = threading.Lock()
_daemon_write_lock = threading.Lock()
_daemon_proc: Optional[subprocess.Popen] = None
_daemon_id = 0
_pending: dict = {}          # id → (event, result_holder)
_daemon_reader_thread = None


def _daemon_script_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "math_renderer", "render_math.js")


def _start_daemon() -> Optional[subprocess.Popen]:
    script = _daemon_script_path()
    if not os.path.isfile(script):
        return None
    try:
        proc = subprocess.Popen(
            ["node", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        return proc
    except Exception:
        return None


def _reader_loop(proc: subprocess.Popen):
    """Read JSON-RPC responses from daemon stdout and wake waiting callers."""
    global _pending
    try:
        for raw_line in proc.stdout:
            try:
                resp = json.loads(raw_line.decode("utf-8", errors="replace"))
            except Exception:
                continue
            rid = resp.get("id")
            with _daemon_lock:
                entry = _pending.pop(rid, None)
            if entry:
                ev, holder = entry
                holder["html"] = resp.get("html", "")
                holder["error"] = resp.get("error")
                ev.set()
    except Exception:
        pass


def _kill_daemon():
    global _daemon_proc
    with _daemon_lock:
        if _daemon_proc is not None:
            try:
                _daemon_proc.terminate()
                _daemon_proc.wait(timeout=1.0)
            except Exception:
                try:
                    _daemon_proc.kill()
                except Exception:
                    pass
            _daemon_proc = None


atexit.register(_kill_daemon)


def _ensure_daemon() -> Optional[subprocess.Popen]:
    global _daemon_proc, _daemon_reader_thread
    with _daemon_lock:
        if _daemon_proc is None or _daemon_proc.poll() is not None:
            _daemon_proc = _start_daemon()
            if _daemon_proc:
                t = threading.Thread(
                    target=_reader_loop,
                    args=(_daemon_proc,),
                    daemon=True,
                    name="mathjax-reader",
                )
                t.start()
                _daemon_reader_thread = t
        return _daemon_proc


def render_latex_sync(latex: str, color: str = "#E2E8F0", font_size: int = 18, padding_top: int = 16, timeout: float = 5.0) -> str:
    """
    Send latex to the MathJax daemon, wait up to `timeout` seconds,
    return rendered HTML string. Returns "" on failure.
    Thread-safe write to daemon stdin.
    """
    global _daemon_id, _daemon_proc
    proc = _ensure_daemon()
    if not proc:
        return ""

    with _daemon_lock:
        _daemon_id += 1
        rid = _daemon_id
        ev = threading.Event()
        holder: dict = {"html": "", "error": None}
        _pending[rid] = (ev, holder)

    payload = json.dumps({
        "id": rid,
        "latex": latex,
        "color": color,
        "fontSize": font_size,
        "paddingTop": padding_top
    }) + "\n"

    try:
        with _daemon_write_lock:
            proc.stdin.write(payload.encode("utf-8"))
            proc.stdin.flush()
    except Exception:
        with _daemon_lock:
            _pending.pop(rid, None)
            if _daemon_proc is not None:
                try:
                    _daemon_proc.kill()
                except Exception:
                    pass
                _daemon_proc = None
        return ""

    ev.wait(timeout)
    return holder["html"] or ""


# ── Fallback: syntax-highlighted raw LaTeX ────────────────────────────────────
import re as _re


def _latex_to_html_fallback(latex: str, theme: dict, base_font_px: int = 13) -> str:
    """Syntax-highlighted raw LaTeX — used only when daemon is unavailable."""
    if not latex or not latex.strip():
        return (
            f"<html><body style='background:{theme.get('bg','#0d1117')};"
            f"color:{theme.get('text_dim','#8b949e')};font-family:monospace;"
            f"font-size:{base_font_px}px;padding:12px;'>"
            "<i>No LaTeX content yet\u2026</i></body></html>"
        )

    bg   = theme.get("bg", "#0d1117")
    text = theme.get("text", "#e6edf3")
    acc  = theme.get("accent", "#58a6ff")
    acc2 = theme.get("accent2", "#1f6feb")
    dim  = theme.get("text_dim", "#8b949e")
    surf = theme.get("surface", "#161b22")
    mp   = max(6, base_font_px - 4)

    css = f"""
<style>
  body {{background:{bg};color:{text};font-family:'JetBrains Mono','Fira Code',monospace;
         font-size:{base_font_px}px;line-height:1.65;padding:{mp}px {mp+4}px;margin:0;}}
  .mb  {{display:block;background:{surf};border-left:3px solid {acc};
          padding:{mp}px {mp+4}px;margin:{mp}px 0;border-radius:0 6px 6px 0;
          font-size:{base_font_px}px;white-space:pre-wrap;color:{acc};word-break:break-all;}}
  .mi  {{background:{acc}18;border-radius:3px;padding:1px 3px;color:{acc};font-size:{base_font_px}px;}}
  .cmd {{color:#ff79c6;}} .brc {{color:{acc2};font-weight:bold;}}
  .num {{color:#a6e3a1;}} .cmt {{color:{dim};font-style:italic;}}
  .txt {{color:{text};}} p {{margin:4px 0;}}
</style>"""

    def _esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _colorize(s):
        s = _esc(s)
        s = _re.sub(r'(%[^\n]*)', r'<span class="cmt">\1</span>', s)
        s = _re.sub(r'(\\[a-zA-Z@]+\*?)', r'<span class="cmd">\1</span>', s)
        s = _re.sub(r'([{}])', r'<span class="brc">\1</span>', s)
        s = _re.sub(r'\b(\d+(?:\.\d+)?)\b', r'<span class="num">\1</span>', s)
        return s

    pattern = _re.compile(
        r'(\\\[.*?\\\]|\$\$.*?\$\$|\\\(.*?\\\)|\$[^$\n]+?\$)',
        _re.DOTALL
    )
    parts = []
    pos = 0
    for m in pattern.finditer(latex):
        if m.start() > pos:
            parts.append(f'<span class="txt">{_colorize(latex[pos:m.start()]).replace(chr(10),"<br>")}</span>')
        is_display = m.group(0).startswith(r'\[') or m.group(0).startswith('$$')
        c = _colorize(m.group(0))
        parts.append(f'<div class="mb">{c}</div>' if is_display else f'<span class="mi">{c}</span>')
        pos = m.end()
    if pos < len(latex):
        parts.append(f'<span class="txt">{_colorize(latex[pos:]).replace(chr(10),"<br>")}</span>')

    return f"<html><head>{css}</head><body>{''.join(parts)}</body></html>"


# ── Background render worker ───────────────────────────────────────────────────
class _RenderWorker(QObject):
    done = pyqtSignal(str)   # emits rendered HTML

    def __init__(self, latex: str, color: str, font_size: int = 18, padding_top: int = 16):
        super().__init__()
        self._latex = latex
        self._color = color
        self._font_size = font_size
        self._padding_top = padding_top

    def run(self):
        html = render_latex_sync(self._latex, self._color, self._font_size, self._padding_top)
        self.done.emit(html)


# ── Widget ─────────────────────────────────────────────────────────────────────
class MathPreviewWidget(QWidget):
    """
    Live LaTeX preview with real MathJax rendering.
    - Dynamic responsive font size: scales equation size and text to fill available space.
    - Interactive zoom: Ctrl + Mouse Wheel zooms equation size.
    - Rendering runs in a background thread → UI stays responsive.
    - Falls back to syntax-highlighted raw LaTeX if Node/daemon unavailable.
    """

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._current_latex = ""
        self._zoom = 1.0
        self._needs_rerender = False
        self._worker: Optional[_RenderWorker] = None
        self._worker_thread: Optional[QThread] = None

        self._setup_ui()

        # Debounce resize
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._refresh)

        # Pre-warm the daemon on first instantiation
        QTimer.singleShot(0, _ensure_daemon)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._browser = QTextBrowser()
        self._browser.setOpenLinks(False)
        self._browser.setReadOnly(True)
        self._browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._apply_browser_style()
        layout.addWidget(self._browser)

    def _apply_browser_style(self):
        t = self._theme
        self._browser.setStyleSheet(f"""
            QTextBrowser {{
                background: {t.get('bg','#0d1117')};
                color: {t.get('text','#e6edf3')};
                border: none;
                font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
            }}
            QScrollBar:vertical {{
                background: {t.get('surface','#161b22')};
                width: 7px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {t.get('border','#30363d')};
                border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def _text_color(self) -> str:
        return self._theme.get("text", "#E2E8F0")

    def _font_size(self) -> int:
        h = max(80, self.height())
        w = max(100, self.width())
        raw = self._current_latex.strip()
        length = len(raw)
        lines = max(1, raw.count("\n") + 1)

        # Dynamic space utilization:
        # Scale the formula and text dynamically based on the space available
        if length <= 220 and lines <= 4:
            # Concise problem statement / single formula (like user screenshot)
            if h >= 500:
                base = 28
            elif h >= 360:
                base = 25
            elif h >= 240:
                base = 21
            elif h >= 160:
                base = 18
            else:
                base = 15
        elif length <= 500 and lines <= 7:
            if h >= 450:
                base = 22
            elif h >= 300:
                base = 19
            elif h >= 200:
                base = 16
            else:
                base = 14
        elif length <= 1000:
            base = 17 if h >= 320 else 14
        else:
            base = 14 if h >= 260 else 12

        # Width capacity check for single long formulas
        if lines <= 2 and length > 40:
            est_width_capacity = int((w - 40) / (length * 0.32))
            if 12 <= est_width_capacity < base:
                base = est_width_capacity

        # Apply zoom multiplier
        scaled = int(round(base * self._zoom))
        return max(11, min(44, scaled))

    def _padding_top(self, fs: int) -> int:
        h = max(80, self.height())
        raw = self._current_latex.strip()
        lines = max(1, raw.count("\n") + 1)
        # Vertically center short equations in large views
        if lines <= 4 and len(raw) <= 250 and h > 200:
            est_content_h = lines * fs * 2.6
            if h > est_content_h:
                return max(16, min(140, int((h - est_content_h) / 3)))
        return 16

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = ev.angleDelta().y()
            if delta > 0:
                self._zoom = min(2.5, self._zoom + 0.15)
            elif delta < 0:
                self._zoom = max(0.6, self._zoom - 0.15)
            self._refresh()
            ev.accept()
        else:
            super().wheelEvent(ev)

    def _refresh(self):
        fs = self._font_size()
        if not self._current_latex:
            self._browser.setHtml(_latex_to_html_fallback("", self._theme, fs))
            return

        # If a render is already in-flight, just flag that we need another pass.
        if self._worker_thread and self._worker_thread.isRunning():
            self._needs_rerender = True
            return

        self._needs_rerender = False
        pt = self._padding_top(fs)
        # Parent=self so Python/Qt don't GC the thread while it's running
        worker = _RenderWorker(self._current_latex, self._text_color(), fs, pt)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_render_done)
        worker.done.connect(thread.quit)
        thread.finished.connect(lambda t=thread: self._thread_finished(t))
        thread.start()
        self._worker = worker
        self._worker_thread = thread

    def _thread_finished(self, thread: QThread):
        if self._worker_thread is thread:
            self._worker_thread = None
            self._worker = None
        thread.deleteLater()
        # Re-render if content changed while we were rendering
        if getattr(self, "_needs_rerender", False):
            self._needs_rerender = False
            self._refresh()

    def _on_render_done(self, html: str):
        if html:
            self._browser.setHtml(html)
        else:
            self._browser.setHtml(
                _latex_to_html_fallback(self._current_latex, self._theme, self._font_size())
            )

    # ── Public API ─────────────────────────────────────────────────────────────
    def set_latex(self, latex: str):
        self._current_latex = latex
        self._refresh()

    def get_latex(self) -> str:
        return self._current_latex

    def apply_theme(self, theme: dict):
        self._theme = theme
        self._apply_browser_style()
        self._refresh()

    def render_to_png(self) -> Optional[bytes]:
        pixmap = self._browser.grab()
        from PyQt6.QtCore import QBuffer, QIODeviceBase
        buf = QBuffer()
        buf.open(QIODeviceBase.OpenModeFlag.WriteOnly)
        pixmap.save(buf, "PNG")
        return bytes(buf.data())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._resize_timer.start(120)

    def cleanup(self):
        """Ensure background render thread is cleanly stopped and waited for."""
        if self._worker_thread:
            try:
                if self._worker_thread.isRunning():
                    self._worker_thread.quit()
                    self._worker_thread.wait(1500)
            except Exception:
                pass
            self._worker_thread = None

    def closeEvent(self, ev):
        self.cleanup()
        super().closeEvent(ev)
