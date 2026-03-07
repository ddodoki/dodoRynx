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
    properties_needed = Signal(object) 

    HANDLE_SCREEN_PX = 9
    HANDLE_PX = 9

    def __init__(
        self,
        text:        str    = "텍스트",
        font_family: str    = "맑은 고딕",
        font_size:   int    = 40,  
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

        self._resize_mode:   bool              = False
        self._resize_handle: Optional[str]     = None   # 'tl','tr','bl','br'
        self._resize_origin: Optional[QPointF] = None   # 고정 반대편 꼭지점
        self._resize_base_size: float          = 0.0    # 드래그 시작 시 font_size
        self._resize_base_dist: float          = 0.0 

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
        m  = self._s2i(self.HANDLE_SCREEN_PX / 2.0 + 2)
        br = self._rect.adjusted(-m, -m, m, m)
        return br.united(self._rot_handle_rect().adjusted(-2, -2, 2, 2))


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

        # 회전 핸들 (드래그 가능)
        hr  = self._rot_handle_rect()
        lw  = self._s2i(1.2)
        painter.setPen(QPen(QColor(255, 160, 30, 160), lw))
        painter.drawLine(QPointF(cx, r.top()), QPointF(cx, hr.bottom()))
        painter.setPen(QPen(QColor(255, 160, 30), self._s2i(1.5)))
        painter.setBrush(QBrush(QColor(180, 90, 10, 210)))
        painter.drawEllipse(hr)

    # ── 이벤트 ───────────────────────────────────────────

    def mouseDoubleClickEvent(self, event) -> None:
        self.properties_needed.emit(self)
        event.accept()


    def hoverMoveEvent(self, event) -> None:
        pos = event.pos()
        if self._rot_handle_rect().contains(pos):
            self.setCursor(Qt.CursorShape.CrossCursor)
            super().hoverMoveEvent(event)
            return
        corner_cursors = {
            'tl': Qt.CursorShape.SizeFDiagCursor,
            'tr': Qt.CursorShape.SizeBDiagCursor,
            'bl': Qt.CursorShape.SizeBDiagCursor,
            'br': Qt.CursorShape.SizeFDiagCursor,
        }
        for key, rect in self._handle_rects().items():
            if rect.contains(pos):
                self.setCursor(corner_cursors[key])
                super().hoverMoveEvent(event)
                return
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)


    def mousePressEvent(self, event) -> None:
        self._notified_this_press = False

        if event.button() == Qt.MouseButton.LeftButton:
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)

            if self._rot_handle_rect().contains(event.pos()):
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
            
            for key, rect in self._handle_rects().items():
                if rect.contains(event.pos()):
                    self.about_to_change.emit()
                    self._notified_this_press = True
                    self._resize_mode      = True
                    self._resize_handle    = key
                    self._resize_base_size = float(self._font_size)
                    opposite = {
                        'tl': self._rect.bottomRight(),
                        'tr': self._rect.bottomLeft(),
                        'bl': self._rect.topRight(),
                        'br': self._rect.topLeft(),
                    }[key]
                    self._resize_origin = self.mapToScene(opposite)
                    self._resize_base_dist  = math.hypot(   
                                            self._rect.width(), self._rect.height()
                                        )          
                    self._resize_start_pos   = QPointF(self.pos())          
                    self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                    event.accept()
                    return
                
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
        
        if self._resize_mode and self._resize_origin is not None:
            # 반대편 앵커 → 현재 마우스 거리로 스케일 계산
            cur   = event.scenePos()
            org   = self._resize_origin
            dist = math.hypot(cur.x() - org.x(), cur.y() - org.y())
            if self._resize_base_dist > 0: 
                scale = dist / self._resize_base_dist
                new_size = max(8, int(self._resize_base_size * scale))
                if new_size != self._font_size:
                    self.prepareGeometryChange()
                    self._font_size = new_size
                    self._font      = self._make_font()
                    self._rect      = self._compute_rect()
                    self._sync_origin()
                    # tl 핸들일 때: item을 오른쪽/아래로 이동해서 br 앵커 고정
                    if self._resize_handle in ('tl', 'tr', 'bl'):
                        anchor_local = {
                            'tl': self._rect.bottomRight(),
                            'tr': self._rect.bottomLeft(),
                            'bl': self._rect.topRight(),
                        }[self._resize_handle]
                        new_anchor_scene = self.mapToScene(anchor_local)
                        delta = self._resize_origin - new_anchor_scene
                        self.setPos(self.pos() + delta)
                    self.update()
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
        
        if self._resize_mode:
            self._resize_mode   = False
            self._resize_handle = None
            self._resize_origin = None
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


    def _rot_handle_rect(self) -> QRectF:
        """회전 핸들 영역 (item 로컬 좌표)."""
        r   = self._s2i(float(self.HANDLE_PX))
        cx  = self._rect.center().x()
        top = self._rect.top() - self._s2i(22.0)
        return QRectF(cx - r, top - r, r * 2, r * 2)


    def _handle_rects(self) -> dict[str, QRectF]:
        """4개 꼭지점 핸들의 로컬 좌표 rect."""
        r  = self._rect
        hs = self._s2i(self.HANDLE_SCREEN_PX / 2.0)
        return {
            'tl': QRectF(r.left()  - hs, r.top()    - hs, hs*2, hs*2),
            'tr': QRectF(r.right() - hs, r.top()    - hs, hs*2, hs*2),
            'bl': QRectF(r.left()  - hs, r.bottom() - hs, hs*2, hs*2),
            'br': QRectF(r.right() - hs, r.bottom() - hs, hs*2, hs*2),
        }

def _angle(pos: QPointF, center: QPointF) -> float:
    return math.degrees(math.atan2(pos.y() - center.y(), pos.x() - center.x()))

