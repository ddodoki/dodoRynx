# -*- coding: utf-8 -*-
# ui/dual_view_panel.py

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, Signal, QPoint, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSplitter,
    QSplitterHandle,
    QWidget,
)

from core.cache_manager import CacheManager
from ui.image_viewer import ImageViewer
from utils.config_manager import ConfigManager
from utils.debug import debug_print, error_print, info_print

if TYPE_CHECKING:
    from ui.main_window import MainWindow


# ============================================
# Splitter UI 구성 (핸들 / 분할 위젯)
# ============================================

class DualSplitterHandle(QSplitterHandle):

    _HANDLE_W = 4

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter) -> None:
        super().__init__(orientation, parent)
        self._hovered = False
        self.setMouseTracking(True)
        cur = (Qt.CursorShape.SplitHCursor
               if orientation == Qt.Orientation.Horizontal
               else Qt.CursorShape.SplitVCursor)
        self.setCursor(cur)


    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()


    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()


    def mouseDoubleClickEvent(self, event) -> None:
        spl = self.splitter()
        if spl is None:
            return
        total = sum(spl.sizes())
        half  = total // 2
        spl.setSizes([half, total - half])


    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w  = self.width()
        h  = self.height()
        cx = w // 2

        p.fillRect(self.rect(), QColor(0, 0, 0, 0))

        if not self._hovered:
            p.end()
            return

        grad = QLinearGradient(cx - 3, 0, cx + 3, 0)
        grad.setColorAt(0.0, QColor(255, 255, 255,   0))
        grad.setColorAt(0.3, QColor(100, 160, 255, 180))
        grad.setColorAt(0.5, QColor(120, 180, 255, 220))
        grad.setColorAt(0.7, QColor(100, 160, 255, 180))
        grad.setColorAt(1.0, QColor(255, 255, 255,   0))
        pen = QPen()
        pen.setBrush(grad)
        pen.setWidth(4)
        p.setPen(pen)
        p.drawLine(cx, 0, cx, h)

        p.end()


class DualSplitter(QSplitter):

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self.setHandleWidth(DualSplitterHandle._HANDLE_W)
        self.setStyleSheet("QSplitter { background: transparent; }") 


    def createHandle(self) -> QSplitterHandle:
        return DualSplitterHandle(self.orientation(), self)


# ============================================
# 플레이스홀더 위젯 (마지막 이미지 표시)
# ============================================

class NoNextImageWidget(QWidget):

    _MSG_MAIN = "Last Image"
    _MSG_SUB  = "No more images available"

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2

        # 배경
        p.fillRect(self.rect(), QColor(0, 0, 0))

        # 점선 사각형 (이미지 프레임 느낌)
        box_w, box_h = 120, 90
        box_x = cx - box_w // 2
        box_y = cy - box_h // 2 - 24
        pen = QPen(QColor(80, 80, 80), 1.5, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(box_x, box_y, box_w, box_h, 6, 6)

        # 이미지 없음 아이콘 (산 + 원)
        p.setPen(QPen(QColor(70, 70, 70), 1.5))
        # 태양 원
        sun_r = 10
        p.drawEllipse(box_x + 22, box_y + 14, sun_r, sun_r)
        # 산 삼각형
        from PySide6.QtGui import QPolygon
        mountain = QPolygon([
            QPoint(box_x + 10,  box_y + box_h - 16),
            QPoint(box_x + 50,  box_y + 24),
            QPoint(box_x + 90,  box_y + box_h - 16),
        ])
        p.drawPolyline(mountain)
        # 작은 산
        small = QPolygon([
            QPoint(box_x + 48,  box_y + box_h - 16),
            QPoint(box_x + 72,  box_y + 42),
            QPoint(box_x + 110, box_y + box_h - 16),
        ])
        p.drawPolyline(small)

        # 텍스트
        font_main = QFont()
        font_main.setPointSize(12)
        font_main.setWeight(QFont.Weight.Medium)
        p.setFont(font_main)
        p.setPen(QColor(130, 130, 130))
        p.drawText(0, cy + 48, w, 24, Qt.AlignmentFlag.AlignHCenter, self._MSG_MAIN)

        font_sub = QFont()
        font_sub.setPointSize(9)
        p.setFont(font_sub)
        p.setPen(QColor(70, 70, 70))
        p.drawText(0, cy + 72, w, 20, Qt.AlignmentFlag.AlignHCenter, self._MSG_SUB)

        p.end()


# ============================================
# DualViewPanel 기본 구성
# ============================================

class DualViewPanel(QWidget):

    dual_mode_changed = Signal(bool)

    # ============================================
    # DualViewPanel 초기화
    # ============================================

    def __init__(
        self,
        primary_viewer: ImageViewer,
        cache_manager: CacheManager,
        config_manager: ConfigManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self._is_dual: bool = False
        self._primary = primary_viewer

        self._secondary = ImageViewer(cache_manager, config_manager, self, use_opengl=False)
        self._secondary.setVisible(False)

        self._no_next = NoNextImageWidget(self._secondary)
        self._no_next.setVisible(False)

        self._splitter = DualSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._primary)
        self._splitter.addWidget(self._secondary)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._splitter)

        self._last_overlay_file: Optional[Path] = None
        self._last_overlay_meta: dict            = {}
        self._config_manager = config_manager 

        self._splitter.splitterMoved.connect(self._on_splitter_moved)

        self._hovered_viewer: Optional[ImageViewer] = None
        self._primary.installEventFilter(self)
        self._secondary.installEventFilter(self)


    @property
    def primary_viewer(self) -> ImageViewer:
        return self._primary


    @property
    def secondary_viewer(self) -> ImageViewer:
        return self._secondary


    @property
    def is_dual_mode(self) -> bool:
        return self._is_dual


    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.Enter:
            if obj is self._primary:
                self._hovered_viewer = self._primary
            elif obj is self._secondary:
                self._hovered_viewer = self._secondary
        elif event.type() == QEvent.Type.Leave:
            if obj is self._hovered_viewer:
                self._hovered_viewer = None
        return super().eventFilter(obj, event)

    def get_active_viewer(self) -> ImageViewer:
        """마우스가 올라있는 뷰어 반환. 없으면 primary."""
        if self._is_dual and self._hovered_viewer is not None:
            return self._hovered_viewer
        return self._primary
    
    # ============================================
    # 듀얼 모드 토글 / 레이아웃 제어
    # ============================================

    def toggle_dual_mode(self) -> bool:
        self.set_dual_mode(not self._is_dual)
        return self._is_dual


    def set_dual_mode(self, enabled: bool) -> None:
        if self._is_dual == enabled:
            return
        self._is_dual = enabled
        self._apply_layout()
        self.dual_mode_changed.emit(self._is_dual)
        info_print(f"듀얼 뷰 {'ON' if enabled else 'OFF'}")


    def _apply_layout(self) -> None:
        if self._is_dual:
            self._no_next.setVisible(False)
            self._secondary.setVisible(True) 
            total = max(self._splitter.width(), 600)
            self._splitter.setSizes([total // 2, total // 2])
        else:
            self._no_next.setVisible(False)
            self._secondary.setVisible(False)
            self._secondary.clear()

    # ============================================
    # 보조 이미지 로드
    # ============================================

    def load_secondary(self, main_window: "MainWindow", secondary_index: int) -> None:
        if not self._is_dual:
            return
        
        self._secondary.set_main_window(main_window)
        files = main_window.navigator.image_files

        if not (0 <= secondary_index < len(files)):
            self._secondary.setVisible(True) 
            self._secondary.clear()
            self._no_next.setGeometry(0, 0, self._secondary.width(), self._secondary.height())
            self._no_next.setVisible(True)
            self._no_next.raise_()
            debug_print("DualView: 마지막 이미지 → 플레이스홀더 표시")
            return

        self._no_next.setVisible(False)
        self._secondary.setVisible(True)
        sec_file = files[secondary_index]
        debug_print(f"DualView: 보조 로드 → {sec_file.name}")
        self._load_via_cache(main_window, sec_file)


    def _load_via_cache(self, main_window: "MainWindow", file_path: Path) -> None:
        try:
            cm     = main_window.cache_manager
            loader = cm.loader

            if loader.is_animated(file_path):
                if loader.is_apng(file_path):
                    self._secondary.set_apng_image(file_path)
                else:
                    movie = loader.load_animated(file_path)
                    if movie:
                        self._secondary.set_animated_image(movie, file_path)
                return

            files = main_window.navigator.image_files
            try:
                sec_index = files.index(file_path)
            except ValueError:
                sec_index = -1

            pixmap = None
            if sec_index >= 0:
                with cm._lock(cm.cache_mutex):
                    if sec_index in cm.cache:
                        pixmap = cm.cache[sec_index]
                    elif sec_index in cm.preview_cache:
                        pixmap = cm.preview_cache[sec_index]

            if not pixmap or pixmap.isNull():
                from core.image_loader import ImageLoader
                pixmap = ImageLoader().load(file_path)
                if pixmap and not pixmap.isNull():
                    pixmap = loader.apply_exif_rotation(file_path, pixmap)

            if pixmap and not pixmap.isNull():
                self._secondary.set_image(pixmap)
            else:
                error_print(f"DualView: 이미지 로드 실패 {file_path.name}")

        except Exception as e:
            error_print(f"DualView _load_via_cache 실패: {e}")

    # ============================================
    # 세컨드 오버레이 적용
    # ============================================

    def update_secondary_overlay(
        self,
        file_path: Path,
        metadata: dict,
    ) -> None:
        if not self._is_dual:
            return
        if not self._secondary.overlay_widget:
            return

        self._last_overlay_file = file_path
        self._last_overlay_meta = metadata

        self._apply_overlay_to_secondary(file_path, metadata)
        QTimer.singleShot(0, self._apply_secondary_overlay_deferred)


    def _apply_secondary_overlay_deferred(self) -> None:
        if not self._is_dual or not self._secondary.overlay_widget:
            return
        if not self._last_overlay_file:
            return
        
        ow = self._secondary.overlay_widget
        ow.setGeometry(self._secondary.rect())
        ow.raise_()

        self._apply_overlay_to_secondary(self._last_overlay_file, self._last_overlay_meta)
        ow.update() 


    def _apply_overlay_to_secondary(self, file_path: Path, metadata: dict) -> None:

        ow = self._secondary.overlay_widget
        if not ow:
            return
        
        cfg = self._config_manager

        scale_value = cfg.get_overlay_scale() 
        ow.set_scale(scale_value / 100.0)

        enabled     = cfg.get_overlay_setting("enabled",          False)
        show_file   = cfg.get_overlay_setting("show_file_info",   True)
        show_camera = cfg.get_overlay_setting("show_camera_info", True)
        show_exif   = cfg.get_overlay_setting("show_exif_info",   True)
        show_lens   = cfg.get_overlay_setting("show_lens_info",   False)
        show_gps    = cfg.get_overlay_setting("show_gps_info",    False)
        show_map    = cfg.get_overlay_setting("show_map",         False)
        opacity     = cfg.get_overlay_setting("opacity",          0.8)
        position    = cfg.get_overlay_setting("position",         "top_left")

        ow.update_settings(
            enabled,
            show_file, show_camera, show_exif, show_lens,
            show_gps, show_map, opacity, position
        )
        ow.set_data(file_path, metadata)
        ow.update() 

        debug_print(
            f"[DUAL] apply_overlay_to_secondary: show_map={show_map}, "
            f"gps={metadata.get('gps')}, file={file_path.name}"
        )

        debug_print(f"DualView overlay 갱신: {file_path.name}")


    def refresh_secondary_overlay(self) -> None:
        """
        옵션 변경 시 MainWindow에서 호출.
        캐시된 데이터로 secondary overlay를 재렌더링.
        """
        if not self._is_dual:
            return
        if not self._last_overlay_file:
            return
        
        self._update_overlay_settings_cache()
        self._apply_overlay_to_secondary(
            self._last_overlay_file,
            self._last_overlay_meta,
        )

        ow = self._secondary.overlay_widget
        if ow is None:
            return

        scale = self._config_manager.config.get("overlay.scale", 100)
        ow.set_scale(scale)

        debug_print("DualView: overlay 설정 동기화")


    def _update_overlay_settings_cache(self) -> None:
        """캐시된 설정을 최신 config_manager로 갱신"""
        cfg = self._config_manager
        self._last_overlay_settings = {
            'enabled':     cfg.get_overlay_setting("enabled", False),
            'show_file':   cfg.get_overlay_setting("show_file_info", True),
            'show_camera': cfg.get_overlay_setting("show_camera_info", True),
            'show_exif':   cfg.get_overlay_setting("show_exif_info", True),
            'show_lens':   cfg.get_overlay_setting("show_lens_info", False),
            'show_gps':    cfg.get_overlay_setting("show_gps_info", False),
            'show_map':    cfg.get_overlay_setting("show_map", False),
            'opacity':     cfg.get_overlay_setting("opacity", 0.8),
            'position':    cfg.get_overlay_setting("position", "top_left"),
        }

    # ============================================
    # 리사이즈 / 지오메트리 동기화
    # ============================================

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_no_next_geometry()

        QTimer.singleShot(50, self._on_resize_delayed)


    def _on_resize_delayed(self) -> None:
        """타이머 지연 후 resize 후처리 - event 없이 현재 크기 기준으로 처리"""
        self.update()

        if hasattr(self, 'overlay_widget') and self._secondary.overlay_widget:
            self._secondary.overlay_widget.setGeometry(self.rect())
            self._secondary.overlay_widget.raise_()


    def _sync_no_next_geometry(self) -> None:
        """_no_next는 _secondary의 자식이므로 _secondary 크기에 맞게 조정."""
        if self._no_next.isVisible():
            self._no_next.setGeometry(0, 0, self._secondary.width(), self._secondary.height())
            self._no_next.raise_()


    def _on_splitter_moved(self, pos: int, index: int) -> None:
        if not self._is_dual:
            return
        ow = self._secondary.overlay_widget
        if ow:
            ow.setGeometry(self._secondary.rect())
            ow.raise_()
            ow.update()

    # ============================================
    # 세컨드 뷰어 초기화
    # ============================================

    def clear_secondary(self) -> None:
        self._secondary.clear()
        self._last_overlay_file = None
        self._last_overlay_meta = {}
        if self._secondary.overlay_widget:
            self._secondary.overlay_widget.clear()
        debug_print("DualView: 보조 뷰어 클리어")

