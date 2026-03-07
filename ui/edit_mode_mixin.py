# -*- coding: utf-8 -*-
# ui/edit_mode_mixin.py

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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from core.image_editor import ImageEditor
from core.qt_pil import pil_to_qpixmap, qpixmap_to_pil
from core.image_filters import BasicParams, apply_basic, apply_pro, apply_style
from core.ai_bg_remover import (
    BEN2Worker,
    ModelDownloadWorker,
    check_dependencies,
    get_model_dir,
    is_model_cached,
    _ONNX_FILE,
)

from ui.edit_toolbar import EditToolbar
from ui.edit_filter_panel import EditFilterPanel, PANEL_TOTAL_H, PANEL_W
from ui.model_download_dialog import ModelDownloadDialog
from ui.selection_item import SelectionItem
from ui.shape_item import ResizableShapeItem
from ui.text_item import TextShapeItem

from ui.eraser_mixin import EraserMixin
from ui.resize_mixin import ResizeMixin
from ui.shape_text_mixin import ShapeTextMixin

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

class EditModeMixin(EraserMixin, ResizeMixin, ShapeTextMixin):
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
        self._filter_basic: BasicParams           = BasicParams()
        self._filter_style_name: str             = "none"
        self._filter_style_intensity: int        = 0
        self._filter_pro_name: str               = "none"
        self._filter_pro_intensity: int          = 0
        self._filter_preview_pending: bool       = False
        self._filter_timer_active: bool          = False
        self._filter_panel_widget: Optional[EditFilterPanel] = None  

        # ── MMB 패닝 상태 ──
        self._panning: bool                      = False
        self._pan_start_pos: Optional[QPoint] = None
        self._pan_start_hbar: int                = 0
        self._pan_start_vbar: int                = 0

        # ── AI 기능 상태 ──
        self._bg_workers:       list         = []
        self._ai_workers:       list         = []
        self._mask_item:        Optional[object] = None
        self._ai_brush_on:      bool         = False
        self._ai_brush_drawing: bool         = False 
        self._ai_brush_size:    int          = 30     
        self._ai_panel_widget:  Optional[object] = None

        # 상태 초기화
        self._init_eraser()
        self._init_resize()
        self._init_shape_text()

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
        self._filter_basic = BasicParams()
        self._filter_style_name, self._filter_style_intensity = "none", 0
        self._filter_pro_name, self._filter_pro_intensity = "none", 0
        self._filter_preview_pending = False
        self._filter_timer_active = False

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
        # tb.set_image_size(w, h)
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
        self._remove_mask_item()
        self._ai_brush_on = False
        self._cleanup_eraser()
        self._cleanup_resize() 
        self._cleanup_shape_text() 

        ai_panel = getattr(self, '_ai_panel_widget', None)
        if ai_panel is not None:
            ai_panel.setVisible(False)

        if self._selection:
            self.graphics_scene.removeItem(self._selection)  # type: ignore[attr-defined]
            self._selection = None

        for item in self._edit_shapes:
            self.graphics_scene.removeItem(item)  # type: ignore[attr-defined]
        self._edit_shapes.clear()

        self._drag_start_scene = None
        self._editor           = None
        self._edit_mode        = False
        self._filter_preview_pending = False
        self._filter_timer_active    = False

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
        # _edit_original_pixmap: enter_edit_mode()에서 저장한 딥카피 원본
        # → AI 지우개, BG 제거, 필터, 크롭 등 모든 작업을 완전히 무시하고 복원
        original = getattr(self, '_edit_original_pixmap', None)
        if original is not None and not original.isNull():
            self._replace_pixmap_inplace(original)       # type: ignore[attr-defined]
        self._filter_basic = BasicParams()
        self._filter_style_name, self._filter_style_intensity = "none", 0
        self._filter_pro_name,  self._filter_pro_intensity  = "none", 0
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
        block  = max(8, min(w, h) // 15) # 짧은 변의 1/15, 최소 8px
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
            # PIL → QPixmap : raw RGBA 직접 변환 (PNG 인코딩 없이 빠름)
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
        title: str,
        *,
        text_height: int = 80,
        dark_style: bool = False,
    ) -> "tuple[QDialog, Callable[[], dict]]":
        """
        텍스트 편집 다이얼로그를 빌드하고 (dlg, get_props) 튜플을 반환.

        dlg.exec() 후 → item.update_properties(**get_props()) 로 사용.
        """
        dlg = QDialog(self.window())           # type: ignore[attr-defined]
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(420)
        if dark_style:
            dlg.setStyleSheet(_TEXT_DLG_DARK_SS)

        layout = QVBoxLayout(dlg)
        row1   = QHBoxLayout()

        font_cb = QFontComboBox()
        font_cb.setCurrentFont(QFont(item._font_family))   # type: ignore[attr-defined]

        size_spin = QSpinBox()
        size_spin.setRange(8, 500)
        size_spin.setValue(item._font_size)                # type: ignore[attr-defined]
        size_spin.setSuffix("px")

        btn_bold   = QPushButton("B")
        btn_italic = QPushButton("I")
        for btn, attr in ((btn_bold, "_bold"), (btn_italic, "_italic")):
            btn.setCheckable(True)
            btn.setChecked(getattr(item, attr))            # type: ignore[attr-defined]
            btn.setFixedSize(28, 28)

        _color: List[QColor] = [QColor(item._color)]       # type: ignore[attr-defined]
        btn_color = QPushButton("■ 색상")

        def _refresh_color() -> None:
            lum = _color[0].lightness()
            btn_color.setStyleSheet(
                f"QPushButton{{background:{_color[0].name()};"
                f"color:{'#000' if lum > 128 else '#fff'};"
                f"border:1px solid #555;padding:4px 10px;border-radius:3px;}}"
            )
        _refresh_color()

        def _pick_color() -> None:
            c = QColorDialog.getColor(_color[0], dlg, t('edit_mode_mixin.color_picker_title'))
            if c.isValid():
                _color[0] = c
                _refresh_color()
        btn_color.clicked.connect(_pick_color)

        row1.addWidget(font_cb, 1)
        row1.addWidget(size_spin)
        row1.addWidget(btn_bold)
        row1.addWidget(btn_italic)
        row1.addWidget(btn_color)
        layout.addLayout(row1)

        layout.addWidget(QLabel(t('edit_mode_mixin.text_content_label')))
        text_edit = QTextEdit()
        text_edit.setPlainText(item._text)                 # type: ignore[attr-defined]
        text_edit.setMinimumHeight(text_height)
        layout.addWidget(text_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        def get_props() -> dict:
            return dict(
                text = text_edit.toPlainText() or t('edit_mode_mixin.default_text'),
                font_family = font_cb.currentFont().family(),
                font_size   = size_spin.value(),
                color       = _color[0],
                bold        = btn_bold.isChecked(),
                italic      = btn_italic.isChecked(),
            )

        return dlg, get_props


    def _show_text_dialog(self, item: object) -> bool:
        """텍스트 입력 다이얼로그. OK → True, Cancel → False."""
        dlg, get_props = self._build_text_dialog(
            item, t('edit_mode_mixin.text_input_title'), text_height=80
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        item.update_properties(**get_props())   # type: ignore[attr-defined]
        return True


    def _edit_text_item(self, item: object) -> None:
        """기존 텍스트 아이템 속성 편집 다이얼로그."""
        dlg, get_props = self._build_text_dialog(
            item, t('edit_mode_mixin.text_edit_title'), text_height=100, dark_style=True
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._push_undo()
            item.update_properties(**get_props())  # type: ignore[attr-defined]

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
    # 필터 패널 — 생성·위치·토글
    # ──────────────────────────────────────────────────────────────────

    def _ensure_filter_panel(self) -> None:
        if getattr(self, '_filter_panel_widget', None) is not None:
            return
        panel = EditFilterPanel(self)  # type: ignore[arg-type]
        panel.setVisible(False)
        panel.basic_changed.connect(self._on_filter_basic_changed)
        panel.style_changed.connect(self._on_filter_style_changed)
        panel.pro_changed.connect(self._on_filter_pro_changed)
        panel.reset_requested.connect(self._on_filter_reset_requested)
        self._filter_panel_widget = panel
        debug_print("EditFilterPanel 생성 완료")


    def _position_filter_panel(self) -> None:
        fp = getattr(self, '_filter_panel_widget', None)
        if fp is None or not fp.isVisible():
            return
        tb_h = self._edit_toolbar.height() if self._edit_toolbar else EditToolbar._BASE_H
        vp_w = self.width()   # type: ignore[attr-defined]
        x    = vp_w - PANEL_W - 8
        y    = tb_h + 6
        fp.setGeometry(x, y, PANEL_W, PANEL_TOTAL_H)
        fp.raise_()


    def _on_filter_panel_toggle(self, visible: bool) -> None:
        self._ensure_filter_panel()
        fp = self._filter_panel_widget
        if fp is None:
            return
        fp.setVisible(visible)
        if visible:
            self._position_filter_panel()

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
        if self._edit_tool == 'ai_erase' and self._mask_item is not None \
                and isinstance(event, QMouseEvent):

            if et == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._ai_brush_drawing = True

                    sp = self.mapToScene(event.pos().x(), event.pos().y())      # type: ignore[attr-defined]
                    pi = self.pixmap_item
                    if pi is not None:
                        local = pi.mapFromScene(sp) 
                        self._mask_item.paint_at(local.x(), local.y())      # type: ignore[attr-defined]
                    return True

            elif et == QEvent.Type.MouseMove:
                if self._ai_brush_drawing:
                    sp = self.mapToScene(event.pos().x(), event.pos().y())      # type: ignore[attr-defined]
                    pi = self.pixmap_item
                    if pi is not None:
                        local = pi.mapFromScene(sp) 
                        self._mask_item.paint_at(local.x(), local.y())      # type: ignore[attr-defined]
                    return True

            elif et == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._ai_brush_drawing = False
                    self._mask_item.reset_stroke()      # type: ignore[attr-defined]
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

    # ──────────────────────────────────────────────────────────────────
    # 필터 — 시그널 핸들러 (기존과 동일)
    # ──────────────────────────────────────────────────────────────────

    def _on_filter_basic_changed(self, d: dict) -> None:
        try:
            self._filter_basic = BasicParams(**{k: int(v) for k, v in d.items()})
        except Exception as e:
            error_print(f"필터 파라미터 오류: {e}")
            return
        self._schedule_filter_preview()


    def _on_filter_style_changed(self, name: str, intensity: int) -> None:
        self._filter_style_name      = name or "none"
        self._filter_style_intensity = int(intensity)
        self._schedule_filter_preview()


    def _on_filter_pro_changed(self, name: str, intensity: int) -> None:
        self._filter_pro_name      = name or "none"
        self._filter_pro_intensity = int(intensity)
        self._schedule_filter_preview()


    def _on_filter_reset_requested(self) -> None:
        self._filter_basic           = BasicParams()
        self._filter_style_name      = "none"
        self._filter_style_intensity = 0
        self._filter_pro_name        = "none"
        self._filter_pro_intensity   = 0
        self._schedule_filter_preview(force=True)

    # ──────────────────────────────────────────────────────────────────
    # 필터 — 프리뷰 (속도 최적화 포함)
    # ──────────────────────────────────────────────────────────────────

    def _schedule_filter_preview(self, *, force: bool = False) -> None:
        if not getattr(self, '_edit_mode', False):
            return
        if self._editor is None or self.pixmap_item is None:  # type: ignore[attr-defined]
            return
        if (not force) and getattr(self, '_filter_timer_active', False):
            self._filter_preview_pending = True
            return
        self._filter_timer_active    = True
        self._filter_preview_pending = True
        delay_ms = 80 if (
            self._editor._working is not None
            and self._editor._working.width * self._editor._working.height > 2_000_000
        ) else 40
        _weak_self = weakref.ref(self)
        QTimer.singleShot(delay_ms, lambda: (s := _weak_self()) and s._do_filter_preview())


    def _do_filter_preview(self) -> None:
        self._filter_timer_active = False
        if not getattr(self, '_filter_preview_pending', False):
            return
        self._filter_preview_pending = False
        if not getattr(self, '_edit_mode', False):
            return
        if self._editor is None or self.pixmap_item is None:  # type: ignore[attr-defined]
            return
        if (getattr(self, '_mosaic_timer_active', False)
                or getattr(self, '_mosaic_preview_pending', False)):
            self._filter_preview_pending = True
            _weak_self = weakref.ref(self)
            QTimer.singleShot(80, lambda: (s := _weak_self()) and s._do_filter_preview())
            return

        if (self._filter_basic == BasicParams()
                and self._filter_style_name == "none"
                and self._filter_pro_name == "none"):
            try:
                px = pil_to_qpixmap(self._editor.get_working())
                if not px.isNull() and self.pixmap_item:     # type: ignore[attr-defined]
                    self.pixmap_item.setPixmap(px)           # type: ignore[attr-defined]
                    self.graphics_scene.update()             # type: ignore[attr-defined]
            except Exception:
                pass
            return

        try:
            base = self._editor.get_working()
            iw, ih = base.size
            vp     = self.viewport()                         # type: ignore[attr-defined]
            scale  = min(1.0, vp.width() / iw, vp.height() / ih)

            if scale < 0.77 and iw > 800:
                pw      = max(1, int(iw * scale * 1.5))
                ph      = max(1, int(ih * scale * 1.5))
                working = base.resize((pw, ph), Image.Resampling.BILINEAR)
            else:
                working = base.copy()

            out = apply_basic(working, self._filter_basic)
            out = apply_style(out, self._filter_style_name, self._filter_style_intensity)
            out = apply_pro(out,   self._filter_pro_name,   self._filter_pro_intensity)

            if working.size != (iw, ih):
                out = out.resize((iw, ih), Image.Resampling.BILINEAR)

            px = pil_to_qpixmap(out)
            if not px.isNull() and self.pixmap_item:         # type: ignore[attr-defined]
                self.pixmap_item.setPixmap(px)               # type: ignore[attr-defined]
                self.graphics_scene.update()                 # type: ignore[attr-defined]

            if getattr(self, '_filter_preview_pending', False):
                self._schedule_filter_preview(force=True)

        except Exception as e:
            error_print(f"_do_filter_preview 오류: {e}")


    def _apply_filter_pipeline_sync(self) -> None:
        if self._editor is None or self.pixmap_item is None:  # type: ignore[attr-defined]
            return
        if (self._filter_basic == BasicParams()
                and self._filter_style_name == "none"
                and self._filter_pro_name == "none"):
            return
        try:
            base = self._editor.get_working().copy()
            out  = apply_basic(base, self._filter_basic)
            out  = apply_style(out, self._filter_style_name, self._filter_style_intensity)
            out  = apply_pro(out,   self._filter_pro_name,   self._filter_pro_intensity)
            px   = pil_to_qpixmap(out)
            if not px.isNull() and self.pixmap_item:         # type: ignore[attr-defined]
                self.pixmap_item.setPixmap(px)               # type: ignore[attr-defined]
        except Exception as e:
            error_print(f"_apply_filter_pipeline_sync 오류: {e}")


    def _on_bg_remove_requested(self) -> None:
        ok, missing = check_dependencies()
        if not ok:
            from ui.dep_install_dialog import DepInstallDialog
            dlg = DepInstallDialog(missing, parent=self.window())  # type: ignore[attr-defined]
            if dlg.exec() != 1:
                return
            ok, _ = check_dependencies()
            if not ok:
                return

        if not is_model_cached():
            from core.ai_bg_remover import ModelDownloadWorker
            dlworker = ModelDownloadWorker()
            dlg = ModelDownloadDialog(
                worker=dlworker,        # type: ignore[attr-defined]
                title=t('bg_remove.dl_title'),
                desc=t('bg_remove.dl_desc'),
                filename=_ONNX_FILE,
                parent=self.window(),  # type: ignore[attr-defined]
            )
            if dlg.exec() != 1:
                return
            if not is_model_cached():
                return

        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is None:
            return
        src_pixmap = pi.pixmap()
        if src_pixmap.isNull():
            return

        # BG 버튼만 비활성화. erase 버튼은 절대 건드리지 않는다.
        ai_panel = getattr(self, '_ai_panel_widget', None)
        if ai_panel is not None and hasattr(ai_panel, 'set_bg_task_running'):
            ai_panel.set_bg_task_running(True)

        self._start_loading_overlay(t('loading_overlay.bg_loading'))
        self._push_undo()

        worker = BEN2Worker(src_pixmap)
        worker.progress.connect(self._on_bg_remove_progress)
        worker.finished.connect(self._on_bg_remove_done)
        worker.failed.connect(self._on_bg_remove_failed)
        worker.finished.connect(lambda _: self._cleanup_bg_worker(worker))
        worker.failed.connect(lambda _: self._cleanup_bg_worker(worker))
        worker.start()
        self._bg_workers.append(worker)
        debug_print("[BG Remove] Worker 시작")


    def _on_bg_remove_progress(self, key: str) -> None:
        overlay = getattr(self, '_loading_overlay', None)
        if overlay and hasattr(overlay, 'set_message'):
            if key == "model_loading":
                overlay.set_message(t('loading_overlay.bg_loading'))
            elif key == "inferring":
                overlay.set_message(t('loading_overlay.bg_inferring'))


    def _on_bg_remove_done(self, result: QPixmap) -> None:
        self._stop_loading_overlay()
        ai_panel = getattr(self, '_ai_panel_widget', None)
        if ai_panel is not None and hasattr(ai_panel, 'set_bg_task_running'):
            ai_panel.set_bg_task_running(False)
        if result.isNull():
            return
        self._replace_pixmap_inplace(result)  # type: ignore[attr-defined]
        if self._editor is not None:
            # 동일한 이유로 _working만 교체
            self._editor.set_working(qpixmap_to_pil(result))
            self._editor.commit() 

        tb = self._edit_toolbar
        if tb and hasattr(tb, 'btn_fmt_webp'):
            tb.btn_fmt_webp.setChecked(True)
            tb._on_fmt_changed(1)

        QMessageBox.information(
            self.window(),  # type: ignore[attr-defined]
            t('bg_remove.done'),
            t('bg_remove.transparency_tip'),
        )
        debug_print("[BG Remove] 완료")


    def _on_bg_remove_failed(self, error: str) -> None:
        self._stop_loading_overlay()

        # BG 버튼만 복원. erase 버튼은 건드리지 않는다.
        ai_panel = getattr(self, '_ai_panel_widget', None)
        if ai_panel is not None and hasattr(ai_panel, 'set_bg_task_running'):
            ai_panel.set_bg_task_running(False)

        self._pop_undo()
        error_print(f"[BG Remove] {error}")
        QMessageBox.critical(
            self.window(),  # type: ignore[attr-defined]
            t('bg_remove.confirm_title'),
            t('bg_remove.failed', error=error[:300]),
        )


    def _cleanup_bg_worker(self, worker) -> None:
        try:
            self._bg_workers.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()
        debug_print(f"BG워커 정리 완료 (남은: {len(self._bg_workers)})")

    # ──────────────────────────────────────────────────────────────────
    # AI 패널 — 생성·위치·토글
    # ──────────────────────────────────────────────────────────────────

    def _ensure_ai_panel(self) -> None:
        if getattr(self, '_ai_panel_widget', None) is not None:
            return
        from ui.ai_panel import AIPanel
        panel = AIPanel(self)
        panel.setVisible(False)

        panel.bg_remove_requested.connect(self._on_bg_remove_requested)
        panel.erase_activate_requested.connect(self._on_ai_erase_activate)
        panel.erase_run_requested.connect(self._on_ai_erase_run)
        panel.erase_clear_requested.connect(self._on_ai_erase_clear)
        panel.brush_size_changed.connect(self._on_ai_brush_size_changed)

        panel.preload_requested.connect(self._start_ai_preloader)
        self._ai_panel_widget = panel

        debug_print("[AI Panel] 위젯 생성 완료")

        
    def _position_ai_panel(self) -> None:
        panel = getattr(self, '_ai_panel_widget', None)
        if panel is None or not panel.isVisible():
            return
        from ui.ai_panel import PANEL_W
        tb_h = self._edit_toolbar.height() if self._edit_toolbar else EditToolbar._BASE_H
        # 필터 패널 바로 왼쪽에 배치 (겹치지 않도록)
        fp = getattr(self, '_filter_panel_widget', None)
        if fp is not None and fp.isVisible():
            x = fp.x() - PANEL_W - 8
        else:
            x = self.width() - PANEL_W - 8    # type: ignore[attr-defined]
        y = tb_h + 6
        panel.setGeometry(x, y, PANEL_W, panel.height())
        panel.raise_()


    def _start_ai_preloader(self) -> None:
        """AIPanel.showEvent → preload_requested → 여기서 실행."""
        panel = getattr(self, '_ai_panel_widget', None)
        if panel is None:
            return

        if any(getattr(w, '__class__', None).__name__ == 'AIModelPreloader' # type: ignore[attr-defined]
            for w in self._ai_workers):
            debug_print("[AI Panel] Preloader 이미 실행 중 — 스킵")
            # 이미 ready 상태면 패널에 즉시 반영
            self._sync_ai_panel_state(panel)
            return

        from core.ai_eraser import AIModelPreloader
        loader = AIModelPreloader()
        loader.one_loading.connect(panel.set_model_loading)
        loader.one_no_model.connect(panel.set_model_not_installed)
        loader.one_ready.connect(panel.set_models_ready)
        loader.one_failed.connect(
            lambda name, msg: (
                error_print(f"[AI Preload] {name} 실패: {msg}"),
                panel.set_models_ready(name),
            )
        )
        self._ai_workers.append(loader)
        loader.finished.connect(
            lambda: self._ai_workers.remove(loader)
            if loader in self._ai_workers else None
        )
        loader.start()
        debug_print("[AI Panel] Preloader 시작")


    def _sync_ai_panel_state(self, panel) -> None:
        """Preloader 재실행 없이 현재 캐시 상태를 패널에 반영."""
        try:
            from core.ai_eraser import _SESSION_CACHE
            from core.ai_bg_remover import _BEN2_SESSION_CACHE, get_onnx_path as ben2_path
            from core.ai_model_manager import get_onnx_path
            if str(ben2_path()) in _BEN2_SESSION_CACHE:
                panel.set_models_ready("ben2")
            if str(get_onnx_path("lama")) in _SESSION_CACHE:
                panel.set_models_ready("lama")
        except ImportError:
            pass


    def _on_ai_panel_toggle(self, visible: bool) -> None:
        self._ensure_ai_panel()
        panel = self._ai_panel_widget
        if panel is None:
            return

        panel.setVisible(visible)   # type: ignore[attr-defined]

        if not visible:
            self._deactivate_brush()
            return

        self._position_ai_panel()
        if hasattr(panel, 'set_brush_size'):
            panel.set_brush_size(self._ai_brush_size)   # type: ignore[attr-defined]


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

    # ──────────────────────────────────────────────────────────────────
    # AI 지우개
    # ──────────────────────────────────────────────────────────────────

    def _on_ai_erase_activate(self) -> None:
        """브러시 ON/OFF 토글."""
        if self._ai_brush_on:
            self._deactivate_brush()
        else:
            self._activate_brush()


    def _activate_brush(self) -> None:
        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is None:
            return
        if self._mask_item is None:
            from ui.ai_mask_item import AIMaskItem
            w = int(pi.boundingRect().width())
            h = int(pi.boundingRect().height())
            self._mask_item = AIMaskItem(w, h)
            self._mask_item.brush_size = self._ai_brush_size
            self.graphics_scene.addItem(self._mask_item)      # type: ignore[attr-defined]

        self._ai_brush_on  = True
        self._edit_tool    = 'ai_erase'
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)  # type: ignore[attr-defined]


    def _deactivate_brush(self) -> None:
        self._ai_brush_on = False
        if self._edit_tool == 'ai_erase':
            self._edit_tool = 'select'
        self.viewport().unsetCursor()   # type: ignore[attr-defined]
        panel = getattr(self, '_ai_panel_widget', None)
        if panel and hasattr(panel, 'set_brush_active'):
            panel.set_brush_active(False)


    def _remove_mask_item(self) -> None:
        if self._mask_item is not None:
            self.graphics_scene.removeItem(self._mask_item)  # type: ignore[attr-defined]
            self._mask_item = None


    def _on_ai_erase_clear(self) -> None:
        if self._mask_item is not None:
            self._mask_item.clear()   # type: ignore[attr-defined]
        self._deactivate_brush()


    def _on_ai_brush_size_changed(self, size: int) -> None:
        self._ai_brush_size = size  
        if self._mask_item is not None:
            self._mask_item.brush_size = size            # type: ignore[attr-defined]


    def _on_ai_erase_run(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from core.ai_model_manager import (
            check_dependencies, is_model_cached,
            AIModelDownloadWorker, MODEL_REGISTRY,
        )
        from core.ai_eraser import AIEraserWorker

        if self._mask_item is None or self._mask_item.is_empty():  # type: ignore[attr-defined]
            QMessageBox.information(
                self.window(),      # type: ignore[attr-defined]
                t('edit_mode_mixin.ai_erase_title'),
                t('edit_mode_mixin.ai_erase_guide'),
            )
            return

        ok, missing = check_dependencies()
        if not ok:
            self._show_dep_install_dialog(missing)
            return

        if not is_model_cached("lama"):
            if not self._download_ai_model("lama"):
                return

        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is None:
            return
        src = pi.pixmap()
        mask_px = self._mask_item.get_mask_pixmap()  # type: ignore[attr-defined]

        self._push_undo()
        self._deactivate_brush()
        self._start_loading_overlay(t('edit_mode_mixin.ai_erasing'))
        self._set_ai_btn_enabled(False)

        worker = AIEraserWorker(src, mask_px)
        worker.progress.connect(lambda k: None)
        worker.finished.connect(self._on_ai_erase_done)
        worker.failed.connect(self._on_ai_failed)
        worker.finished.connect(lambda _: self._cleanup_ai_worker(worker))
        worker.failed.connect(lambda _: self._cleanup_ai_worker(worker))
        worker.start()
        self._ai_workers.append(worker)
        self._remove_mask_item()
        debug_print("AIEraserWorker 시작")


    def _on_ai_erase_done(self, result: QPixmap) -> None:
        self._stop_loading_overlay()
        self._set_ai_btn_enabled(True)
        if result.isNull():
            return
        self._replace_pixmap_inplace(result)  # type: ignore[attr-defined]
        if self._editor is not None:
            # _working만 교체. _original은 절대 건드리지 않는다.
            #   → 취소/저장안함 시 ed.reset()이 진짜 원본을 반환할 수 있음
            #self._editor._working = qpixmap_to_pil(result)
            self._editor.set_working(qpixmap_to_pil(result))
        debug_print("AI 지우개 완료")

    # ──────────────────────────────────────────────────────────────────
    # AI 공통 헬퍼
    # ──────────────────────────────────────────────────────────────────

    def _download_ai_model(self, key: str) -> bool:
        """모델 다운로드 다이얼로그 실행. 성공 True / 취소·실패 False."""
        from core.ai_model_manager import AIModelDownloadWorker, MODEL_REGISTRY
        from ui.model_download_dialog import ModelDownloadDialog

        info   = MODEL_REGISTRY[key]
        worker = AIModelDownloadWorker(key)
        dlg = ModelDownloadDialog(
            worker   = worker,  # type: ignore[attr-defined]
            title    = t('edit_mode_mixin.model_download_title', label=info.label),
            desc     = t('edit_mode_mixin.model_download_desc', label=info.label),
            filename = info.filename,
            parent   = self.window(),   # type: ignore[attr-defined]
        )
        result = dlg.exec()
        from core.ai_model_manager import is_model_cached
        return result == 1 and is_model_cached(key)


    def _show_dep_install_dialog(self, missing: list) -> None:
        from ui.dep_install_dialog import DepInstallDialog
        dlg = DepInstallDialog(missing, parent=self.window())  # type: ignore[attr-defined]
        dlg.exec()


    def _start_loading_overlay(self, message: str = "") -> None:
        overlay = getattr(self, '_loading_overlay', None)
        if overlay:
            overlay.start(message)


    def _stop_loading_overlay(self) -> None:
        overlay = getattr(self, '_loading_overlay', None)
        if overlay:
            overlay.stop()


    def _set_ai_btn_enabled(self, enabled: bool) -> None:
        """AI 지우기 실행 버튼 활성/비활성."""
        panel = getattr(self, '_ai_panel_widget', None)
        if panel is None:
            return
        if hasattr(panel, 'set_erase_run_enabled'):
            panel.set_erase_run_enabled(enabled)  # type: ignore[attr-defined]


    def _on_ai_failed(self, msg: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        self._stop_loading_overlay()
        self._set_ai_btn_enabled(True)
        error_print(f"AI 처리 실패:\n{msg}")
        QMessageBox.critical(
            self.window(),       # type: ignore[attr-defined]
            t('edit_mode_mixin.ai_failed_title'),
            t('edit_mode_mixin.ai_failed_msg', msg=msg[:300]),
        )


    def _cleanup_ai_worker(self, worker: object) -> None:
        try:
            self._ai_workers.remove(worker)
        except ValueError:
            pass
        if hasattr(worker, 'deleteLater'):
            worker.deleteLater()    # type: ignore[attr-defined]

