# core/qt_pil.py
from __future__ import annotations

import ctypes

from PIL import Image
from PySide6.QtGui import QImage, QPixmap


def qpixmap_to_pil(px: QPixmap) -> Image.Image:
    if px is None or px.isNull():
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    qimg = px.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = qimg.width(), qimg.height()
    bpl  = qimg.bytesPerLine()

    ptr = qimg.bits()

    if isinstance(ptr, (bytes, bytearray, memoryview)):
        raw = bytes(ptr)
    else:
        raw = bytes(ctypes.string_at(int(ptr), bpl * h))  # type: ignore[arg-type]

    im = Image.frombuffer("RGBA", (w, h), raw, "raw", "RGBA", bpl, 1)
    return im.copy()


def pil_to_qpixmap(im: Image.Image) -> QPixmap:
    if im is None:
        return QPixmap()

    rgba = im.convert("RGBA")
    w, h = rgba.size
    data = rgba.tobytes("raw", "RGBA")

    qimg = QImage(data, w, h, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimg)
