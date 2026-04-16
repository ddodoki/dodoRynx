# -*- coding: utf-8 -*-
# ui\editor\ai_mask_item.py

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QGraphicsObject


class AIMaskItem(QGraphicsObject):
    """AI 지우개 브러시 마스크 오버레이 아이템."""

    def __init__(self, w: int, h: int, parent=None) -> None:
        super().__init__(parent)
        self._w = w
        self._h = h
        self.brush_size: int = 30
        self._mask = np.zeros((h, w), dtype=np.uint8)
        self._last_x: float | None = None
        self._last_y: float | None = None
        self.setZValue(100)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsSelectable, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        self._overlay_cache: bytes | None = None 
        self._mask_dirty: bool = False 
        self._painted_count: int = 0
        self._dirty_rect: QRectF | None = None
        self._stroke_history: list = []       
        self._MAX_HISTORY: int = 20            
        
    def boundingRect(self) -> QRectF:
        return QRectF(0.0, 0.0, float(self._w), float(self._h))


    def paint(self, painter, option, widget=None):
        if self._painted_count == 0:
            self._overlay_cache = None
            return
        if self._mask_dirty or self._overlay_cache is None:
            overlay = np.zeros((self._h, self._w, 4), dtype=np.uint8)
            overlay[self._mask > 0] = [255, 60, 60, 140]
            self._overlay_cache = overlay.tobytes()
            self._mask_dirty = False
        qimg = QImage(self._overlay_cache, self._w, self._h,
                    QImage.Format.Format_RGBA8888)
        painter.drawImage(0, 0, qimg)

    # ── 브러시 드로잉 API ─────────────────────────────────────────

    def paint_at(self, x, y):
        self._dirty_rect = None
        ix, iy = int(x), int(y)
        r = max(1, self.brush_size // 2)

        if self._last_x is not None and self._last_y is not None:
            lx, ly = int(self._last_x), int(self._last_y)
            raw_steps = max(abs(ix - lx), abs(iy - ly), 1)
            steps = max(1, raw_steps // max(1, r))
            for i in range(steps + 1):
                t = i / steps
                self._draw_circle(int(lx + (ix - lx) * t),
                                int(ly + (iy - ly) * t), r)
        else:
            self._draw_circle(ix, iy, r)

        self._last_x = x
        self._last_y = y

        if self._dirty_rect is not None:
            self.update(self._dirty_rect)
        self._dirty_rect = None


    def reset_stroke(self) -> None:
        """마우스 릴리즈 시 호출 — 다음 stroke 보간 기준점 리셋."""
        self._last_x = None
        self._last_y = None


    def _draw_circle(self, cx: int, cy: int, r: int) -> None:
        y1 = max(0, cy - r);  y2 = min(self._h, cy + r + 1)
        x1 = max(0, cx - r);  x2 = min(self._w, cx + r + 1)
        if y2 <= y1 or x2 <= x1:
            return
        yy, xx = np.ogrid[y1:y2, x1:x2]
        before_count = int(np.sum(self._mask[y1:y2, x1:x2] > 0))
        self._mask[y1:y2, x1:x2][((xx - cx)**2 + (yy - cy)**2) <= r*r] = 255
        after_count = int(np.sum(self._mask[y1:y2, x1:x2] > 0))
        self._painted_count += after_count - before_count
        self._mask_dirty = True

        self._dirty_rect = self._dirty_rect.united(
            QRectF(float(x1), float(y1), float(x2 - x1), float(y2 - y1))
        ) if self._dirty_rect else QRectF(
            float(x1), float(y1), float(x2 - x1), float(y2 - y1)
        )

    # ── 상태 조회 / 초기화 ────────────────────────────────────────

    def is_empty(self) -> bool:
        return self._painted_count == 0


    def clear(self) -> None:
        self._mask[:] = 0
        self._painted_count = 0
        self._last_x = None
        self._last_y = None
        self._mask_dirty = True
        self._overlay_cache = None
        self._stroke_history.clear()
        self.update()


    def get_mask_pixmap(self) -> QPixmap:
        rgba = np.zeros((self._h, self._w, 4), dtype=np.uint8)
        rgba[:, :, :3] = self._mask[:, :, np.newaxis]
        rgba[:, :, 3] = 255
        _buf = rgba.tobytes() 
        qimg = QImage(_buf, self._w, self._h, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimg)

    # ── 실행 취소 ────────────────────────────────────────

    def begin_stroke(self) -> None:
        """스트로크 시작 직전 마스크 상태를 스택에 저장."""
        self._stroke_history.append(self._mask.copy())
        if len(self._stroke_history) > self._MAX_HISTORY:
            self._stroke_history.pop(0)

    def undo_stroke(self) -> bool:
        """마지막 스트로크 하나를 되돌린다. 히스토리가 있으면 True."""
        if not self._stroke_history:
            return False
        self._mask       = self._stroke_history.pop()
        self.painted_count = int(np.sum(self._mask > 0))
        self._mask_dirty    = True
        self.overlay_cache = None
        self.last_x = None
        self.last_y = None
        self.update()
        return True

    def has_stroke_history(self) -> bool:
        return bool(self._stroke_history)