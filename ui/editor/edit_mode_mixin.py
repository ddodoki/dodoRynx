# -*- coding: utf-8 -*-
# ui\editor\edit_mode_mixin.py

"""
ImageViewer 편집 모드 전용 Mixin.

EditModeMixin은 단독으로 인스턴스화하지 않으며,
ImageViewer가 다중 상속으로 사용한다.
"""

from __future__ import annotations

import weakref
import math  
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List, Optional, Protocol, runtime_checkable

from PIL import Image

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QThread,
    QTimer,
    Signal,
)

from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QBrush
)

from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from core.image_editor import ImageEditor

from ui.editor.edit_toolbar import EditToolbar
from ui.editor.selection_item import SelectionItem
from ui.editor.shape_item import ResizableShapeItem
from ui.editor.text_item import TextShapeItem
from ui.editor.watermark_mixin import WatermarkMixin
from ui.editor.eraser_mixin import EraserMixin
from ui.editor.resize_mixin import ResizeMixin
from ui.editor.shape_text_mixin import ShapeTextMixin
from ui.editor.filter_mixin import FilterMixin
from ui.editor.ai_feature_mixin import AIFeatureMixin

from utils.dark_dialog import DarkMessageBox as _DarkMessageBox, DarkTextEditDialog as _DarkTextEditDialog
from utils.debug import debug_print, error_print, warning_print
from utils.lang_manager import t

if TYPE_CHECKING:
    from PySide6.QtCore import SignalInstance
    from PySide6.QtWidgets import QGraphicsScene, QGraphicsPixmapItem

    from main_window import MainWindow
    from ui.overlay_widget import OverlayWidget


_TEXT_DLG_DARK_SS: str = """
QDialog       { background:#2a2a2a; color:#ddd; }
QLabel        { color:#ddd; background:transparent; }
QTextEdit     { background:#1e1e1e; color:#eee;
                border:1px solid #555; border-radius:3px; padding:4px; }
QPushButton   { background:#3a3a3a; color:#ddd;
                border:1px solid #555; padding:4px 10px; border-radius:3px; }
QPushButton:hover    { background:#4a4a4a; }
QPushButton:checked  { background:#4a9eff; color:#fff; }
QFontComboBox, QSpinBox { background:#1e1e1e; color:#eee;
                           border:1px solid #555; padding:2px; }
"""

# ──────────────────────────────────────────────────────────────────────────────
# Protocol — Pylance 가 Mixin 내 self.xxx 를 인식하도록 ImageViewer 인터페이스 명세
# ──────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class _ViewerProtocol(Protocol):
    """EditModeMixin 이 의존하는 ImageViewer 의 공개 인터페이스."""

    # 씬 / 아이템
    graphics_scene: QGraphicsScene
    pixmap_item:    Optional[QGraphicsPixmapItem]
    current_pixmap: Optional[QPixmap]

    # 줌
    zoom_mode:             str
    zoom_factor:           float
    _zoom_intent_stack:    list
    _suppress_fit_in_view: bool

    # 편집 상태
    _edit_mode:             bool
    _edit_tool:             str
    _edit_toolbar:          Optional[EditToolbar]
    _editor:                Optional[ImageEditor]
    _selection:             Optional[SelectionItem]
    _edit_shapes:           list
    _drag_start_scene:      Optional[QPointF]
    _edit_history:          list
    _EDIT_HISTORY_MAX:      int
    _shape_clipboard:       list
    _overlay_was_visible:   bool
    _lock_high_res_replace: bool

    # 위젯 참조
    overlay_widget: object
    main_window:    object

    # 시그널
    edit_mode_changed:   SignalInstance
    edit_save_requested: SignalInstance

    # 메서드
    def viewport(self) -> QGraphicsView: ...
    def mapToScene(self, x: int, y: int) -> QPointF: ...
    def window(self) -> QGraphicsView: ...
    def width(self) -> int: ...
    def height(self) -> int: ...
    def setDragMode(self, mode: QGraphicsView.DragMode) -> None: ...
    def resetTransform(self) -> None: ...
    def scale(self, sx: float, sy: float) -> None: ...
    def _replace_pixmap_inplace(self, pixmap: QPixmap) -> None: ...
    def _fit_in_view(self) -> None: ...
    def _calculate_and_emit_zoom(self) -> None: ...
    def replace_pixmap(self, pixmap: QPixmap) -> None: ...
    def set_image(self, pixmap: QPixmap) -> None: ...
    def setViewportUpdateMode(self, mode: QGraphicsView.ViewportUpdateMode) -> None: ...
    def horizontalScrollBar(self) -> object: ...
    def verticalScrollBar(self)   -> object: ...

# ──────────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class _ShapeSnap:
    shape_type: str
    rect:       QRectF
    scene_pos:  QPointF
    rotation:   float
    pen_color:  QColor
    fill_color: Optional[QColor]
    line_width: int
    line_style: int = 1 


@dataclass
class _EditSnap:
    pixmap: Optional[QPixmap]
    shapes: List[_ShapeSnap]

# ──────────────────────────────────────────────────────────────────────────────
# _ClipboardImageItem
# ──────────────────────────────────────────────────────────────────────────────

def _clip_angle(pos: QPointF, center: QPointF) -> float:
    return math.degrees(math.atan2(
        pos.y() - center.y(), pos.x() - center.x()
    ))


class _ClipboardImageItem(QGraphicsObject):
    """클립보드 이미지 — 이동 · 꼭지점 리사이즈 · 회전 지원."""

    about_to_change:   Signal = Signal()         # type: ignore[assignment]
    properties_needed: Signal = Signal(object)   # type: ignore[assignment]

    HANDLE_SCREEN_PX: int = 9


    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        event.accept()


    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__()
        self._pixmap: QPixmap = pixmap
        self._rect:   QRectF  = QRectF(0.0, 0.0,
                                        float(pixmap.width()),
                                        float(pixmap.height()))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable      |
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable   |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(55)
        self._sync_origin()

        # 이동 undo 알림
        self._notified_this_press: bool = False

        # 회전 상태
        self._rotation_mode:     bool              = False
        self._rot_center_scene:  Optional[QPointF] = None
        self._rot_start_angle:   float             = 0.0
        self._rot_initial:       float             = 0.0

        # 리사이즈 상태
        self._resize_mode:       bool              = False
        self._resize_handle:     Optional[str]     = None
        self._resize_origin:     Optional[QPointF] = None
        self._resize_base_w:     float             = 0.0
        self._resize_base_h:     float             = 0.0
        self._resize_base_dist:  float             = 0.0

    # ── boundingRect ────────────────────────────────────────────────

    def boundingRect(self) -> QRectF:
        m  = self._s2i(float(self.HANDLE_SCREEN_PX) / 2.0 + 2)
        br = self._rect.adjusted(-m, -m, m, m)
        return br.united(self._rot_handle_rect().adjusted(-2, -2, 2, 2))

    # ── paint ───────────────────────────────────────────────────────

    def paint(self, painter: QPainter, option, widget=None) -> None:  # type: ignore[override]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(self._rect.toRect(), self._pixmap)

        if not self.isSelected():
            return

        r   = self._rect
        lw  = self._s2i(1.2)
        hs  = self._s2i(self.HANDLE_SCREEN_PX / 2.0)

        # 선택 테두리
        painter.setPen(QPen(QColor(74, 158, 255),
                            self._s2i(1.5), Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(r)

        # 4개 꼭지점 핸들 (파란 사각형)
        painter.setPen(QPen(QColor(255, 255, 255), lw))
        painter.setBrush(QBrush(QColor(74, 158, 255)))
        for hx, hy in [
            (r.left(),  r.top()),    (r.right(), r.top()),
            (r.left(),  r.bottom()), (r.right(), r.bottom()),
        ]:
            painter.drawRect(QRectF(hx - hs, hy - hs, hs * 2, hs * 2))

        # 회전 핸들 (주황 원)
        hr  = self._rot_handle_rect()
        cx  = r.center().x()
        painter.setPen(QPen(QColor(255, 160, 30, 160), lw))
        painter.drawLine(QPointF(cx, r.top()), QPointF(cx, hr.bottom()))
        painter.setPen(QPen(QColor(255, 160, 30), self._s2i(1.5)))
        painter.setBrush(QBrush(QColor(180, 90, 10, 210)))
        painter.drawEllipse(hr)

    # ── 이벤트 ──────────────────────────────────────────────────────

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

            # 회전 핸들
            if self._rot_handle_rect().contains(event.pos()):
                self.about_to_change.emit()
                self._notified_this_press = True
                self._rotation_mode    = True
                self._rot_center_scene = self.mapToScene(self._rect.center())
                self._rot_start_angle  = _clip_angle(
                    event.scenePos(), self._rot_center_scene)
                self._rot_initial = self.rotation()
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                event.accept()
                return

            # 꼭지점 핸들
            for key, rect in self._handle_rects().items():
                if rect.contains(event.pos()):
                    self.about_to_change.emit()
                    self._notified_this_press = True
                    self._resize_mode      = True
                    self._resize_handle    = key
                    self._resize_base_w    = self._rect.width()
                    self._resize_base_h    = self._rect.height()
                    self._resize_base_dist = math.hypot(
                        self._rect.width(), self._rect.height())
                    opposite = {
                        'tl': self._rect.bottomRight(),
                        'tr': self._rect.bottomLeft(),
                        'bl': self._rect.topRight(),
                        'br': self._rect.topLeft(),
                    }[key]
                    self._resize_origin = self.mapToScene(opposite)
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
            delta = (_clip_angle(event.scenePos(), self._rot_center_scene)
                     - self._rot_start_angle)
            self.setRotation(self._rot_initial + delta)
            event.accept()
            return

        if self._resize_mode and self._resize_origin is not None:
            cur  = event.scenePos()
            org  = self._resize_origin
            dist = math.hypot(cur.x() - org.x(), cur.y() - org.y())
            if self._resize_base_dist > 0:
                scale = dist / self._resize_base_dist
                new_w = max(20.0, self._resize_base_w * scale)
                new_h = max(20.0, self._resize_base_h * scale)
                self.prepareGeometryChange()
                self._rect = QRectF(0, 0, new_w, new_h)
                self._sync_origin()
                self.update()
            event.accept()
            return

        super().mouseMoveEvent(event)


    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
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

        self._notified_this_press = False
        super().mouseReleaseEvent(event)


    def itemChange(self, change: QGraphicsItem.GraphicsItemChange,
                   value: object) -> object:
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
                and not self._notified_this_press):
            self._notified_this_press = True
            self.about_to_change.emit()
        return super().itemChange(change, value)

    # ── 유틸 ────────────────────────────────────────────────────────

    def _sync_origin(self) -> None:
        self.setTransformOriginPoint(self._rect.center())


    def _rot_handle_rect(self) -> QRectF:
        r   = self._s2i(float(self.HANDLE_SCREEN_PX))
        cx  = self._rect.center().x()
        top = self._rect.top() - self._s2i(22.0)
        return QRectF(cx - r, top - r, r * 2, r * 2)


    def _handle_rects(self) -> dict[str, QRectF]:
        r  = self._rect
        hs = self._s2i(self.HANDLE_SCREEN_PX / 2.0)
        return {
            'tl': QRectF(r.left()  - hs, r.top()    - hs, hs*2, hs*2),
            'tr': QRectF(r.right() - hs, r.top()    - hs, hs*2, hs*2),
            'bl': QRectF(r.left()  - hs, r.bottom() - hs, hs*2, hs*2),
            'br': QRectF(r.right() - hs, r.bottom() - hs, hs*2, hs*2),
        }


    def _s2i(self, px: float) -> float:
        scene = self.scene()
        if scene:
            views = scene.views()
            if views:
                sc = views[0].transform().m11()
                return px / sc if sc > 0 else px
        return px


# ──────────────────────────────────────────────────────────────────────────────
# EditModeMixin
# ──────────────────────────────────────────────────────────────────────────────

class EditModeMixin(AIFeatureMixin, FilterMixin, WatermarkMixin, EraserMixin, ResizeMixin, ShapeTextMixin):
    """
    ImageViewer에 편집 모드 기능을 주입하는 Mixin.

    다중 상속 MRO:
        class ImageViewer(EditModeMixin, QGraphicsView): ...
    """

    if TYPE_CHECKING:
        graphics_scene:         QGraphicsScene
        pixmap_item:            Optional[QGraphicsPixmapItem]
        current_pixmap:         Optional[QPixmap]
        overlay_widget:         Optional[OverlayWidget] 
        main_window:            Optional[MainWindow]     
        zoom_mode:              str
        zoom_factor:            float
        _zoom_intent_stack:     list
        _suppress_fit_in_view:  bool
        _lock_high_res_replace: bool
        edit_mode_changed:      Signal 
        edit_save_requested:    Signal  

    # ------------------------------------------------------------------
    # 인스턴스 변수 초기화 — ImageViewer.__init__() 에서 호출 필수
    # ------------------------------------------------------------------

    def _init_edit_mode(self) -> None:
        self._edit_mode:             bool                  = False
        self._editor:                Optional[ImageEditor] = None   # type: ignore[type-arg]
        self._selection:             Optional[SelectionItem] = None # type: ignore[type-arg]
        self._edit_shapes:           list                  = []
        self._edit_tool:             str                   = 'select'
        self._drag_start_scene:      Optional[QPointF]     = None
        self._edit_toolbar:          Optional[EditToolbar] = None   # type: ignore[type-arg]
        self._edit_history:          list                  = []
        self._EDIT_HISTORY_MAX:      int                   = 5
        self._shape_clipboard:       list                  = []
        self._overlay_was_visible:   bool                  = False
        self._lock_high_res_replace: bool                  = False
        self._shape_preview_item: Optional[object] = None

        # ── 필터 상태 ──
        self._init_filter()

        # ── MMB 패닝 상태 ──
        self._panning: bool                      = False
        self._pan_start_pos: Optional[QPoint] = None
        self._pan_start_hbar: int                = 0
        self._pan_start_vbar: int                = 0

        # ── AI 기능 상태 ──
        self._init_ai()

        # 상태 초기화
        self._init_eraser()
        self._init_resize()
        self._init_shape_text()
        self._init_watermark()


    # ------------------------------------------------------------------
    # Undo 히스토리
    # ------------------------------------------------------------------

    def _push_undo(self) -> None:
        if not self._edit_mode:
            return

        snaps: list = []
        for item in self._edit_shapes:
            if isinstance(item, ResizableShapeItem):
                snaps.append(('shape', _ShapeSnap(
                    shape_type = item._shape_type,
                    rect       = QRectF(item._rect),
                    scene_pos  = QPointF(item.pos()),
                    rotation   = item.rotation(),
                    pen_color  = QColor(item._pen.color()),
                    fill_color = QColor(item._fill_color) if item._fill_color else None,
                    line_width = item._pen.width(),
                    line_style = item._pen.style().value,
                )))
            elif isinstance(item, TextShapeItem):
                snaps.append(('text', dict(
                    text        = item._text,
                    font_family = item._font_family,
                    font_size   = item._font_size,
                    color       = QColor(item._color),
                    bold        = item._bold,
                    italic      = item._italic,
                    scene_pos   = QPointF(item.pos()),
                    rotation    = item.rotation(),
                )))
            elif isinstance(item, _ClipboardImageItem):
                snaps.append(('clip', dict(
                    pixmap    = QPixmap(item._pixmap),
                    scene_pos = QPointF(item.pos()),
                    rotation  = item.rotation(),
                    width     = item._rect.width(),
                    height    = item._rect.height(),
                )))

        px: Optional[QPixmap] = (
            self._editor.get_preview() if self._editor is not None else None
        )
        self._edit_history.append({'pixmap': px, 'shapes': snaps})
        if len(self._edit_history) > self._EDIT_HISTORY_MAX:
            self._edit_history.pop(0)
        debug_print(f"편집 이력 저장 ({len(self._edit_history)}/{self._EDIT_HISTORY_MAX})")


    def _pop_undo(self) -> None:
        if not self._edit_history:
            debug_print("편집 이력 없음")
            return

        snap: dict = self._edit_history.pop()

        px: Optional[QPixmap] = snap.get('pixmap')
        if px is not None and not px.isNull():
            self._editor = ImageEditor(px)
            self._replace_pixmap_inplace(px)  # type: ignore[attr-defined]

        for item in self._edit_shapes:
            self.graphics_scene.removeItem(item)  # type: ignore[attr-defined]
        self._edit_shapes.clear()

        for kind, data in snap['shapes']:
            new_item: QGraphicsObject
            if kind == 'shape':
                s: _ShapeSnap = data
                new_item = ResizableShapeItem(
                    s.shape_type, QRectF(s.rect),
                    pen_color  = QColor(s.pen_color),
                    fill_color = QColor(s.fill_color) if s.fill_color else None,
                    line_width = s.line_width,
                )
                new_item.set_line_style(Qt.PenStyle(s.line_style))  # type: ignore[attr-defined]
                new_item.setPos(s.scene_pos)
                new_item.setRotation(s.rotation)
            elif kind == 'text':
                d: dict = data
                new_item = TextShapeItem(
                    text        = d['text'],
                    font_family = d['font_family'],
                    font_size   = d['font_size'],
                    color       = d['color'],
                    bold        = d['bold'],
                    italic      = d['italic'],
                )
                new_item.setPos(d['scene_pos'])
                new_item.setRotation(d['rotation'])
            else:  # 'clip'
                d = data
                new_item = _ClipboardImageItem(d['pixmap'])
                new_item.setPos(d['scene_pos'])
                new_item.setRotation(d['rotation'])
                # 리사이즈된 크기 복원
                w = d.get('width',  d['pixmap'].width()) 
                h = d.get('height', d['pixmap'].height())
                new_item._rect = QRectF(0, 0, w, h)       
                new_item._sync_origin()                        

            new_item.about_to_change.connect(self._push_undo)
            if hasattr(new_item, 'properties_needed'):
                new_item.properties_needed.connect(self._on_shape_properties)
            self.graphics_scene.addItem(new_item)  # type: ignore[attr-defined]
            self._edit_shapes.append(new_item)

        debug_print(f"실행 취소 완료, 남은 이력: {len(self._edit_history)}")


    def _clear_undo_history(self) -> None:
        self._edit_history.clear()
        debug_print("편집 이력 초기화")

    # ------------------------------------------------------------------
    # 툴바 초기화 및 연결
    # ------------------------------------------------------------------

    def _ensure_edit_toolbar(self) -> None:
        if self._edit_toolbar is not None:
            return

        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() is not app.thread():
            warning_print("_ensure_edit_toolbar: 메인 스레드 아님 → 무시")
            return

        self._edit_toolbar = EditToolbar(self)  # type: ignore[arg-type]
        self._edit_toolbar.setVisible(False)
        self._edit_toolbar.setFixedHeight(EditToolbar._BASE_H)
        self._connect_edit_toolbar()
        debug_print("EditToolbar 지연 초기화 완료")


    def _connect_edit_toolbar(self) -> None:
        tb = self._edit_toolbar
        assert tb is not None, "_connect_edit_toolbar: toolbar 미생성"

        tb.tool_changed.connect(self._on_edit_tool_changed)
        tb.crop_requested.connect(self._edit_crop)
        tb.copy_requested.connect(self._edit_copy)
        tb.mosaic_requested.connect(self._edit_mosaic)
        tb.apply_requested.connect(self._edit_apply)
        tb.cancel_requested.connect(self._edit_cancel)
        tb.filters_visibility_changed.connect(self._on_filter_panel_toggle)
        tb.ai_panel_requested.connect(self._on_ai_panel_toggle)
        if hasattr(tb, 'btn_watermark'):
            tb.btn_watermark.clicked.connect(
                lambda checked: self._on_watermark_panel_toggle(checked)
            )
            
    # ------------------------------------------------------------------
    # 편집 모드 진입 / 종료
    # ------------------------------------------------------------------

    def enter_edit_mode(self) -> None:
        current_pixmap: Optional[QPixmap] = self.current_pixmap  # type: ignore[attr-defined]
        if current_pixmap is None or current_pixmap.isNull():
            return

        self.setViewportUpdateMode(  # type: ignore[attr-defined]
            QGraphicsView.ViewportUpdateMode.FullViewportUpdate
        )

        self._edit_original_pixmap: QPixmap = QPixmap(current_pixmap)
        self._ensure_edit_toolbar()
        tb = self._edit_toolbar
        assert tb is not None

        # ── 필터 상태 초기화 (재진입 대비)
        self._reset_filter_state()

        # ── 툴바 필터 버튼 체크 해제 (누락) — 재진입 시 버튼 상태 동기화
        if hasattr(tb, 'btn_filters') and tb.btn_filters.isChecked():
            tb.btn_filters.blockSignals(True)
            tb.btn_filters.setChecked(False)
            tb.btn_filters.blockSignals(False)

        if hasattr(tb, 'btn_ai') and tb.btn_ai.isChecked():
            tb.btn_ai.blockSignals(True)
            tb.btn_ai.setChecked(False)
            tb.btn_ai.blockSignals(False)

        if hasattr(tb, 'btn_resize') and tb.btn_resize.isChecked():
            tb.btn_resize.blockSignals(True)
            tb.btn_resize.setChecked(False)
            tb.btn_resize.blockSignals(False)

        if hasattr(tb, 'btn_shapes') and tb.btn_shapes.isChecked():
            tb.btn_shapes.blockSignals(True)
            tb.btn_shapes.setChecked(False)
            tb.btn_shapes.blockSignals(False)

        if hasattr(tb, 'btn_eraser') and tb.btn_eraser.isChecked():
            tb.btn_eraser.blockSignals(True)
            tb.btn_eraser.setChecked(False)
            tb.btn_eraser.blockSignals(False)

        if hasattr(tb, 'btn_watermark') and tb.btn_watermark.isChecked():
            tb.btn_watermark.blockSignals(True)
            tb.btn_watermark.setChecked(False)
            tb.btn_watermark.blockSignals(False)

        # ── 뷰포트에 이벤트 필터 설치 — MMB 패닝
        self.viewport().installEventFilter(self)  # type: ignore[attr-defined]

        # ── 필터 패널 초기화 (항상 숨김 상태로 시작)
        self._ensure_filter_panel()
        fp = self._filter_panel_widget
        if fp is not None:
            fp.reset_all()
            fp.setVisible(False)

        ow = self.overlay_widget  # type: ignore[attr-defined]
        if ow is not None and ow.isVisible():
            ow.hide()
            self._overlay_was_visible = True
        else:
            self._overlay_was_visible = False

        self._editor = ImageEditor(current_pixmap)
        self._selection = SelectionItem()
        self.graphics_scene.addItem(self._selection)  # type: ignore[attr-defined]

        w, h = self._editor.get_size()
        tb.setVisible(True)
        self._position_edit_toolbar()

        self._edit_mode = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)  # type: ignore[attr-defined]

        mw = self.main_window  # type: ignore[attr-defined]
        if mw is not None:
            try:
                mw.removeEventFilter(self)  # type: ignore[arg-type]
            except Exception:
                pass
            mw.installEventFilter(self)  # type: ignore[arg-type]

        self.edit_mode_changed.emit(True)  # type: ignore[attr-defined]

        debug_print("편집 모드 진입")
        

    def _exit_edit_mode(self) -> None:
        self._cancel_shape_preview()
        self._cleanup_eraser()
        self._cleanup_resize() 
        self._cleanup_shape_text() 
        self._cleanup_watermark()
        self._cleanup_ai()

        if self._selection:
            self.graphics_scene.removeItem(self._selection)  # type: ignore[attr-defined]
            self._selection = None

        for item in self._edit_shapes:
            self.graphics_scene.removeItem(item)  # type: ignore[attr-defined]
        self._edit_shapes.clear()

        self._drag_start_scene = None
        self._editor           = None
        self._edit_mode        = False
        self._reset_filter_state()

        try:
            self.viewport().removeEventFilter(self)  # type: ignore[attr-defined, arg-type]
        except Exception:
            pass

        self._panning       = False
        self._pan_start_pos = None

        fp = getattr(self, '_filter_panel_widget', None)
        if fp is not None:
            fp.setVisible(False)

        self.setViewportUpdateMode(  # type: ignore[attr-defined]
            QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate
        )
        if self._edit_toolbar:
            self._edit_toolbar.setVisible(False)

        self._clear_undo_history()

        if self.main_window:
            try:
                self.main_window.removeEventFilter(self)  # type: ignore[attr-defined, arg-type]
            except Exception:
                pass

        if getattr(self, '_overlay_was_visible', False) and self.overlay_widget:
            self.overlay_widget.show()  # type: ignore[attr-defined]
            self._overlay_was_visible = False

        self.edit_mode_changed.emit(False)  # type: ignore[attr-defined]
        debug_print("편집 모드 종료")

    # ------------------------------------------------------------------
    # 툴바 위치 / 도구 변경
    # ------------------------------------------------------------------

    def _position_edit_toolbar(self) -> None:
        tb = self._edit_toolbar
        if tb is None:
            return
        tb.setGeometry(0, 0, self.width(), tb.height())  # type: ignore[attr-defined]
        tb.raise_()
        self._position_filter_panel() 

    # ------------------------------------------------------------------
    # 편집 취소 / 적용
    # ------------------------------------------------------------------

    def _edit_cancel(self) -> None:

        original = getattr(self, '_edit_original_pixmap', None)
        if original is not None and not original.isNull():
            self._replace_pixmap_inplace(original)       # type: ignore[attr-defined]
        self._exit_edit_mode()


    def _edit_apply(self) -> None:
        ed = self._editor
        if ed is None:
            self._exit_edit_mode()
            return

        ed.reset()

        sel = self._selection
        if self._edit_tool == 'select' and sel is not None and sel.isVisible():
            self._edit_crop()
            self._apply_filter_pipeline_sync()

        final_pixmap = self._render_scene_with_shapes()
        if final_pixmap.isNull():
            error_print("_edit_apply: 렌더링 실패")
            self._exit_edit_mode()
            return

        pre_zoom_mode:   str   = self.zoom_mode    # type: ignore[attr-defined]
        pre_zoom_factor: float = self.zoom_factor  # type: ignore[attr-defined]

        self._exit_edit_mode()
        self.set_image(final_pixmap)               # type: ignore[attr-defined]

        self._zoom_intent_stack    = []            # type: ignore[attr-defined]
        self._suppress_fit_in_view = False         # type: ignore[attr-defined]

        if pre_zoom_mode == 'fit':
            self.zoom_mode = 'fit'                 # type: ignore[attr-defined]
            self._fit_in_view()                    # type: ignore[attr-defined]
        elif pre_zoom_mode == 'actual':
            self._user_has_zoomed = True
            self.zoom_mode  = 'actual'             # type: ignore[attr-defined]
            self.resetTransform()                  # type: ignore[attr-defined]
            self.zoom_factor = 1.0                 # type: ignore[attr-defined]
            self._calculate_and_emit_zoom()        # type: ignore[attr-defined]
        elif pre_zoom_mode == 'manual':
            self.zoom_mode  = 'manual'             # type: ignore[attr-defined]
            self.zoom_factor = pre_zoom_factor     # type: ignore[attr-defined]
            self.resetTransform()                  # type: ignore[attr-defined]
            self.scale(pre_zoom_factor,            # type: ignore[attr-defined]
                    pre_zoom_factor)            # type: ignore[attr-defined]
            self._calculate_and_emit_zoom()        # type: ignore[attr-defined]

        _weak_self = weakref.ref(self)
        self._lock_high_res_replace = True         # type: ignore[attr-defined]
        QTimer.singleShot(
            3000,
            lambda: (s := _weak_self()) and setattr(s, '_lock_high_res_replace', False)
        )
        self.edit_save_requested.emit(final_pixmap)  # type: ignore[attr-defined]
            
    # ------------------------------------------------------------------
    # 씬 렌더링 (이미지 + 도형 합성)
    # ------------------------------------------------------------------

    def _render_scene_with_shapes(self) -> QPixmap:
        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is None:
            return QPixmap()

        for item in self._edit_shapes:
            item.setSelected(False)
        sel = self._selection
        if sel is not None:
            sel.setVisible(False)

        base_pixmap: QPixmap = pi.pixmap()
        if base_pixmap.isNull():
            return QPixmap()

        iw, ih   = base_pixmap.width(), base_pixmap.height()
        scenerect = QRectF(0.0, 0.0, float(iw), float(ih))

        result = QPixmap(iw, ih)
        result.fill(Qt.GlobalColor.transparent)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.graphics_scene.render(   # type: ignore[attr-defined]
            painter,
            QRectF(0.0, 0.0, float(iw), float(ih)),
            scenerect,
        )
        painter.end()

        debug_print(f"_render_scene_with_shapes: {iw}×{ih}px")
        return result

    # ------------------------------------------------------------------
    # 편집 액션 (자르기 / 복사 / 리사이즈)
    # ------------------------------------------------------------------

    def _edit_crop(self) -> None:
        ed = self._editor
        sel = self._selection
        if ed is None:
            debug_print("_edit_crop: editor 없음 → 스킵")
            return
        if sel is None:
            debug_print("_edit_crop: selection 없음 → 스킵")
            return
        if not sel.isVisible():
            debug_print("_edit_crop: selection 비표시 → 스킵")
            return
        rect = sel.rect()
        if rect.width() < 5 or rect.height() < 5:
            debug_print(f"_edit_crop: 선택 영역 너무 작음 ({rect.width():.0f}×{rect.height():.0f})")
            return
        self._push_undo()
        cropped: QPixmap = ed.crop(rect)
        debug_print(f"_edit_crop: 완료 → {cropped.width()}×{cropped.height()}")
        self._replace_pixmap_inplace(cropped)  # type: ignore[attr-defined]
        sel.setVisible(False)

        self._schedule_filter_preview(force=True)


    def _edit_copy(self) -> None:
        ed  = self._editor
        sel = self._selection
        if ed is None or sel is None or not sel.isVisible():
            return
        ed.copy_region_to_clipboard(sel.rect())
        # 범위 복사는 시스템 클립보드 이미지로 저장되므로
        # 도형 내부 클립보드를 비워 Ctrl+V 충돌 방지
        self._shape_clipboard = []


    @staticmethod
    def _apply_mosaic_to_pil(
        src: "Image.Image",
        rect: "QRectF",
    ) -> "Optional[Image.Image]":
        """
        PIL Image 의 rect 영역에 픽셀화 모자이크를 적용한 새 Image 반환.
        - src 는 변경하지 않음 (.copy() 후 반환)
        - rect 가 src 범위 밖이면 None 반환
        """
        x,  y  = max(0, int(rect.x())),  max(0, int(rect.y()))
        w,  h  = int(rect.width()),       int(rect.height())
        iw, ih = src.size
        x2, y2 = min(x + w, iw),         min(y + h, ih)

        if x2 <= x or y2 <= y:
            return None

        region = src.crop((x, y, x2, y2))
        block  = max(8, min(w, h) // 15) 
        small  = region.resize(
            (max(1, (x2 - x) // block), max(1, (y2 - y) // block)),
            Image.Resampling.NEAREST,
        )
        mosaic = small.resize((x2 - x, y2 - y), Image.Resampling.NEAREST)
        result = src.copy()
        result.paste(mosaic, (x, y))
        return result


    def _do_mosaic_preview(self) -> None:
        """미리보기 — _working 변경 없이 화면만 갱신"""
        self._mosaic_timer_active = False           # type: ignore[attr-defined]
        if not getattr(self, '_mosaic_preview_pending', False):
            return
        self._mosaic_preview_pending = False        # type: ignore[attr-defined]

        if not self._editor or not self._selection or not self._selection.isVisible():
            return
        rect = self._selection.rect()
        if rect.width() < 5 or rect.height() < 5:
            return
        try:
            preview = self._apply_mosaic_to_pil(self._editor.get_working(), rect)
            if preview is None:
                return

            preview_rgba = preview.convert('RGBA')
            data = preview_rgba.tobytes('raw', 'RGBA')
            qimg = QImage(data, preview_rgba.width, preview_rgba.height,
                        QImage.Format.Format_RGBA8888)
            px = QPixmap.fromImage(qimg)
            if not px.isNull() and self.pixmap_item:    # type: ignore[attr-defined]
                self.pixmap_item.setPixmap(px)           # type: ignore[attr-defined]
                self.graphics_scene.update()             # type: ignore[attr-defined]
        except Exception:
            pass


    def _edit_mosaic(self) -> None:
        """모자이크 확정 적용"""
        if not self._editor or not self._selection or not self._selection.isVisible():
            return
        rect = self._selection.rect()
        if rect.isEmpty():
            return
        self._push_undo()
        try:
            result = self._apply_mosaic_to_pil(self._editor.get_working(), rect)
            if result is None:
                return
            self._editor.set_working(result)
            self._editor.commit()
            self.replace_pixmap(self._editor.get_preview())  # type: ignore[attr-defined]
            self._selection.setVisible(False)
            self._drag_start_scene = None
            self._schedule_filter_preview(force=True)
        except Exception as e:
            error_print(f"모자이크 처리 오류: {e}")

    # ------------------------------------------------------------------
    # 텍스트 다이얼로그
    # ------------------------------------------------------------------

    def _build_text_dialog(
        self,
        item: object,
        title: str = "",
        *,
        text_height: int = 80,
    ) -> tuple["QDialog", "Callable[[], dict]"]:
        """DarkTextEditDialog 래퍼 — 기존 호출부와 인터페이스 유지."""
        from PySide6.QtGui import QColor
        dlg = _DarkTextEditDialog(
            self.window(),           # type: ignore[attr-defined]
            title=title,
            text=getattr(item, 'text', ''),
            font_family=getattr(item, 'font_family', ''),
            font_size=getattr(item, 'font_size', 24),
            bold=getattr(item, 'bold', False),
            italic=getattr(item, 'italic', False),
            color=QColor(getattr(item, 'color', QColor(255, 255, 255))),
            text_height=text_height,
        )
        return dlg, dlg.result_props


    def _show_text_dialog(self, item: object) -> bool:
        dlg, getprops = self._build_text_dialog(
            item, t('edit_mode_mixin.text_input_title'), text_height=80
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        item.update_properties(**getprops())        # type: ignore[attr-defined]
        return True


    def _edit_text_item(self, item: object) -> None:
        dlg, getprops = self._build_text_dialog(
            item, t('edit_mode_mixin.text_edit_title'), text_height=100
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._push_undo()
            item.update_properties(**getprops())    # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # 도형 속성 콜백
    # ------------------------------------------------------------------

    def _on_shape_properties(self, item: object) -> None:
        tb = self._edit_toolbar
        if tb is None:
            return

        if isinstance(item, TextShapeItem):
            self._edit_text_item(item)
            tb.sync_from_text(item)
        elif isinstance(item, ResizableShapeItem):
            tb.sync_from_shape(item._pen.color(), item._pen.width())

    # ------------------------------------------------------------------
    # 도형 선택 삭제 / 복사 / 붙여넣기
    # ------------------------------------------------------------------

    def _delete_selected_shapes(self) -> None:

        selected = [
            it for it in self._edit_shapes
            if isinstance(it, (ResizableShapeItem, TextShapeItem, _ClipboardImageItem))
            and it.isSelected()
        ]
        if not selected:
            return

        self._push_undo()
        for it in selected:
            self.graphics_scene.removeItem(it)  # type: ignore[attr-defined]
            self._edit_shapes.remove(it)
        debug_print(f"도형 삭제: {len(selected)}개")


    def _copy_selected_shapes(self) -> None:

        self._shape_clipboard = []
        for it in self._edit_shapes:
            if not it.isSelected():
                continue
            if isinstance(it, ResizableShapeItem):
                self._shape_clipboard.append(('shape', _ShapeSnap(
                    shape_type = it._shape_type,
                    rect       = QRectF(it._rect),
                    scene_pos  = QPointF(it.pos()),
                    rotation   = it.rotation(),
                    pen_color  = QColor(it._pen.color()),
                    fill_color = QColor(it._fill_color) if it._fill_color else None,
                    line_width = it._pen.width(),
                    line_style = it._pen.style().value,
                )))
            elif isinstance(it, TextShapeItem):
                self._shape_clipboard.append(('text', dict(
                    text        = it._text,
                    font_family = it._font_family,
                    font_size   = it._font_size,
                    color       = QColor(it._color),
                    bold        = it._bold,
                    italic      = it._italic,
                    scene_pos   = QPointF(it.pos()),
                    rotation    = it.rotation(),
                )))
        debug_print(f"복사: {len(self._shape_clipboard)}개")


    def _paste_shapes(self) -> None:
        if not self._shape_clipboard:
            return

        self._push_undo()
        OFFSET = 50.0

        for kind, data in self._shape_clipboard:
            new_item: QGraphicsObject
            if kind == 'shape':
                s: _ShapeSnap = data
                new_item = ResizableShapeItem(
                    s.shape_type, QRectF(s.rect),
                    pen_color  = QColor(s.pen_color),
                    fill_color = QColor(s.fill_color) if s.fill_color else None,
                    line_width = s.line_width,
                )
                new_item.set_line_style(Qt.PenStyle(s.line_style))
                new_item.setPos(s.scene_pos + QPointF(OFFSET, OFFSET))
                new_item.setRotation(s.rotation)
            else:
                d: dict = data
                new_item = TextShapeItem(
                    text        = d['text'],
                    font_family = d['font_family'],
                    font_size   = d['font_size'],
                    color       = d['color'],
                    bold        = d['bold'],
                    italic      = d['italic'],
                )
                new_item.setPos(d['scene_pos'] + QPointF(OFFSET, OFFSET))
                new_item.setRotation(d['rotation'])

            new_item.about_to_change.connect(self._push_undo)
            if hasattr(new_item, 'properties_needed'):
                new_item.properties_needed.connect(self._on_shape_properties)
            self.graphics_scene.addItem(new_item)  # type: ignore[attr-defined]
            self._edit_shapes.append(new_item)
            new_item.setSelected(True)


    def _paste_clipboard_image(self, pixmap: QPixmap) -> None:
        """외부 클립보드 이미지를 씬에 이동 가능한 레이어로 삽입."""
        self._push_undo()

        vp = self.viewport()                                # type: ignore[attr-defined]
        cx = vp.width()  / 2.0
        cy = vp.height() / 2.0
        sc = self.mapToScene(int(cx), int(cy))              # type: ignore[attr-defined]

        pi = self.pixmap_item                               # type: ignore[attr-defined]
        if pi is not None:
            max_w = pi.boundingRect().width() * 0.5
            if pixmap.width() > max_w:
                pixmap = pixmap.scaledToWidth(
                    int(max_w), Qt.TransformationMode.SmoothTransformation
                )

        item = _ClipboardImageItem(pixmap)
        item.setPos(
            sc.x() - pixmap.width()  / 2.0,
            sc.y() - pixmap.height() / 2.0,
        )
        item.about_to_change.connect(self._push_undo)
        self.graphics_scene.addItem(item)                   # type: ignore[attr-defined]
        self._edit_shapes.append(item)
        item.setSelected(True)
        debug_print(f"클립보드 이미지 삽입: {pixmap.width()}×{pixmap.height()}")


    def _cancel_shape_preview(self) -> None:
        self._cancel_shape_preview_st()


    # ──────────────────────────────────────────────────────────────────
    # 줌 드래그 수정 — MMB 패닝 (Bug 3)
    # ──────────────────────────────────────────────────────────────────

    def eventFilter(self, obj: object, event: object) -> bool:  # type: ignore[override]
        # 뷰포트 이벤트 — MMB 패닝
        try:
            if obj is self.viewport():  # type: ignore[attr-defined]
                return self._edit_viewport_event(event)
        except Exception:
            pass

        # 기존 키보드 처리 (변경 없음)
        if not getattr(self, '_edit_mode', False):
            return False
        if self._edit_toolbar is None:
            return False
        if not isinstance(event, QEvent):
            return False

        et = event.type()

        if et == QEvent.Type.ShortcutOverride:
            if not isinstance(event, QKeyEvent):
                return False
            key  = event.key()
            mods = event.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            override_keys = {
                Qt.Key.Key_Delete, Qt.Key.Key_Backspace,
                Qt.Key.Key_Left,   Qt.Key.Key_Right,
                Qt.Key.Key_Up,     Qt.Key.Key_Down,
                Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
            }
            if key in override_keys:
                event.accept()
                return False
            if ctrl and key in (Qt.Key.Key_Z, Qt.Key.Key_C, Qt.Key.Key_V):
                event.accept()
                return False

        elif et == QEvent.Type.KeyPress:
            if not isinstance(event, QKeyEvent):
                return False
            key  = event.key()
            mods = event.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._delete_selected_shapes()
                return True

            if ctrl and key == Qt.Key.Key_Z:
                # AI 브러시 모드: 마스크 스트로크 undo 우선 소비
                mask = getattr(self, '_mask_item', None)
                if (self._edit_tool == 'ai_erase'
                        and mask is not None
                        and mask.has_stroke_history()):
                    mask.undo_stroke()
                else:
                    self._pop_undo()
                return True

            if ctrl and key == Qt.Key.Key_C:
                self._copy_selected_shapes()
                if self._shape_clipboard:
                    QApplication.clipboard().clear()
                return True
            if ctrl and key == Qt.Key.Key_V:
                if self._shape_clipboard:
                    self._paste_shapes()
                else:
                    cb_pixmap = QApplication.clipboard().pixmap()
                    if not cb_pixmap.isNull():
                        self._paste_clipboard_image(cb_pixmap)
                return True

            nav_keys = {
                Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up,  Qt.Key.Key_Down,
                Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                Qt.Key.Key_Home,   Qt.Key.Key_End,
            }
            if key in nav_keys:
                return True

        return False


    def _edit_viewport_event(self, event: object) -> bool:
        if not getattr(self, '_edit_mode', False):
            return False
        if not isinstance(event, QEvent):
            return False

        et = event.type()

        # 지우개 이벤트 위임
        if self._edit_tool == 'eraser' and isinstance(event, QMouseEvent):
            return self._handle_eraser_event(event, et)

        # ── AI 브러시 이벤트 처리 (LMB) ───────────────────────────
        if isinstance(event, QMouseEvent):
            if self._handle_ai_brush_event(event, et):
                return True

        # ── MMB 패닝 (기존 코드 유지) ────────────────────────────────
        if et == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.MiddleButton:
                self._panning = True
                self._pan_start_pos  = event.pos()
                hbar = self.horizontalScrollBar()  # type: ignore[attr-defined]
                vbar = self.verticalScrollBar()    # type: ignore[attr-defined]
                self._pan_start_hbar = hbar.value()  # type: ignore[attr-defined]
                self._pan_start_vbar = vbar.value()  # type: ignore[attr-defined]
                self.viewport().setCursor(           # type: ignore[attr-defined]
                    Qt.CursorShape.ClosedHandCursor
                )
                return True

        elif et == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            if getattr(self, '_panning', False):
                start: Optional[QPoint] = getattr(self, '_pan_start_pos', None)
                if start is not None:
                    delta = event.pos() - start
                    hbar = self.horizontalScrollBar()  # type: ignore[attr-defined]
                    vbar = self.verticalScrollBar()    # type: ignore[attr-defined]
                    hbar.setValue(self._pan_start_hbar - delta.x())  # type: ignore[attr-defined]
                    vbar.setValue(self._pan_start_vbar - delta.y())  # type: ignore[attr-defined]
                return True

        elif et == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.MiddleButton:
                self._panning      = False
                self._pan_start_pos = None
                if self._edit_tool and (
                    self._edit_tool.startswith('shape:') or self._edit_tool == 'text'
                ):
                    self.viewport().setCursor(  # type: ignore[attr-defined]
                        Qt.CursorShape.CrossCursor
                    )
                else:
                    self.viewport().unsetCursor()  # type: ignore[attr-defined]
                return True

        return False


    def _on_edit_tool_changed(self, tool: str) -> None:
        prev = self._edit_tool

        if prev == 'eraser' and tool != 'eraser':
            self._on_eraser_tool_leave()
        if prev == 'resize' and tool != 'resize':
            self._on_resize_tool_leave()
        if prev == 'shapes' and tool != 'shapes': 
            self._on_shape_text_tool_leave() 
        if prev == 'ai_erase' and tool != 'ai_erase':
            self._deactivate_brush()

        self._edit_tool = tool
        if self._selection:
            self._selection.setVisible(False)
        self._drag_start_scene = None
        self._cancel_shape_preview_st() 

        if tool == 'eraser':
            self._on_eraser_tool_enter()
        elif tool == 'resize':
            self._on_resize_tool_enter()
        elif tool == 'shapes':                   
            self._on_shape_text_tool_enter()   
        else:
            self.viewport().unsetCursor()   # type: ignore[attr-defined]

