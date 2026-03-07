# -*- coding: utf-8 -*-
# ui/image_viewer.py

import math
import time
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

import numpy as np
from PIL import Image as PILImage

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QThread,
    QTimer,
    Signal,
)

from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QImage,
    QMouseEvent,
    QMovie,
    QPainter,
    QPalette,
    QPixmap,
    QSurfaceFormat,
    QWheelEvent,
)

from PySide6.QtOpenGLWidgets import QOpenGLWidget

from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
)

from core.cache_manager import CacheManager
from ui.animated_items import (
    AnimatedGraphicsItem,
    ApngDecodeWorker,
    LoadingOverlay,
    WebPAnimatedItem,
    WebPDecodeWorker,
)
from ui.edit_mode_mixin import EditModeMixin, _ClipboardImageItem
from ui.shape_item import ResizableShapeItem
from ui.text_item import TextShapeItem
from ui.minimap_widget import MiniMapWidget

from utils.config_manager import ConfigManager
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t

if TYPE_CHECKING:
    from main_window import MainWindow
    from ui.overlay_widget import OverlayWidget
    from ui.minimap_widget import MiniMapWidget


class ImageViewer(EditModeMixin, QGraphicsView):
    """이미지 뷰어 위젯"""
    
    zoom_changed = Signal(float)
    file_dropped = Signal(Path)
    wheel_navigation = Signal(int)
    edit_mode_changed   = Signal(bool) 

    edit_save_requested = Signal(QPixmap)

    toggle_metadata_requested = Signal(bool)
    toggle_thumbnail_requested = Signal(bool)
    toggle_statusbar_requested = Signal(bool)
    toggle_overlay_requested = Signal(bool)
    settings_requested = Signal()
    
    delete_file_requested = Signal()
    copy_file_requested = Signal()
    cut_file_requested = Signal()
    paste_file_requested = Signal()    
    open_location_requested = Signal()
    
    toggle_highlight_requested = Signal()
    delete_highlighted_requested = Signal()
    copy_highlighted_requested = Signal()
    cut_highlighted_requested = Signal()
    
    view_gps_requested = Signal()

    rename_file_requested = Signal() 
    clear_highlights_requested = Signal()

    MINIMAP_UPDATE_DELAY = 50  # ms
    overlay_refresh_requested = Signal()

# ============================================
# 초기화 및 설정
# ============================================

    def __init__(self, cache_manager: CacheManager, config_manager: ConfigManager, parent=None, use_opengl: bool = True) -> None:
        super().__init__(parent)  

        self._suppress_fit_in_view: bool  = False
        self._suppress_start_ms:    float = 0.0
        self._zoom_intent_stack:    list  = [] 

        self._init_edit_mode()

        self.cache_manager = cache_manager
        self.main_window: Optional['MainWindow'] = None
        self.current_is_highlighted = False
        
        self.wheel_timer = QTimer()
        self.wheel_timer.setSingleShot(True)
        self.wheel_timer.setInterval(200)

        # 스레드 안전성 추가
        self._state_lock = RLock()
        
        self._pending_timers: list = []
        
        # 이미지 로딩 상태
        self._loading_image = False
        self._loading_timer_id: Optional[int] = None

        # 이미지 전환 상태 추가
        self._transition_in_progress = False
        self._pending_image: Optional[QPixmap] = None

        # OpenGL 설정 적용
        self.config_manager = config_manager

        # 렌더링 품질 설정
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Scene 설정
        self.graphics_scene = QGraphicsScene()
        self.setScene(self.graphics_scene)

        # 이미지 아이템
        self.pixmap_item: Optional[QGraphicsPixmapItem] = None
        self.current_pixmap: Optional[QPixmap] = None
        self.current_movie: Optional[QMovie] = None
        self.original_pixmap_size: Tuple[int, int] = (0, 0)
        
        # 이미지 고유 ID (타이머 검증용)
        self.current_image_id: int = 0
        
        # 줌 설정
        self.zoom_mode = 'fit'
        self.zoom_factor = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 10.0
        self._user_has_zoomed: bool = False
        
        # 드래그 설정
        self.is_dragging = False
        self.last_mouse_pos: Optional[QPoint] = None

        # 줌 적용 딜레이 타이머
        self.zoom_apply_timer = QTimer()
        self.zoom_apply_timer.setSingleShot(True)
        self.zoom_apply_timer.timeout.connect(self._delayed_apply_zoom)
        
        # 오버레이 딜레이 타이머
        self.overlay_timer = QTimer(self)
        self.overlay_timer.setSingleShot(True)
        self.overlay_timer.timeout.connect(self._apply_overlay)
        
        # 타입 힌트 명확히: Tuple[Path, Dict[str, Any], int]
        self.pending_overlay_data: Optional[Tuple[Path, Dict[str, Any], int]] = None
        
        # 오버레이 위젯 참조
        self.overlay_widget: Optional['OverlayWidget'] = None

        # View 최적화
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        
        # 배경색
        self.setBackgroundBrush(Qt.GlobalColor.black)

        if use_opengl:
            self._setup_opengl() 
        else:
            debug_print("소프트웨어 렌더링 사용 (secondary viewer)")

        # OpenGL viewport는 autoFillBackground가 기본 False
        self.viewport().setAutoFillBackground(True)
        palette = self.viewport().palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
        self.viewport().setPalette(palette)

        # 스크롤바
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("""
            /* 세로 스크롤바 */
            QScrollBar:vertical {
                width: 6px;
                background: transparent;
                margin: 0px;
            }

            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.18);
                border-radius: 3px;
                min-height: 30px;
            }

            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.30);
            }

            QScrollBar::handle:vertical:pressed {
                background: rgba(74, 158, 255, 0.60);
            }

            /* 가로 스크롤바 */
            QScrollBar:horizontal {
                height: 6px;
                background: transparent;
                margin: 0px;
            }

            QScrollBar::handle:horizontal {
                background: rgba(255, 255, 255, 0.18);
                border-radius: 3px;
                min-width: 30px;
            }

            QScrollBar::handle:horizontal:hover {
                background: rgba(255, 255, 255, 0.30);
            }

            QScrollBar::handle:horizontal:pressed {
                background: rgba(74, 158, 255, 0.60);
            }

            /* 버튼 제거 */
            QScrollBar::add-line,
            QScrollBar::sub-line {
                width: 0px;
                height: 0px;
                border: none;
                background: none;
            }

            /* 페이지 영역 제거 */
            QScrollBar::add-page,
            QScrollBar::sub-page {
                background: none;
            }
        """)

        # 드래그 모드
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        
        # 드래그 앤 드롭
        self.setAcceptDrops(True)
        debug_print(f"드래그앤드롭 활성화됨")

        # UI 가시성 상태
        self.metadata_visible = True
        self.thumbnail_visible = True
        self.statusbar_visible = True

        # GPS 정보
        self.current_gps: Optional[tuple] = None

        # 캐시 모드 설정
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        
        # 최적화 플래그
        self.setOptimizationFlags(
            QGraphicsView.OptimizationFlag.DontSavePainterState
        )        

        # 자동 스크롤 관련 변수 추가
        self.auto_scroll_active = False
        self.auto_scroll_origin = QPoint()
        self.auto_scroll_timer = QTimer()
        self.auto_scroll_timer.timeout.connect(self._auto_scroll)
        self.auto_scroll_timer.setInterval(16)  # ~60 FPS

        self.minimap = MiniMapWidget(self)
        self.minimap.position_clicked.connect(self._on_minimap_clicked)
        self._position_minimap()  # 우측 하단 배치

        # 미니맵 업데이트 타이머
        self.minimap_update_timer = QTimer()
        self.minimap_update_timer.setSingleShot(True)
        self.minimap_update_timer.timeout.connect(self._update_minimap)

        self.horizontalScrollBar().valueChanged.connect(self._on_scrollbar_changed)
        self.verticalScrollBar().valueChanged.connect(self._on_scrollbar_changed)

        self._webp_workers: list = [] 
        self._loading_overlay = LoadingOverlay(self)


    # 히스토리
    def _replace_pixmap_inplace(self, pixmap: QPixmap) -> None:
        if not self.pixmap_item or pixmap.isNull():
            return

        self.pixmap_item.setPixmap(pixmap)
        self.pixmap_item.setPos(0, 0)
        self.current_pixmap       = pixmap
        self.original_pixmap_size = (pixmap.width(), pixmap.height())
        self.graphics_scene.setSceneRect(
            QRectF(0, 0, pixmap.width(), pixmap.height())
        )

        # 선택 영역이 새 이미지 범위를 벗어나지 않도록 초기화
        if self._selection and self._selection.isVisible():
            self._selection.setVisible(False)

        self.graphics_scene.update()


    def keyPressEvent(self, event) -> None:
        """편집 모드 키 처리는 eventFilter(mainwindow)가 전담.
        여기서는 네비게이션 키 차단만 수행.
        """
        if self._edit_mode:
            nav_keys = {
                Qt.Key.Key_Left,   Qt.Key.Key_Right,
                Qt.Key.Key_Up,     Qt.Key.Key_Down,
                Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                Qt.Key.Key_Home,   Qt.Key.Key_End,
            }
            if event.key() in nav_keys:
                event.accept()
                return
            # 편집 모드 단축키(Del/Ctrl+Z/C/V)는 eventFilter에서 처리

        super().keyPressEvent(event)


    def set_main_window(self, main_window) -> None:
        """메인 윈도우 참조 설정"""
        self.main_window = main_window


    def set_overlay_widget(self, overlay_widget) -> None:
        """오버레이 위젯 참조 설정"""
        self.overlay_widget = overlay_widget
        debug_print(f"오버레이 위젯 연결됨")


    def set_gps_info(self, lat: Optional[float], lon: Optional[float]) -> None:
        """현재 이미지의 GPS 정보 설정"""
        if lat is not None and lon is not None:
            self.current_gps = (lat, lon)
        else:
            self.current_gps = None


    def set_highlight_state(self, is_highlighted: bool) -> None:
        """현재 하이라이트 상태 설정"""
        self.current_is_highlighted = is_highlighted


# ============================================
# 이미지 설정 (메인 로직)
# ============================================

    def _auto_zoom_mode(self, img_w: int, img_h: int) -> str:
        use_auto = self.config_manager.get('viewer.auto_zoom_on_small', True)
        if not use_auto:
            return 'fit'

        # 사용자가 수동 줌 중이면 그대로 유지
        if self.zoom_mode == 'manual':
            debug_print("_auto_zoom_mode: 수동 줌 상태 → manual 유지")
            return 'manual'

        # 풀스크린에서는 항상 fit
        if getattr(self, '_fullscreen_mode', False):
            debug_print(
                f"_auto_zoom_mode: 이미지 {img_w}×{img_h} / "
                f"뷰포트 {self.viewport().width()}×{self.viewport().height()} → fit (풀스크린 강제)"
            )
            return 'fit'

        # 사용자가 아직 휠 줌을 한 적 없으면 → 무조건 fit 유지
        # actual 모드가 자동으로 드래그를 활성화하는 문제 방지
        if not self._user_has_zoomed:
            debug_print(
                f"_auto_zoom_mode: 이미지 {img_w}×{img_h} / "
                f"뷰포트 {self.viewport().width()}×{self.viewport().height()} → fit (미줌 상태)"
            )
            return 'fit'

        vp_w = self.viewport().width()
        vp_h = self.viewport().height()

        if self.config_manager.get('viewer.auto_zoom_diagonal', False):
            fits = math.hypot(img_w, img_h) <= math.hypot(vp_w, vp_h) * 0.95
        else:
            fits = (img_w <= vp_w * 0.95 and img_h <= vp_h * 0.95)

        mode = 'actual' if fits else 'fit'
        debug_print(
            f"_auto_zoom_mode: 이미지 {img_w}×{img_h} / "
            f"뷰포트 {vp_w}×{vp_h} → {mode}"
            + (" (대각선)" if self.config_manager.get('viewer.auto_zoom_diagonal', False)
            else " (가로·세로)")
        )
        return mode


    def set_fullscreen_mode(self, enabled: bool) -> None:
        self._fullscreen_mode = enabled
        

    def _consume_zoom_intent(self) -> Optional[str]:
        """줌 intent 스택에서 꺼냄. 없으면 None 반환."""
        if self._zoom_intent_stack:
            return self._zoom_intent_stack.pop()
        return None


    def _on_fit_key(self) -> None:
        """F키 — fit 모드 명시적 전환 (manual 리셋)"""
        self._user_has_zoomed = False
        self.zoom_mode = 'fit'
        self._fit_in_view()


    def set_image(self, pixmap: QPixmap) -> None:
        """정적 이미지 설정 - 깜빡임 없는 전환"""
        
        debug_print(f"\n{'='*50}")
        debug_print(f"[SET_IMAGE] 시작")

        self._stop_webp_workers()

        if self._transition_in_progress:
            self._pending_image = pixmap
            debug_print("[SET_IMAGE] 전환 중 - 대기 이미지 저장")
            return
        
        self._transition_in_progress = True
        self._pending_image = None
        
        self._stop_timers_only()
        self._loading_image = True
        new_image_id = id(pixmap)
        self._loading_timer_id = new_image_id
        
        # ===== 애니메이션 정리 =====
        if self.current_movie:
            self.current_movie.stop()
            try:
                self.current_movie.frameChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.current_movie.deleteLater()
            self.current_movie = None
        
        # ===== 기존 아이템 참조 저장 =====
        old_item = self.pixmap_item

        new_item = QGraphicsPixmapItem(pixmap)
        new_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        new_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        new_item.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)

        # ===== Scene rect 먼저 교체 (OpenGL 클리어 타이밍) =====
        self.graphics_scene.setSceneRect(QRectF(pixmap.rect()))

        # ===== 새 아이템 추가 =====
        self.graphics_scene.addItem(new_item)
        self.pixmap_item = new_item

        # ===== 기존 아이템 즉시 제거 (고아 방지) =====
        if old_item:
            if isinstance(old_item, (AnimatedGraphicsItem, WebPAnimatedItem)):
                old_item.cleanup()
            self.graphics_scene.removeItem(old_item)
            # old_item 참조 해제 (GC 허용)
            del old_item

        # ===== 상태 갱신 =====
        self.current_image_id = new_image_id
        self.current_pixmap = pixmap
        self.original_pixmap_size = (pixmap.width(), pixmap.height())
        
        self.resetTransform()
        self.zoom_factor = 1.0
        self._user_has_zoomed = False
        self.zoom_mode = 'fit'

        fw, fh = pixmap.width(), pixmap.height()
        vp     = self.viewport()
        if fw > 0 and fh > 0 and fw <= vp.width() and fh <= vp.height():
            intent = 'actual'
        else:
            intent = 'fit'

        self.zoom_mode          = intent
        self._zoom_intent_stack = [intent]

        if intent == 'actual':
            self._user_has_zoomed      = True  
            self._suppress_fit_in_view = True
            self._suppress_start_ms    = time.monotonic()
            self.resetTransform()
            self.zoom_factor = 1.0
            self._calculate_and_emit_zoom()
        else:
            self._user_has_zoomed      = False
            self._suppress_fit_in_view = False
            self._fit_in_view()

        self.zoom_apply_timer.start(50)
        
        self.is_dragging = False
        self.last_mouse_pos = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        
        if hasattr(self, 'minimap'):
            self.minimap.set_thumbnail(pixmap)
            self.minimap.hide()
        
        item_count = len(self.graphics_scene.items())
        if item_count > 1:
            debug_print(f"[SET_IMAGE] ⚠️ 씬 아이템 수 이상: {item_count}개")

        self.graphics_scene.update()
        self.viewport().update()

        debug_print(f"[SET_IMAGE] ✅ 완료")
        debug_print(f"{'='*50}\n")
        
        self._transition_in_progress = False
        
        if self._pending_image:
            pending = self._pending_image
            self._pending_image = None
            debug_print("[SET_IMAGE] 대기 이미지 처리")
            QTimer.singleShot(0, lambda: self.set_image(pending))
        else:
            QTimer.singleShot(50, lambda: self._unlock_resize(new_image_id))


    def set_rotation_preview(self, pixmap: QPixmap) -> None:
        """회전 미리보기 전용 — zoom/scroll 유지하면서 픽스맵만 교체.
        
        set_image()는 zoom_mode='fit'을 강제하므로 회전 시 사용 불가.
        이 메서드는 현재 zoom_mode와 zoom_factor를 보존한다.
        """
        if pixmap is None or pixmap.isNull():
            return
        if not self.pixmap_item:
            # pixmap_item이 없으면 일반 set_image로 폴백
            self.set_image(pixmap)
            return

        saved_zoom_factor = self.zoom_factor
        saved_zoom_mode = self.zoom_mode

        # 픽스맵 교체 (Scene 재초기화 없음)
        self.pixmap_item.setPixmap(pixmap)
        self.current_pixmap = pixmap
        self.original_pixmap_size = (pixmap.width(), pixmap.height())
        self.graphics_scene.setSceneRect(QRectF(pixmap.rect()))

        # zoom 모드 복원
        self.zoom_mode = saved_zoom_mode
        if saved_zoom_mode == 'fit':
            self._fit_in_view()
        elif saved_zoom_mode == 'actual':
            self.resetTransform()
            self.zoom_factor = 1.0
            self._calculate_and_emit_zoom()
        elif saved_zoom_mode in ('manual', 'width'):
            self.resetTransform()
            self.scale(saved_zoom_factor, saved_zoom_factor)
            self._calculate_and_emit_zoom()

        self.graphics_scene.update()
        debug_print(f"[set_rotation_preview] zoom_mode={saved_zoom_mode}, factor={saved_zoom_factor:.3f}")


    def _stop_webp_workers(self) -> None:
        """진행 중인 WebP 워커 전부 종료 요청 + 오버레이 즉시 제거"""
        if hasattr(self, '_loading_overlay'):
            self._loading_overlay.stop()
        if hasattr(self, '_webp_workers'):
            for w in self._webp_workers:
                if w.isRunning():
                    w.quit() 


    def _stop_timers_only(self) -> None:
        for name in ['zoom_apply_timer', 'overlay_timer',
                    'minimap_update_timer', 'wheel_timer', 'auto_scroll_timer']:
            timer = getattr(self, name, None)
            if timer and timer.isActive():
                timer.stop()

    def _cleanup_all_timers(self) -> None:
        self._stop_timers_only()  
        for timer in self._pending_timers:
            if timer and timer.isActive():
                timer.stop()
            try: timer.deleteLater()
            except: pass
        self._pending_timers.clear()


    def set_animated_image(self, movie: QMovie, file_path: Optional[Path] = None) -> None:
        """애니메이션 이미지 설정

        분기 전략:
        1) WebP + 품질 모드 → Pillow 사전 디코딩 (백그라운드, 로딩 오버레이 표시)
        2) WebP + 고속 모드 → QMovie (즉시 재생)
        3) GIF / APNG       → QMovie (즉시 재생)
        """
        if self._transition_in_progress:
            warning_print("애니메이션 설정: 전환 중 - 무시")
            return

        self._transition_in_progress = True
        self._user_has_zoomed = False
        self._stop_timers_only()
        self.pending_overlay_data = None

        new_image_id = id(movie)
        self.current_image_id = new_image_id

        # ── 기존 리소스 정리 ──────────────────────────────────────────
        if self.current_movie:
            self.current_movie.stop()
            try:
                self.current_movie.frameChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.current_movie.deleteLater()
            self.current_movie = None

        old_item = self.pixmap_item
        if old_item:
            if isinstance(old_item, (AnimatedGraphicsItem, WebPAnimatedItem)):
                old_item.cleanup()

        # ── 설정값 읽기 ───────────────────────────────────────────────
        anim_cfg       = self.config_manager.get('animation', {})
        scale_quality: str = anim_cfg.get('scale_quality', 'high')
        cache_mode: bool   = anim_cfg.get('cache_mode', True)
        webp_mode: str     = anim_cfg.get('webp_mode', 'quality')  # 'fast' | 'quality'

        # ── 분기 결정 ────────────────────────────────────────────────
        is_webp = (file_path is not None and file_path.suffix.lower() == '.webp')
        use_pillow = is_webp and (webp_mode == 'quality')

        # ════════════════════════════════════════════════════════════
        # 경로 A: WebP 품질 모드 — Pillow 백그라운드 디코딩
        # ════════════════════════════════════════════════════════════
        if use_pillow:
            assert file_path is not None  # mypy/Pylance 만족용

            # 1) 첫 프레임 즉시 정적 표시 (set_image 내부에서 _stop_webp_workers 호출되지만
            #    오버레이는 아직 꺼진 상태라 무해)
            from core.image_loader import ImageLoader
            first_static = ImageLoader().load(file_path)
            if first_static and not first_static.isNull():
                self._transition_in_progress = False
                self.set_image(first_static)
                self._transition_in_progress = True

                fw, fh = first_static.width(), first_static.height()
                vp = self.viewport()
                if fw <= vp.width() and fh <= vp.height():
                    self._user_has_zoomed      = True
                    self._suppress_fit_in_view = True
                    self._suppress_start_ms    = time.monotonic()
                    self.zoom_mode             = 'actual'
                    self._zoom_intent_stack    = ['actual'] 
                    self.resetTransform()
                    self.zoom_factor = 1.0
                    self._calculate_and_emit_zoom()

            # 2) set_image() 이후 snap_id 캡처 (current_image_id가 갱신된 이후)
            snap_id = self.current_image_id

            # 3) 로딩 오버레이 시작 (set_image() 완료 이후라 _stop_webp_workers에 의해 꺼지지 않음)
            self._loading_overlay.start()

            # 4) 기존 진행 중 워커 종료 요청
            for w in self._webp_workers:
                if w.isRunning():
                    w.quit()

            # 5) 콜백 정의
            def _on_webp_done(frames: list, delays: list) -> None:
                self._loading_overlay.stop()
                if self.current_image_id != snap_id:
                    debug_print("WebP 워커 결과 무시 (이미지 변경됨)")
                    return
                item = WebPAnimatedItem(frames, delays)
                old = self.pixmap_item
                self.pixmap_item = item
                self.graphics_scene.addItem(item)
                item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
                if old:
                    self.graphics_scene.removeItem(old)
                # current_pixmap 동기화 (get_current_pixmap / 미니맵 정확성 보장)
                self.current_pixmap = frames[0]
                if hasattr(self, 'minimap'):
                    self.minimap.set_thumbnail(frames[0])
                    if hasattr(self, 'minimap_update_timer'):
                        self.minimap_update_timer.start(self.MINIMAP_UPDATE_DELAY)
                self.graphics_scene.update()
                debug_print(f"WebP 품질 모드 재생 시작: {len(frames)}프레임")

            def _on_webp_failed() -> None:
                self._loading_overlay.stop()
                debug_print("WebP 사전 디코딩 실패 → 정적 유지")

            # 6) 워커 시작
            worker = WebPDecodeWorker(file_path)
            worker.decode_finished.connect(_on_webp_done)
            worker.decode_failed.connect(_on_webp_failed)
            worker.finished.connect(lambda: self._cleanup_worker(worker))
            worker.start()
            self._webp_workers.append(worker)

            self._transition_in_progress = False
            self.zoom_apply_timer.start(50)
            return 

        # ════════════════════════════════════════════════════════════
        # 경로 B: QMovie — WebP 고속 모드 / GIF / APNG 모두 이 경로
        # ════════════════════════════════════════════════════════════
        from core.image_loader import ImageLoader
        movie = ImageLoader().configure_movie(
            movie,
            viewport_size=(self.viewport().width(), self.viewport().height()),
            scale_quality=scale_quality,
            cache_mode=cache_mode,
        )
        self.current_movie = movie
        # current_pixmap 리셋: 남아있으면 _delayed_apply_zoom()의 id 검증 실패 → 줌 무효
        self.current_pixmap = None

        # jumpToFrame(0): start() 전 currentPixmap()이 null인 경우 방지
        movie.jumpToFrame(0)
        first_pixmap = movie.currentPixmap()

        self.original_pixmap_size = (
            (first_pixmap.width(), first_pixmap.height())
            if not first_pixmap.isNull() else (0, 0)
        )

        new_item = AnimatedGraphicsItem(movie)
        self.pixmap_item = new_item
        self.graphics_scene.addItem(self.pixmap_item)
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)

        if old_item:
            self.graphics_scene.removeItem(old_item)

        self.graphics_scene.setSceneRect(
            QRectF(first_pixmap.rect()) if not first_pixmap.isNull()
            else self.graphics_scene.sceneRect()
        )

        # 미니맵 썸네일 갱신
        if hasattr(self, 'minimap'):
            if not first_pixmap.isNull():
                self.minimap.set_thumbnail(first_pixmap)
            self.minimap.hide()

        fw = first_pixmap.width()  if not first_pixmap.isNull() else 0
        fh = first_pixmap.height() if not first_pixmap.isNull() else 0

        if fw > 0 and fh > 0:
            vp = self.viewport()
            if fw <= vp.width() and fh <= vp.height():
                intent = 'actual'
            else:
                intent = 'fit'
        else:
            intent = 'fit'

        self.zoom_mode          = intent
        self._zoom_intent_stack = [intent]

        if intent == 'actual':
            self._user_has_zoomed      = True
            self._suppress_fit_in_view = True            
            self._suppress_start_ms    = time.monotonic()
            self.resetTransform()
            self.zoom_factor = 1.0
            self._calculate_and_emit_zoom()
        else:
            self._user_has_zoomed      = False
            self._suppress_fit_in_view = False
            self.resetTransform()
            self.zoom_factor = 1.0
            self._fit_in_view()

        self.is_dragging    = False
        self.last_mouse_pos = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.graphics_scene.update()
        self._transition_in_progress = False
        self.zoom_apply_timer.start(50)
        debug_print(f"QMovie 경로 완료 (is_webp={is_webp}, mode={webp_mode}), ID={new_image_id}")


    def set_apng_image(self, file_path: Path) -> None:
        """APNG 파일 Pillow 디코딩 재생.
        set_animated_image() WebP quality 모드와 동일 패턴."""

        if self._transition_in_progress:
            warning_print("set_apng_image - transition 중 무시")
            return
        self._transition_in_progress = True

        self._stop_timers_only()
        self.pending_overlay_data = None

        # current_image_id 설정 (워커 staleness 검사용)
        new_image_id = id(file_path)
        self.current_image_id = new_image_id

        # 기존 movie 정리
        if self.current_movie:
            self.current_movie.stop()
            try:
                self.current_movie.frameChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.current_movie.deleteLater()
            self.current_movie = None

        # 기존 animated item 정리
        old_item = self.pixmap_item
        if old_item and isinstance(old_item, (AnimatedGraphicsItem, WebPAnimatedItem)):
            old_item.cleanup()

        # 첫 프레임 즉시 표시
        # ImageLoader.load()는 APNG에 None 반환 → Pillow 직접 디코딩
        first_pixmap = QPixmap()
        try:
            with PILImage.open(str(file_path)) as _img:
                _img.seek(0)
                _frame = _img.convert('RGBA')
                _arr = np.ascontiguousarray(np.array(_frame))
                _h, _w = _arr.shape[:2]
                _qimg = QImage(_arr.tobytes(), _w, _h, _w * 4,
                            QImage.Format.Format_RGBA8888)
                first_pixmap = QPixmap.fromImage(_qimg)
        except Exception as e:
            error_print(f"APNG 첫 프레임 로드 실패: {e}")

        if not first_pixmap.isNull():
            self._transition_in_progress = False
            self.set_image(first_pixmap)
            self._transition_in_progress = True

            fw, fh = first_pixmap.width(), first_pixmap.height()
            vp = self.viewport()
            if fw <= vp.width() and fh <= vp.height():
                self._user_has_zoomed      = True
                self._suppress_fit_in_view = True
                self._suppress_start_ms    = time.monotonic()
                self.zoom_mode             = 'actual'
                self._zoom_intent_stack    = ['actual'] 
                self.resetTransform()
                self.zoom_factor = 1.0
                self._calculate_and_emit_zoom()

            snap_id = self.current_image_id
        else:
            snap_id = new_image_id

        # 로딩 오버레이 + 기존 WebP 워커 정리
        self._loading_overlay.start()
        for _w in self._webp_workers:
            if _w.isRunning():
                _w.quit()

        # 백그라운드 디코딩 워커
        worker = ApngDecodeWorker(file_path)

        def _on_done(frames: list, delays: list) -> None:
            self._loading_overlay.stop()
            if self.current_image_id != snap_id:
                debug_print("APNG 워커 결과 무시 (이미지 변경됨)")
                return
            if not frames:
                debug_print("APNG 디코딩 결과 없음 → 정적 첫 프레임 유지")
                return

            # WebPAnimatedItem 재사용 (프레임 + 딜레이 구조 동일)
            item = WebPAnimatedItem(frames, delays)
            old = self.pixmap_item
            self.pixmap_item = item
            self.graphics_scene.addItem(item)
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            if old:
                self.graphics_scene.removeItem(old)

            self.current_pixmap = frames[0]
            self.graphics_scene.setSceneRect(QRectF(frames[0].rect()))

            if hasattr(self, 'minimap'):
                self.minimap.set_thumbnail(frames[0])
                self.minimap.hide()
            if hasattr(self, 'minimap_update_timer'):
                self.minimap_update_timer.start(self.MINIMAP_UPDATE_DELAY)

            self.graphics_scene.update()
            debug_print(f"APNG 재생: {len(frames)}프레임 / {file_path.name}")

        def _on_failed() -> None:
            self._loading_overlay.stop()
            error_print(f"APNG 디코딩 실패: {file_path.name}")

        worker.decode_finished.connect(_on_done)
        worker.decode_failed.connect(_on_failed)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        worker.start()
        self._webp_workers.append(worker)

        self._transition_in_progress = False
        self.zoom_apply_timer.start(50)   # set_animated_image()와 동일


    def _cleanup_worker(self, worker: QThread) -> None:
        # WebPDecodeWorker, _ApngDecodeWorker 모두 수용
        try:
            self._webp_workers.remove(worker)
        except ValueError:
            pass
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        debug_print(f"워커 정리 완료, 남은 워커: {len(self._webp_workers)}")


    def replace_pixmap(self, pixmap: QPixmap) -> None:
        if self._transition_in_progress:
            return
        if not self.pixmap_item:
            warning_print("replace_pixmap: pixmap_item 없음")
            return
        if pixmap.isNull():
            error_print("replace_pixmap: 픽스맵 없음")
            return
        if self.current_pixmap:

            if pixmap.width() != self.current_pixmap.width() or \
            pixmap.height() != self.current_pixmap.height():
                if getattr(self, 'lock_highres_replace', False):
                    debug_print("replace_pixmap: lock → 무시")
                    return

                new_w, new_h = pixmap.width(), pixmap.height()
                cur_w, cur_h = self.current_pixmap.width(), self.current_pixmap.height()

                # 고해상도 업그레이드 허용 조건
                # 1) 새 이미지가 현재보다 크거나 같음 (양방향 모두)
                # 2) 종횡비가 5% 이내 일치 (회전/크롭 아님을 보장)
                is_larger  = new_w >= cur_w and new_h >= cur_h
                cur_ratio  = cur_w / max(cur_h, 1)
                new_ratio  = new_w / max(new_h, 1)
                ratio_ok   = abs(cur_ratio - new_ratio) / max(cur_ratio, 0.001) < 0.05

                if is_larger and ratio_ok:
                    debug_print(
                        f"replace_pixmap: 고해상도 업그레이드 허용 "
                        f"({new_w}×{new_h} ← {cur_w}×{cur_h})"
                    )
                    self.original_pixmap_size = (new_w, new_h) 
                else:
                    warning_print(
                        f"replace_pixmap: 크기 불일치 - 무시 "
                        f"({new_w}×{new_h} vs {cur_w}×{cur_h})"
                    )
                    return

        debug_print(f"replace_pixmap: {pixmap.width()}x{pixmap.height()}")

        # 스크롤 위치 저장
        h_scroll = self.horizontalScrollBar().value()
        v_scroll = self.verticalScrollBar().value()
        h_max    = self.horizontalScrollBar().maximum()
        v_max    = self.verticalScrollBar().maximum()
        h_ratio  = h_scroll / h_max if h_max > 0 else 0.0
        v_ratio  = v_scroll / v_max if v_max > 0 else 0.0

        current_zoom = self.zoom_factor
        current_mode = self.zoom_mode

        self.current_pixmap = pixmap
        self.pixmap_item.setPixmap(pixmap)
        self.graphics_scene.setSceneRect(QRectF(pixmap.rect()))

        # _load_zoom_intent 우선 적용 (set_image에서 결정한 실제 의도)
        intent = self._consume_zoom_intent()
        target_mode = intent if intent else current_mode

        debug_print(f"replace_pixmap: intent={intent}, target={target_mode}")

        if target_mode == 'actual':
            self.resetTransform()
            self.zoom_factor = 1.0
            self.zoom_mode   = 'actual'
            self._calculate_and_emit_zoom()
        elif target_mode == 'fit':
            self._fit_in_view()
        elif target_mode == 'width':
            self._fit_width()
        elif target_mode == 'manual':
            self.resetTransform()
            self.scale(current_zoom, current_zoom)
            self._calculate_and_emit_zoom()

        if h_ratio > 0 or v_ratio > 0:
            QTimer.singleShot(
                30, lambda: self._restore_scroll_position(h_ratio, v_ratio)
            )
        self._update_cursor()

        self.graphics_scene.update()
        self.viewport().update() 

        debug_print("replace_pixmap 완료")


    def clear(self) -> None:
        """이미지 초기화"""
        self._stop_webp_workers()
        self._transition_in_progress = False
        self._pending_image = None
        self._user_has_zoomed = False
        self._stop_timers_only()
        
        # ===== 기존 애니메이션 정리 =====
        if self.current_movie:
            self.current_movie.stop()
            try:
                self.current_movie.frameChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.current_movie.deleteLater()
            self.current_movie = None
        
        # ===== pixmap_item 명시적 정리 (핵심 수정) =====
        if self.pixmap_item:
            if isinstance(self.pixmap_item, (AnimatedGraphicsItem, WebPAnimatedItem)):
                self.pixmap_item.cleanup()
            
            # C++ 객체가 살아있을 때만 제거
            try:
                self.graphics_scene.removeItem(self.pixmap_item)
            except RuntimeError:
                pass  # 이미 clear()나 다른 경로에서 삭제된 경우
            
            # Python 래퍼 참조 해제 (RuntimeError 방지)
            self.pixmap_item = None
        
        # ===== Scene 클리어 (안전) =====
        self.graphics_scene.clear()
        
        # ===== 상태 초기화 =====
        self.current_pixmap   = None
        self.current_gps      = None
        self.current_image_id = 0
        self.original_pixmap_size = (0, 0)
        self.zoom_mode = 'fit'
        self.zoom_factor = 1.0
        
        debug_print("✅ ImageViewer 완전 초기화 완료")


# ============================================
# 줌 제어
# ============================================

    def set_zoom_mode(self, mode: str) -> None:
        debug_print(f"set_zoom_mode: {self.zoom_mode} → {mode}")

        self.zoom_mode = mode

        if mode == 'fit':
            self._user_has_zoomed = False 
            self._fit_in_view()
        elif mode == 'actual':
            self._user_has_zoomed = True  
            self._actual_size()
        elif mode == 'width':
            self._user_has_zoomed = True
            self._fit_width()

        self._update_cursor()
        

    def zoom_in(self) -> None:
        self._user_has_zoomed = True
        self.zoom_factor = min(self.zoom_factor * 1.2, self.max_zoom)
        self.zoom_mode = 'manual' 
        
        self.resetTransform()
        self.scale(self.zoom_factor, self.zoom_factor)
        self._update_cursor()
        
        self._calculate_and_emit_zoom()
        debug_print(f"줌 인: {self.zoom_factor:.2f}, mode=manual")


    def zoom_out(self) -> None:
        new_factor = self.zoom_factor / 1.2

        # manual 모드(줌인 이력 있음)에서만 fit 복귀 체크
        if getattr(self, '_user_has_zoomed', False) and self.pixmap_item:
            vp = self.viewport().rect()
            pr = self.pixmap_item.boundingRect()
            if pr.width() > 0 and pr.height() > 0:
                fit_scale = min(vp.width() / pr.width(), vp.height() / pr.height())
                already_at_floor = (self.zoom_factor <= self.min_zoom)
                should_fit = (new_factor <= fit_scale) or already_at_floor

                if should_fit:
                    self._user_has_zoomed = False
                    self.zoom_mode = 'fit'
                    self._fit_in_view()
                    self._update_cursor()
                    debug_print(
                        f"줌 아웃: fit 복귀 "
                        f"(new={new_factor:.3f}, fit_scale={fit_scale:.3f}, "
                        f"at_floor={already_at_floor})"
                    )
                    return

        # min_zoom 클램프 제거 → 이미지가 뷰포트보다 작아도 되고,
        # 스크롤바가 생기지 않으므로 _update_cursor가 Arrow를 반환
        ABSOLUTE_MIN = 0.01
        self.zoom_factor = max(new_factor, ABSOLUTE_MIN)
        self.zoom_mode = 'manual'
        self.resetTransform()
        self.scale(self.zoom_factor, self.zoom_factor)
        self._update_cursor()
        self._calculate_and_emit_zoom()
        debug_print(f"줌 아웃: {self.zoom_factor:.3f}, mode=manual")


    def _fit_in_view(self) -> None:
        if getattr(self, '_suppress_fit_in_view', False):
            elapsed = time.monotonic() - getattr(self, '_suppress_start_ms', 0.0)
            if elapsed < 0.15:
                debug_print("_fit_in_view: suppress 중 → 건너뜀")
                return
            else:
                debug_print("_fit_in_view: suppress 타임아웃(150ms) → 강제 해제")
                self._suppress_fit_in_view = False

        if not self.pixmap_item:
            return

        viewport_rect = self.viewport().rect()
        pixmap_rect   = self.pixmap_item.boundingRect()

        if pixmap_rect.width() == 0 or pixmap_rect.height() == 0:
            return

        scale_x = viewport_rect.width()  / pixmap_rect.width()
        scale_y = viewport_rect.height() / pixmap_rect.height()
        scale   = min(scale_x, scale_y)

        if getattr(self, '_user_has_zoomed', False):
            scale = max(scale, self.min_zoom)

        self.resetTransform()
        self.scale(scale, scale)
        self.zoom_factor = scale
        self.centerOn(self.pixmap_item)
        self._update_cursor()
        self._calculate_and_emit_zoom()
        debug_print(f"Fit: scale={scale:.2f}")


    def _actual_size(self) -> None:
        """실제 크기 (1:1)"""
        self.resetTransform()
        self.scale(1.0, 1.0)
        
        # zoom_factor 업데이트
        self.zoom_factor = 1.0
        
        if self.pixmap_item:
            self.centerOn(self.pixmap_item)
        
        # 커서 업데이트
        self._update_cursor()

        #self.zoom_changed.emit(1.0)
        self._calculate_and_emit_zoom()
        
        debug_print(f"Actual Size: 1.0")


    def _fit_width(self) -> None:
        """이미지 폭을 뷰포트에 맞춤"""
        if not self.pixmap_item:
            return
        
        viewport_rect = self.viewport().rect()
        pixmap_rect = self.pixmap_item.boundingRect()
        
        if pixmap_rect.width() == 0:
            return
        
        scale = viewport_rect.width() / pixmap_rect.width()
        scale = max(scale, self.min_zoom)
        
        self.resetTransform()
        self.scale(scale, scale)
        
        # zoom_factor 업데이트
        self.zoom_factor = scale
        
        # 가로 중앙, 세로 상단
        self.centerOn(self.pixmap_item.boundingRect().center().x(), 0)
        
        # 커서 업데이트
        self._update_cursor()
            
        self._calculate_and_emit_zoom()
        
        debug_print(f"Fit Width: scale={scale:.2f}")


    def _apply_zoom_mode(self) -> None:
        """현재 줌 모드 적용 (즉시 적용 - 수동 줌용)"""
        if not self.pixmap_item:
            return
        
        if self.zoom_mode == 'fit':
            self._fit_in_view()
        elif self.zoom_mode == 'actual':
            self._actual_size()
        elif self.zoom_mode == 'width':
            self._fit_width()
        
        self._update_cursor()
        debug_print(f"줌 모드 즉시 적용: {self.zoom_mode}, zoom_factor={self.zoom_factor:.2f}")


    def _delayed_apply_zoom(self) -> None:
        """딜레이 후 줌 적용 (2단계 렌더링 완료 후)"""
        # suppress 플래그 반드시 해제
        self._suppress_fit_in_view = False
        self._suppress_start_ms    = 0.0

        if not self.pixmap_item:
            warning_print("[WARN] pixmap_item 없음, 줌 적용 불가")
            return

        # 이미지 ID 검증
        if self.current_pixmap:
            if id(self.current_pixmap) != self.current_image_id:
                warning_print("줌 타이머 무효 (이미지 변경됨)")
                return
        elif self.current_movie:
            if id(self.current_movie) != self.current_image_id:
                warning_print("줌 타이머 무효 (애니메이션 변경됨)")
                return

        # intent 스택 우선 소비, 없으면 zoom_mode fallback
        intent = self._consume_zoom_intent() or self.zoom_mode

        if intent == 'fit' and getattr(self, '_user_has_zoomed', False):
            debug_print(f"줌 적용 스킵: _user_has_zoomed=True (actual 모드 유지)")
            return
    
        debug_print(f"줌 적용: intent={intent}, ID={self.current_image_id}")

        if intent == 'actual':
            self._actual_size()
        elif intent == 'fit':
            self._fit_in_view()
        elif intent == 'width':
            self._fit_width()
        elif intent == 'manual':
            self.resetTransform()
            self.scale(self.zoom_factor, self.zoom_factor)
            self._calculate_and_emit_zoom()

        self._update_cursor()
        debug_print(f"줌 적용 완료: zoom_factor={self.zoom_factor:.2f}")


    def _calculate_and_emit_zoom(self) -> None:
        """현재 줌 레벨 계산 및 시그널 발생"""
        if not self.pixmap_item:
            return
        
        # Transform 행렬에서 스케일 추출
        transform = self.transform()
        scale_x = transform.m11()
        scale_y = transform.m22()
        
        # 평균 스케일 (보통 x, y가 동일)
        actual_zoom = (scale_x + scale_y) / 2.0
        
        self.zoom_factor = actual_zoom
        self.zoom_changed.emit(actual_zoom)
        debug_print(f"줌 레벨: {actual_zoom:.3f}x ({actual_zoom*100:.1f}%)")


# ============================================
# 커서 및 캐시 관리
# ============================================

    def _update_cursor(self) -> None:
        if getattr(self, '_edit_mode', False):
            if getattr(self, 'is_dragging', False):
                return
            has_scrollbars = (
                self.horizontalScrollBar().isVisible() or
                self.verticalScrollBar().isVisible()
            )
            if self._edit_tool and (
                self._edit_tool.startswith('shape:') or self._edit_tool == 'text'
            ):
                self.viewport().setCursor(Qt.CursorShape.CrossCursor)
            elif has_scrollbars:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        # 일반 모드 (기존 코드 그대로)
        has_scrollbars = (
            self.horizontalScrollBar().isVisible() or
            self.verticalScrollBar().isVisible()
        )
        if has_scrollbars:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if self.pixmap_item:
                self.pixmap_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if self.pixmap_item:
                self.pixmap_item.setCacheMode(
                    QGraphicsItem.CacheMode.DeviceCoordinateCache
                )


    def _update_cache_mode(self) -> None:
        if not self.pixmap_item:
            return

        has_scrollbars = (
            self.horizontalScrollBar().isVisible() or
            self.verticalScrollBar().isVisible()
        )

        if has_scrollbars:
            self.pixmap_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
            debug_print("[CACHE] 캐시 비활성화 (스크롤바 있음)")
        else:
            self.pixmap_item.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
            debug_print("[CACHE] 캐시 활성화 (스크롤바 없음)")            
            

# ============================================
# 이벤트 핸들러 (마우스/휠)
# ============================================

    def wheelEvent(self, event: QWheelEvent) -> None:
        """휠 이벤트 - 줌, 스크롤, 또는 이미지 전환"""
        delta = event.angleDelta().y()
        
        # Ctrl + 휠 = 줌
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            debug_print(f"Ctrl+휠 → 줌 ({'+' if delta > 0 else '-'})")
            
            # 수동 줌 모드로 전환
            if self.zoom_mode != 'manual':
                self.zoom_mode = 'manual'
            
            # 줌 인/아웃
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            
            # 미니맵 업데이트 (줌 변경 후)
            if hasattr(self, 'minimap_update_timer'):
                self.minimap_update_timer.start(self.MINIMAP_UPDATE_DELAY)
            
            event.accept()
            return
        
        # 스크롤바가 있으면 기본 스크롤 동작
        has_scrollbars = (
            self.horizontalScrollBar().isVisible() or 
            self.verticalScrollBar().isVisible()
        )
        
        if has_scrollbars:
            super().wheelEvent(event)
            
            # 스크롤 후 미니맵 업데이트
            if hasattr(self, 'minimap_update_timer'):
                self.minimap_update_timer.start(self.MINIMAP_UPDATE_DELAY)
            
            event.accept()
            return
        
        # main_window 및 navigator 확인
        if not self.main_window or not hasattr(self.main_window, 'navigator'):
            warning_print(f"main_window 또는 navigator 없음")
            event.ignore()
            return
        
        # 타이머가 작동 중이면 무시 (연속 스크롤 방지)
        if self.wheel_timer.isActive():
            event.accept()
            return
        
        navigator = self.main_window.navigator
        
        # 휠 위/아래로 이미지 이동
        # 편집 모드 중에는 네비게이션 완전 차단
        if self._edit_mode:
            # 줌/패닝 용도로만 동작하도록 기본 스크롤 처리
            super().wheelEvent(event)
            return

        if delta > 0:
            # 이전 이미지
            if navigator.has_prev():
                debug_print(f"휠 ↑ → 이전 이미지")
                navigator.previous()
                self.wheel_timer.start()
                
                if hasattr(self, 'minimap_update_timer'):
                    # 이미지 로딩 시간 고려하여 더 긴 딜레이
                    self.minimap_update_timer.start(self.MINIMAP_UPDATE_DELAY * 3)
        else:
            # 다음 이미지
            if navigator.has_next():
                debug_print(f"휠 ↓ → 다음 이미지")
                navigator.next()
                self.wheel_timer.start()
                
                if hasattr(self, 'minimap_update_timer'):
                    self.minimap_update_timer.start(self.MINIMAP_UPDATE_DELAY * 3)
        
        event.accept()


    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._edit_mode:

            if self._edit_tool == 'ai_erase':
                if self._mask_item is not None:
                    self._mask_item.reset_stroke()      # type: ignore[attr-defined]
                    sp = self.mapToScene(event.pos())
                    if event.button() == Qt.MouseButton.LeftButton:
                        self._mask_item.draw_at(sp)     # type: ignore[attr-defined]
                    elif event.button() == Qt.MouseButton.RightButton:
                        self._mask_item.erase_at(sp)        # type: ignore[attr-defined]
                event.accept()
                return

            if event.button() == Qt.MouseButton.LeftButton:

                if self._edit_tool in ('crop_select', 'copy_select', 'mosaic_select'):
                    if self._selection:
                        self._selection.setVisible(False)
                    self.graphics_scene.clearSelection()
                    self._drag_start_scene = self.mapToScene(event.pos())
                    event.accept()

                elif self._edit_tool == 'select':
                    hit      = self.itemAt(event.pos())
                    is_shape = isinstance(
                        hit, (ResizableShapeItem, TextShapeItem, _ClipboardImageItem)
                    )
                    if is_shape:
                        super().mousePressEvent(event)
                    else:
                        self.graphics_scene.clearSelection()
                        has_scrollbars = (
                            self.horizontalScrollBar().isVisible() or
                            self.verticalScrollBar().isVisible()
                        )
                        if has_scrollbars:
                            self.is_dragging    = True
                            self.last_mouse_pos = event.pos()
                            self.setCursor(Qt.CursorShape.ClosedHandCursor)
                            event.accept()

                elif self._edit_tool == 'shapes':
                    handled = self._handle_shape_text_event(event, QEvent.Type.MouseButtonPress)
                    if not handled: 
                        hit = self.itemAt(event.pos())
                        is_shape = isinstance(hit, (ResizableShapeItem, TextShapeItem, _ClipboardImageItem))
                        if is_shape:
                            super().mousePressEvent(event)
                        else:
                            self.graphics_scene.clearSelection()
                            event.accept()
                    else:
                        event.accept()

                else:
                    super().mousePressEvent(event)
            return

        # ── 일반 모드 ──────────────────────────────────────────────────
        if event.button() == Qt.MouseButton.MiddleButton:
            has_scrollbars = (
                self.horizontalScrollBar().isVisible() or
                self.verticalScrollBar().isVisible()
            )
            if has_scrollbars:
                if not self.auto_scroll_active:
                    self.auto_scroll_active = True
                    self.auto_scroll_origin = event.pos()
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    self.auto_scroll_timer.start()
                else:
                    self._stop_auto_scroll()
                event.accept()
                return

        if self.auto_scroll_active and event.button() != Qt.MouseButton.MiddleButton:
            self._stop_auto_scroll()

        if event.button() == Qt.MouseButton.LeftButton:
            has_scrollbars = (
                self.horizontalScrollBar().isVisible() or
                self.verticalScrollBar().isVisible()
            )
            if has_scrollbars:
                self.is_dragging    = True
                self.last_mouse_pos = event.pos()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
            else:
                super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)


    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._edit_mode:

            if self.is_dragging and self.last_mouse_pos is not None:
                delta = event.pos() - self.last_mouse_pos
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - delta.x()
                )
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - delta.y()
                )
                self.last_mouse_pos = event.pos()
                if hasattr(self, 'minimap_update_timer'):
                    self.minimap_update_timer.start(self.MINIMAP_UPDATE_DELAY)
                event.accept()
                return

            if (self._edit_tool in ('crop_select', 'copy_select', 'mosaic_select')
                    and self._drag_start_scene is not None):
                cur  = self.mapToScene(event.pos())
                rect = QRectF(self._drag_start_scene, cur).normalized()
                if self._selection:
                    self._selection.set_rect(rect)
                    self._selection.setVisible(True)
                if self._edit_tool == 'mosaic_select':
                    self._mosaic_preview_pending = True
                    if not getattr(self, '_mosaic_timer_active', False):
                        self._mosaic_timer_active = True
                        QTimer.singleShot(50, self._do_mosaic_preview)
                event.accept()
                return

            elif self._edit_tool == 'shapes':
                handled = self._handle_shape_text_event(event, QEvent.Type.MouseMove)
                if not handled:
                    super().mouseMoveEvent(event) 
                else:
                    event.accept()
                return

            elif self._edit_tool == 'ai_erase' and self._mask_item is not None:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self._mask_item.draw_at(self.mapToScene(event.pos()))       # type: ignore[attr-defined]
                    event.accept()
                    return
                elif event.buttons() & Qt.MouseButton.RightButton:
                    self._mask_item.erase_at(self.mapToScene(event.pos()))      # type: ignore[attr-defined]
                    event.accept()
                    return
                self._mask_item.reset_stroke()      # type: ignore[attr-defined]

            super().mouseMoveEvent(event)
            return

        # ── 일반 모드 ──────────────────────────────────────────────────
        if self.auto_scroll_active:
            event.accept()
            return

        if self.is_dragging and self.last_mouse_pos:
            delta = event.pos() - self.last_mouse_pos
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            self.last_mouse_pos = event.pos()
            if hasattr(self, 'minimap'):
                self._update_minimap()
            event.accept()
        else:
            super().mouseMoveEvent(event)


    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._edit_mode:

            if self._edit_tool == 'ai_erase':
                if self._mask_item is not None:
                    self._mask_item.reset_stroke()      # type: ignore[attr-defined]
                event.accept()
                return

            if (event.button() == Qt.MouseButton.LeftButton
                    and self.is_dragging):
                self.is_dragging    = False
                self.last_mouse_pos = None
                has_scrollbars = (
                    self.horizontalScrollBar().isVisible() or
                    self.verticalScrollBar().isVisible()
                )
                self.setCursor(
                    Qt.CursorShape.OpenHandCursor
                    if has_scrollbars else Qt.CursorShape.ArrowCursor
                )
                event.accept()
                return

            if (self._edit_tool in ('crop_select', 'copy_select', 'mosaic_select')
                    and self._drag_start_scene is not None):
                self._drag_start_scene = None
                tool = self._edit_tool
                super().mouseReleaseEvent(event)
                if tool == 'crop_select':     self._edit_crop()
                elif tool == 'copy_select':   self._edit_copy()
                elif tool == 'mosaic_select': self._edit_mosaic()
                self._on_edit_tool_changed('select')
                if self._edit_toolbar:
                    self._edit_toolbar.reset_area_buttons()
                return

            elif self._edit_tool == 'shapes':
                handled = self._handle_shape_text_event(event, QEvent.Type.MouseButtonRelease)
                if not handled:
                    super().mouseReleaseEvent(event)
                else:
                    event.accept()
                return

            super().mouseReleaseEvent(event)
            return

        # ── 일반 모드 ──
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            if self.is_dragging:
                self.is_dragging = False
                self.last_mouse_pos = None
                self._update_cursor()
                event.accept()
                return
        super().mouseReleaseEvent(event) 


    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if not self.main_window:
            return

        if not hasattr(self.main_window, 'create_context_menu'):
            warning_print(f"[WARN] create_context_menu 메소드 없음")
            return
        
        menu = self.main_window.create_context_menu(self)
        if menu:
            menu.exec(event.globalPos())


    def _default_shape_rect(self) -> QRectF:
        """클릭만 했을 때 기본 도형 크기 — 뷰포트 기준 20%"""
        pi   = self.pixmap_item
        base = (
            max(200.0, min(*pi.boundingRect().size().toTuple()) * 0.20)
            if pi is not None else 200.0
        )
        vp = self.viewport()
        sc = self.mapToScene(int(vp.width() / 2), int(vp.height() / 2))
        return QRectF(sc.x() - base / 2.0, sc.y() - base * 0.375, base, base * 0.75)


# ============================================
# 드래그 앤 드롭
# ============================================

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """드래그 진입"""
        debug_print(f"ImageViewer.dragEnterEvent 호출")
        
        mime_data = event.mimeData()
        
        if not mime_data.hasUrls():
            debug_print(f"URL 데이터 없음 - 거부")
            event.ignore()
            return
        
        urls = mime_data.urls()
        debug_print(f"URL 개수: {len(urls)}")
        
        if not urls:
            debug_print(f"URL 리스트 비어있음 - 거부")
            event.ignore()
            return
        
        path = Path(urls[0].toLocalFile())
        debug_print(f"첫 번째 경로: {path}")
        debug_print(f"존재 여부: {path.exists()}")
        
        if path.exists() and (path.is_file() or path.is_dir()):
            debug_print(f"드래그 허용")
            event.acceptProposedAction()
        else:
            debug_print(f"유효하지 않은 경로 - 거부")
            event.ignore()


    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """드래그 이동"""
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()


    def dropEvent(self, event: QDropEvent) -> None:
        """드롭"""
        debug_print(f"ImageViewer.dropEvent 호출")
        
        mime_data = event.mimeData()
        
        if not mime_data.hasUrls():
            debug_print(f"URL 데이터 없음 - 거부")
            event.ignore()
            return
        
        urls = mime_data.urls()
        debug_print(f"드롭된 URL 개수: {len(urls)}")
        
        if not urls:
            debug_print(f"URL 리스트 비어있음 - 거부")
            event.ignore()
            return
        
        path = Path(urls[0].toLocalFile())
        debug_print(f"드롭된 경로: {path}")
        debug_print(f"존재 여부: {path.exists()}")
        
        if path.exists():
            debug_print(f"file_dropped 시그널 발생")
            self.file_dropped.emit(path)
            event.acceptProposedAction()
            debug_print(f"드롭 이벤트 수락됨")
        else:
            error_print(f"경로가 존재하지 않음 - 거부")
            event.ignore()


# ============================================
# 자동 스크롤
# ============================================

    def _auto_scroll(self) -> None:
        """자동 스크롤 처리 (타이머 콜백)"""
        if not self.auto_scroll_active:
            return
        
        # 현재 마우스 위치
        current_pos = self.mapFromGlobal(self.cursor().pos())
        
        # 기준점과의 거리 계산
        delta = current_pos - self.auto_scroll_origin
        
        # 거리에 비례한 스크롤 속도 (최대 20픽셀/프레임)
        speed_factor = 0.2
        scroll_x = int(delta.x() * speed_factor)
        scroll_y = int(delta.y() * speed_factor)
        
        # 데드존 (중심에서 5픽셀 이내는 무시)
        dead_zone = 5
        if abs(delta.x()) < dead_zone:
            scroll_x = 0
        if abs(delta.y()) < dead_zone:
            scroll_y = 0
        
        # 스크롤 적용
        if scroll_x != 0:
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() + scroll_x
            )
        if scroll_y != 0:
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() + scroll_y
            )
        
        if hasattr(self, 'minimap') and (scroll_x != 0 or scroll_y != 0):
            self._update_minimap()


    def _stop_auto_scroll(self) -> None:
        """자동 스크롤 종료"""
        self.auto_scroll_active = False
        self.auto_scroll_timer.stop()
        self._update_cursor()
        debug_print(f"[DEBUG] 자동 스크롤 종료")


# ============================================
# 미니맵
# ============================================

    def _update_minimap(self) -> None:
        """미니맵 업데이트 (현재 보이는 영역 표시)"""
        if not self.pixmap_item:
            self.minimap.hide()
            return

        # current_pixmap(정적) 또는 current_movie(애니메이션) 중 유효한 것으로 크기 결정
        if self.current_pixmap and not self.current_pixmap.isNull():
            image_width  = self.current_pixmap.width()
            image_height = self.current_pixmap.height()
        elif self.current_movie:
            ref_pixmap = self.current_movie.currentPixmap()
            if ref_pixmap.isNull():
                self.minimap.hide()
                return
            image_width  = ref_pixmap.width()
            image_height = ref_pixmap.height()
        else:
            self.minimap.hide()
            return

        # 전체화면
        # if self.main_window and getattr(self.main_window, 'is_fullscreen', False):
        #     self.minimap.hide()
        #     return

        # 스크롤바 없음
        has_scrollbars = (
            self.horizontalScrollBar().isVisible() or
            self.verticalScrollBar().isVisible()
        )
        if not has_scrollbars:
            self.minimap.hide()
            return

        if image_width == 0 or image_height == 0:
            self.minimap.hide()
            return

        # 현재 보이는 영역 (scene 좌표)
        visible_scene_rect = self.mapToScene(self.viewport().rect()).boundingRect()

        # 비율로 변환 (0.0 ~ 1.0)
        ratio_x = visible_scene_rect.x() / image_width
        ratio_y = visible_scene_rect.y() / image_height
        ratio_w = visible_scene_rect.width()  / image_width
        ratio_h = visible_scene_rect.height() / image_height

        # 범위 제한
        ratio_x = max(0.0, min(1.0, ratio_x))
        ratio_y = max(0.0, min(1.0, ratio_y))
        ratio_w = max(0.0, min(1.0 - ratio_x, ratio_w))
        ratio_h = max(0.0, min(1.0 - ratio_y, ratio_h))

        self._position_minimap()
        self.minimap.set_visible_rect(QRectF(ratio_x, ratio_y, ratio_w, ratio_h))
        self.minimap.show()
        self.minimap.raise_()


    def _on_minimap_clicked(self, ratio_x: float, ratio_y: float) -> None:
        px = self.get_current_pixmap()
        if not px or px.isNull():
            return
        image_width = px.width()
        image_height = px.height()
        
        # 클릭한 위치를 scene 좌표로 변환
        scene_x = ratio_x * image_width
        scene_y = ratio_y * image_height
        
        # 해당 위치를 중앙으로
        self.centerOn(scene_x, scene_y)
        
        # 미니맵 즉시 업데이트
        self._update_minimap()


    def _position_minimap(self) -> None:
        """미니맵을 우측 하단에 배치"""
        margin_right = 20   # 우측 여백
        margin_bottom = 20  # 하단 여백
        x = self.width() - self.minimap.width() - margin_right
        y = self.height() - self.minimap.height() - margin_bottom
        self.minimap.move(x, y)


    def _on_scrollbar_changed(self, value: int) -> None:
        """스크롤바 값 변경 시 미니맵 업데이트"""
        try:
            if hasattr(self, 'minimap_update_timer'):
                self.minimap_update_timer.start(self.MINIMAP_UPDATE_DELAY)
        except RuntimeError:
            # Qt 종료 시퀀스 중 C++ QTimer 객체가 이미 소멸된 경우
            pass


# ============================================
# 오버레이
# ============================================

    def _apply_overlay(self) -> None:
        """오버레이 실제 적용 (딜레이 후 호출)"""
        debug_print(f"========== _apply_overlay() 호출됨 ==========")
        
        if not self.pending_overlay_data:
            warning_print(f"pending_overlay_data 없음")
            return
        
        if not self.overlay_widget:
            warning_print(f"overlay_widget 연결 안됨")
            return
        
        file_path, overlay_data, image_id = self.pending_overlay_data
        
        # 이미지 ID 검증
        if image_id != self.current_image_id:
            warning_print(f"오버레이 타이머 무효 (이미지 변경됨)")
            return
        
        debug_print(f"파일: {file_path.name}, ID={image_id}")
        debug_print(f"overlay_data keys: {list(overlay_data.keys())}")
        
        # 오버레이 데이터 설정
        self.overlay_widget.set_data(file_path, overlay_data)
        
        self.pending_overlay_data = None
        debug_print(f"========== 오버레이 적용 완료 ==========")


    def update_overlay(self) -> None:
        self.overlay_refresh_requested.emit()  # MainWindow가 처리
        

# ============================================
# 스크롤 위치 복원
# ============================================

    def _restore_scroll_position(self, h_ratio: float, v_ratio: float) -> None:
        """스크롤 위치 복원 (비율 기반)"""
        h_max = self.horizontalScrollBar().maximum()
        v_max = self.verticalScrollBar().maximum()
        
        h_value = 0
        v_value = 0
        
        if h_max > 0:
            h_value = int(h_ratio * h_max)
            self.horizontalScrollBar().setValue(h_value)
        
        if v_max > 0:
            v_value = int(v_ratio * v_max)
            self.verticalScrollBar().setValue(v_value)
        
        debug_print(f"스크롤 복원: 비율({h_ratio:.2f}, {v_ratio:.2f}) → 값({h_value}, {v_value}), max=({h_max}, {v_max})")


# ============================================
# resize 및 유틸리티
# ============================================

    def resizeEvent(self, event):
        """창 크기 변경 - 타이밍 개선"""
        super().resizeEvent(event)

        if getattr(self, '_edit_mode', False):
            self._position_ai_panel()

        # 로딩 중이면 지연
        if self._loading_image:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._on_resize_delayed)
            timer.start(50)
            self._pending_timers.append(timer)
            return

        # 초기 표시 무시
        if not event.oldSize().isValid():
            return

        # 크기 변경 없으면 무시
        if event.oldSize() == event.size():
            return

        debug_print(f"[RESIZE] 크기 변경: {event.oldSize()} → {event.size()}")

        self._apply_resize_logic()

        # 미니맵/오버레이/툴바 등 위치 업데이트
        self._post_resize_ui_update()

        self._reposition_eraser_overlay()
        

    def _on_resize_delayed(self):
        """이미지 로딩 중일 때 지연된 리사이즈 처리 (event 없이 현재 크기 기준)"""
        if self._loading_image:
            # 아직도 로딩 중이면 한 번 더 미룰지, 그냥 스킵할지 정책 결정
            return

        debug_print("[RESIZE] 지연 리사이즈 처리")
        self._apply_resize_logic()
        self._post_resize_ui_update()


    def _apply_resize_logic(self):
        # suppress / zoom_mode 관련 기존 로직만 떼어낸 부분
        if getattr(self, '_suppress_fit_in_view', False):
            if self.pixmap_item and not self.pixmap_item.pixmap().isNull():
                px = self.pixmap_item.pixmap()
                new_intent = self._auto_zoom_mode(px.width(), px.height())
                self._zoom_intent_stack = [new_intent]
                if new_intent != 'actual':
                    self._suppress_fit_in_view = False
                    self._fit_in_view()
        else:
            if self.zoom_mode == 'fit' and self.pixmap_item:
                self._fit_in_view()
            elif self.zoom_mode == 'width' and self.pixmap_item:
                self._fit_width()

        self._update_cursor()


    def _post_resize_ui_update(self):
        # 미니맵 업데이트
        if hasattr(self, 'minimap_update_timer'):
            self.minimap_update_timer.start(self.MINIMAP_UPDATE_DELAY * 2)

        # 오버레이/미니맵/툴바 위치 조정
        if hasattr(self, '_loading_overlay'):
            self._loading_overlay.setGeometry(self.rect())
        if hasattr(self, 'minimap'):
            self._position_minimap()
        if self._edit_toolbar is not None and self._edit_toolbar.isVisible():
            self._position_edit_toolbar()


    def _unlock_resize(self, image_id: int) -> None:
        """resize 이벤트 잠금 해제"""
        if image_id != getattr(self, '_loading_timer_id', None):
            return
        
        self._loading_image = False
        debug_print(f"resize 활성화 (ID: {image_id})")


    def get_viewport_size(self) -> Tuple[int, int]:
        """뷰포트 크기 반환"""
        return (self.viewport().width(), self.viewport().height())
    

    def get_current_pixmap(self) -> Optional[QPixmap]:
        """현재 표시 중인 이미지 반환"""
        if self.current_movie:
            return self.current_movie.currentPixmap()
        return self.current_pixmap
    

    def get_image_rect(self) -> QRect:
        """현재 표시된 이미지의 실제 영역 계산"""
        if not self.pixmap_item or self.pixmap_item.pixmap().isNull():
            return QRect()
        
        scene_rect = self.pixmap_item.sceneBoundingRect()
        top_left = self.mapFromScene(scene_rect.topLeft())
        bottom_right = self.mapFromScene(scene_rect.bottomRight())
        
        return QRect(top_left, bottom_right)


    def _setup_opengl(self) -> None:
        """OpenGL 렌더링 설정"""
        use_opengl = self.config_manager.get_rendering_setting('use_opengl', True)
        if use_opengl:
            try:
                fmt = QSurfaceFormat()
                fmt.setVersion(3, 3)
                fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)

                vsync = self.config_manager.get_rendering_setting('vsync', True)
                fmt.setSwapInterval(1 if vsync else 0)

                msaa_samples = self.config_manager.get_rendering_setting('msaa_samples', 4)
                if msaa_samples > 0:
                    fmt.setSamples(msaa_samples)

                QSurfaceFormat.setDefaultFormat(fmt)

                gl_widget = QOpenGLWidget()
                self.setViewport(gl_widget)

                info_print(f"[INFO] OpenGL 렌더링 활성화 (MSAA: {msaa_samples}x, V-Sync: {vsync})")
            except ImportError:
                warning_print("[WARN] OpenGL 모듈 없음 - 소프트웨어 렌더링 사용")
            except Exception as e:
                error_print(f"[ERROR] OpenGL 초기화 실패: {e} - 소프트웨어 렌더링 사용")
        else:
            info_print("[INFO] 소프트웨어 렌더링 사용 (OpenGL 비활성화)")


    def toggle_opengl(self, enabled: bool) -> None:
        """
        OpenGL 렌더링 토글 (재시작 필요)
        
        Args:
            enabled: True이면 OpenGL 활성화
        """
        self.config_manager.set_rendering_setting('use_opengl', enabled)
        
        # 설정 즉시 저장
        self.config_manager.save_immediate()
        
        info_print(f"[INFO] OpenGL 설정 변경: {enabled} (재시작 후 적용)")


    @staticmethod
    def is_apng(file_path: Path) -> bool:
        suffix = file_path.suffix.lower()
        # .apng 확장자는 무조건 APNG
        if suffix == '.apng':
            return True
        # .png 확장자는 Pillow로 프레임 수 확인
        if suffix == '.png':
            try:
                with PILImage.open(str(file_path)) as img:
                    return getattr(img, 'n_frames', 1) > 1
            except Exception:
                return False
        return False

