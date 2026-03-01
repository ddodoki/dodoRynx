# -*- coding: utf-8 -*-
# ui/main_window.py

"""
메인 윈도우 - 전체 UI 구성 및 이벤트 핸들링
"""

import traceback
from pathlib import Path
from typing import List, Optional

import numpy as np
import piexif
from PIL import Image
from PIL.Image import Exif

from PySide6.QtCore import (
    QObject,
    QPoint,
    QRect,
    QRunnable,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.cache_manager import CacheManager
from core.file_manager import FileManager
from core.folder_navigator import FolderNavigator
from core.folder_watcher import FolderWatcher
from core.image_loader import ImageLoader
from core.map_loader import _prefetcher
from core.metadata_reader import MetadataReader
from core.rotation_manager import RotationManager

from ui.about_dialog import AboutDialog
from ui.folder_explorer import FolderExplorer
from ui.image_viewer import ImageViewer
from ui.menu_shortcuts import MenuShortcutController
from ui.metadata_panel import MetadataPanel
from ui.overlay_widget import OverlayWidget
from ui.status_bar import AppStatusBar, StatusBarController
from ui.system_info_dialog import SystemInfoDialog
from ui.thumbnail_bar import ThumbnailBar

from utils.app_meta import APP_NAME, APP_VERSION
from utils.config_manager import ConfigManager
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import LangManager, t
from utils.paths import get_icon_path
from utils.performance_monitor import PerformanceMonitor


class PerfOverlayWidget(QLabel):
    """
    성능 정보 플로팅 라벨.
    - QMainWindow 를 parent 로 사용
    - WA_TransparentForMouseEvents → 클릭 방해 없음
    - resizeEvent 마다 reposition() 호출 → 항상 우상단 유지
    - 배경 완전 투명 → 타이틀바 바로 아래에서 '타이틀바 내부'처럼 보임
    """
    _MARGIN_RIGHT = 8
    _MARGIN_TOP   = 4

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("""
            QLabel {
                color: #888888;
                font-size: 10px;
                background: transparent;
                padding: 1px 6px;
            }
        """)
        self.setVisible(False)

    def reposition(self) -> None:
        p = self.parent()
        if not isinstance(p, QWidget):  # QWidget 타입 보장
            return
        self.adjustSize()
        x = p.width() - self.width() - self._MARGIN_RIGHT
        self.move(max(0, x), self._MARGIN_TOP)
        self.raise_()


class _GpsReaderSignals(QObject): 
    ready = Signal(list)


class _GpsReader(QRunnable): 
    def __init__(self, files: list, zoom: int,
                 signals: _GpsReaderSignals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._signals = signals
        self._files   = files
        self._zoom    = zoom

    def run(self) -> None:
        reader = MetadataReader()
        tasks: list[tuple] = []
        for f in self._files:
            try:
                meta = reader.read(f)
                gps  = (meta or {}).get("gps")
                if not gps:
                    continue
                lat = gps.get("latitude")
                lon = gps.get("longitude")
                if lat is None or lon is None:
                    continue
                tasks.append((lat, lon, self._zoom, 400, 300))
            except Exception:
                pass

        if tasks:
            try:
                self._signals.ready.emit(tasks)
            except RuntimeError:
                pass   


class MainWindow(QMainWindow):
    """메인 윈도우"""

    # ── 상태바 위젯 하위 호환 프로퍼티 ──────────────────────────
    @property
    def progress_label(self):           return self.status_bar.progress_label
    @property
    def op_label(self):                 return self.status_bar.op_label
    @property
    def thumb_label(self):              return self.status_bar.thumb_label
    @property
    def status_message_label(self):
        return getattr(self.status_bar, 'status_message_label', None)

    @property
    def status_message_timer(self):
        return getattr(self.status_bar, 'status_message_timer', None)


# ============================================
# 초기화
# ============================================
        
    def __init__(self, config: ConfigManager, initial_file: Optional[Path] = None) -> None:
        super().__init__()
        self.config = config
        self._initialization_complete = False

        self._set_window_icon()
        self._setup_initial_palette()

        # QTimer 없이 동기 전체 초기화 → 깜박임 완전 제거
        self._init_all()

        self._initialization_complete = True


    def _setup_initial_palette(self) -> None:
        """Phase 1: 배경색만 빠르게 설정"""
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(26, 26, 26))
        palette.setColor(QPalette.ColorRole.Base, QColor(20, 20, 20))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(1200, 800)


    def _init_all(self) -> None:
        """
        전체 초기화.

        - 반드시 지켜야 할 순서:
            1. _init_core()        핵심 데이터 객체 (UI 없음)
            2. _init_ui()          위젯 생성 (core 객체 의존)
            3. _create_statusbar() 상태바 생성 (init_ui 이후)
            4. _connect_signals()  시그널 연결 (모든 위젯 준비 후)
            5. _post_init()        복원/후처리 (시그널 연결 후)
            6. menu_ctrl.setup()   단축키 등록 (모든 것 완료 후 맨 마지막)
        """
        """전체 동기 초기화 (기존 구조 유지)"""
        self._init_core()
        self._init_ui()
        self._perf_overlay = PerfOverlayWidget(self)
        self._connect_signals()
        self._post_init()
        self.menu_ctrl = MenuShortcutController(self)
        self.menu_ctrl.setup()


    def _init_core(self) -> None:
        """
        Step 1 — UI 없이 동작하는 핵심 데이터 객체 초기화.

        모든 인스턴스 속성을 여기서 명시적으로 선언.
        _init_ui() 이후 등장하는 속성은 _init_ui() 끝에 선언.
        이렇게 하면 메서드 전체에서 hasattr() 체크가 불필요해짐.
        """
        # 설정
        self.overlay_enabled: bool = self.config.get_overlay_setting("enabled", False)

        # 핵심 컴포넌트 (타입 힌트 명시)
        self.navigator:        FolderNavigator  = FolderNavigator()
        self.cache_manager:    CacheManager     = CacheManager(
            ahead_count   = self.config.get('cache.ahead_count',    25),
            behind_count  = self.config.get('cache.behind_count',    5),
            max_memory_mb = self.config.get('cache.max_memory_mb', 500),
        )
        self.perf_monitor:     PerformanceMonitor = PerformanceMonitor()
        self.current_cpu_usage: float = 0.0
        self.folder_watcher:   FolderWatcher    = FolderWatcher(FolderNavigator.SUPPORTED_EXTENSIONS)
        self.imageloader:      ImageLoader      = ImageLoader()
        self.rotation_manager: RotationManager  = RotationManager()
        self.file_manager:     FileManager      = FileManager(self)

        # 상태 플래그
        self._current_file:           Optional[Path] = None
        self.is_fullscreen:           bool           = False
        self._print_manager = None
        self._edit_locked:            bool           = False
        self.pending_rotation_for:    Optional[Path] = None

        # open_image/open_folder 상태 (hasattr 제거용)
        self._pending_file_to_open:   Optional[Path] = None
        self._open_first_on_scan:     bool           = False

        # 전체화면 진입 전 UI 상태 저장 (hasattr 제거용, #_toggle_fullscreen 패치)
        self._pre_fullscreen_thumb_visible:  bool = True
        self._pre_fullscreen_meta_visible:   bool = True
        self._pre_fullscreen_status_visible: bool = True
        self._pre_fullscreen_overlay_visible: bool = True

        # 타이머
        self.hide_timer = QTimer(self)
        self.hide_timer.timeout.connect(self._auto_hide_ui)
        self.hide_timer.setSingleShot(True)

        # GPS 프리패치 signals — MainWindow 수명과 동일하게 관리
        self._prefetch_signals = _GpsReaderSignals()
        self._prefetch_signals.ready.connect(
            _prefetcher.schedule,
            Qt.ConnectionType.QueuedConnection
        )

        debug_print("_init_core() 완료")


    def _init_ui(self) -> None:
        """
        Step 2 — 위젯 생성.
        _init_core() 완료 후 실행. cache_manager 등 core 객체 사용 가능.
        """
        self.setMinimumSize(1200, 800)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # ──────────────────────────────────────────────────────
        # [수정] ① FolderExplorer — index 0, 기본 숨김
        # ──────────────────────────────────────────────────────
        self.folder_explorer = FolderExplorer(self)
        self.folder_explorer.set_main_window(self)
        self.folder_explorer.setVisible(False)

        self.folder_explorer.setMinimumWidth(270)
        self.folder_explorer.setMaximumWidth(270)
        self._fe_panel_width = 270

        self.h_splitter.addWidget(self.folder_explorer)   # index 0

        # ──────────────────────────────────────────────────────
        # [기존 유지] ② 왼쪽: 이미지 뷰어 + 썸네일 — index 1
        # ──────────────────────────────────────────────────────
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        viewer_container = QWidget()
        viewer_layout = QVBoxLayout(viewer_container)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(0)

        self.image_viewer = ImageViewer(
            cache_manager=self.cache_manager,
            config_manager=self.config,
            parent=self
        )
        self.image_viewer.set_main_window(self)
        viewer_layout.addWidget(self.image_viewer)

        self.overlay_widget = OverlayWidget(self.image_viewer)
        self.image_viewer.set_overlay_widget(self.overlay_widget)
        debug_print("OverlayWidget 생성 및 연결 완료")

        left_layout.addWidget(viewer_container, 1)

        self.thumbnail_bar = ThumbnailBar(
            self.cache_manager,
            thumb_memory_mb=self.config.get('cache.thumb_memory_mb', 100),
            thumb_disk_mb=self.config.get('cache.thumb_disk_mb', 500),
        )
        left_layout.addWidget(self.thumbnail_bar)

        # 편집 모드 잠금 오버레이
        self._thumb_lock_overlay = QWidget(self.thumbnail_bar)
        self._thumb_lock_overlay.setStyleSheet("background: rgba(0, 0, 0, 170);")
        self._thumb_lock_overlay.setVisible(False)
        from PySide6.QtWidgets import QLabel as _QL
        _lbl = _QL("🔒 Move after exiting Edit Mode.", self._thumb_lock_overlay)
        _lbl.setStyleSheet("color: #aaaaaa; font-size: 12px; background: transparent;")
        _lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lock_label = _lbl

        # [수정] addWidget 1회만 — 원본의 첫 번째 addWidget 위치에서만 호출
        self.h_splitter.addWidget(left_widget)            # index 1

        # ──────────────────────────────────────────────────────
        # [기존 유지] ③ 오른쪽: 메타데이터 — index 2
        # ──────────────────────────────────────────────────────
        # [수정] self.metadata_panel 하나만 생성, self.metadatapanel 제거
        self.metadata_panel = MetadataPanel(self.config)
        self.h_splitter.addWidget(self.metadata_panel)    # index 2

        # [수정] 위젯 3개이므로 setSizes 값도 3개
        self.h_splitter.setSizes([0, 1140, 300])

        # splitter stretch 설정: viewer(index 1)만 늘어남
        self.h_splitter.setStretchFactor(0, 0)   # folder_explorer: 고정
        self.h_splitter.setStretchFactor(1, 1)   # image_viewer: 가변
        self.h_splitter.setStretchFactor(2, 0)   # metadata_panel: 고정

        # metadata_panel 너비 고정
        self.metadata_panel.setMinimumWidth(300)
        self.metadata_panel.setMaximumWidth(300)

        main_layout.addWidget(self.h_splitter)

        # 상태바
        self._create_statusbar()

        # 오버레이 스케일 적용
        saved_scale = self.config.get("overlay.scale", 100)
        self.overlay_widget.set_scale(saved_scale / 100.0)

        # 오버레이 설정 로드
        self._load_overlay_settings()

        # 미니맵 투명도
        def _apply_minimap_opacity():
            opacity = self.config.get('minimap.opacity', 0.8)
            if hasattr(self.image_viewer, 'minimap') and self.image_viewer.minimap:
                self.image_viewer.minimap.set_opacity(opacity)
                debug_print(f"미니맵 투명도 적용: {opacity:.2f}")
        QTimer.singleShot(100, _apply_minimap_opacity)

        debug_print("_init_ui() 완료")


    def _create_statusbar(self) -> None:
        """하단 상태바 생성 — AppStatusBar / StatusBarController 위임"""
        # 1. 위젯 팩토리 생성
        self.status_bar = AppStatusBar(self)
        self.setStatusBar(self.status_bar.statusbar)
        self.statusbar = self.status_bar.statusbar  # 하위 호환 alias

        # 2. 컨트롤러 생성 및 시그널 연결
        self.status_ctrl = StatusBarController(self, self.status_bar)
        self.status_ctrl.connect_signals()

        debug_print("상태바 생성 완료")


    def _init_settings_dialog(self):
        """설정 다이얼로그 초기화 (지연 생성)"""
        self._settings_dialog = None


    def _connect_signals(self) -> None:
        """
        모든 시그널-슬롯 연결을 한 곳에서 관리.

        연결 순서 규칙 (중요):
        같은 시그널에 여러 슬롯을 연결할 때는 Qt가 연결 순서대로 슬롯을 호출함.
        folder_scan_completed 의 경우:
            1. status_ctrl.on_folder_scan_completed  → 상태바 갱신 (부작용 없음)
            2. self._on_folder_scan_completed        → 실제 이미지 로딩 처리
        이 순서를 유지해야 함. 절대 중간에 다른 핸들러를 삽입하지 말 것.
        """
        debug_print("_connect_signals() 시작")

        # ── 1. FolderNavigator ──────────────────────────────────
        self.navigator.index_changed.connect(self._on_index_changed)
        # folder_scan_completed: 순서 중요 (status 먼저, 로딩 나중)
        self.navigator.folder_scan_started.connect(self.status_ctrl.on_folder_scan_started)
        self.navigator.folder_scan_progress.connect(self.status_ctrl.on_folder_scan_progress)
        self.navigator.folder_scan_completed.connect(self.status_ctrl.on_folder_scan_completed)
        self.navigator.folder_scan_completed.connect(self._on_folder_scan_completed)

        # ── 2. ImageViewer ──────────────────────────────────────
        self.image_viewer.file_dropped.connect(self._on_file_dropped)
        self.image_viewer.zoom_changed.connect(self._on_zoom_changed)
        self.image_viewer.edit_mode_changed.connect(self._on_edit_mode_changed)
        self.image_viewer.edit_save_requested.connect(self._on_edit_save_requested)

        # ── 3. FolderWatcher → MainWindow (모두 QueuedConnection) ──
        # batch_added 연결 추가 (기존 누락 버그 수정)
        self.folder_watcher.file_added.connect(
            self._on_fs_file_added,    Qt.ConnectionType.QueuedConnection)
        self.folder_watcher.file_deleted.connect(
            self._on_fs_file_deleted,  Qt.ConnectionType.QueuedConnection)
        self.folder_watcher.file_modified.connect(
            self._on_fs_file_modified, Qt.ConnectionType.QueuedConnection)
        self.folder_watcher.file_moved.connect(
            self._on_fs_file_moved,    Qt.ConnectionType.QueuedConnection)
        self.folder_watcher.batch_added.connect( 
            self._on_fs_batch_added,   Qt.ConnectionType.QueuedConnection)
        self.folder_watcher.batch_deleted.connect(
            self._on_fs_batch_deleted, Qt.ConnectionType.QueuedConnection)

        # ── 4. CacheManager ─────────────────────────────────────
        self.cache_manager.cache_hit.connect(self._on_cache_hit)
        self.cache_manager.cache_miss.connect(self._on_cache_miss)
        self.cache_manager.full_image_loaded.connect(self._on_full_image_loaded)

        # ── 5. ThumbnailBar ─────────────────────────────────────
        try:
            self.thumbnail_bar.thumbnail_clicked.connect(self._on_thumbnail_clicked)
            self.thumbnail_bar.thumbnail_load_started.connect(self.status_ctrl.on_thumb_load_started)
            self.thumbnail_bar.thumbnail_load_progress.connect(self.status_ctrl.on_thumb_load_progress)
            self.thumbnail_bar.thumbnail_load_finished.connect(self.status_ctrl.on_thumb_load_finished)

            self.thumbnail_bar.highlight_toggle_requested.connect(self._on_highlight_toggle_requested)
            self.thumbnail_bar.highlight_range_requested.connect(self._on_highlight_range_requested)
            self.thumbnail_bar.temp_highlights_clear_requested.connect(
                self.navigator.clear_temporary_highlights
            )
            self.thumbnail_bar.status_message_requested.connect(self._show_status_message)
            self.thumbnail_bar.context_menu_requested.connect(self._on_thumbnail_context_menu)

        except Exception as e:
            error_print(f"ThumbnailBar 시그널 연결 실패: {e}")

        # ── 6. FolderExplorer ───────────────────────────────────
        self.folder_explorer.folder_selected.connect(self._on_folder_selected_from_explorer)
        self.metadata_panel.gps_clicked.connect(self._on_gps_clicked)
        self.metadata_panel.map_zoom_changed.connect(self._on_map_zoom_changed)

        # ── Navigator → ThumbnailBar 단방향 동기화 ────────────────
        self.navigator.highlight_changed.connect(self.thumbnail_bar.on_highlight_changed)
        self.navigator.highlights_cleared.connect(self.thumbnail_bar.on_highlights_cleared)
        self.navigator.highlights_set.connect(self.thumbnail_bar.on_highlights_set)

        debug_print("_connect_signals() 완료")


    def _post_init(self) -> None:
        self.image_viewer.current_is_highlighted = False
        wheel_delay = self.config.get('viewer.wheel_delay_ms', 100)
        self.image_viewer.wheel_timer.setInterval(wheel_delay)

        self._init_settings_dialog()
        self._restore_ui_visibility()
        self._restore_window_state()

        QTimer.singleShot(5000, self._start_perf_monitoring)
        QTimer.singleShot(3000, self._warmup_edit_toolbar)

        saved_sizes = self.config.get("window.splitter_sizes", None)

        # 한 번만 읽기
        fe_visible = self.config.is_folder_explorer_visible()

        if saved_sizes and len(saved_sizes) == 3:
            self.h_splitter.setSizes(saved_sizes)
        else:
            fe_w = self._fe_panel_width if fe_visible else 0
            self.h_splitter.setSizes([fe_w, 1140 - fe_w, 300])

        # 반드시 실제 위젯에 적용
        self.folder_explorer.setVisible(fe_visible)


    def _start_perf_monitoring(self) -> None:
        """5초 후 성능 모니터링 시작"""
        self.cpu_timer = QTimer(self)
        self.cpu_timer.timeout.connect(self._update_cpu_usage)
        self.cpu_timer.start(1000)

        self.perf_timer = QTimer(self)
        self.perf_timer.timeout.connect(self._update_performance_info)
        self.perf_timer.start(500)
        debug_print("성능 모니터링 시작 (지연 5초)")


    def _warmup_edit_toolbar(self) -> None:
        """유휴 시간에 EditToolbar 미리 생성 (첫 진입 지연 방지)"""
        if self.image_viewer._edit_toolbar is None:
            self.image_viewer._ensure_edit_toolbar()
            debug_print("EditToolbar 워밍업 완료")


    def _set_window_icon(self):
        """윈도우 아이콘 설정"""
        from PySide6.QtGui import QIcon
        
        # .ico 파일 우선 (Windows)
        icon_path = get_icon_path("icon.ico")
        
        if not icon_path.exists():
            # .ico 없으면 .png 시도
            icon_path = get_icon_path("icon.png")
        
        if icon_path.exists():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                self.setWindowIcon(icon)
                info_print(f"윈도우 아이콘 설정: {icon_path.name}")
            else:
                warning_print(f"아이콘 로드 실패: {icon_path}")
        else:
            warning_print(f"아이콘 파일 없음: {icon_path}")


# ============================================
# Qt 이벤트 오버라이드
# ============================================

    def resizeEvent(self, event):
        """창 크기 변경 시 오버레이 위치 업데이트"""
        super().resizeEvent(event)

        # 편집 모드 중 오버레이 업데이트 금지 (resize 시 재표시 방지)
        if not getattr(self, '_edit_locked', False):
            if hasattr(self, 'overlay_widget'):
                self._update_overlay_position()

        if hasattr(self, "status_ctrl") and hasattr(self.status_ctrl, "perf_overlay"):
            self.status_ctrl.reposition_perf_overlay()
            self.status_ctrl._toast_mgr._reposition()

        # 썸네일바 오버레이 크기 동기화
        if hasattr(self, '_thumb_lock_overlay') and self._thumb_lock_overlay.isVisible():
            self._thumb_lock_overlay.setGeometry(
                0, 0,
                self.thumbnail_bar.width(),
                self.thumbnail_bar.height()
            )
            if hasattr(self, '_thumb_lock_label'):
                self._thumb_lock_label.setGeometry(
                    0, 0,
                    self.thumbnail_bar.width(),
                    self.thumbnail_bar.height()
                )
                

    def closeEvent(self, event) -> None:
        """프로그램 종료 이벤트"""
        debug_print(f"========== 프로그램 종료 시작 ==========")

        # 가장 먼저: prefetcher 즉시 취소 (2500ms 타이머 + 큐 + 진행 중 로더)
        try:
            _prefetcher.cancel()
        except Exception as e:
            warning_print(f"prefetcher 취소 실패: {e}")
                    
        # 오버레이 정리
        if hasattr(self, 'overlay_widget'):
            try:
                self.overlay_widget.clear()
                debug_print(f"오버레이 위젯 정리 완료")
            except Exception as e:
                warning_print(f"오버레이 정리 실패: {e}")
        
        # 오버레이 상태 저장
        if hasattr(self, 'overlay_enabled'):
            debug_print(f"오버레이 상태 저장: enabled={self.overlay_enabled}")
            self.config.set_overlay_setting("enabled", self.overlay_enabled)
        
        # 창 상태 저장
        try:
            # ===== QByteArray → str 변환 (Python 3.12+ 호환) =====
            from PySide6.QtCore import QByteArray
            
            # Geometry 저장
            geometry_qba: QByteArray = self.saveGeometry().toBase64()
            # .data()가 memoryview를 반환할 수 있으므로 bytes()로 감싸기
            geometry = bytes(geometry_qba.data()).decode('utf-8') 
            self.config.set('window.geometry', geometry)
            debug_print(f"창 geometry 저장 완료")
            
            # State 저장
            state_qba: QByteArray = self.saveState().toBase64()
            # .data()가 memoryview를 반환할 수 있으므로 bytes()로 감싸기
            state = bytes(state_qba.data()).decode('utf-8')  
            self.config.set('window.state', state)
            debug_print(f"창 state 저장 완료")
            
            # Splitter 크기 저장
            splitter_sizes = self.h_splitter.sizes()
            self.config.set('window.splitter_sizes', splitter_sizes)
            debug_print(f"Splitter 크기 저장: {splitter_sizes}")
        
        except Exception as e:
            error_print(f"창 상태 저장 실패: {e}")
        
        # 설정 저장
        self.config.save()
        debug_print(f"설정 저장 완료")

        # 폴더 감시 중지
        if hasattr(self, 'folder_watcher'):
            try:
                self.folder_watcher.stop_watching()
                debug_print(f"폴더 감시 중지")
            except Exception as e:
                warning_print(f"폴더 감시 중지 실패: {e}")

        # 폴더 탐색기
        if hasattr(self, "folder_explorer"):
            try:
                self.folder_explorer.deactivate()   # OS 핸들 해제
                debug_print("folder_explorer 정리 완료")
            except Exception as e:
                warning_print(f"folder_explorer 정리 실패: {e}")

        # 캐시 정리
        if hasattr(self, 'cache_manager'):
            try:
                self.cache_manager.clear()
                debug_print(f"캐시 정리 완료")
            except Exception as e:
                warning_print(f"캐시 정리 실패: {e}")
        
        # 썸네일 스레드 풀 정리
        if hasattr(self, 'thumbnail_bar'):
            if hasattr(self.thumbnail_bar, 'thread_pool'):
                try:
                    self.thumbnail_bar.thread_pool.waitForDone(1000)
                    debug_print(f"썸네일 스레드 풀 정리 완료")
                except Exception as e:
                    warning_print(f"썸네일 스레드 풀 정리 실패: {e}")

        # 썸네일 캐시 DB 정리 (종료 시 1회)
        if hasattr(self, 'thumbnail_bar'):
            cache = getattr(self.thumbnail_bar, '_thumb_cache', None)
            if cache and hasattr(cache, '_db_vacuum'):
                try:
                    cache._db_vacuum()  # VACUUM으로 파편화 제거
                    debug_print("썸네일 DB VACUUM 완료")
                except Exception as e:
                    warning_print(f"DB VACUUM 실패: {e}")

        # 메타데이터 캐시 정리
        if hasattr(self, 'metadata_panel'):
            if hasattr(self.metadata_panel, 'metadata_reader'):
                try:
                    self.metadata_panel.metadata_reader.clear_cache()
                    debug_print(f"메타데이터 캐시 정리 완료")
                except Exception as e:
                    warning_print(f"메타데이터 캐시 정리 실패: {e}")
        
        if hasattr(self.file_manager, '_file_worker') and self.file_manager._file_worker:
            self.file_manager._file_worker.cancel()
            self.file_manager._file_worker.wait(3000)   # 최대 3초 대기

        if hasattr(self.navigator, '_sort_thread') and self.navigator._sort_thread:
            self.navigator._sort_thread.cancel()
            self.navigator._sort_thread.wait(2000)

        if hasattr(self.navigator, 'scan_thread') and self.navigator.scan_thread:
            self.navigator.scan_thread.requestInterruption()
            self.navigator.scan_thread.wait(2000)

        # 프리패치 스레드 종료 대기 (최대 2초)
        QThreadPool.globalInstance().clear()
        QThreadPool.globalInstance().waitForDone(500)

        debug_print(f"========== 프로그램 종료 완료 ==========")
        event.accept()


    def mouseMoveEvent(self, event):
        """마우스 이동"""

        super().mouseMoveEvent(event)


    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.is_fullscreen:
                self._toggle_fullscreen()
                event.accept()
                return
        super().keyPressEvent(event)


# ============================================
# 창 상태 관리
# ============================================

    def _restore_window_state(self) -> None:
        """창 상태 복원"""
        debug_print(f"_restore_window_state() 시작")
        
        geometry = self.config.get('window.geometry')
        if geometry:
            try:
                from PySide6.QtCore import QByteArray
                
                # 문자열 → bytes → QByteArray
                geometry_bytes = geometry.encode('utf-8')  # 문자열을 bytes로
                geometry_array = QByteArray.fromBase64(geometry_bytes)  # base64 디코딩
                
                self.restoreGeometry(geometry_array)
                debug_print(f"창 geometry 복원 완료")
            except Exception as e:
                warning_print(f"창 geometry 복원 실패: {e}")
        
        # State 복원
        state = self.config.get('window.state')
        if state:
            try:
                from PySide6.QtCore import QByteArray
                
                # 문자열 → bytes → QByteArray
                state_bytes = state.encode('utf-8')
                state_array = QByteArray.fromBase64(state_bytes)
                
                self.restoreState(state_array)
                debug_print(f"창 state 복원 완료")
            except Exception as e:
                warning_print(f"창 state 복원 실패: {e}")

        splitter_sizes = self.config.get('window.splitter_sizes')
        if splitter_sizes and isinstance(splitter_sizes, list):
            if len(splitter_sizes) == 2:
                splitter_sizes = [0] + splitter_sizes   # 구버전 config 자동 마이그레이션
            if len(splitter_sizes) == 3:
                self.h_splitter.setSizes(splitter_sizes)

        debug_print(f"_restore_window_state() 완료")


    def _restore_ui_visibility(self) -> None:
        """저장된 UI 가시성 복원"""
        metadata_visible = self.config.get_ui_visibility("metadata")
        thumbnail_visible = self.config.get_ui_visibility("thumbnail_bar")
        statusbar_visible = self.config.get_ui_visibility("status_bar")
        perf_visible = self.config.get_ui_visibility("perf_overlay")  # 기본 False
        if perf_visible:
            self.status_ctrl.toggle_performance_overlay(True)

        folder_explorer_visible = self.config.is_folder_explorer_visible()
        if folder_explorer_visible:
            # QTimer로 지연: splitter 크기 복원 후 실행
            QTimer.singleShot(50, lambda: self.toggle_folder_explorer(True))

        # 썸네일바와 상태바는 바로 적용
        self._toggle_thumbnail_bar(thumbnail_visible)
        self._toggle_status_bar(statusbar_visible)

        # 메타데이터는 UI가 준비된 후 적용 (QTimer 사용)
        if not metadata_visible:
            # 숨김 상태면 바로 적용
            self._toggle_metadata(False)
        else:
            # 표시 상태면 UI 렌더링 후 크기 조정
            QTimer.singleShot(0, lambda: self._toggle_metadata(True))


# ============================================
# 파일/폴더 열기
# ============================================

    def open_image(self, file_path: Path) -> None:
        """이미지 파일 열기"""

        if not self._initialization_complete:
            debug_print(f"초기화 대기 중... 100ms 후 재시도")
            QTimer.singleShot(100, lambda: self.open_image(file_path))
            return

        if self._edit_lock_guard("파일 열기"):
            return

        if not file_path.exists():
            warning_print(f"파일 없음: {file_path}")
            return

        folder = file_path.parent

        if self.folder_explorer.isVisible():
            try:
                self.folder_explorer.navigate_to_folder(folder)
            except Exception:
                pass

        # 이미 같은 폴더라면 재스캔 없이 인덱스만 이동
        if self.navigator.current_folder == folder and self.navigator.image_files:
            try:
                index = self.navigator.image_files.index(file_path)
                self.navigator.go_to(index)          # #5 패치에서 추가될 메서드
                info_print(f"같은 폴더 내 파일 이동: {file_path.name} → index {index}")
                return
            except ValueError:
                warning_print(f"파일이 목록에 없음, 재스캔: {file_path.name}")

        # 다른 폴더이거나 목록에 없으면 → 재스캔
        # 기존 클로저 잔존 방지: 항상 이전 것을 먼저 해제 후 재연결
        self._pending_file_to_open = file_path
        self._open_first_on_scan   = False   # open_image는 첫 번째 자동선택 불필요

        self.navigator.scan_folder(folder)
        # 클로저 완전 제거: _on_folder_scan_completed 단일 경로로 처리


    def open_folder(self, folder_path: Path) -> None:
        
        if not self._initialization_complete:
            QTimer.singleShot(100, lambda: self.open_folder(folder_path))
            return

        if self._edit_lock_guard("파일 열기"):
            return

        if not folder_path.is_dir():
            warning_print(f"폴더가 아님: {folder_path}")
            return

        # 폴더 열기 시 첫 번째 이미지 자동 표시 플래그
        # scan_folder()는 비동기 → 완료 시 on_folder_scan_completed()에서 처리
        self._pending_file_to_open = None        # 특정 파일 없음
        self._open_first_on_scan   = True        # 첫 번째 이미지 자동 선택 요청

        # ── 추가: FolderExplorer 동기화 ──
        if hasattr(self, "folder_explorer") and self.folder_explorer.isVisible():
            try:
                self.folder_explorer.navigate_to_folder(folder_path)
            except Exception:
                pass

        self.navigator.scan_folder(folder_path)


    def _open_file_dialog(self):          
        self.file_manager.open_file_dialog()


    def _open_folder_dialog(self):        
        self.file_manager.open_folder_dialog()


    @Slot(Path)
    def _on_file_dropped(self, path: Path) -> None:
        """파일/폴더 드롭 이벤트 (ImageViewer에서 발생)"""
        debug_print(f"========== _on_file_dropped 호출 ==========")
        debug_print(f"드롭된 경로: {path}")
        debug_print(f"존재 여부: {path.exists()}")
        
        if not path.exists():
            error_print(f"경로가 존재하지 않음: {path}")
            return
        
        if path.is_file():
            info_print(f"파일 열기: {path.name}")
            self.open_image(path)
        
        elif path.is_dir():
            info_print(f"폴더 열기: {path}")
            self.open_folder(path)
        
        else:
            error_print(f"알 수 없는 타입: {path}")
        
        debug_print(f"========== _on_file_dropped 처리 완료 ==========")


    def _on_folder_scan_completed(self, filecount: int) -> None:
        info_print(f"폴더 스캔 완료: {filecount}개")

        current_folder = self.navigator.current_folder

        if hasattr(self, "folder_explorer") and self.folder_explorer.isVisible() and current_folder:
            self.folder_explorer.navigate_to_folder(current_folder)

        if current_folder:
            self.folder_watcher.start_watching(current_folder)
            if self.folder_explorer:
                self.folder_explorer.refresh_empty_state(current_folder)

        if filecount == 0:
            if self.navigator.current_folder:
                self.folder_explorer.mark_empty_folder(self.navigator.current_folder)
            self._handle_empty_folder()
            return

        image_list = self.navigator.get_image_list()

        # 초기 인덱스 결정 로직을 명확하게 정리
        if self._pending_file_to_open is not None:
            # open_image()로 특정 파일을 열도록 요청된 경우
            target = self._pending_file_to_open
            self._pending_file_to_open = None   # 소비 즉시 초기화
            try:
                current_index = image_list.index(target)
                info_print(f"pending 파일 인덱스: {current_index}/{filecount}")
            except ValueError:
                warning_print(f"pending 파일 목록에 없음: {target.name}, index=0으로 fallback")
                current_index = 0
            self.navigator.current_index = current_index

        elif self._open_first_on_scan:
            # open_folder()로 폴더를 열도록 요청된 경우
            self._open_first_on_scan = False    # 소비 즉시 초기화
            current_index = 0
            self.navigator.current_index = 0
            info_print("폴더 열기: 첫 번째 이미지 자동 선택")

        else:
            # 폴더 감시에 의한 reload 등 → 현재 인덱스 유지
            current_index = max(0, min(
                self.navigator.current_index,
                len(image_list) - 1
            ))
            self.navigator.current_index = current_index

        self.cache_manager.set_image_list(image_list)
        self.thumbnail_bar.set_image_list(image_list, current_index)
        #self._load_current_image()

        # 썸네일 로딩(16ms 타이머) 시작 이후로 트리 탐색을 지연
        # set_image_list의 singleShot(16ms)보다 뒤에 실행되도록 충분한 여유 부여
        if hasattr(self, "folder_explorer") and self.folder_explorer.isVisible() and current_folder:
            QTimer.singleShot(50, lambda: self._sync_folder_explorer(current_folder))


    def _sync_folder_explorer(self, folder: Path) -> None:
        """폴더 탐색기 트리 동기화 (썸네일 로딩 시작 이후 실행)"""
        if hasattr(self, "folder_explorer") and self.folder_explorer.isVisible():
            self.folder_explorer.navigate_to_folder(folder)


# ============================================
# 이미지 로딩 및 표시
# ============================================

    def _load_current_image(self) -> None:
        """현재 이미지 로드"""

        # 편집 모드 활성 상태에서 이미지 이동 시 자동 취소 종료
        # - 저장하지 않고 종료 (이동 = 명시적 포기 의사)
        # - 적용 후 저장은 사용자가 ✔ 버튼으로 명시적으로 해야 함
        if hasattr(self, 'image_viewer') and getattr(self.image_viewer, '_edit_mode', False):
            self.image_viewer._edit_cancel()
            self._show_status_message(t('msg.edit_auto_exit'), 2000)
            debug_print("편집 모드 자동 종료: 이미지 이동 감지")

        self._current_file = self.navigator.current()
        if not self._current_file:
            warning_print(f"_load_current_image: current_file이 None")
            self._handle_empty_folder() 
            return

        debug_print(f"_load_current_image 시작: {self._current_file.name}")

        self.perf_monitor.start_load()

        if hasattr(self, 'overlay_widget') and self.overlay_widget:
            self.overlay_widget.stop_map_loader()

        # 애니메이션 체크
        is_animated = self.cache_manager.loader.is_animated(self._current_file)

        if is_animated:
            # APNG vs GIF/WebP 분기
            if self.cache_manager.loader.is_apng(self._current_file):
                # QMovie 불가 → Pillow 프레임 경로
                self.image_viewer.set_apng_image(self._current_file)
            else:
                # 기존 GIF / WebP 경로 (변경 없음)
                movie = self.cache_manager.loader.load_animated(self._current_file)
                if movie:
                    self.image_viewer.set_animated_image(movie, file_path=self._current_file)
        else:
            viewport_size = self.image_viewer.get_viewport_size()
            pixmap = self.cache_manager.get(
                self.navigator.current_index,
                viewport_size,
                load_full=True
            )
            if pixmap:
                self.image_viewer.set_image(pixmap)
        
        # 오버레이 geometry 설정
        if hasattr(self, 'overlay_widget') and self.overlay_widget is not None:
            self.overlay_widget.setGeometry(self.image_viewer.rect())
            self.overlay_widget.raise_()
            debug_print(f"오버레이 geometry 설정: {self.overlay_widget.geometry()}")
        
        # 메타데이터 로드
        metadata = self.metadata_panel.load_metadata(self._current_file)
        self._trigger_map_prefetch(self.navigator.current_index)

        debug_print(f"메타데이터 로드 완료: {list(metadata.keys()) if metadata else 'None'}")
        
        # GPS 정보 설정
        if metadata and 'gps' in metadata and metadata['gps']:
            gps = metadata['gps']
            self.image_viewer.set_gps_info(gps['latitude'], gps['longitude'])
        else:
            self.image_viewer.set_gps_info(None, None)
        
        # 오버레이 업데이트
        if metadata:
            debug_print(f"_load_current_image: _update_overlay() 호출")
            self._update_overlay(self._current_file, metadata)
        else:
            warning_print(f"메타데이터가 없어서 오버레이 업데이트 생략")
        
        # 하이라이트 상태
        is_highlighted = self.navigator.is_current_highlighted()
        self.image_viewer.set_highlight_state(is_highlighted)
        
        # UI 업데이트
        self._update_statusbar()
        self.thumbnail_bar.set_current_index(self.navigator.current_index)
        self.setWindowTitle(f"{self._current_file.name} - dodoRynx")
        
        self.perf_monitor.end_load()
        
        debug_print(f"_load_current_image 완료")


    @Slot(int)
    def _on_index_changed(self, index: int) -> None:
        """네비게이터 인덱스 변경 시"""
        debug_print(f"_on_index_changed: index={index}")
        if index < 0:                    
            self._handle_empty_folder()
            return
        self._load_current_image()


    @Slot(int)
    def _on_full_image_loaded(self, index: int) -> None:
        """고품질 이미지 로드 완료"""
        debug_print(f"_on_full_image_loaded: index={index}, current_index={self.navigator.current_index}")
        if index == self.navigator.current_index:
            # 회전 미리보기 활성 상태면 고품질 이미지로 덮어쓰지 않음
            rot_state = self.rotation_manager.get_state()
            if rot_state and rot_state.file_path == self._current_file:
                debug_print(f"_on_full_image_loaded: 회전 미리보기 활성 → 고품질 교체 건너뜀")
                return
            debug_print(f"현재 이미지 → 고품질로 갱신")
            viewport_size = self.image_viewer.get_viewport_size()
            pixmap = self.cache_manager.get(index, viewport_size, load_full=False)
            if pixmap:
                debug_print(f"고품질 이미지 적용: {pixmap.width()}x{pixmap.height()}")
                self.image_viewer.replace_pixmap(pixmap)
            else:
                warning_print(f"고품질 이미지 가져오기 실패")
        else:
            debug_print(f"다른 이미지 ({index}) → 무시")


    def _trigger_map_prefetch(self, current_index: int) -> None:
        _prefetcher.cancel()

        file_list = self.navigator.image_files
        if not file_list or len(file_list) < 2:
            return

        adjacent_files = []
        for offset in range(-5, 6):
            if offset != 0:
                i = current_index + offset
                if 0 <= i < len(file_list):
                    adjacent_files.append(file_list[i])

        if not adjacent_files:
            return

        zoom = self.config.get_gps_map_setting("default_zoom", 15)

        reader = _GpsReader(adjacent_files, zoom, self._prefetch_signals)
        QThreadPool.globalInstance().start(reader)


# ============================================
# 이미지 네비게이션
# ============================================

    def _next_image(self) -> None:
        if self._edit_lock_guard("이미지 이동"): return
        if not self.navigator.image_files: return
        self._clear_all_temp_highlights()
        if not self.navigator.next():           # 경계 피드백만 담당
            self._show_status_message("This is the last image.", 1200)
        # _load_current_image() 제거 — go_to() → index_changed → _on_index_changed가 처리
        

    def _previous_image(self) -> None:
        if self._edit_lock_guard("이미지 이동"): return
        if not self.navigator.image_files: return
        self._clear_all_temp_highlights()
        if not self.navigator.previous():
            self._show_status_message("This is the first image.", 1200)


    def _first_image(self) -> None:
        if self._edit_lock_guard("이미지 이동"): return
        if not self.navigator.image_files: return
        self._clear_all_temp_highlights()
        self.navigator.first()                  # 반환값 불필요
        

    def _last_image(self) -> None:
        if self._edit_lock_guard("이미지 이동"): return
        if not self.navigator.image_files: return
        self._clear_all_temp_highlights()
        self.navigator.last()
        

    @Slot(Path)
    def _on_highlight_toggle_requested(self, file_path: Path) -> None:
        """ThumbnailBar Ctrl+클릭 → Navigator 토글 → 시그널로 ThumbnailBar에 반영"""
        self.navigator.toggle_highlight(file_path)  # highlight_changed 자동 emit
        count = self.navigator.get_highlight_count()
        self._show_status_message(t('status.highlight_count', count=count), 1500)


    @Slot(int, int, bool)
    def _on_highlight_range_requested(self, start: int, end: int, is_ctrl: bool) -> None:
        image_list = self.navigator.image_files

        if is_ctrl:
            # Ctrl+Shift: 범위 내 하이라이트 해제
            # toggle() 반복이지만 해제만 하므로 highlight_changed가 실제 변경 시에만 emit
            for i in range(start, end + 1):
                if i < len(image_list):
                    fp = image_list[i]
                    if self.navigator.is_highlighted(fp):
                        self.navigator.toggle_highlight(fp)   # highlight_changed emit
        else:
            # 일반 Shift: set_highlights() 1회 호출 → highlights_set 1회 emit
            # → ThumbnailBar.on_highlights_set() 1회 일괄 UI 갱신
            new_set = {
                image_list[i]
                for i in range(start, end + 1)
                if i < len(image_list)
            }
            self.navigator.set_highlights(new_set)  # highlights_set.emit(new_set) 1회

        count = self.navigator.get_highlight_count()
        action = "해제" if is_ctrl else "선택"
        self._show_status_message(
            t('thumbnail_bar.highlight_range',
            count=end - start + 1, action=action, total=count),
            1500
        )


    @Slot(QPoint)
    def _on_thumbnail_context_menu(self, global_pos: QPoint) -> None:
        """ThumbnailBar 우클릭 → MainWindow에서 메뉴 생성"""
        menu = self.create_context_menu(self.thumbnail_bar)
        menu_height = menu.sizeHint().height()
        menu.exec(QPoint(global_pos.x(), global_pos.y() - menu_height))


    @Slot(int)
    def _on_thumbnail_clicked(self, index: int) -> None:
        """썸네일 클릭"""
        if self._edit_lock_guard("이미지 이동"):
            return
        self._clear_all_temp_highlights()
        self.navigator.go_to(index)


    @Slot(int)
    def _on_wheel_navigation(self, delta: int) -> None:
        """마우스 휠 네비게이션"""
        if self._edit_lock_guard("이미지 이동"):
            return

        # ===== 확실한 임시 하이라이트 해제 =====
        self._clear_all_temp_highlights()
        
        if delta > 0:
            self._previous_image()
        else:
            self._next_image()


    def _clear_all_temp_highlights(self) -> None:
        """
        모든 임시 하이라이트 확실히 해제
        Navigator + ThumbnailBar 동시 처리
        """
        # Navigator 해제
        if hasattr(self.navigator, '_temporary_highlights'):
            if self.navigator._temporary_highlights:
                count = len(self.navigator._temporary_highlights)
                self.navigator._temporary_highlights.clear()
                debug_print(f"Navigator 임시 하이라이트 해제: {count}개")
        
        # ThumbnailBar 해제
        if hasattr(self.thumbnail_bar, 'temp_highlighted_files'):
            if self.thumbnail_bar.temp_highlighted_files:
                count = len(self.thumbnail_bar.temp_highlighted_files)
                self.thumbnail_bar.temp_highlighted_files.clear()
                
                # UI 업데이트
                for item in self.thumbnail_bar.thumbnail_items:
                    item.set_temp_highlighted(False)
                
                debug_print(f"ThumbnailBar 임시 하이라이트 해제: {count}개")


# ============================================
# 파일 작업
# ============================================

    def _delete_file(self):               
        self.file_manager.delete_file()


    def _cut_file(self):                  
        self.file_manager.cut_file()


    def _paste_file(self) -> None:
        """
        Ctrl+V / 메뉴 Paste 공용 진입점.
        - FolderExplorer에 포커스가 있으면: 폴더 탐색기 기준 붙여넣기
        - 아니면: 기존대로 파일 뷰어 기준 붙여넣기
        """
        try:
            if hasattr(self, "folder_explorer") and self.folder_explorer.isVisible():
                if self._focus_is_in_folder_explorer():
                    # FolderExplorer 쪽 붙여넣기 (현재 선택/현재 위치 폴더)
                    # NOTE: folder_explorer.py에 _paste_folder(dst) 존재
                    self.folder_explorer._paste_folder(None)  # pylint: disable=protected-access
                    return
        except Exception:
            pass

        # fallback: 기존 파일 기준 붙여넣기 (target = current_file.parent)
        if hasattr(self, "file_manager"):
            if hasattr(self.file_manager, "paste_file"):
                self.file_manager.paste_file()
            # elif hasattr(self.file_manager, "pastefile"):
            #     self.file_manager.paste_file()


    def _copy_file(self):                 
        self.file_manager.copy_file()


    def _copy_file_path(self):            
        self.file_manager.copy_file_path()


    def _copy_image_to_clipboard(self):   
        self.file_manager.copy_image_to_clipboard()


    def _rename_file(self):               
        self.file_manager.rename_file()


    def _open_file_location(self):        
        self.file_manager.open_file_location()


    def _show_file_properties(self):      
        self.file_manager.show_file_properties()


    def _set_op_status(self, text: str, current: int = 0, total: int = 0) -> None:
        if not text:
            self.status_ctrl.on_file_op_finished()
            return

        display = text if len(text) <= 42 else f"...{text[-39:]}"

        if current == 0 and total == 0:
            self.status_ctrl.on_file_op_started(display)
        else:
            # task_update → task_progress (기존 패턴에 맞게 수정)
            self.status_ctrl.on_file_op_progress(display, current, total)


    def _clear_op_status(self, done_msg: str = "", duration: int = 2500) -> None:
        # "진행 표시 숨기고 완료 토스트 표시"
        self._set_op_status("")
        if done_msg:
            self._show_status_message(done_msg, duration)


    def _run_file_worker(self, operation, files, target_folder=None):
        self.file_manager.run_file_worker(operation, files, target_folder)


    @Slot(Path)
    def _on_fs_file_added(self, file_path: Path) -> None:

        # 현재 폴더 소속 검증 추가
        if file_path.parent != self.navigator.current_folder:
            return        
        
        # 1) 기존 동작 유지: 파일매니저 쪽 디바운스/리로드 로직
        self.file_manager.on_file_added(file_path)

        # 2) FolderExplorer의 X(빈폴더) 상태 즉시 갱신
        if hasattr(self, "folder_explorer") and self.folder_explorer:
            self.folder_explorer.on_files_changed(file_path)

    @Slot(Path)
    def _on_fs_file_deleted(self, file_path: Path) -> None:
        self.file_manager.on_file_deleted(file_path)
        if hasattr(self, "folder_explorer") and self.folder_explorer:
            self.folder_explorer.on_files_changed(file_path)

    @Slot(Path)
    def _on_fs_file_modified(self, file_path: Path) -> None:
        self.file_manager.on_file_modified(file_path)
        if hasattr(self, "folder_explorer") and self.folder_explorer:
            self.folder_explorer.on_files_changed(file_path)

    @Slot(Path, Path)
    def _on_fs_file_moved(self, src_path: Path, dest_path: Path) -> None:
        self.file_manager.on_file_moved(src_path, dest_path)
        if hasattr(self, "folder_explorer") and self.folder_explorer:
            # moved는 (src,dst)라서 FolderExplorer에는 각각 1개씩 알려줘야 안전
            self.folder_explorer.on_files_changed(src_path)
            self.folder_explorer.on_files_changed(dest_path)

    @Slot(list)
    def _on_fs_batch_deleted(self, deleted_files: list) -> None:
        self.file_manager.on_batch_deleted(deleted_files)

        if hasattr(self, "folder_explorer") and self.folder_explorer:
            # 삭제된 파일들의 부모 폴더들만 갱신(중복 제거)
            parents = set()
            for fp in deleted_files:
                try:
                    parents.add(fp.parent)
                except Exception:
                    pass
            for p in parents:
                try:
                    self.folder_explorer.refresh_empty_state(p)
                except Exception:
                    pass

    @Slot(list)
    def _on_fs_batch_added(self, added_files: list) -> None:
        """파일 배치 추가 이벤트 (FolderWatcher.batch_added)"""
        if not added_files:
            return
        info_print(f"파일 배치 추가 감지: {len(added_files)}개")
        # 이미지 목록 재스캔으로 반영
        self.navigator.reload_async()
        # FolderExplorer 빈폴더 상태 갱신
        for fp in added_files:
            self.folder_explorer.on_files_changed(fp)


    @Slot(Path)
    def _on_folder_selected_from_explorer(self, folder_path: Path) -> None:
        """
        FolderExplorer에서 폴더를 선택했을 때 호출.

        open_folder()와의 차이:
        - 동일 폴더 재선택 시: 재스캔 없이 현재 인덱스 유지
        - 다른 폴더 선택 시: 정상적으로 open_folder() 위임

        이렇게 분리하는 이유:
        open_folder()는 항상 _open_first_on_scan=True를 설정하므로
        폴더 탐색기에서 현재 폴더를 다시 클릭해도 index=0으로 초기화됨.
        탐색기는 '탐색'이 목적이므로 현재 위치를 유지하는 것이 자연스러움.
        """
        if not folder_path or not folder_path.is_dir():
            warning_print(f"folder_selected: 유효하지 않은 경로 — {folder_path}")
            return

        # 현재 폴더와 동일하면 재스캔 없이 현재 상태 유지
        if self.navigator.current_folder == folder_path and self.navigator.image_files:
            info_print(f"folder_selected: 동일 폴더 재선택 — 재스캔 건너뜀: {folder_path.name}")
            # FolderExplorer UI만 동기화
            self.folder_explorer.navigate_to_folder(folder_path)
            return

        # 다른 폴더이면 open_folder()로 위임
        self.open_folder(folder_path)


# ============================================
# 하이라이트 기능
# ============================================

    def _toggle_highlight(self):          
        self.file_manager.toggle_highlight()


    def _clear_all_highlights(self):      
        self.file_manager.clear_all_highlights()


    def _delete_highlighted_files(self):  
        self.file_manager.delete_highlighted_files()


    def _copy_highlighted_files(self):    
        self.file_manager.copy_highlighted_files()


    def _cut_highlighted_files(self):     
        self.file_manager.cut_highlighted_files()

    
    def _update_highlight_ui(self, file_path: Path, is_highlighted: bool):

        """하이라이트 UI 동기화 (Single Source of Truth)"""
        # ThumbnailBar 업데이트
        if is_highlighted:
            self.thumbnail_bar.highlighted_files.add(file_path)
        else:
            self.thumbnail_bar.highlighted_files.discard(file_path)
        
        # 썸네일 아이템 업데이트
        current_index = self.navigator.current_index
        if 0 <= current_index < len(self.thumbnail_bar.thumbnail_items):
            self.thumbnail_bar.thumbnail_items[current_index].set_highlighted(is_highlighted)


    def _sync_highlight_state(self, file_path: Optional[Path] = None, force_full_sync: bool = False) -> None:
        """
        하이라이트 상태 동기화 (Single Source of Truth: Navigator).
        """
        # QApplication.instance() 체크로 Qt 이벤트 루프 안전성 확보
        if QApplication.instance() is None:
            return

        if force_full_sync or file_path is None:
            # ── 전체 동기화 ──────────────────────────────────────
            debug_print("하이라이트 전체 동기화 시작")

            highlighted: set[Path] = set(self.navigator.get_highlighted_files())

            # ThumbnailBar.highlighted_files를 navigator 기준으로 완전 교체 (SSOT)
            self.thumbnail_bar.highlighted_files = highlighted.copy()

            # 모든 썸네일 아이템 일괄 업데이트
            image_list   = self.thumbnail_bar.image_list
            thumb_items  = self.thumbnail_bar.thumbnail_items
            item_count   = len(thumb_items)
            for i, img_path in enumerate(image_list):
                if i >= item_count:
                    break
                thumb_items[i].set_highlighted(img_path in highlighted)

            debug_print(f"하이라이트 전체 동기화 완료: {len(highlighted)}개")

        else:
            # ── 단일 파일 동기화 ─────────────────────────────────
            debug_print(f"하이라이트 단일 동기화: {file_path.name}")

            # Navigator가 SSOT → 항상 navigator에서 상태 확인
            is_highlighted = self.navigator.is_highlighted(file_path)

            # ThumbnailBar.highlighted_files 갱신
            if is_highlighted:
                self.thumbnail_bar.highlighted_files.add(file_path)
            else:
                self.thumbnail_bar.highlighted_files.discard(file_path)

            # 썸네일 아이템 UI 갱신
            image_list  = self.thumbnail_bar.image_list
            thumb_items = self.thumbnail_bar.thumbnail_items
            try:
                index = image_list.index(file_path)
                if 0 <= index < len(thumb_items):
                    thumb_items[index].set_highlighted(is_highlighted)
            except ValueError:
                warning_print(f"하이라이트 동기화: 썸네일 목록에 없음 — {file_path.name}")


    def _sync_highlights_to_thumbnail_bar(self) -> None:
        """
        (Deprecated) Navigator의 하이라이트 상태를 ThumbnailBar에 동기화
        대신 _sync_highlight_state()를 사용하세요
        """
        warning_print(f"_sync_highlights_to_thumbnail_bar()는 deprecated입니다. _sync_highlight_state()를 사용하세요.")
        self._sync_highlight_state(force_full_sync=True)


# ============================================
# UI 토글
# ============================================

    def _toggle_metadata(self, visible: Optional[bool] = None) -> None:
        if visible is None:
            visible = not self.metadata_panel.isVisible()
        self.metadata_panel.setVisible(visible)
        self.image_viewer.metadata_visible = visible
        self.config.set_ui_visibility("metadata", visible)

        sizes = self.h_splitter.sizes()          # [fe_w, viewer_w, meta_w]
        fe_w, viewer_w, meta_w = sizes[0], sizes[1], sizes[2]
        total = fe_w + viewer_w + meta_w

        if visible:
            new_meta_w   = 280
            new_viewer_w = max(100, total - fe_w - new_meta_w)
            self.h_splitter.setSizes([fe_w, new_viewer_w, new_meta_w])
        else:
            self.h_splitter.setSizes([fe_w, viewer_w + meta_w, 0])


    def _toggle_thumbnail_bar(self, visible: Optional[bool] = None) -> None:
        """썸네일바 토글"""
        if visible is None:
            visible = not self.thumbnail_bar.isVisible()
        
        self.thumbnail_bar.setVisible(visible)
        self.image_viewer.thumbnail_visible = visible
        self.config.set_ui_visibility("thumbnail_bar", visible)
        info_print(f"썸네일바: {'표시' if visible else '숨김'}")


    def _toggle_status_bar(self, visible: Optional[bool] = None) -> None:
        self.status_ctrl.toggle(visible)


    def _toggle_overlay(self, visible: Optional[bool] = None) -> None:
        """오버레이 토글"""

        # 편집 모드 중 오버레이 활성화 차단
        if visible and self.image_viewer._edit_mode:
            warning_print("편집 모드 중에는 오버레이를 활성화할 수 없습니다.")
            return

        if visible is None:
            visible = not self.overlay_widget.isVisible()

        # 상태 저장 (단일 소스)
        self.overlay_enabled = visible

        # 설정 저장
        self.config.set_overlay_setting("enabled", visible)
        self.config.save()

        # 오버레이 설정 다시 로드
        self._load_overlay_settings()

        # 현재 이미지 다시 업데이트
        if self._current_file:
            metadata = self.metadata_panel.get_current_metadata()
            if metadata:
                self._update_overlay(self._current_file, metadata)

        info_print(f"오버레이: {'표시' if visible else '숨김'}")


    def _toggle_fullscreen(self) -> None:
        """전체화면 토글"""
        if self.is_fullscreen:
            # 전체화면 종료

            app = QApplication.instance()
            if app:
                app.removeEventFilter(self)
            QApplication.restoreOverrideCursor()

            self.thumbnail_bar.setVisible(self._pre_fullscreen_thumb_visible)
            self.metadata_panel.setVisible(self._pre_fullscreen_meta_visible)
            self.statusbar.setVisible(self._pre_fullscreen_status_visible)

            if self._pre_fullscreen_overlay_visible:
                self.overlay_widget.show_overlay() 
            else:
                self.overlay_widget.hide_overlay() 

            self.showNormal()
            self.is_fullscreen = False

            # folder_explorer: config에 저장된 상태로 복원
            if self.config.is_folder_explorer_visible():
                self.folder_explorer.setVisible(True)

            # 타이머 정지
            if hasattr(self, 'hide_timer') and self.hide_timer.isActive():
                self.hide_timer.stop()
            
            # 커서 표시
            self.unsetCursor()
            
            # ===== 미니맵 상태 업데이트 =====
            if hasattr(self, 'image_viewer'):
                self.image_viewer._update_minimap()
            
        else:
            # 전체화면 진입
            self._pre_fullscreen_thumb_visible = self.thumbnail_bar.isVisible()
            self._pre_fullscreen_meta_visible  = self.metadata_panel.isVisible()
            self._pre_fullscreen_status_visible = self.statusbar.isVisible()
            self.overlay_widget.hide_overlay()

            self.showFullScreen()
            self.metadata_panel.hide()
            self.statusbar.hide()
            self.thumbnail_bar.hide()
            self.folder_explorer.setVisible(False)
            self.is_fullscreen = True

            app = QApplication.instance()
            if app:
                app.installEventFilter(self)

            # ===== 미니맵 숨김 =====
            if hasattr(self, 'image_viewer') and hasattr(self.image_viewer, 'minimap'):
                self.image_viewer.minimap.hide()
            
            # 3초 후 UI 숨김 타이머 시작
            if hasattr(self, 'hide_timer'):
                self.hide_timer.start(3000)


    def _toggle_performance_overlay(self, visible: Optional[bool] = None) -> None:
        self.status_ctrl.toggle_performance_overlay(visible)


    def toggle_folder_explorer(self, visible: Optional[bool] = None) -> None:
        """폴더 탐색기 토글 (ON/OFF)."""
        if visible is None:
            visible = not self.folder_explorer.isVisible()

        if visible:
            current_folder = (
                self.navigator.current_folder
                if self.navigator.current_folder and self.navigator.current_folder.is_dir()
                else None
            )

            self.folder_explorer.activate(current_folder)
            self.folder_explorer.setVisible(True)

            sizes = self.h_splitter.sizes()
            if sizes and sizes[0] == 0:
                new_fe_width = self.config.get_folder_explorer_setting("panel_width", 220)
                new_fe_width = max(120, min(int(new_fe_width), 400))

                sizes[0] = new_fe_width
                sizes[1] = max(400, sizes[1] - new_fe_width)
                self.h_splitter.setSizes(sizes)

            QTimer.singleShot(
                0,
                lambda: self.folder_explorer.setFocus(Qt.FocusReason.OtherFocusReason)
            )

        else:
            sizes = self.h_splitter.sizes()
            if sizes and sizes[0] > 0:
                self.config.set_folder_explorer_setting("panel_width", sizes[0])

            self.folder_explorer.deactivate()
            self.folder_explorer.setVisible(False)

            if sizes:
                sizes[1] = sizes[0] + sizes[1]
                sizes[0] = 0
                self.h_splitter.setSizes(sizes)

        self.config.set_folder_explorer_visible(bool(visible))

        debug_print(f"toggle_folder_explorer: {'ON' if visible else 'OFF'}")


    def _focus_is_in_folder_explorer(self) -> bool:
        if not hasattr(self, "folder_explorer"):
            return False
        if not self.folder_explorer.isVisible():
            return False

        fw = QApplication.focusWidget()
        if fw is None:
            return False

        # folder_explorer 자신이거나, 그 자식 위젯에 포커스가 있으면 True
        return (fw is self.folder_explorer) or self.folder_explorer.isAncestorOf(fw)


    def _auto_hide_ui(self) -> None:
        """UI 자동 숨김"""
        if self.is_fullscreen:
            QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)


    def eventFilter(self, obj, event) -> bool:
        """전체화면 중 모든 하위 위젯의 마우스 이동 감지"""
        if (self.is_fullscreen
                and event.type() == event.Type.MouseMove):
            QApplication.restoreOverrideCursor()
            self.hide_timer.start(3000)
        return False  # 이벤트 소비 안 함 (다른 위젯 정상 동작 유지)
            

# ============================================
# 오버레이 관리
# ============================================

    def _load_overlay_settings(self) -> None:
        """오버레이 설정 로드"""
        debug_print(f"_load_overlay_settings() 시작")
        
        # 설정 파일 경로 출력
        config_path = self.config.config_file
        debug_print(f"설정 파일 경로: {config_path}")
        
        if not hasattr(self, 'overlay_widget') or self.overlay_widget is None:
            error_print(f"overlay_widget이 없습니다!")
            return
        
        # 전체 오버레이 설정 출력
        overlay_config = self.config.config.get("overlay", {})
        debug_print(f"전체 오버레이 설정:")
        for key, value in overlay_config.items():
            debug_print(f"  {key}: {value}")
        
        # 설정 로드
        self.overlay_enabled = self.config.get_overlay_setting("enabled", False)
        show_file = self.config.get_overlay_setting("show_file_info", True)
        show_camera = self.config.get_overlay_setting("show_camera_info", True)
        show_exif = self.config.get_overlay_setting("show_exif_info", True)
        show_lens = self.config.get_overlay_setting("show_lens_info", False)
        show_gps = self.config.get_overlay_setting("show_gps_info", False)
        show_map = self.config.get_overlay_setting("show_map", False)
        opacity = self.config.get_overlay_setting("opacity", 0.8)
        position = self.config.get_overlay_setting("position", "top_left")
        
        debug_print(f"오버레이 설정 로드:")
        debug_print(f"  enabled={self.overlay_enabled}")
        debug_print(f"  show_file={show_file}, show_camera={show_camera}")
        debug_print(f"  show_exif={show_exif}, show_lens={show_lens}")
        debug_print(f"  show_gps={show_gps}, show_map={show_map}")
        debug_print(f"  opacity={opacity}, position={position}")
        
        # 오버레이 위젯 업데이트
        self.overlay_widget.update_settings(
            self.overlay_enabled, show_file, show_camera, show_exif, show_lens,
            show_gps, show_map, opacity, position
        )

        debug_print(f"오버레이 설정 로드 완료")


    def _update_overlay(self, file_path: Path, metadata: dict) -> None:
        """오버레이 데이터 업데이트 (딜레이 적용)"""
        
        debug_print(f"_update_overlay() 호출: {file_path.name}")
        debug_print(f"metadata keys: {list(metadata.keys())}")
        
        # overlay_widget 체크
        if not hasattr(self, 'image_viewer'):
            error_print(f"self.image_viewer 없음!")
            return
        
        if not hasattr(self.image_viewer, 'overlay_timer'):
            error_print(f"self.image_viewer.overlay_timer 없음!")
            return
        
        if not hasattr(self.image_viewer, 'overlay_widget'):
            error_print(f"self.image_viewer.overlay_widget 없음!")
            return
        
        if self.image_viewer.overlay_widget is None:
            error_print(f"self.image_viewer.overlay_widget is None!")
            return
        
        # ===== 현재 이미지 ID 캡처 (ImageViewer에서) =====
        current_image_id = self.image_viewer.current_image_id
        debug_print(f"오버레이 업데이트 요청: ID={current_image_id}, 파일={file_path.name}")
        
        overlay_data = {}
        
        # 1. 파일 정보
        if 'file' in metadata:
            file_info = metadata['file']
            
            if file_info.get('size'):
                overlay_data['file_size'] = file_info['size']
            
            if file_info.get('resolution'):
                resolution_str = file_info['resolution']
                try:
                    parts = resolution_str.replace('×', 'x').replace(' ', '').split('x')
                    if len(parts) == 2:
                        width = int(parts[0])
                        height = int(parts[1])
                        overlay_data['dimensions'] = (width, height)
                except:
                    pass

        # 3. EXIF 촬영 정보
        if 'camera' in metadata and metadata['camera']:
            camera = metadata['camera']

            camera_info = {}
            
            # 식별 정보
            if camera.get('make'):           camera_info['make']          = camera['make']
            if camera.get('model'):          camera_info['model']         = camera['model']
            if camera.get('date_taken'):     camera_info['date_taken']    = camera['date_taken']
            if camera.get('orientation'):    camera_info['orientation']   = camera['orientation']
            if camera.get('lens_make'):      camera_info['lens_make']     = camera['lens_make']
            if camera.get('lens_model'):     camera_info['lens_model']    = camera['lens_model']
            
            # 노출 파라미터 (e.g. "ISO 1600", "f/2.8", "1/250s", "24mm")
            if camera.get('iso'):            camera_info['iso']           = camera['iso']
            if camera.get('f_stop'):         camera_info['f_stop']        = camera['f_stop']
            if camera.get('exposure_time'):  camera_info['exposure_time'] = camera['exposure_time']
            if camera.get('focal_length'):   camera_info['focal_length']  = camera['focal_length']
            
            if camera_info:
                overlay_data['camera'] = camera_info

        # 4. GPS 정보
        if 'gps' in metadata and metadata['gps']:
            gps = metadata['gps']
            overlay_data['gps'] = {
                'latitude': gps['latitude'],
                'longitude': gps['longitude'],
                'display': gps['display']
            }
            
            if 'altitude' in gps:
                overlay_data['gps']['altitude'] = gps['altitude']
        
        debug_print(f"overlay_data 생성 완료: {list(overlay_data.keys())}")
        
        # ===== 타이머 ID와 함께 저장 (ImageViewer의 ID 사용) =====
        self.image_viewer.pending_overlay_data = (
            file_path, 
            overlay_data,
            current_image_id  # ImageViewer에서 가져온 ID
        )
        debug_print(f"pending_overlay_data 설정 완료, ID={current_image_id}")
        
        # 기존 타이머 중지 후 재시작
        if self.image_viewer.overlay_timer.isActive():
            debug_print(f"기존 오버레이 타이머 중지")
            self.image_viewer.overlay_timer.stop()
        
        # 타이머 시작
        self.image_viewer.overlay_timer.start(100)
        debug_print(f"오버레이 타이머 시작 (100ms), 검증 ID={current_image_id}")


    def _update_overlay_position(self) -> None:
        """오버레이 위치 업데이트"""
        if not hasattr(self, 'overlay_widget'):
            return
        
        debug_print(f"오버레이 위치 업데이트")
        
        # ImageViewer의 geometry에 맞춤
        self.overlay_widget.setGeometry(self.image_viewer.rect())
        self.overlay_widget.raise_()
        
        debug_print(f"overlay_widget geometry: {self.overlay_widget.geometry()}")
        debug_print(f"overlay_widget isVisible: {self.overlay_widget.isVisible()}")
        
        # 현재 메타데이터로 다시 표시
        if self._current_file and hasattr(self.metadata_panel, 'current_metadata'):
            metadata = self.metadata_panel.get_current_metadata()
            if metadata:
                self._update_overlay(self._current_file, metadata)


# ============================================
# 상태바 및 UI 업데이트
# ============================================

    def _update_statusbar(self) -> None:
        """상태바 파일 번호 업데이트"""
        self.status_ctrl.update_progress()


    def _update_performance_info(self) -> None:
        """성능 정보 업데이트"""

        try:
            stats      = self.cache_manager.get_stats()
            load_time  = self.perf_monitor.get_last_load_time()
            memory_mb  = float(stats.get("memory_mb", 0))
            cache_size = int(stats.get("cache_size", 0))
            hit_rate   = float(stats.get("hit_rate", 0))
            max_mem    = self.config.get("cache.max_memory_mb", 500)

            self.status_ctrl.update_performance_info(
                load_time_ms  = load_time,
                memory_mb     = memory_mb,
                cpu_usage     = self.current_cpu_usage,
                cache_size    = cache_size,
                hit_rate      = hit_rate,
                max_memory_mb = max_mem,
            )
        except Exception as e:
            error_print(f"[ERROR] 성능 정보 업데이트 실패: {e}")

    def _update_cpu_usage(self) -> None:
        """CPU 사용률 업데이트"""
        try:
            self.current_cpu_usage = self.perf_monitor.get_cpu_usage(interval=0.1)
            #debug_print(f"CPU: {self.current_cpu_usage:.1f}%")
        except Exception as e:
            error_print(f"[ERROR] CPU 측정 실패: {e}")
            self.current_cpu_usage = 0.0


    def _show_status_message(self, message: str, duration: int = 2000) -> None:
        """상태바 임시 메시지 표시"""
        self.status_ctrl.show_message(message, duration)


    def _hide_status_message(self) -> None:
        """상태 메시지 숨김"""
        self.status_bar._hide_status_message()


    @Slot(float)
    def _on_zoom_changed(self, zoom_factor: float) -> None:
        """줌 레벨 변경"""
        self.status_ctrl.on_zoom_changed(zoom_factor)


# ============================================
# 썸네일 및 정렬
# ============================================

    def _ensure_thumbnail_visible(self, index: int) -> None:
        """썸네일이 보이도록 스크롤 (헬퍼 메서드)"""
        if 0 <= index < len(self.thumbnail_bar.thumbnail_items):
            item = self.thumbnail_bar.thumbnail_items[index]
            
            # 레이아웃 강제 업데이트
            self.thumbnail_bar.thumbnail_container.updateGeometry()
            self.thumbnail_bar.scroll_area.updateGeometry()
            QApplication.processEvents()
            
            # 스크롤
            self.thumbnail_bar.scroll_area.ensureWidgetVisible(item, 200, 0)
            
            debug_print(f"썸네일 스크롤: {index}, x={item.x()}")


    def _on_sort_requested(self, sort_type: str, reverse: bool) -> None:
        """정렬 요청 → StatusBarController 위임"""
        self.status_ctrl._on_sort_requested(sort_type, reverse)


# ============================================
# 캐시 관리
# ============================================

    @Slot(int)
    def _on_cache_hit(self, index: int) -> None:
            self._update_performance_info()
    

    @Slot(int)
    def _on_cache_miss(self, index: int) -> None:
            self._update_performance_info()


# ============================================
# GPS 기능
# ============================================

    def _view_gps(self) -> None:
        """GPS 위치 보기"""
        if not self._current_file: 
            return
        if not self.image_viewer.current_gps:
            self._show_status_message(t('No GPS data.'), 1500) 
            return
        lat, lon = self.image_viewer.current_gps
        self._on_gps_clicked(lat, lon)
            

    @Slot(float, float)
    def _on_gps_clicked(self, lat: float, lon: float) -> None:
        """GPS 클릭"""
        from utils.gps_handler import GPSHandler
        
        browser = self.config.get('browser.path', 'system_default')
        map_service = self.config.get('map.service', 'naver')
        
        handler = GPSHandler(browser, map_service)
        handler.open_map(lat, lon)


    def _on_map_zoom_changed(self, zoom: int) -> None:
        """지도 줌 레벨 변경 (패널 → 오버레이 동기화) """
        # 오버레이 지도 줌 업데이트
        self.overlay_widget.update_map_zoom(zoom)
        self.config.save()
        info_print(f"지도 줌 레벨: {zoom}")


# ============================================
# 화면 캡쳐
# ============================================

    def _capture_to_clipboard(self) -> None:
        """미리보기 영역(오버레이 포함)을 클립보드에 복사"""
        try:
            # ===== 화면 직접 캡처 (오버레이 포함) =====
            if not self.image_viewer:
                QMessageBox.warning(self, t('capture.fail_title'), t('capture.no_viewer'))
                return
            
            # 뷰어의 화면 상 절대 좌표 가져오기
            viewport = self.image_viewer.viewport()
            global_pos = viewport.mapToGlobal(viewport.rect().topLeft())
            capture_rect = QRect(global_pos, viewport.size())
            
            # 화면 캡처
            screen = QApplication.primaryScreen()
            pixmap = screen.grabWindow(0, capture_rect.x(), capture_rect.y(), 
                                    capture_rect.width(), capture_rect.height())
            
            if pixmap.isNull():
                QMessageBox.warning(self, t('capture.fail_title'), t('capture.no_capture'))
                return
            
            # 클립보드에 복사
            clipboard = QApplication.clipboard()
            clipboard.setPixmap(pixmap)
            
            # 상태바에 메시지
            self._show_status_message(
                t('msg.capture_clipboard', width=pixmap.width(), height=pixmap.height()), 3000
            )
            info_print(f"클립보드 복사: {pixmap.width()}×{pixmap.height()}")
        
        except Exception as e:
            QMessageBox.critical(self, t('capture.error_title'), t('capture.error_msg', error=e))
            error_print(f"클립보드 복사 실패: {e}")


    def _capture_and_save(self) -> None:
        """미리보기 영역(오버레이 포함)을 파일로 저장"""
        try:
            # 현재 파일이 없으면 저장 불가
            if not self._current_file:
                QMessageBox.warning(self, t('capture.no_file_title'), t('capture.no_file_msg'))
                return
            
            # ===== 화면 직접 캡처 (오버레이 포함) =====
            if not self.image_viewer:
                QMessageBox.warning(self, t('capture.fail_title'), t('capture.no_viewer'))
                return
            
            # 뷰어의 화면 상 절대 좌표 가져오기
            viewport = self.image_viewer.viewport()
            global_pos = viewport.mapToGlobal(viewport.rect().topLeft())
            capture_rect = QRect(global_pos, viewport.size())
            
            # 화면 캡처
            screen = QApplication.primaryScreen()
            pixmap = screen.grabWindow(0, capture_rect.x(), capture_rect.y(), 
                                    capture_rect.width(), capture_rect.height())
            
            if pixmap.isNull():
                QMessageBox.warning(self, t('capture.fail_title'), t('capture.no_capture'))
                return
            
            # 저장 경로 생성
            original_path = self._current_file
            save_path = self._generate_capture_filename(original_path)
            
            # JPG로 저장
            if pixmap.save(str(save_path), "JPG", 95):
                # 상태바에 메시지
                self._show_status_message(t('msg.capture_saved', name=save_path.name), 3000)
                info_print(f"캡쳐 저장: {save_path}")
                
                # ===== 비 동기식 새로고침  =====
                self.navigator.reload_async()

                # 성공 메시지
                QMessageBox.information(
                    self,
                    t('capture.save_ok_title'),
                    t('capture.save_ok_msg',
                    name=save_path.name, width=pixmap.width(), height=pixmap.height()),
                )
            else:
                QMessageBox.critical(
                    self, t('capture.save_fail_title'), t('capture.save_fail_msg', path=save_path)
                )
        
        except Exception as e:
            QMessageBox.critical(self, t('capture.error_title'), t('capture.save_error_msg', error=e))
            error_print(f"캡쳐 저장 실패: {e}")


    def _generate_capture_filename(self, original_path: Path) -> Path:
        """캡쳐 파일명 생성 (중복 방지)"""
        # 원본 파일명에서 확장자 분리
        stem = original_path.stem
        parent = original_path.parent
        
        # 기본 파일명: dodoRynx.jpg
        base_name = f"{stem}_dodoRynx"
        save_path = parent / f"{base_name}.jpg"
        
        # 중복 확인 및 번호 추가
        counter = 1
        while save_path.exists():
            save_path = parent / f"{base_name}({counter}).jpg"
            counter += 1
        
        return save_path


# ============================================
# 설정 및 다이얼로그
# ============================================

    def _open_settings(self) -> None:
        """설정 열기"""
        from ui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self.config, self)

        # ── 시그널 연결 (accept() 내부에서 emit됨) ──────────────
        dialog.cache_settings_changed.connect(self._on_cache_settings_changed)
        dialog.overlay_settings_changed.connect(self._on_overlay_settings_changed)
        dialog.rendering_settings_changed.connect(self._on_rendering_settings_changed)

        # ── 캐시 삭제 버튼 ────────────────────────────────────────
        dialog.thumbnail_cache_clear_requested.connect(
            lambda: self.thumbnail_bar._thumb_cache.clear_memory()
        )
        # tile_cache_clear_requested는 settings_dialog 내부에서 shutil.rmtree로 처리

        # exec() 반환값 불필요 — 모든 처리는 시그널로
        dialog.exec()
        

    def _on_overlay_settings_changed(self):
        """오버레이 설정 변경 시 즉시 적용"""
        debug_print(f"오버레이 설정 변경 - 즉시 적용")
        
        if hasattr(self, 'overlay_widget') and self.overlay_widget:
            # 설정 다시 로드
            scale_value = self.config.get("overlay.scale", 100)
            self.overlay_widget.set_scale(scale_value / 100.0)

            show_file = self.config.get_overlay_setting("show_file_info", True)
            show_camera = self.config.get_overlay_setting("show_camera_info", True)
            show_exif = self.config.get_overlay_setting("show_exif_info", True)
            show_lens = self.config.get_overlay_setting("show_lens_info", False)
            show_gps = self.config.get_overlay_setting("show_gps_info", False)
            show_map = self.config.get_overlay_setting("show_map", False)
            opacity = self.config.get_overlay_setting("opacity", 0.8)
            position = self.config.get_overlay_setting("position", "top_left")
            
            # 오버레이 위젯 업데이트
            self.overlay_enabled = self.config.get_overlay_setting("enabled", False)
            self.overlay_widget.update_settings(
                self.overlay_enabled,  # enabled 상태는 유지
                show_file, show_camera, show_exif, show_lens,
                show_gps, show_map, opacity, position
            )
            
            # 현재 이미지 오버레이 갱신
            if self._current_file:
                metadata = self.metadata_panel.get_current_metadata()
                if metadata:
                    self._update_overlay(self._current_file, metadata)
            
            info_print(f"오버레이 설정 적용 완료")


    def _on_cache_settings_changed(self) -> None:
        """캐시 설정 런타임 반영 (accept() → cache_settings_changed 시그널 수신)"""
        debug_print("캐시 설정 변경 적용")

        # CacheManager (이미지 뷰어) — 즉시 반영
        self.cache_manager.ahead_count   = self.config.get('cache.ahead_count', 25)
        self.cache_manager.behind_count  = self.config.get('cache.behind_count', 5)
        self.cache_manager.max_memory_mb = self.config.get('cache.max_memory_mb', 700) 

        # ThumbnailBar HybridCache — 한도 런타임 갱신 (DB 재생성 불필요)
        self.thumbnail_bar._thumb_cache.max_memory_bytes = (
            self.config.get('cache.thumb_memory_mb', 100) * 1024 * 1024
        )
        self.thumbnail_bar._thumb_cache.max_disk_bytes = (
            self.config.get('cache.thumb_disk_mb', 500) * 1024 * 1024
        )

        # config.save() 제거 — accept()에서 이미 저장됨
        debug_print("캐시 설정 런타임 반영 완료")


    def _on_rendering_settings_changed(self) -> None:
        """렌더링 설정 변경 처리 — 재시작 필요, 런타임 미반영"""
        debug_print("렌더링 설정 변경됨 (재시작 후 적용)")


    def _show_about_dialog(self):
        """프로그램 정보 다이얼로그 표시"""

        dialog = AboutDialog(self)
        dialog.exec()


    def _show_system_info(self):
        """시스템 정보 다이얼로그 표시"""

        dialog = SystemInfoDialog(self.config, self)
        dialog.exec()


# ============================================
# 컨텍스트 메뉴
# ============================================

    def create_context_menu(self, parent_widget=None) -> "QMenu":
        """컨텍스트 메뉴 반환 (MenuShortcutController 위임)."""
        return self.menu_ctrl.build_context_menu(parent_widget)


# ============================================
# 회전기능
# ============================================

    @Slot()
    def _on_rotate_left(self):
        current = self._current_file
        if not current:
            return

        state = self.rotation_manager.get_state()
        if not state or state.file_path != current:
            loader = self.imageloader
            is_anim = loader.is_animated(current)
            self.rotation_manager.set_current_file(current, is_anim)
            state = self.rotation_manager.get_state()

        if not state:
            return

        if state.has_animation:
            QMessageBox.warning(self, t('rotate.blocked_title'), t('rotate.blocked_msg'))
            return

        self.rotation_manager.rotate_left()
        self._apply_rotation_to_view_only()


    @Slot()
    def _on_rotate_right(self):
        current = self._current_file
        if not current:
            return
        state = self.rotation_manager.get_state()
        if not state or state.file_path != current:
            loader = self.imageloader
            is_anim = loader.is_animated(current)
            self.rotation_manager.set_current_file(current, is_anim)
        state = self.rotation_manager.get_state()
        if not state:
            return
        if state.has_animation:
            QMessageBox.warning(self, t('rotate.blocked_title'), t('rotate.blocked_msg'))
            return
        self.rotation_manager.rotate_right()
        self._apply_rotation_to_view_only()


    @Slot()
    def _on_rotate_apply(self):
        if not self._current_file:
            return

        state = self.rotation_manager.get_state()
        if not state or state.file_path != self._current_file:
            # 다른 파일에서 생성된 회전 상태는 무시 (요구사항)
            QMessageBox.information(
                self, t('rotate.no_change_title'), t('rotate.no_change_msg')
            )
            return

        if state.has_animation:
            QMessageBox.warning(self, t('rotate.blocked_title'), t('rotate.blocked_msg'))
            return

        # 사용자 안내
        reply = QMessageBox.question(
            self,
            t('rotate.apply_title'),
            t('rotate.apply_msg'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        success = self.rotation_manager.apply()
        if success:
            self._show_status_message(t('msg.rotate_saved'), 2000)
            # 캐시/썸네일 무효화 & 재로딩
            self._invalidate_after_rotation(self._current_file)
            #self._reload_current_image()
            # 현재 파일의 회전 상태는 리셋 (요구사항: 파일 이동하면 이전 것은 무시)
            self.rotation_manager.set_current_file(self._current_file, state.has_animation)
        else:
            self._show_status_message(t('msg.rotate_no_change'), 2000)


    @Slot()
    def _on_rotate_reset(self):
        current = self._current_file
        if not current:
            return
        self.rotation_manager.reset()
        # 원본 다시 로드
        self._load_current_image()


    def _apply_rotation_to_view_only(self) -> None:
        """회전 미리보기만 뷰에 적용. XMP 저장 없음."""
        state = self.rotation_manager.get_state()
        if not state:
            return
        if not self.image_viewer:
            return
        pix = self.image_viewer.get_current_pixmap()
        if not pix:
            return
        rotated = self.rotation_manager.get_preview_pixmap(pix)
        if rotated is None or rotated.isNull():
            return
        self.image_viewer.set_rotation_preview(rotated)


    def _invalidate_after_rotation(self, path: Path) -> None:
        self.cache_manager.clear()
        self.navigator.reload_async()


    def _reload_current_image(self):
        """현재 파일을 다시 읽어서 뷰/썸네일 모두 갱신."""
        if not self._current_file:
            return
        # 캐시 무효화
        idx = self.navigator.current_index
        if idx >= 0:
            self.cache_manager.invalidate(idx)
        # 썸네일 캐시도 삭제 (hash 기반이라 mtime 변경되면 자동으로 새 생성되지만, 확실히 하려면 캐시 폴더 제거도 고려)
        self.navigator.reload_async()  # 또는 현재 파일만 다시 로드하는 경량 함수가 있으면 사용


# ============================================
# 인쇄 기능
# ============================================

    def _ensure_print_manager(self):
        """인쇄 관리자 지연 로딩"""
        if self._print_manager is None:
            try:
                info_print(f"🖨️ 인쇄 모듈 로딩 시작...")
                
                # 단계별 import로 어디서 에러나는지 확인
                try:
                    import printing
                    info_print(f"✅ printing 모듈 import 성공: {printing}")
                except ImportError as e:
                    error_print(f"❌ printing 모듈 import 실패: {e}")
                    raise
                
                try:
                    from printing import PrintManager
                    info_print(f"✅ PrintManager 클래스 import 성공")
                except ImportError as e:
                    error_print(f"❌ PrintManager import 실패: {e}")
                    raise
                
                # PrintManager 인스턴스 생성
                self._print_manager = PrintManager()
                info_print(f"✅ 인쇄 모듈 로딩 완료")
                
            except Exception as e:
                error_detail = traceback.format_exc()
                error_print(f"인쇄 모듈 로딩 실패:\n{error_detail}")
                
                QMessageBox.critical(
                    self, t('print_dialog.error_title'), t('print_dialog.error_msg', error=e)
                )
                return False
        
        return True

    
    def _show_print_dialog(self, image_paths: list, metadata_list: list):
        """인쇄 다이얼로그 표시"""
        try:
            from printing.print_dialog import PrintDialog
            
            dialog = PrintDialog(image_paths, metadata_list, self)
            dialog.exec()
        
        except Exception as e:
            error_print(f"인쇄 다이얼로그 오류: {e}")
            QMessageBox.critical(
                self, t('print_dialog.error_title'), t('print_dialog.dialog_error_msg', error=e)
            )


    def _on_print_current(self):
        """현재 파일 인쇄"""
        if not self._ensure_print_manager():
            return
        
        # 현재 파일 가져오기
        try:
            # current가 프로퍼티인지 메서드인지 확인
            current_file = self.navigator.current()
            
            # callable 체크는 Path 객체가 아닐 때만
            if current_file and not isinstance(current_file, Path):
                if callable(current_file):
                    error_print(f"navigator.current가 함수입니다. 호출합니다.")
                    current_file = current_file()  # 함수면 호출
        
        except AttributeError:
            error_print(f"navigator에서 현재 파일을 가져올 수 없습니다.")
            current_file = None
        
        if not current_file or not isinstance(current_file, Path):
            QMessageBox.warning(self, t('print_dialog.warn_title'), t('print_dialog.no_file_msg'))
            return
        
        debug_print(f"현재 파일: {current_file} (타입: {type(current_file)})")
        
        # 메타데이터 읽기
        try:
            reader = MetadataReader()
            metadata = reader.read(current_file)
        except Exception as e:
            error_print(f"메타데이터 읽기 실패: {e}")
            metadata = {}
        
        self._show_print_dialog([current_file], [metadata])


    def _on_print_highlighted(self):
        """하이라이트 파일 인쇄"""
        if not self._ensure_print_manager():
            return
        
        # get_highlighted_files()는 메서드이므로 () 필요
        highlighted = self.navigator.get_highlighted_files()
        
        # Path 객체인지 확인
        if not highlighted or not all(isinstance(f, Path) for f in highlighted):
            QMessageBox.warning(self, t('print_dialog.warn_title'), t('print_dialog.no_highlight_msg'))
            return
        
        debug_print(f"하이라이트 파일 수: {len(highlighted)}")
        
        # 메타데이터 읽기
        try:
            reader = MetadataReader()
            metadata_list = [reader.read(f) for f in highlighted]
        except Exception as e:
            error_print(f"메타데이터 읽기 실패: {e}")
            metadata_list = [{}] * len(highlighted)
        
        self._show_print_dialog(highlighted, metadata_list)


    def _on_print_all(self):
        """전체 파일 인쇄"""
        if not self._ensure_print_manager():
            return
        
        # get_image_list()는 메서드이므로 () 필요
        all_files = self.navigator.get_image_list()
        
        # Path 객체인지 확인
        if not all_files or not all(isinstance(f, Path) for f in all_files):
            QMessageBox.warning(self, t('print_dialog.warn_title'), t('print_dialog.no_file_msg'))
            return
        
        # 확인 메시지
        reply = QMessageBox.question(
            self,
            t('print_dialog.confirm_all_title'),
            t('print_dialog.confirm_all_msg', count=len(all_files)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # 메타데이터 읽기 (진행 표시)
        progress = QProgressDialog(
            t('print_dialog.meta_loading'),
            t('print_dialog.meta_cancel'),
            0, len(all_files), self,
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        
        try:
            reader = MetadataReader()
            
            metadata_list = []
            for i, file in enumerate(all_files):
                if progress.wasCanceled():
                    return
                
                try:
                    metadata_list.append(reader.read(file))
                except Exception as e:
                    error_print(f"메타데이터 읽기 실패 {file.name}: {e}")
                    metadata_list.append({})
                
                progress.setValue(i + 1)
                QApplication.processEvents()
        
        except Exception as e:
            error_print(f"메타데이터 로딩 실패: {e}")
            progress.close()
            return
        
        progress.close()
        
        self._show_print_dialog(all_files, metadata_list)


# ============================================
# 편집 모드
# ============================================

    def enter_edit_mode(self) -> None:
        if not self._current_file:
            return

        if getattr(self.image_viewer, '_edit_mode', False):
            self.image_viewer._edit_cancel()
            self._show_status_message(t('msg.edit_exited'), 1500)
        else:
            # current_pixmap이 None이면 pixmap_item에서 복구 시도
            # (_exit_edit_mode가 current_pixmap을 None으로 초기화하기 때문)
            cp = self.image_viewer.current_pixmap
            if not cp or cp.isNull():
                pi = getattr(self.image_viewer, 'pixmap_item', None)
                if pi is not None and not pi.pixmap().isNull():
                    self.image_viewer.current_pixmap = pi.pixmap()

            if not self.image_viewer.current_pixmap or \
                    self.image_viewer.current_pixmap.isNull():
                self._show_status_message(t('msg.edit_auto_exit'), 2000)
                return
            self.image_viewer.enter_edit_mode()


    def _edit_lock_guard(self, action: str = "이동") -> bool:
        """편집 모드 중 동작 차단. 차단됐으면 True 반환."""
        if getattr(self, '_edit_locked', False):
            self._show_status_message(
                t('msg.edit_blocked', action=action), 2500
            )
            return True
        return False


    def _on_edit_mode_changed(self, active: bool) -> None:
        """편집 모드 진입/종료 시 UI 잠금 처리"""
        self._edit_locked = active

        # ── 썸네일바 오버레이 ──────────────────────────────
        overlay = self._thumb_lock_overlay
        overlay.setGeometry(0, 0,
                            self.thumbnail_bar.width(),
                            self.thumbnail_bar.height())
        if hasattr(self, '_thumb_lock_label'):
            self._thumb_lock_label.setGeometry(
                0, 0,
                self.thumbnail_bar.width(),
                self.thumbnail_bar.height()
            )
        overlay.setVisible(active)
        overlay.raise_()

        if active:
            debug_print("편집 모드: UI 잠금")
            self._show_status_message(t('msg.edit_nav_blocked'), 3000)
        else:
            debug_print("편집 모드 해제: UI 잠금 풀림")


    def _on_edit_save_requested(self, pixmap: QPixmap) -> None:
        """편집 완료 후 저장 방식 선택"""

        msg = QMessageBox(self)
        msg.setWindowTitle(t('edit_dialog.save_title'))
        msg.setText(t('edit_dialog.save_text'))
        msg.setInformativeText(t('edit_dialog.save_info'))
        btn_same  = msg.addButton(t('edit_dialog.btn_same_folder'), QMessageBox.ButtonRole.AcceptRole)
        btn_other = msg.addButton(t('edit_dialog.btn_save_as'),     QMessageBox.ButtonRole.ActionRole)
        btn_disc  = msg.addButton(t('edit_dialog.btn_discard'),     QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_disc)
        msg.exec()

        clicked = msg.clickedButton()

        if clicked == btn_same:
            self._save_edit_same_folder(pixmap)

        elif clicked == btn_other:
            self._save_edit_choose_path(pixmap)

        else:
            self._restore_viewer_after_discard()

        if hasattr(self.image_viewer, '_edit_original_pixmap'):
            del self.image_viewer._edit_original_pixmap


    def _restore_viewer_after_discard(self) -> None:
        """편집 저장 취소 시 뷰어 원본 복원"""
        viewer = self.image_viewer
        original = getattr(viewer, '_edit_original_pixmap', None)

        if original is not None and not original.isNull():
            # replace_pixmap_in_place: lock/크기비교 로직 없이 강제 교체
            viewer._replace_pixmap_inplace(original)
        else:
            # 백업이 없으면 현재 파일을 디스크에서 재로드
            if self._current_file:
                self._load_current_image()
                

    def _save_edit_same_folder(self, pixmap: QPixmap) -> None:
        """원본과 같은 폴더에 {이름}_edited.jpg로 자동 저장"""
        if not self._current_file:
            return
        try:
            stem   = self._current_file.stem
            parent = self._current_file.parent
            # 중복 방지
            save_path = parent / f"{stem}_edited.jpg"
            counter = 1
            while save_path.exists():
                save_path = parent / f"{stem}_edited_{counter}.jpg"
                counter += 1

            self._do_save_as_jpg(pixmap, save_path)
        except Exception as e:
            error_print(f"편집 저장 오류: {e}")
            QMessageBox.critical(self, t('edit_dialog.error_title'), str(e))


    def _save_edit_choose_path(self, pixmap: QPixmap) -> None:
        """사용자가 직접 경로를 선택하여 저장"""
        if not self._current_file:
            return

        default = str(self._current_file.parent / f"{self._current_file.stem}_edited.jpg")
        save_path_str, _ = QFileDialog.getSaveFileName(
            self,
            t('edit_dialog.save_as_title'),
            default,
            t('edit_dialog.save_as_filter'),
        )
        if not save_path_str:
            return
        try:
            save_path = Path(save_path_str)
            # 확장자 없으면 .jpg 강제
            if not save_path.suffix:
                save_path = save_path.with_suffix('.jpg')
            self._do_save_as_jpg(pixmap, save_path)
        except Exception as e:
            error_print(f"사본 저장 오류: {e}")
            QMessageBox.critical(self, t('edit_dialog.error_title'), str(e))


    def _do_save_as_jpg(self, pixmap: QPixmap, save_path: Path) -> None:

        qimg = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = qimg.width(), qimg.height()
        arr  = np.frombuffer(qimg.bits(), dtype=np.uint8).reshape((h, w, 4)).copy()

        pil_rgba   = Image.fromarray(arr, 'RGBA')
        background = Image.new('RGB', (w, h), (255, 255, 255))
        background.paste(pil_rgba, mask=pil_rgba.split()[3])
        pil_img = background

        exif_bytes = self._build_save_exif(self._current_file)  

        save_kwargs: dict = {'quality': 95, 'optimize': True}
        if exif_bytes:
            save_kwargs['exif'] = exif_bytes

        pil_img.save(str(save_path), 'JPEG', **save_kwargs)
        info_print(f"편집 저장: {save_path}")


    def _build_save_exif(self, filepath: Optional[Path]) -> Optional[bytes]:
        # 버전 정보: dodoRynx 패키지 import 대신 utils.version 또는 상수 사용

        from utils.app_meta import APP_VERSION as app_version

        software_tag = f"dodoRynx v{app_version}"

        # ── piexif 경로 ──────────────────────────────────────
        try:
            if filepath and filepath.exists():
                exif_dict = piexif.load(str(filepath))
            else:
                exif_dict = {'0th': {}, 'Exif': {}, 'GPS': {}, '1st': {}}

            ifd_0th = exif_dict.setdefault('0th', {})
            ifd_0th.pop(piexif.ImageIFD.Orientation, None)
            ifd_0th[piexif.ImageIFD.Software] = software_tag.encode('utf-8')  

            SKIP_TAGS = {
                piexif.ExifIFD.ComponentsConfiguration,
                piexif.ExifIFD.ExifVersion,
                piexif.ExifIFD.SceneType,
            }
            exif_ifd = exif_dict.get('Exif', {})
            for tag in SKIP_TAGS:
                if isinstance(exif_ifd.get(tag), (tuple, list)):
                    exif_ifd.pop(tag)

            return piexif.dump(exif_dict)

        except ImportError:
            pass
        except Exception as e:
            debug_print(f"piexif 처리 실패: {e} → Pillow fallback")

        # ── Pillow fallback ──────────────────────────────────
        try:
            if filepath and filepath.exists():
                with Image.open(str(filepath)) as img:
                    exif = img.getexif()
            else:
                exif = Exif()

            exif.pop(274, None)         
            exif[305] = software_tag
            return exif.tobytes()

        except Exception as e:
            debug_print(f"Pillow EXIF 처리 실패: {e}")
            return None


    def _get_exif_without_rotation(self, filepath: Optional[Path]) -> Optional[bytes]:
        if not filepath or not filepath.exists():
            return None

        # ── piexif ───────────────────────────────────────────
        try:
            exif_dict = piexif.load(str(filepath))

            # Orientation 제거
            ifd_0th = exif_dict.get('0th', {})
            ifd_0th.pop(piexif.ImageIFD.Orientation, None)

            # dump 실패를 유발하는 문제 태그 사전 제거
            #   ComponentsConfiguration(37121): bytes여야 하는데 tuple로 저장된 경우
            #   FlashPixVersion(40960), ExifVersion(36864) 등도 동일 문제 가능
            PROBLEMATIC_EXIF_TAGS = {
                piexif.ExifIFD.ComponentsConfiguration,   # 37121
                piexif.ExifIFD.ExifVersion,               # 36864
                piexif.ExifIFD.SceneType,                 # 41729
            }
            exif_ifd = exif_dict.get('Exif', {})
            for tag in PROBLEMATIC_EXIF_TAGS:
                val = exif_ifd.get(tag)
                if val is not None and isinstance(val, (tuple, list)):
                    debug_print(f"piexif: 문제 태그 {tag} 제거 (tuple → skip)")
                    exif_ifd.pop(tag)

            # dump 전 검증 — 실패하면 즉시 Pillow fallback으로
            exif_bytes = piexif.dump(exif_dict)
            return exif_bytes

        except ImportError:
            debug_print("piexif 미설치 → Pillow fallback")
        except Exception as e:
            debug_print(f"piexif EXIF 처리 실패: {e} → Pillow fallback")

        # ── Pillow fallback ──────────────────────────────────
        try:
            with Image.open(str(filepath)) as img:
                exif = img.getexif()
                if not exif:
                    return None
                exif.pop(274, None)   # Orientation
                return exif.tobytes()
        except Exception as e:
            debug_print(f"Pillow EXIF 로드 실패: {e}")
            return None


    def _handle_empty_folder(self) -> None:
        """모든 파일이 제거됐을 때 전체 UI 초기화 (단일 진입점)"""
        info_print("폴더가 비어있음 - UI 전체 초기화")

        # 1. 이미지 뷰어 클리어 (마지막 이미지 잔류 제거)
        if hasattr(self, 'image_viewer') and self.image_viewer:
            self.image_viewer.clear()

        # 2. 오버레이 숨기기
        if hasattr(self, 'overlay_widget') and self.overlay_widget:
            self.overlay_widget.clear()

        # 3. 썸네일바 완전 초기화
        if hasattr(self, 'thumbnail_bar'):
            self.thumbnail_bar.set_image_list([], -1)

        # 4. 메타데이터 패널 클리어 (추가)
        if hasattr(self, 'metadata_panel') and self.metadata_panel:
            try:
                self.metadata_panel.load_metadata(None)
            except Exception as e:
                warning_print(f"metadata_panel 클리어 실패: {e}")

        # 5. 미니맵 숨기기
        if hasattr(self, 'image_viewer') and hasattr(self.image_viewer, 'minimap'):
            self.image_viewer.minimap.hide()

        # 6. CacheManager 목록 초기화
        if hasattr(self, 'cache_manager'):
            self.cache_manager.set_image_list([])

        # 7. 타이틀바 초기화
        self.setWindowTitle("dodoRynx")

