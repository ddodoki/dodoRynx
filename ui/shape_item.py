# -*- coding: utf-8 -*-
# ui/shape_item.py

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

    HANDLE_SCREEN_PX  = 9
    MIN_SCREEN_PX     = 30
    # 수정 1: 회전 핸들 오프셋 상수화 — boundingRect와 반드시 동기화
    ROT_HANDLE_OFFSET_PX = 22   # 화면 픽셀 기준 (boundingRect margin보다 커야 함)
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

    about_to_change   = Signal()
    properties_needed = Signal(object)
    DEFAULT_LINE_WIDTH = 3


    def __init__(
        self,
        shape_type:  str,
        rect:        QRectF,
        pen_color:   QColor           = QColor(255, 80, 80),
        fill_color:  Optional[QColor] = None,
        line_width:  int              = 2,
    ) -> None:
        super().__init__()
        self._shape_type = shape_type
        self._rect       = QRectF(rect)
        self._pen        = QPen(pen_color, line_width)
        self._pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self._pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._fill_color = fill_color

        self._active_handle:      int               = -1
        self._drag_start_local:   Optional[QPointF] = None
        self._rect_start:         Optional[QRectF]  = None

        self._rotation_mode:      bool              = False
        self._rot_center_scene:   Optional[QPointF] = None
        self._rot_start_angle:    float             = 0.0
        self._rot_initial:        float             = 0.0

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

        # 도형 본체 path
        body = QPainterPath()
        if st in ('rect', 'rect_filled'):
            body.addRect(r)
        elif st in ('ellipse', 'ellipse_filled'):
            body.addEllipse(r)
        elif st in ('line', 'arrow', 'cross'):
            # 선 계열: pen 두께를 고려한 스트로크 영역 생성
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
            # star는 복잡하므로 bounding rect 사용
            body.addRect(r)
        else:
            body.addRect(r)

        # 핸들 영역 합집합 — 모든 도형에서 핸들 클릭 보장
        handles = QPainterPath()
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

            if st in ('rect', 'rect_filled'):
                painter.drawRect(r)
            elif st in ('ellipse', 'ellipse_filled'):
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

            # 핸들
            hs = self._handle_half()
            painter.setPen(QPen(QColor(255, 255, 255), self._screen_to_scene(1.2)))
            painter.setBrush(QBrush(QColor(74, 158, 255)))
            for hx, hy in self._handle_centers():
                painter.drawRect(QRectF(hx - hs, hy - hs, hs * 2, hs * 2))

            # 수정 3: 회전 핸들 오프셋을 상수 기반으로 통일
            cx  = r.center().x()
            ty  = r.top() - self._screen_to_scene(self.ROT_HANDLE_OFFSET_PX)
            cr  = self._screen_to_scene(self.ROT_HANDLE_RADIUS_PX)
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
        # 수정 4: hit-test 영역을 렌더 크기보다 약간 넓게 설정해
        #    고해상도/저줌에서도 핸들 클릭이 확실하게 감지되도록 함
        hit_extra = self._screen_to_scene(2.0)  # 화면 2px 여유
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
        """회전 핸들(노란 원)의 hit 영역"""
        r   = self._rect.normalized()
        cx  = r.center().x()
        ty  = r.top() - self._screen_to_scene(self.ROT_HANDLE_OFFSET_PX)
        cr  = self._screen_to_scene(self.ROT_HANDLE_RADIUS_PX + 4)  # 여유 4px
        return QRectF(cx - cr, ty - cr, cr * 2, cr * 2)


    def _is_rotation_handle(self, pos: QPointF) -> bool:
        return self._rotation_handle_rect().contains(pos)


    def hoverMoveEvent(self, event) -> None:
        # 수정: Ctrl+Shift 대신 회전 핸들 위치에서만 CrossCursor
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

            # 수정: 회전 핸들 클릭 감지 — Ctrl+Shift 불필요
            if self._is_rotation_handle(event.pos()):
                self.about_to_change.emit()
                self._notified_this_press = True
                self._rotation_mode    = True
                self._rot_center_scene = self.mapToScene(
                    self._rect.normalized().center()
                )
                self._rot_start_angle  = self._scene_angle(
                    event.scenePos(), self._rot_center_scene
                )
                self._rot_initial      = self.rotation()
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                event.accept()
                return

            # 핸들 리사이즈 (기존과 동일)
            h_idx = self._handle_at(event.pos())
            if h_idx >= 0:
                self._active_handle = h_idx
                self.about_to_change.emit()
                self._notified_this_press = True
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                self._drag_start_local = self.mapFromScene(event.scenePos())
                self._rect_start       = QRectF(self._rect)
                event.accept()
                return

            # 일반 이동
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
        r  = QRectF(self._rect_start)
        dx, dy = delta_local.x(), delta_local.y()

        if handle in (self.TL, self.BL, self.ML): r.setLeft(r.left()   + dx)
        if handle in (self.TR, self.MR, self.BR): r.setRight(r.right() + dx)
        if handle in (self.TL, self.TM, self.TR): r.setTop(r.top()     + dy)
        if handle in (self.BL, self.BM, self.BR): r.setBottom(r.bottom() + dy)

        r_norm = r.normalized()

        # 절대 하한(10 scene px)으로 폭발 방지
        MIN_SCENE_ABS = 10.0
        min_s = max(self._min_size_scene(), MIN_SCENE_ABS)

        w_ok = r_norm.width()  >= min_s
        h_ok = r_norm.height() >= min_s

        if w_ok and h_ok:
            self._rect = r_norm
        elif w_ok:          # 높이만 한계 → 너비만 반영
            cur = QRectF(self._rect)
            cur.setLeft(r_norm.left()); cur.setRight(r_norm.right())
            self._rect = cur.normalized()
        elif h_ok:          # 너비만 한계 → 높이만 반영
            cur = QRectF(self._rect)
            cur.setTop(r_norm.top()); cur.setBottom(r_norm.bottom())
            self._rect = cur.normalized()

        self._sync_transform_origin()
        self.update()


    def _sync_transform_origin(self) -> None:
        self.setTransformOriginPoint(self._rect.normalized().center())


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


    def update_rect(self, rect: QRectF) -> None:
        """드래그 중 미리보기 크기 업데이트용"""
        self.prepareGeometryChange()
        self._rect = QRectF(rect)
        self._sync_transform_origin()
        self.update()