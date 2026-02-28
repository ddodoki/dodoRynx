# -*- coding: utf-8 -*-
# ui/text_item.py

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject


class TextShapeItem(QGraphicsObject):
    """이동 + Ctrl+Shift 회전 + 더블클릭 편집 텍스트 아이템"""

    about_to_change   = Signal()
    properties_needed = Signal(object)   # 편집 다이얼로그 요청

    HANDLE_SCREEN_PX = 9

    def __init__(
        self,
        text:        str    = "텍스트",
        font_family: str    = "맑은 고딕",
        font_size:   int    = 40,           # scene 단위 px
        color:       QColor = QColor(255, 50, 50),
        bold:        bool   = False,
        italic:      bool   = False,
    ) -> None:
        super().__init__()
        self._text        = text
        self._font_family = font_family
        self._font_size   = font_size
        self._color       = QColor(color)
        self._bold        = bold
        self._italic      = italic
        self._font        = self._make_font()
        self._rect        = self._compute_rect()

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable    |
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(60)
        self._sync_origin()

        self._rotation_mode:     bool              = False
        self._rot_center_scene:  Optional[QPointF] = None
        self._rot_start_angle:   float             = 0.0
        self._rot_initial:       float             = 0.0
        self._notified_this_press                  = False

    # ── API ──────────────────────────────────────────────

    def update_properties(
        self,
        text:        Optional[str]    = None,
        font_family: Optional[str]    = None,
        font_size:   Optional[int]    = None,
        color:       Optional[QColor] = None,
        bold:        Optional[bool]   = None,
        italic:      Optional[bool]   = None,
    ) -> None:
        self.prepareGeometryChange()
        if text        is not None: self._text        = text
        if font_family is not None: self._font_family = font_family
        if font_size   is not None: self._font_size   = max(8, font_size)
        if color       is not None: self._color       = QColor(color)
        if bold        is not None: self._bold        = bold
        if italic      is not None: self._italic      = italic
        self._font = self._make_font()
        self._rect = self._compute_rect()
        self._sync_origin()
        self.update()

    # ── QGraphicsItem ────────────────────────────────────

    def boundingRect(self) -> QRectF:
        m = self._s2i(self.HANDLE_SCREEN_PX / 2.0 + 2)
        return self._rect.adjusted(-m, -m, m, m)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        r = self._rect

        if self.isSelected():
            inv = self._s2i(1.0)
            painter.setPen(QPen(QColor(74, 158, 255), inv, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r)

        # 텍스트
        painter.setFont(self._font)
        painter.setPen(QPen(self._color))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text)

        if not self.isSelected():
            return

        # 핸들
        hs  = self._s2i(self.HANDLE_SCREEN_PX / 2.0)
        lw  = self._s2i(1.2)
        cx, cy = r.center().x(), r.center().y()
        painter.setPen(QPen(QColor(255, 255, 255), lw))
        painter.setBrush(QBrush(QColor(74, 158, 255)))
        for hx, hy in [
            (r.left(), r.top()), (cx, r.top()), (r.right(), r.top()),
            (r.right(), cy),
            (r.right(), r.bottom()), (cx, r.bottom()), (r.left(), r.bottom()),
            (r.left(), cy),
        ]:
            painter.drawRect(QRectF(hx - hs, hy - hs, hs * 2, hs * 2))

        # 회전 힌트 (Ctrl+Shift)
        ty = r.top() - self._s2i(18)
        cr = self._s2i(5)
        painter.setPen(QPen(QColor(255, 200, 50), self._s2i(1.5)))
        painter.setBrush(QBrush(QColor(255, 200, 50, 180)))
        painter.drawEllipse(QPointF(cx, ty), cr, cr)
        painter.drawLine(QPointF(cx, ty + cr), QPointF(cx, r.top()))

    # ── 이벤트 ───────────────────────────────────────────

    def mouseDoubleClickEvent(self, event) -> None:
        self.properties_needed.emit(self)
        event.accept()

    def hoverMoveEvent(self, event) -> None:
        mods = event.modifiers()
        ctrl  = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and shift:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._notified_this_press = False

        if event.button() == Qt.MouseButton.LeftButton:
            ctrl  = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

            # Ctrl+Shift → 회전
            if ctrl and shift:
                self.about_to_change.emit()
                self._notified_this_press = True
                self._rotation_mode    = True
                self._rot_center_scene = self.mapToScene(self._rect.center())
                self._rot_start_angle  = _angle(
                    event.scenePos(), self._rot_center_scene
                )
                self._rot_initial = self.rotation()
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                event.accept()
                return

            # Ctrl 없이 → super() 직전에 씬 전체 선택 해제
            if not ctrl:
                s = self.scene()
                if s:
                    s.clearSelection()

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._rotation_mode and self._rot_center_scene:
            delta = _angle(event.scenePos(), self._rot_center_scene) - self._rot_start_angle
            self.setRotation(self._rot_initial + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._rotation_mode:
            self._rotation_mode    = False
            self._rot_center_scene = None
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
                and not self._notified_this_press):
            self._notified_this_press = True
            self.about_to_change.emit()
        return super().itemChange(change, value)

    # ── 유틸 ─────────────────────────────────────────────

    def _make_font(self) -> QFont:
        f = QFont(self._font_family)
        f.setPixelSize(max(8, self._font_size))
        f.setBold(self._bold)
        f.setItalic(self._italic)
        return f

    def _compute_rect(self) -> QRectF:
        fm    = QFontMetricsF(self._font)
        lines = self._text.split('\n') if self._text else ['텍스트']
        w     = max((fm.horizontalAdvance(l) for l in lines), default=60.0) + 16
        h     = (fm.height() + fm.leading()) * len(lines) + 12
        return QRectF(0, 0, max(w, 60), max(h, self._font_size + 12))

    def _sync_origin(self) -> None:
        self.setTransformOriginPoint(self._rect.center())

    def _view_scale(self) -> float:
        s = self.scene()
        if s:
            v = s.views()
            if v: return v[0].transform().m11()
        return 1.0

    def _s2i(self, px: float) -> float:
        sc = self._view_scale()
        return px / sc if sc > 0 else px


def _angle(pos: QPointF, center: QPointF) -> float:
    return math.degrees(math.atan2(pos.y() - center.y(), pos.x() - center.x()))
