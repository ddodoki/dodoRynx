# -*- coding: utf-8 -*-
# ui\editor\shape_item.py

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject


class ResizableShapeItem(QGraphicsObject):
    """이동 + 8핸들 크기 조절 + Ctrl+Shift 드래그 회전."""

    TL, TM, TR, MR, BR, BM, BL, ML = range(8)

    HANDLE_SCREEN_PX     = 9
    MIN_SCREEN_PX        = 30
    ROT_HANDLE_OFFSET_PX = 22
    ROT_HANDLE_RADIUS_PX = 6

    HANDLE_CURSORS = [
        Qt.CursorShape.SizeFDiagCursor,
        Qt.CursorShape.SizeVerCursor,
        Qt.CursorShape.SizeBDiagCursor,
        Qt.CursorShape.SizeHorCursor,
        Qt.CursorShape.SizeFDiagCursor,
        Qt.CursorShape.SizeVerCursor,
        Qt.CursorShape.SizeBDiagCursor,
        Qt.CursorShape.SizeHorCursor,
    ]

    about_to_change    = Signal()
    properties_needed  = Signal(object)
    DEFAULT_LINE_WIDTH = 3

    def __init__(
        self,
        shape_type: str,
        rect:       QRectF,
        pen_color:  QColor           = QColor(255, 80, 80),
        fill_color: Optional[QColor] = None,
        line_width: int              = 2,
    ) -> None:
        super().__init__()
        self._shape_type = shape_type
        self._rect       = QRectF(rect)
        self._pen        = QPen(pen_color, line_width)
        self._pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self._pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._fill_color = fill_color

        self._active_handle:    int               = -1
        self._drag_start_local: Optional[QPointF] = None
        self._rect_start:       Optional[QRectF]  = None

        self._rotation_mode:    bool              = False
        self._rot_center_scene: Optional[QPointF] = None
        self._rot_start_angle:  float             = 0.0
        self._rot_initial:      float             = 0.0

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable    |
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(50)
        self._sync_transform_origin()
        self._notified_this_press = False

    # ── 외부 API ────────────────────────────────────────────────────

    @property
    def rect(self) -> QRectF:
        return QRectF(self._rect)


    @property
    def pen(self) -> QPen:
        return self._pen


    @property
    def fill_color(self) -> Optional[QColor]:
        return self._fill_color


    @property
    def shape_type(self) -> str:
        return self._shape_type


    def set_pen_color(self, color: QColor) -> None:
        self._pen.setColor(color)
        self.update()


    def set_line_width(self, width: int) -> None:
        self._pen.setWidth(width)
        self.update()


    def set_fill_color(self, color: Optional[QColor]) -> None:
        self._fill_color = color
        self.update()


    def set_line_style(self, style) -> None:
        try:
            self._pen.setStyle(Qt.PenStyle(style))
        except Exception:
            try:
                self._pen.setStyle(Qt.PenStyle(int(style)))
            except Exception:
                pass
        self.update()

    # ── 줌 배율 역산 ────────────────────────────────────────────────

    def _view_scale(self) -> float:
        scene = self.scene()
        if scene:
            views = scene.views()
            if views:
                s = views[0].transform().m11()
                return s if s > 0 else 1.0
        return 1.0


    def _screen_to_scene(self, screen_px: float) -> float:
        return screen_px / self._view_scale()


    def _handle_half(self) -> float:
        return self._screen_to_scene(self.HANDLE_SCREEN_PX / 2.0)


    def _min_size_scene(self) -> float:
        return self._screen_to_scene(self.MIN_SCREEN_PX)

    # ── QGraphicsItem 구현 ──────────────────────────────────────────

    def boundingRect(self) -> QRectF:
        handle_m = self._handle_half() + 2
        rot_m    = self._screen_to_scene(
            self.ROT_HANDLE_OFFSET_PX + self.ROT_HANDLE_RADIUS_PX + 2
        )
        m = max(handle_m, rot_m)
        return self._rect.normalized().adjusted(-m, -m, m, m)


    def shape(self) -> QPainterPath:
        r  = self._rect.normalized()
        st = self._shape_type
        hs = self._handle_half()

        body = QPainterPath()

        if st == 'rect':
            body.addRect(r)

        elif st == 'rect_round':  
            radius = min(r.width(), r.height()) * 0.15
            body.addRoundedRect(r, radius, radius)

        elif st == 'ellipse':
            body.addEllipse(r)

        elif st in ('line', 'arrow', 'cross'):
            stroker = QPainterPathStroker()
            stroker.setWidth(max(self._pen.widthF(), 1.0) + self._screen_to_scene(8))
            raw = QPainterPath()
            if st == 'cross':
                raw.moveTo(r.topLeft());  raw.lineTo(r.bottomRight())
                raw.moveTo(r.topRight()); raw.lineTo(r.bottomLeft())
            else:
                raw.moveTo(r.topLeft()); raw.lineTo(r.bottomRight())
            body = stroker.createStroke(raw)

        elif st == 'triangle':
            body.moveTo(r.center().x(), r.top())
            body.lineTo(r.bottomRight())
            body.lineTo(r.bottomLeft())
            body.closeSubpath()

        elif st == 'star':
            body.addRect(r)

        elif st in ('heart', 'diamond', 'pentagon'):      
            body.addRect(r)  

        else:
            body.addRect(r)

        handles  = QPainterPath()
        for hx, hy in self._handle_centers():
            handles.addRect(QRectF(hx - hs, hy - hs, hs * 2, hs * 2))

        rot_path = QPainterPath()
        rot_path.addRect(self._rotation_handle_rect())
        return body.united(handles).united(rot_path)


    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            painter.setPen(self._pen)
            painter.setBrush(
                QBrush(self._fill_color) if self._fill_color
                else Qt.BrushStyle.NoBrush
            )

            r  = self._rect.normalized()
            st = self._shape_type

            if st == 'rect':
                painter.drawRect(r)

            elif st == 'rect_round':         
                radius = min(r.width(), r.height()) * 0.15
                painter.drawRoundedRect(r, radius, radius)

            elif st == 'ellipse':
                painter.drawEllipse(r)

            elif st == 'line':
                painter.drawLine(r.topLeft(), r.bottomRight())

            elif st == 'arrow':
                self._draw_arrow(painter, r.topLeft(), r.bottomRight())

            elif st == 'cross':
                painter.drawLine(r.topLeft(),  r.bottomRight())
                painter.drawLine(r.topRight(), r.bottomLeft())

            elif st == 'triangle':
                path = QPainterPath()
                path.moveTo(r.center().x(), r.top())
                path.lineTo(r.bottomRight())
                path.lineTo(r.bottomLeft())
                path.closeSubpath()
                painter.drawPath(path)

            elif st == 'star':
                self._draw_star(painter, r)

            elif st == 'heart':                   
                self._draw_heart(painter, r)

            elif st == 'diamond':          
                path = QPainterPath()
                path.moveTo(r.center().x(), r.top())
                path.lineTo(r.right(),       r.center().y())
                path.lineTo(r.center().x(), r.bottom())
                path.lineTo(r.left(),        r.center().y())
                path.closeSubpath()
                painter.drawPath(path)

            elif st == 'pentagon':           
                self._draw_polygon(painter, r, sides=5, start_angle=-90)

            if not self.isSelected():
                return

            sel_pen = QPen(QColor(74, 158, 255, 180), self._screen_to_scene(1.5))
            sel_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(sel_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r.adjusted(
                -self._screen_to_scene(2), -self._screen_to_scene(2),
                 self._screen_to_scene(2),  self._screen_to_scene(2)
            ))

            hs = self._handle_half()
            painter.setPen(QPen(QColor(255, 255, 255), self._screen_to_scene(1.2)))
            painter.setBrush(QBrush(QColor(74, 158, 255)))
            for hx, hy in self._handle_centers():
                painter.drawRect(QRectF(hx - hs, hy - hs, hs * 2, hs * 2))

            cx = r.center().x()
            ty = r.top() - self._screen_to_scene(self.ROT_HANDLE_OFFSET_PX)
            cr = self._screen_to_scene(self.ROT_HANDLE_RADIUS_PX)
            painter.setPen(QPen(QColor(255, 200, 50), self._screen_to_scene(1.5)))
            painter.setBrush(QBrush(QColor(255, 200, 50, 180)))
            painter.drawEllipse(QPointF(cx, ty), cr, cr)
            painter.drawLine(QPointF(cx, ty + cr), QPointF(cx, r.top()))

        finally:
            painter.restore()

    # ── 핸들 위치 ────────────────────────────────────────────────────

    def _handle_centers(self) -> List[Tuple[float, float]]:
        r  = self._rect.normalized()
        cx = r.center().x()
        cy = r.center().y()
        return [
            (r.left(),  r.top()),
            (cx,        r.top()),
            (r.right(), r.top()),
            (r.right(), cy),
            (r.right(), r.bottom()),
            (cx,        r.bottom()),
            (r.left(),  r.bottom()),
            (r.left(),  cy),
        ]


    def _handle_rects(self) -> List[QRectF]:
        hs = self._handle_half()
        return [
            QRectF(hx - hs, hy - hs, hs * 2, hs * 2)
            for hx, hy in self._handle_centers()
        ]


    def _handle_at(self, pos: QPointF) -> int:
        hit_extra = self._screen_to_scene(2.0)
        hs = self._handle_half() + hit_extra
        for i, (hx, hy) in enumerate(self._handle_centers()):
            if QRectF(hx - hs, hy - hs, hs * 2, hs * 2).contains(pos):
                return i
        return -1


    def itemChange(self, change, value):
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
                and not self._notified_this_press):
            self._notified_this_press = True
            self.about_to_change.emit()
        return super().itemChange(change, value)

    # ── 마우스 이벤트 ───────────────────────────────────────────────

    def _rotation_handle_rect(self) -> QRectF:
        r  = self._rect.normalized()
        cx = r.center().x()
        ty = r.top() - self._screen_to_scene(self.ROT_HANDLE_OFFSET_PX)
        cr = self._screen_to_scene(self.ROT_HANDLE_RADIUS_PX + 4)
        return QRectF(cx - cr, ty - cr, cr * 2, cr * 2)


    def _is_rotation_handle(self, pos: QPointF) -> bool:
        return self._rotation_handle_rect().contains(pos)


    def hoverMoveEvent(self, event) -> None:
        if self._is_rotation_handle(event.pos()):
            self.setCursor(Qt.CursorShape.CrossCursor)
            super().hoverMoveEvent(event)
            return
        h = self._handle_at(event.pos())
        if h >= 0:
            self.setCursor(self.HANDLE_CURSORS[h])
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)


    def mousePressEvent(self, event) -> None:
        self._notified_this_press = False
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_rotation_handle(event.pos()):
                self.about_to_change.emit()
                self._notified_this_press = True
                self._rotation_mode    = True
                self._rot_center_scene = self.mapToScene(
                    self._rect.normalized().center()
                )
                self._rot_start_angle = self._scene_angle(
                    event.scenePos(), self._rot_center_scene
                )
                self._rot_initial = self.rotation()
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                event.accept()
                return

            h_idx = self._handle_at(event.pos())
            if h_idx >= 0:
                self._active_handle       = h_idx
                self.about_to_change.emit()
                self._notified_this_press = True
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                self._drag_start_local    = self.mapFromScene(event.scenePos())
                self._rect_start          = QRectF(self._rect)
                event.accept()
                return

            mods = event.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            if not ctrl:
                s = self.scene()
                if s:
                    s.clearSelection()

        super().mousePressEvent(event)


    def mouseMoveEvent(self, event) -> None:
        if self._rotation_mode and self._rot_center_scene is not None:
            cur   = self._scene_angle(event.scenePos(), self._rot_center_scene)
            delta = cur - self._rot_start_angle
            self.setRotation(self._rot_initial + delta)
            event.accept()
            return

        if self._active_handle >= 0 and self._drag_start_local is not None:
            cur_local   = self.mapFromScene(event.scenePos())
            delta_local = cur_local - self._drag_start_local
            self._apply_handle_resize(self._active_handle, delta_local)
            event.accept()
            return

        super().mouseMoveEvent(event)


    def mouseReleaseEvent(self, event) -> None:
        self._notified_this_press = False
        if self._rotation_mode:
            self._rotation_mode    = False
            self._rot_center_scene = None
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            event.accept()
            return
        if self._active_handle >= 0:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self._active_handle    = -1
        self._drag_start_local = None
        self._rect_start       = None
        super().mouseReleaseEvent(event)


    def mouseDoubleClickEvent(self, event) -> None:
        self.properties_needed.emit(self)
        event.accept()

    # ── 리사이즈 ────────────────────────────────────────────────────

    def _apply_handle_resize(self, handle: int, delta_local: QPointF) -> None:
        if self._rect_start is None:
            return
        self.prepareGeometryChange()
        r      = QRectF(self._rect_start)
        dx, dy = delta_local.x(), delta_local.y()

        if handle in (self.TL, self.BL, self.ML): r.setLeft(r.left()     + dx)
        if handle in (self.TR, self.MR, self.BR): r.setRight(r.right()   + dx)
        if handle in (self.TL, self.TM, self.TR): r.setTop(r.top()       + dy)
        if handle in (self.BL, self.BM, self.BR): r.setBottom(r.bottom() + dy)

        r_norm        = r.normalized()
        MIN_SCENE_ABS = 10.0
        min_s         = max(self._min_size_scene(), MIN_SCENE_ABS)
        w_ok          = r_norm.width()  >= min_s
        h_ok          = r_norm.height() >= min_s

        if w_ok and h_ok:
            self._rect = r_norm
        elif w_ok:
            cur = QRectF(self._rect)
            cur.setLeft(r_norm.left()); cur.setRight(r_norm.right())
            self._rect = cur.normalized()
        elif h_ok:
            cur = QRectF(self._rect)
            cur.setTop(r_norm.top()); cur.setBottom(r_norm.bottom())
            self._rect = cur.normalized()

        self._sync_transform_origin()
        self.update()


    def _sync_transform_origin(self) -> None:
        self.setTransformOriginPoint(self._rect.normalized().center())


    def update_rect(self, rect: QRectF) -> None:
        """드래그 중 미리보기 크기 업데이트용"""
        self.prepareGeometryChange()
        self._rect = QRectF(rect)
        self._sync_transform_origin()
        self.update()

    # ── 유틸 ────────────────────────────────────────────────────────

    @staticmethod
    def _scene_angle(pos: QPointF, center: QPointF) -> float:
        return math.degrees(math.atan2(
            pos.y() - center.y(),
            pos.x() - center.x()
        ))


    @staticmethod
    def _draw_arrow(painter: QPainter, start: QPointF, end: QPointF) -> None:
        painter.drawLine(start, end)
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux, uy = dx / length, dy / length
        size   = max(6.0, min(32.0, 0.06 * length))
        perp   = 0.4
        painter.drawLine(end, QPointF(
            end.x() - size * ux + size * perp * uy,
            end.y() - size * uy - size * perp * ux,
        ))
        painter.drawLine(end, QPointF(
            end.x() - size * ux - size * perp * uy,
            end.y() - size * uy + size * perp * ux,
        ))


    @staticmethod
    def _draw_star(painter: QPainter, r: QRectF) -> None:
        cx, cy  = r.center().x(), r.center().y()
        r_outer = min(r.width(), r.height()) / 2.0
        if r_outer <= 0:
            return
        r_inner = r_outer * 0.4
        pts     = []
        for i in range(10):
            angle = math.radians(-90 + i * 36)
            rad   = r_outer if i % 2 == 0 else r_inner
            pts.append(QPointF(
                cx + rad * math.cos(angle),
                cy + rad * math.sin(angle),
            ))
        path = QPainterPath()
        path.moveTo(pts[0])
        for p in pts[1:]:
            path.lineTo(p)
        path.closeSubpath()
        painter.drawPath(path)


    @staticmethod
    def _draw_heart(painter: QPainter, r: QRectF) -> None:
        """
        파라메트릭 하트 곡선
        x = 16·sin³(t)
        y = 13·cos(t) - 5·cos(2t) - 2·cos(3t) - cos(4t)
        → 자동 정규화로 r에 꽉 맞게 스케일
        """
        cx, cy = r.center().x(), r.center().y()
        hw, hh = r.width() / 2, r.height() / 2

        N   = 120
        raw = []
        for i in range(N):
            t  = math.radians(-180 + i * 360 / N)
            hx =  16 * math.sin(t) ** 3
            hy = -(13 * math.cos(t) 
                - 5 * math.cos(2 * t)
                - 2 * math.cos(3 * t)
                -     math.cos(4 * t))
            raw.append((hx, hy))

        # bounding box 기준 정규화
        xs   = [p[0] for p in raw]
        ys   = [p[1] for p in raw]
        cx0  = (min(xs) + max(xs)) / 2
        cy0  = (min(ys) + max(ys)) / 2
        sx   = (max(xs) - min(xs)) / 2  or 1
        sy   = (max(ys) - min(ys)) / 2  or 1

        path = QPainterPath()
        for i, (hx, hy) in enumerate(raw):
            qx = cx + (hx - cx0) / sx * hw
            qy = cy + (hy - cy0) / sy * hh
            if i == 0:
                path.moveTo(qx, qy)
            else:
                path.lineTo(qx, qy)
        path.closeSubpath()
        painter.drawPath(path)


    @staticmethod
    def _draw_polygon(  
        painter: QPainter,
        r:       QRectF,
        sides:   int,
        start_angle: float = -90,
    ) -> None:
        cx, cy = r.center().x(), r.center().y()
        rx, ry = r.width() / 2, r.height() / 2
        path   = QPainterPath()
        for i in range(sides):
            angle = math.radians(start_angle + i * 360 / sides)
            x = cx + rx * math.cos(angle)
            y = cy + ry * math.sin(angle)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        painter.drawPath(path)
