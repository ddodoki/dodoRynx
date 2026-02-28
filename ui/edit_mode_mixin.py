# -*- coding: utf-8 -*-
# ui/edit_mode_mixin.py

"""
ImageViewer 편집 모드 전용 Mixin.

EditModeMixin은 단독으로 인스턴스화하지 않으며,
ImageViewer가 다중 상속으로 사용한다.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Protocol, runtime_checkable

from PIL import Image
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
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
    QPainter,
    QPen,
    QPixmap,
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
from ui.edit_toolbar import EditToolbar
from ui.selection_item import SelectionItem
from ui.shape_item import ResizableShapeItem
from ui.text_item import TextShapeItem
from utils.debug import debug_print, error_print, warning_print

if TYPE_CHECKING:
    from PySide6.QtCore import SignalInstance
    from PySide6.QtWidgets import QGraphicsScene, QGraphicsPixmapItem
    from core.image_editor import ImageEditor
    from ui.edit_toolbar import EditToolbar
    from ui.selection_item import SelectionItem
    from ui.shape_item import ResizableShapeItem
    from ui.text_item import TextShapeItem
    from main_window import MainWindow         
    from ui.overlay_widget import OverlayWidget 


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
    line_style: int = 1   # Qt.PenStyle.SolidLine


@dataclass
class _EditSnap:
    pixmap: Optional[QPixmap]
    shapes: List[_ShapeSnap]


# ──────────────────────────────────────────────────────────────────────────────
# _ClipboardImageItem
# ──────────────────────────────────────────────────────────────────────────────

class _ClipboardImageItem(QGraphicsObject):
    """클립보드에서 붙여넣기된 이미지 — 씬 내 이동 가능한 레이어."""

    about_to_change:   Signal = Signal()         # type: ignore[assignment]
    properties_needed: Signal = Signal(object)   # type: ignore[assignment]

    HANDLE_SCREEN_PX: int = 9


    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        event.accept()


    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__()
        self._pixmap: QPixmap = pixmap
        self._rect:   QRectF  = QRectF(0.0, 0.0, float(pixmap.width()), float(pixmap.height()))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(55)
        self._notified: bool = False


    def boundingRect(self) -> QRectF:
        # 회전 핸들 없음 — HANDLE_SCREEN_PX margin만 사용
        m = self._s2i(float(self.HANDLE_SCREEN_PX) / 2.0 + 2)
        return self._rect.adjusted(-m, -m, m, m)


    def paint(self, painter: QPainter, option, widget=None) -> None:  # type: ignore[override]
        painter.drawPixmap(self._rect.toRect(), self._pixmap)
        if self.isSelected():
            inv = self._s2i(1.5)
            painter.setPen(QPen(QColor(74, 158, 255), inv, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._rect)

    def itemChange(
        self,
        change: QGraphicsItem.GraphicsItemChange,
        value: object,
    ) -> object:
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
                and not self._notified):
            self._notified = True
            self.about_to_change.emit()
        return super().itemChange(change, value)


    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._notified = False
        super().mouseReleaseEvent(event)


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

class EditModeMixin:
    """
    ImageViewer에 편집 모드 기능을 주입하는 Mixin.

    다중 상속 MRO:
        class ImageViewer(EditModeMixin, QGraphicsView): ...

    TYPE_CHECKING 블록 안에서 ImageViewer 전용 속성을 선언함으로써
    Pylance 가 self.xxx 참조를 정상 해석하도록 유도한다.
    런타임에는 QGraphicsView / ImageViewer 를 통해 실제로 제공된다.
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
        self._shape_preview_item:    Optional[object]      = None 
        
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
        self._edit_toolbar.setFixedHeight(90)
        self._connect_edit_toolbar()
        debug_print("EditToolbar 지연 초기화 완료")


    def _connect_edit_toolbar(self) -> None:
        tb = self._edit_toolbar
        assert tb is not None, "_connect_edit_toolbar: toolbar 미생성"

        tb.tool_changed.connect(self._on_edit_tool_changed)
        tb.crop_requested.connect(self._edit_crop)
        tb.copy_requested.connect(self._edit_copy)
        tb.mosaic_requested.connect(self._edit_mosaic)
        tb.resize_requested.connect(self._edit_resize)
        #    도형 → 드래그로 직접 그리기 (_commit_shape_from_drag)
        #    텍스트 → 클릭 위치에 삽입 (_edit_add_text_at)
        tb.apply_requested.connect(self._edit_apply)
        tb.cancel_requested.connect(self._edit_cancel)


    # ------------------------------------------------------------------
    # 편집 모드 진입 / 종료
    # ------------------------------------------------------------------

    def enter_edit_mode(self) -> None:
        current_pixmap: Optional[QPixmap] = self.current_pixmap  # type: ignore[attr-defined]
        if current_pixmap is None or current_pixmap.isNull():
            return

        # 편집 모드 진입 시 FullViewportUpdate로 전환 — 잔상 방지
        self.setViewportUpdateMode(                      # type: ignore[attr-defined]
            QGraphicsView.ViewportUpdateMode.FullViewportUpdate
        )

        self._edit_original_pixmap: QPixmap = QPixmap(current_pixmap)

        self._ensure_edit_toolbar()
        tb = self._edit_toolbar
        assert tb is not None

        ow = self.overlay_widget  # type: ignore[attr-defined]
        if ow is not None and ow.isVisible():   # ← hasattr 제거, 타입 덕분에 직접 호출
            ow.hide()
            self._overlay_was_visible = True
        else:
            self._overlay_was_visible = False

        self._editor    = ImageEditor(current_pixmap)
        self._selection = SelectionItem()

        self.graphics_scene.addItem(self._selection)  # type: ignore[attr-defined]

        w, h = self._editor.get_size()
        tb.set_image_size(w, h)

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
            mw.installEventFilter(self)     # type: ignore[arg-type]

        self.edit_mode_changed.emit(True)   # type: ignore[attr-defined]
        debug_print("편집 모드 진입")


    def _exit_edit_mode(self) -> None:
        self._cancel_shape_preview() 

        if self._selection:
            self.graphics_scene.removeItem(self._selection)
            self._selection = None

        for item in self._edit_shapes:
            self.graphics_scene.removeItem(item)
        self._edit_shapes.clear()

        self._drag_start_scene = None 
        self._editor    = None
        self._edit_mode = False

        self.setViewportUpdateMode(                      # type: ignore[attr-defined]
            QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate
        )

        if self._edit_toolbar:
            self._edit_toolbar.setVisible(False)

        self._clear_undo_history()

        if self.main_window:
            try:
                self.main_window.removeEventFilter(self)      # type: ignore[attr-defined]
            except Exception:
                pass

        if getattr(self, '_overlay_was_visible', False) and self.overlay_widget:
            self.overlay_widget.show()
        self._overlay_was_visible = False

        self.edit_mode_changed.emit(False)    # type: ignore[attr-defined]
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


    # ------------------------------------------------------------------
    # 편집 취소 / 적용
    # ------------------------------------------------------------------

    def _edit_cancel(self) -> None:
        ed = self._editor
        if ed is not None:
            original = ed.reset()
            if original is not None and not original.isNull():
                self._replace_pixmap_inplace(original)  # type: ignore[attr-defined]

        self._exit_edit_mode()


    def _edit_apply(self) -> None:
        ed = self._editor
        if ed is None:
            self._exit_edit_mode()
            return

        original_pixmap: QPixmap = ed.reset() 

        sel = self._selection
        if self._edit_tool == 'select' and sel is not None and sel.isVisible():
            self._edit_crop()

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
            self.zoom_mode   = 'actual'            # type: ignore[attr-defined]
            self.resetTransform()                  # type: ignore[attr-defined] 
            self.zoom_factor = 1.0                 # type: ignore[attr-defined]
            self._calculate_and_emit_zoom()        # type: ignore[attr-defined]
        elif pre_zoom_mode == 'manual':
            self.zoom_mode   = 'manual'            # type: ignore[attr-defined]
            self.zoom_factor = pre_zoom_factor     # type: ignore[attr-defined]
            self.resetTransform()                  # type: ignore[attr-defined]
            self.scale(pre_zoom_factor, pre_zoom_factor)    # type: ignore[attr-defined]
            self._calculate_and_emit_zoom()        # type: ignore[attr-defined]

        _weak_self = weakref.ref(self)
        self._lock_high_res_replace = True         # type: ignore[attr-defined]
        QTimer.singleShot(
            3000,
            lambda: (s := _weak_self()) and setattr(s, '_lock_high_res_replace', False)
        )

        # 원본도 함께 전달 — 저장 취소 시 MainWindow가 복원에 사용
        self.edit_save_requested.emit(final_pixmap)  # type: ignore[attr-defined]
        

    # ------------------------------------------------------------------
    # 씬 렌더링 (이미지 + 도형 합성)
    # ------------------------------------------------------------------

    def _render_scene_with_shapes(self) -> QPixmap:
        """QGraphicsScene(이미지 + 도형)을 원본 해상도로 렌더링."""
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

        iw, ih     = base_pixmap.width(), base_pixmap.height()
        scene_rect = QRectF(0.0, 0.0, float(iw), float(ih))

        result = QPixmap(iw, ih)
        result.fill(Qt.GlobalColor.white)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.graphics_scene.render(  # type: ignore[attr-defined]
            painter,
            QRectF(0.0, 0.0, float(iw), float(ih)),
            scene_rect,
        )
        painter.end()

        debug_print(f"_render_scene_with_shapes: {iw}×{ih}px 렌더링 완료")
        return result


    # ------------------------------------------------------------------
    # 편집 액션 (자르기 / 복사 / 리사이즈)
    # ------------------------------------------------------------------

    def _on_edit_tool_changed(self, tool: str) -> None:
        self._edit_tool = tool
        if self._selection:
            self._selection.setVisible(False)
        self._drag_start_scene = None
        self._cancel_shape_preview() 

        if tool.startswith('shape:') or tool == 'text':
            self.viewport().setCursor(    # type: ignore[attr-defined]
                Qt.CursorShape.CrossCursor
            )
        else:
            self.viewport().unsetCursor() # type: ignore[attr-defined]


    def _edit_crop(self) -> None:
        ed  = self._editor
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


    def _edit_copy(self) -> None:
        ed  = self._editor
        sel = self._selection
        if ed is None or sel is None or not sel.isVisible():
            return
        ed.copy_region_to_clipboard(sel.rect())
        # 범위 복사는 시스템 클립보드 이미지로 저장되므로
        # 도형 내부 클립보드를 비워 Ctrl+V 충돌 방지
        self._shape_clipboard = []


    def _do_mosaic_preview(self) -> None:
        """모자이크 실시간 미리보기 — _working 변경 없이 화면만 갱신 (빠른 경로)"""
        self._mosaic_timer_active = False  # type: ignore[attr-defined]
        if not getattr(self, '_mosaic_preview_pending', False):
            return
        self._mosaic_preview_pending = False  # type: ignore[attr-defined]

        if not self._editor or not self._selection or not self._selection.isVisible():
            return
        rect = self._selection.rect()
        if rect.width() < 5 or rect.height() < 5:
            return

        try:
            src = self._editor._working          # PIL Image (원본 보존)
            x,  y  = max(0, int(rect.x())),  max(0, int(rect.y()))
            w,  h  = int(rect.width()),       int(rect.height())
            iw, ih = src.size
            x2, y2 = min(x + w, iw),         min(y + h, ih)
            if x2 <= x or y2 <= y:
                return

            region = src.crop((x, y, x2, y2))
            block  = max(8, min(w, h) // 15)
            small  = region.resize(
                (max(1, (x2 - x) // block), max(1, (y2 - y) // block)),
                Image.Resampling.NEAREST
            )
            mosaic  = small.resize((x2 - x, y2 - y), Image.Resampling.NEAREST)
            preview = src.copy()
            preview.paste(mosaic, (x, y))

            # PIL → QPixmap: PNG 인코딩 없이 raw RGBA 직접 변환 (빠름)
            preview_rgba = preview.convert('RGBA')
            data = preview_rgba.tobytes('raw', 'RGBA')
            qimg = QImage(data, preview_rgba.width, preview_rgba.height,
                          QImage.Format.Format_RGBA8888)
            px = QPixmap.fromImage(qimg)

            if not px.isNull():
                # pixmap_item만 교체 — selection/undo/current_pixmap 건드리지 않음
                if self.pixmap_item:                    # type: ignore[attr-defined]
                    self.pixmap_item.setPixmap(px)      # type: ignore[attr-defined]
                    self.graphics_scene.update()        # type: ignore[attr-defined]
        except Exception:
            pass
        

    def _edit_mosaic(self) -> None:
        """선택 영역에 모자이크(픽셀화) 적용"""
        if not self._editor or not self._selection or not self._selection.isVisible():
            return
        rect = self._selection.rect()
        if rect.isEmpty():
            return
        self._push_undo()
        try:
            src = self._editor._working
            x,  y  = max(0, int(rect.x())),     max(0, int(rect.y()))
            w,  h  = int(rect.width()),          int(rect.height())
            iw, ih = src.size
            x2, y2 = min(x + w, iw),            min(y + h, ih)
            if x2 <= x or y2 <= y:
                return
            region = src.crop((x, y, x2, y2))
            block  = max(8, min(w, h) // 15)
            small  = region.resize(
                (max(1, (x2-x)//block), max(1, (y2-y)//block)),
                Image.Resampling.NEAREST
            )
            mosaic = small.resize((x2-x, y2-y), Image.Resampling.NEAREST)
            result = src.copy()
            result.paste(mosaic, (x, y))
            self._editor._working = result
            self.replace_pixmap(self._editor.get_preview())     # type: ignore[attr-defined]
            self._selection.setVisible(False)
            self._drag_start_scene = None
        except Exception as e:
            error_print(f"모자이크 처리 오류: {e}")


    def _edit_resize(self, w: int, h: int) -> None:
        ed = self._editor
        if ed is None:
            return
        tb = self._edit_toolbar
        assert tb is not None, "_edit_resize: toolbar 없음"

        self._push_undo()
        resized: QPixmap = ed.resize(w, h)
        self._replace_pixmap_inplace(resized)  # type: ignore[attr-defined]
        tb.set_image_size(w, h)


    # ------------------------------------------------------------------
    # 도형 / 텍스트 추가
    # ------------------------------------------------------------------

    def _edit_add_shape(
        self,
        shape_type: str,
        pen_color:  QColor,
        line_width: int,
        line_style: int,
    ) -> None:

        self._push_undo()

        pi = self.pixmap_item  # type: ignore[attr-defined]
        base = (
            max(200.0, min(*pi.boundingRect().size().toTuple()) * 0.20)
            if pi is not None else 200.0
        )

        vp   = self.viewport()                         # type: ignore[attr-defined]
        cx   = vp.width()  / 2.0
        cy   = vp.height() / 2.0
        sc   = self.mapToScene(int(cx), int(cy))       # type: ignore[attr-defined]
        rect = QRectF(sc.x() - base / 2.0, sc.y() - base * 0.375, base, base * 0.75)

        fill: Optional[QColor] = None
        if 'filled' in shape_type:
            fill = QColor(pen_color)
            fill.setAlpha(100)

        item = ResizableShapeItem(
            shape_type, rect,
            pen_color  = pen_color,
            fill_color = fill,
            line_width = line_width,
        )
        item.set_line_style(Qt.PenStyle(line_style))
        item.about_to_change.connect(self._push_undo)
        item.properties_needed.connect(self._on_shape_properties)
        self.graphics_scene.addItem(item)              # type: ignore[attr-defined]
        self._edit_shapes.append(item)
        item.setSelected(True)


    def _edit_add_text(
        self,
        font_family: str,
        font_size:   int,
        color:       QColor,
        bold:        bool,
        italic:      bool,
    ) -> None:

        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is not None and font_size == 40:
            font_size = max(20, int(pi.boundingRect().height() * 0.04))

        item = TextShapeItem(
            text        = "텍스트",
            font_family = font_family,
            font_size   = font_size,
            color       = color,
            bold        = bold,
            italic      = italic,
        )

        if not self._show_text_dialog(item):
            return

        self._push_undo()
        vp = self.viewport()                                    # type: ignore[attr-defined]
        cx = vp.width()  / 2.0
        cy = vp.height() / 2.0
        sc = self.mapToScene(int(cx), int(cy))                  # type: ignore[attr-defined]
        item.setPos(
            sc.x() - item._rect.width()  / 2.0,
            sc.y() - item._rect.height() / 2.0,
        )
        item.about_to_change.connect(self._push_undo)
        item.properties_needed.connect(self._on_shape_properties)
        self.graphics_scene.addItem(item)                       # type: ignore[attr-defined]
        self._edit_shapes.append(item)
        item.setSelected(True)


    # ------------------------------------------------------------------
    # 텍스트 다이얼로그
    # ------------------------------------------------------------------

    def _show_text_dialog(self, item: object) -> bool:
        """텍스트 입력 다이얼로그. OK → True, Cancel → False."""
        dlg = QDialog(self.window())  # type: ignore[attr-defined]
        dlg.setWindowTitle("텍스트 입력")
        dlg.setMinimumWidth(420)

        layout = QVBoxLayout(dlg)
        row1   = QHBoxLayout()

        font_cb   = QFontComboBox()
        font_cb.setCurrentFont(QFont(item._font_family))         # type: ignore[attr-defined]
        size_spin = QSpinBox()
        size_spin.setRange(8, 500)
        size_spin.setValue(item._font_size)                      # type: ignore[attr-defined]
        size_spin.setSuffix("px")

        btn_bold   = QPushButton("B")
        btn_bold.setCheckable(True)
        btn_bold.setChecked(item._bold)                          # type: ignore[attr-defined]
        btn_bold.setFixedSize(28, 28)

        btn_italic = QPushButton("I")
        btn_italic.setCheckable(True)
        btn_italic.setChecked(item._italic)                      # type: ignore[attr-defined]
        btn_italic.setFixedSize(28, 28)

        _color: List[QColor] = [QColor(item._color)]             # type: ignore[attr-defined]
        btn_color = QPushButton("■ 색상")

        def _update() -> None:
            lum = _color[0].lightness()
            btn_color.setStyleSheet(
                f"background:{_color[0].name()};"
                f"color:{'#000' if lum > 128 else '#fff'};"
            )
        _update()

        btn_color.clicked.connect(lambda: [                      # type: ignore[union-attr]
            _color.__setitem__(0, c) or _update()
            for c in [QColorDialog.getColor(_color[0], dlg)]
            if c.isValid()
        ])

        row1.addWidget(font_cb, 1)
        row1.addWidget(size_spin)
        row1.addWidget(btn_bold)
        row1.addWidget(btn_italic)
        row1.addWidget(btn_color)
        layout.addLayout(row1)

        layout.addWidget(QLabel("텍스트 내용 (줄 바꿈 지원):"))
        text_edit = QTextEdit()
        text_edit.setPlainText(item._text)                       # type: ignore[attr-defined]
        text_edit.setMinimumHeight(80)
        layout.addWidget(text_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False

        item.update_properties(                                  # type: ignore[attr-defined]
            text        = text_edit.toPlainText() or "텍스트",
            font_family = font_cb.currentFont().family(),
            font_size   = size_spin.value(),
            color       = _color[0],
            bold        = btn_bold.isChecked(),
            italic      = btn_italic.isChecked(),
        )
        return True


    def _edit_text_item(self, item: object) -> None:
        """기존 텍스트 아이템 속성 편집 다이얼로그."""
        dlg = QDialog(self.window())  # type: ignore[attr-defined]
        dlg.setWindowTitle("텍스트 편집")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet("""
            QDialog   { background:#2a2a2a; color:#ddd; }
            QLabel    { color:#ddd; background:transparent; }
            QTextEdit { background:#1e1e1e; color:#eee; border:1px solid #555;
                        border-radius:3px; padding:4px; }
            QPushButton { background:#3a3a3a; color:#ddd; border:1px solid #555;
                          padding:4px 10px; border-radius:3px; }
            QPushButton:hover   { background:#4a4a4a; }
            QPushButton:checked { background:#4a9eff; color:#fff; }
            QFontComboBox, QSpinBox { background:#1e1e1e; color:#eee;
                                      border:1px solid #555; padding:2px; }
        """)

        layout = QVBoxLayout(dlg)
        row1   = QHBoxLayout()

        font_cb   = QFontComboBox()
        font_cb.setCurrentFont(QFont(item._font_family))         # type: ignore[attr-defined]
        size_spin = QSpinBox()
        size_spin.setRange(8, 500)
        size_spin.setValue(item._font_size)                      # type: ignore[attr-defined]
        size_spin.setSuffix("px")

        btn_bold   = QPushButton("B")
        btn_bold.setCheckable(True)
        btn_bold.setChecked(item._bold)                          # type: ignore[attr-defined]
        btn_bold.setFixedSize(28, 28)

        btn_italic = QPushButton("I")
        btn_italic.setCheckable(True)
        btn_italic.setChecked(item._italic)                      # type: ignore[attr-defined]
        btn_italic.setFixedSize(28, 28)

        _color: List[QColor] = [QColor(item._color)]             # type: ignore[attr-defined]
        btn_color = QPushButton("■ 색상")

        def _update_cbtn() -> None:
            lum = _color[0].lightness()
            btn_color.setStyleSheet(
                f"QPushButton{{background:{_color[0].name()};"
                f"color:{'#000' if lum > 128 else '#fff'};"
                f"border:1px solid #555;padding:4px 10px;border-radius:3px;}}"
            )
        _update_cbtn()

        def _pick() -> None:
            c = QColorDialog.getColor(_color[0], dlg, "색상")
            if c.isValid():
                _color[0] = c
                _update_cbtn()

        btn_color.clicked.connect(_pick)

        row1.addWidget(font_cb, 1)
        row1.addWidget(size_spin)
        row1.addWidget(btn_bold)
        row1.addWidget(btn_italic)
        row1.addWidget(btn_color)
        layout.addLayout(row1)

        layout.addWidget(QLabel("텍스트 내용 (줄 바꿈 지원):"))
        text_edit = QTextEdit()
        text_edit.setPlainText(item._text)                       # type: ignore[attr-defined]
        text_edit.setMinimumHeight(100)
        layout.addWidget(text_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._push_undo()
            item.update_properties(                              # type: ignore[attr-defined]
                text        = text_edit.toPlainText() or "텍스트",
                font_family = font_cb.currentFont().family(),
                font_size   = size_spin.value(),
                color       = _color[0],
                bold        = btn_bold.isChecked(),
                italic      = btn_italic.isChecked(),
            )


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
            if isinstance(it, (ResizableShapeItem, TextShapeItem, _ClipboardImageItem)) and it.isSelected()
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


    # ------------------------------------------------------------------
    # 키 이벤트 필터
    # ------------------------------------------------------------------

    def eventFilter(self, obj: object, event: object) -> bool:
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
                Qt.Key.Key_Delete,  Qt.Key.Key_Backspace,
                Qt.Key.Key_Left,    Qt.Key.Key_Right,
                Qt.Key.Key_Up,      Qt.Key.Key_Down,
                Qt.Key.Key_PageUp,  Qt.Key.Key_PageDown,
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
                # _shape_clipboard에 뭔가 복사됐으면 시스템 클립보드 이미지를 지워서
                # Ctrl+V 시 범위 이미지가 우선되는 충돌을 방지
                if self._shape_clipboard:
                    QApplication.clipboard().clear()
                return True

            if ctrl and key == Qt.Key.Key_V:
                if self._shape_clipboard:
                    # 도형/텍스트 내부 복사가 있으면 최우선 붙여넣기
                    self._paste_shapes()
                else:
                    # 도형 복사가 없을 때만 시스템 클립보드 이미지 확인
                    cb_pixmap = QApplication.clipboard().pixmap()
                    if not cb_pixmap.isNull():
                        self._paste_clipboard_image(cb_pixmap)
                return True

            nav_keys = {
                Qt.Key.Key_Left,   Qt.Key.Key_Right,
                Qt.Key.Key_Up,     Qt.Key.Key_Down,
                Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                Qt.Key.Key_Home,   Qt.Key.Key_End,
            }
            if key in nav_keys:
                return True

        return False
    

    def _begin_shape_preview(self, start_scene: QPointF) -> None:
        """드래그 중 표시할 반투명 미리보기 아이템 생성"""

        tb  = self._edit_toolbar
        if tb is None:
            return

        shape_type = self._edit_tool.split(':', 1)[1]  # 'shape:rect' → 'rect'
        pen_color  = tb.current_pen_color()
        line_width = tb.current_line_width()
        line_style = tb.current_line_style()

        fill: Optional[QColor] = None
        if 'filled' in shape_type:
            fill = QColor(pen_color)
            fill.setAlpha(100)

        item = ResizableShapeItem(
            shape_type, QRectF(start_scene, start_scene),
            pen_color=pen_color, fill_color=fill, line_width=line_width,
        )
        item.set_line_style(line_style)
        item.setOpacity(0.7)        # 미리보기는 반투명
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.graphics_scene.addItem(item)   # type: ignore[attr-defined]
        self._shape_preview_item = item


    def _cancel_shape_preview(self) -> None:
        """미리보기 아이템 제거"""
        item = getattr(self, '_shape_preview_item', None)
        if item is not None:
            self.graphics_scene.removeItem(item)  # type: ignore[attr-defined]
        self._shape_preview_item = None


    def _commit_shape_from_drag(self, rect: QRectF) -> None:
        """드래그 완료 → 실제 도형 확정 삽입"""

        tb = self._edit_toolbar
        if tb is None:
            return

        shape_type = self._edit_tool.split(':', 1)[1]
        pen_color  = tb.current_pen_color()
        line_width = tb.current_line_width()
        line_style = tb.current_line_style()

        fill: Optional[QColor] = None
        if 'filled' in shape_type:
            fill = QColor(pen_color)
            fill.setAlpha(100)

        self._push_undo()
        item = ResizableShapeItem(
            shape_type, rect,
            pen_color=pen_color, fill_color=fill, line_width=line_width,
        )
        item.set_line_style(line_style)
        item.about_to_change.connect(self._push_undo)
        item.properties_needed.connect(self._on_shape_properties)
        self.graphics_scene.addItem(item)   # type: ignore[attr-defined]
        self._edit_shapes.append(item)
        item.setSelected(True)


    def _edit_add_text_at(self, scene_pos: QPointF) -> None:

        pi = self.pixmap_item  # type: ignore[attr-defined]
        font_size = 40
        if pi is not None:
            font_size = max(20, int(pi.boundingRect().height() * 0.04))

        item = TextShapeItem(
            text='',
            font_family='맑은 고딕',
            font_size=font_size,
            color=QColor(255, 50, 50),
        )

        if not self._show_text_dialog(item):

            self._on_edit_tool_changed('select')
            if self._edit_toolbar:
                self._edit_toolbar.reset_area_buttons()
            return

        self._push_undo()
        item.setPos(
            scene_pos.x() - item._rect.width()  / 2.0,
            scene_pos.y() - item._rect.height() / 2.0,
        )
        item.about_to_change.connect(self._push_undo)
        item.properties_needed.connect(self._on_shape_properties)
        self.graphics_scene.addItem(item)   # type: ignore[attr-defined]
        self._edit_shapes.append(item)
        item.setSelected(True)

        self._on_edit_tool_changed('select')
        if self._edit_toolbar:
            self._edit_toolbar.reset_area_buttons()