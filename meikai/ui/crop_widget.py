"""Interactive image crop widget with 4-corner perspective handles and loupe."""
import io
import math
import os
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF, pyqtSignal, QSize
from PyQt6.QtGui import (
    QColor, QCursor, QImage, QPainter, QPainterPath, QPen,
    QPixmap, QBrush, QFont, QFontMetrics,
)
from PyQt6.QtWidgets import QWidget, QSizePolicy


HANDLE_RADIUS = 10
LOUPE_RADIUS = 60
LOUPE_ZOOM = 4.0


def pil_to_qimage(img: Image.Image) -> QImage:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    return QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888)


class CropWidget(QWidget):
    """
    4-corner interactive perspective crop canvas.
    Emits corners_changed(list of 4 normalized [0..1] (x,y) tuples) on change.
    """
    corners_changed = pyqtSignal(list)
    image_changed = pyqtSignal(str)    # emitted when image source path changes
    image_dropped = pyqtSignal(bytes)  # emitted when image is dropped onto the canvas

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._img_path: Optional[str] = None
        self._pixmap: Optional[QPixmap] = None
        self._img_w = 1
        self._img_h = 1

        # Normalized corners: TL, TR, BR, BL — full image by default
        self._corners: List[List[float]] = [
            [0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]
        ]
        self._drag_idx: Optional[int] = None
        self._hover_idx: Optional[int] = None
        self._show_loupe = False
        self._loupe_pos = QPointF(0, 0)

        self.setMinimumSize(300, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        # Undo stack for corners
        self._undo_stack: List[List[List[float]]] = []
        self._redo_stack: List[List[List[float]]] = []

    # ── Public API ─────────────────────────────────────────────────────────────
    def set_image_path(self, path: str, reset_corners: bool = True):
        self._img_path = path
        from PIL import Image as PILImage
        try:
            if path and os.path.isfile(path):
                pil = PILImage.open(path).convert("RGB")
                self._img_w, self._img_h = pil.size
                qimg = pil_to_qimage(pil)
                self._pixmap = QPixmap.fromImage(qimg)
            else:
                self._pixmap = None
        except Exception:
            self._pixmap = None

        if reset_corners:
            self._reset_corners()
        self.update()

    def set_corners(self, corners: List[List[float]]):
        if len(corners) == 4:
            self._corners = [list(c) for c in corners]
            self.update()

    def get_corners(self) -> List[List[float]]:
        return [list(c) for c in self._corners]

    def reset_corners(self):
        self._push_undo()
        self._reset_corners()

    def apply_theme(self, theme: dict):
        self._theme = theme
        self.update()

    def undo(self):
        if self._undo_stack:
            self._redo_stack.append([list(c) for c in self._corners])
            self._corners = self._undo_stack.pop()
            self.corners_changed.emit(self.get_corners())
            self.update()

    def redo(self):
        if self._redo_stack:
            self._undo_stack.append([list(c) for c in self._corners])
            self._corners = self._redo_stack.pop()
            self.corners_changed.emit(self.get_corners())
            self.update()

    # ── Internal ───────────────────────────────────────────────────────────────
    def _reset_corners(self):
        # Full image selection — user decides what to crop
        self._corners = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
        self.corners_changed.emit(self.get_corners())

    def _push_undo(self):
        self._undo_stack.append([list(c) for c in self._corners])
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _canvas_rect(self) -> QRectF:
        """Image display rectangle within widget (letterboxed)."""
        if not self._pixmap:
            return QRectF(self.rect())
        ww, wh = self.width(), self.height()
        iw, ih = self._pixmap.width(), self._pixmap.height()
        scale = min(ww / iw, wh / ih)
        dw, dh = iw * scale, ih * scale
        ox, oy = (ww - dw) / 2, (wh - dh) / 2
        return QRectF(ox, oy, dw, dh)

    def _corner_pos(self, idx: int) -> QPointF:
        cr = self._canvas_rect()
        nx, ny = self._corners[idx]
        return QPointF(cr.x() + nx * cr.width(), cr.y() + ny * cr.height())

    def _pos_to_norm(self, p: QPointF) -> Tuple[float, float]:
        cr = self._canvas_rect()
        nx = max(0.0, min(1.0, (p.x() - cr.x()) / cr.width()))
        ny = max(0.0, min(1.0, (p.y() - cr.y()) / cr.height()))
        return nx, ny

    def _hit_corner(self, p: QPointF) -> Optional[int]:
        for i in range(4):
            cp = self._corner_pos(i)
            dist = math.hypot(p.x() - cp.x(), p.y() - cp.y())
            if dist <= HANDLE_RADIUS + 4:
                return i
        return None

    # ── Mouse events ───────────────────────────────────────────────────────────
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            idx = self._hit_corner(ev.position())
            if idx is not None:
                self._push_undo()
                self._drag_idx = idx
                self._show_loupe = True

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        self._loupe_pos = pos
        if self._drag_idx is not None:
            nx, ny = self._pos_to_norm(pos)
            self._corners[self._drag_idx] = [nx, ny]
            self.corners_changed.emit(self.get_corners())
            self.update()
        else:
            idx = self._hit_corner(pos)
            if idx != self._hover_idx:
                self._hover_idx = idx
                self.setCursor(
                    Qt.CursorShape.SizeAllCursor if idx is not None
                    else Qt.CursorShape.CrossCursor
                )
                self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_idx = None
            self._show_loupe = False
            self.update()

    def leaveEvent(self, ev):
        self._hover_idx = None
        self._show_loupe = False
        self.update()

    # ── Drag & Drop ────────────────────────────────────────────────────────────
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
        from ..image_proc import extract_image_bytes
        data = extract_image_bytes(ev.mimeData())
        if data:
            self.image_dropped.emit(data)
            ev.acceptProposedAction()
        else:
            ev.ignore()

    # ── Paint ──────────────────────────────────────────────────────────────────
    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        t = self._theme
        # Background
        painter.fillRect(self.rect(), QColor(t.get("bg", "#0d1117")))

        cr = self._canvas_rect()

        if self._pixmap:
            painter.drawPixmap(cr.toRect(), self._pixmap)
        else:
            # Placeholder
            painter.setPen(QColor(t.get("text_dim", "#8b949e")))
            painter.drawText(cr.toRect(), Qt.AlignmentFlag.AlignCenter,
                             "Drop or paste an image here")
            painter.setPen(QPen(QColor(t.get("border", "#30363d")), 1, Qt.PenStyle.DashLine))
            painter.drawRect(cr.toRect())

        # Draw crop overlay — darken outside polygon
        if self._pixmap:
            corners_px = [self._corner_pos(i) for i in range(4)]
            path_full = QPainterPath()
            path_full.addRect(cr)

            path_crop = QPainterPath()
            path_crop.moveTo(corners_px[0])
            for i in range(1, 4):
                path_crop.lineTo(corners_px[i])
            path_crop.closeSubpath()

            outside = path_full.subtracted(path_crop)
            painter.fillPath(outside, QColor(0, 0, 0, 100))

            # Draw border
            pen = QPen(QColor(t.get("accent", "#58a6ff")), 1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path_crop)

            # Draw handles
            labels = ["TL", "TR", "BR", "BL"]
            for i, cp in enumerate(corners_px):
                is_hover = (i == self._hover_idx)
                is_drag  = (i == self._drag_idx)
                fill_col = QColor(t.get("accent2", "#1f6feb") if is_drag else
                                  t.get("accent", "#58a6ff") if is_hover else
                                  "#ffffff")
                border_col = QColor(t.get("accent", "#58a6ff"))
                painter.setPen(QPen(border_col, 2))
                painter.setBrush(fill_col)
                r = HANDLE_RADIUS + (2 if is_hover else 0)
                painter.drawEllipse(cp, r, r)

                # Corner label
                painter.setPen(QColor(t.get("bg", "#0d1117")))
                painter.setFont(QFont("sans-serif", 7, QFont.Weight.Bold))
                painter.drawText(
                    QRectF(cp.x() - r, cp.y() - r, 2 * r, 2 * r),
                    Qt.AlignmentFlag.AlignCenter, labels[i],
                )

        # Draw loupe
        if self._show_loupe and self._pixmap and self._drag_idx is not None:
            self._draw_loupe(painter, cr)

        painter.end()

    def _draw_loupe(self, painter: QPainter, canvas_rect: QRectF):
        t = self._theme
        cp = self._corner_pos(self._drag_idx)

        # Loupe position: offset to avoid obscuring handle
        lx = cp.x() + LOUPE_RADIUS + 15
        ly = cp.y() - LOUPE_RADIUS - 15
        # Clamp to widget
        lx = min(lx, self.width() - LOUPE_RADIUS - 5)
        ly = max(ly, LOUPE_RADIUS + 5)

        center = QPointF(lx, ly)
        # Sample from pixmap
        nx, ny = self._corners[self._drag_idx]
        px_x = nx * self._pixmap.width()
        px_y = ny * self._pixmap.height()
        zoom_half = LOUPE_RADIUS / LOUPE_ZOOM
        src_rect = QRectF(px_x - zoom_half, px_y - zoom_half, zoom_half * 2, zoom_half * 2)
        dst_rect = QRectF(lx - LOUPE_RADIUS, ly - LOUPE_RADIUS, LOUPE_RADIUS * 2, LOUPE_RADIUS * 2)

        # Clip to circle
        path = QPainterPath()
        path.addEllipse(center, LOUPE_RADIUS, LOUPE_RADIUS)
        painter.save()
        painter.setClipPath(path)
        painter.drawPixmap(dst_rect.toRect(), self._pixmap, src_rect.toRect())
        painter.restore()

        # Crosshair
        painter.setPen(QPen(QColor(255, 100, 100, 200), 1))
        painter.drawLine(QPointF(lx - 8, ly), QPointF(lx + 8, ly))
        painter.drawLine(QPointF(lx, ly - 8), QPointF(lx, ly + 8))

        # Border
        painter.setPen(QPen(QColor(t.get("accent", "#58a6ff")), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, LOUPE_RADIUS, LOUPE_RADIUS)
