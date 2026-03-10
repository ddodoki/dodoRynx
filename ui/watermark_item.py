# -*- coding: utf-8 -*-
# ui/watermark_item.py

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject


def _line_str(l: object) -> str:
    """(label, value) 튜플 또는 str을 표시용 문자열로 변환."""
    if isinstance(l, tuple):
        lbl = str(l[0]) if l[0] else ""
        val = str(l[1]) if len(l) > 1 else ""
        return f"{lbl}: {val}" if lbl else val
    return str(l)


class WatermarkItem(QGraphicsObject):
    """밴드 + 텍스트 블록을 단일 씬 아이템으로 렌더링."""

    about_to_change = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._lines:        List[str]           = []  
        self._font_family:  str                 = "맑은 고딕"
        self._font_size:    int                 = 28
        self._bold:         bool                = False
        self._italic:       bool                = False
        self._text_color:   QColor              = QColor(255, 255, 255)
        self._alignment:    Qt.AlignmentFlag    = Qt.AlignmentFlag.AlignLeft
        self._line_spacing: float               = 1.5
        self._band_enabled: bool                = False
        self._band_mode:    str                 = 'inside'
        self._band_color:   QColor              = QColor(0, 0, 0)
        self._band_alpha:   int                 = 153
        self._band_width:   float               = 800.0
        self._band_padding: int                 = 18
        self._dirty:        bool                = True
        self._cache_w:      float               = 0.0
        self._cache_h:      float               = 0.0
        self._notified:     bool                = False

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable    |
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(100)

    # ── 공개 API ────────────────────────────────────────────────────

    def update_config(self, cfg: dict) -> None:
        self.prepareGeometryChange()
        self._lines        = [l for l in cfg.get('lines', []) if l]
        self._font_family  = cfg.get('font_family',  self._font_family)
        self._font_size    = cfg.get('font_size',     self._font_size)
        self._bold         = cfg.get('bold',          self._bold)
        self._italic       = cfg.get('italic',        self._italic)
        tc = cfg.get('text_color')
        if tc is not None: self._text_color = QColor(tc)
        self._alignment    = cfg.get('alignment',    self._alignment)
        self._line_spacing = cfg.get('line_spacing', self._line_spacing)
        self._band_enabled = cfg.get('band_enabled', self._band_enabled)
        self._band_mode    = cfg.get('band_mode',    self._band_mode)
        bc = cfg.get('band_color')
        if bc is not None: self._band_color = QColor(bc)
        self._band_alpha   = cfg.get('band_alpha',   self._band_alpha)
        self._band_width   = cfg.get('band_width',   self._band_width)
        self._band_padding = cfg.get('band_padding', self._band_padding)
        self._dirty = True
        self.update()


    def content_size(self) -> Tuple[float, float]:
        self._ensure_metrics()
        pad = self._band_padding
        if self._band_enabled:
            return self._band_width, self._cache_h + pad * 2
        return self._cache_w + pad * 2, self._cache_h + pad * 2

    # ── QGraphicsItem ──────────────────────────────────────────────

    def boundingRect(self) -> QRectF:
        w, h = self.content_size()
        return QRectF(0, 0, max(w, 10), max(h, 10))


    def paint(self, painter: QPainter, option, widget=None) -> None:
        if not self._lines:
            return
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            self._ensure_metrics()
            w, h   = self.content_size()
            pad    = self._band_padding
            font   = self._make_font()
            fm     = QFontMetricsF(font)
            line_h = fm.height() * self._line_spacing

            # 밴드
            if self._band_enabled:
                bc = QColor(self._band_color)
                bc.setAlpha(self._band_alpha)
                painter.fillRect(QRectF(0, 0, w, h), QBrush(bc))

            # 텍스트
            painter.setFont(font)
            painter.setPen(QPen(self._text_color))
            for i, txt in enumerate(self._lines):
                y  = pad + i * line_h + fm.ascent()
                lw = fm.horizontalAdvance(txt)
                if self._alignment == Qt.AlignmentFlag.AlignHCenter:
                    x = (w - lw) / 2.0
                elif self._alignment == Qt.AlignmentFlag.AlignRight:
                    x = w - lw - pad
                else:
                    x = float(pad)
                painter.drawText(QPointF(x, y), txt)

            # 선택 테두리
            if self.isSelected():
                sc  = self._view_scale()
                pen = QPen(QColor(74, 158, 255, 200), 1.5 / sc)
                pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRectF(0, 0, w, h))
        finally:
            painter.restore()


    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._notified = False        
            if not bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                s = self.scene()
                if s: s.clearSelection()
        super().mousePressEvent(event)


    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._notified = False  
        super().mouseReleaseEvent(event)


    def itemChange(self, change, value):
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
                and not self._notified):
            self._notified = True
            self.about_to_change.emit()
        return super().itemChange(change, value)

    # ── 내부 유틸 ───────────────────────────────────────────────────

    def _make_font(self) -> QFont:
        f = QFont(self._font_family)
        f.setPixelSize(max(8, self._font_size))
        f.setBold(self._bold); f.setItalic(self._italic)
        return f


    def _ensure_metrics(self) -> None:
        if not self._dirty:
            return
        fm = QFontMetricsF(self._make_font())
        if self._lines:
            self._cache_w = max(
                (fm.horizontalAdvance(str(l)) for l in self._lines),
                default=0.0,
            )
            self._cache_h = fm.height() * self._line_spacing * len(self._lines)
        else:
            self._cache_w = 100.0
            self._cache_h = fm.height() * self._line_spacing
        self._dirty = False


    def _view_scale(self) -> float:
        s = self.scene()
        if s:
            v = s.views()
            if v: return v[0].transform().m11()
        return 1.0
