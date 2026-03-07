# -*- coding: utf-8 -*-
# core/image_editor.py

from __future__ import annotations

from typing import Tuple

import numpy as np
from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPixmap


class ImageEditor:
    """비파괴 이미지 편집 엔진"""

    def __init__(self, pixmap: QPixmap) -> None:
        self._original: Image.Image = self._qpixmap_to_pil(pixmap)
        self._working:  Image.Image = self._original.copy()

    # ── 공개 API ──────────────────────────────────────────────────────

    def reset(self) -> QPixmap:
        self._working = self._original.copy()
        return self._to_qpixmap(self._working)


    def get_preview(self) -> QPixmap:
        """현재 working 상태 반환."""
        return self._to_qpixmap(self._working)


    def get_size(self) -> Tuple[int, int]:
        return self._working.size


    def resize(self, width: int, height: int) -> QPixmap:
        width  = max(1, width)
        height = max(1, height)
        self._working = self._working.resize(
            (width, height), Image.Resampling.LANCZOS
        )
        return self._to_qpixmap(self._working)


    def crop(self, rect: QRectF) -> QPixmap:
        x, y = int(rect.x()),     int(rect.y())
        w, h = int(rect.width()), int(rect.height())
        iw, ih = self._working.size
        x2, y2 = min(x + w, iw), min(y + h, ih)
        x,  y  = max(0, x),      max(0, y)
        self._working = self._working.crop((x, y, x2, y2))
        return self._to_qpixmap(self._working)


    def copy_region_to_clipboard(self, rect: QRectF) -> None:
        from PySide6.QtWidgets import QApplication
        px = self._region_to_qpixmap(rect)
        if not px.isNull():
            QApplication.clipboard().setPixmap(px)


    def get_region_pixmap(self, rect: QRectF) -> QPixmap:
        return self._region_to_qpixmap(rect)


    def get_working(self) -> Image.Image:
        """현재 작업 PIL Image 반환 (필터·모자이크 미리보기용)."""
        return self._working


    def set_working(self, img: Image.Image) -> None:
        """작업 PIL Image 교체 (BG 제거·AI 지우개 결과 반영용)."""
        self._working = img


    def commit(self) -> None:
        """_working을 새 _original로 확정."""
        self._original = self._working.copy()

    # ── 내부 ──────────────────────────────────────────────────────────

    def _region_to_qpixmap(self, rect: QRectF) -> QPixmap:
        x, y   = int(rect.x()),     int(rect.y())
        w, h   = int(rect.width()), int(rect.height())
        iw, ih = self._working.size
        x2, y2 = min(x + w, iw), min(y + h, ih)
        if x2 <= x or y2 <= y:
            return QPixmap()
        return self._to_qpixmap(self._working.crop((x, y, x2, y2)))


    @staticmethod
    def _qpixmap_to_pil(pixmap: QPixmap) -> Image.Image:
        qimg = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = qimg.width(), qimg.height()
        arr  = np.frombuffer(qimg.bits(), dtype=np.uint8).reshape((h, w, 4)).copy()
        return Image.fromarray(arr, 'RGBA')


    @staticmethod
    def _to_qpixmap(img: Image.Image) -> QPixmap:
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        arr  = np.ascontiguousarray(np.array(img))
        h, w = arr.shape[:2]
        qimg = QImage(arr.tobytes(), w, h, 4 * w, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(qimg)
