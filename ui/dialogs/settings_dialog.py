# -*- coding: utf-8 -*-
# ui\dialogs\settings_dialog.py

import platform
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget, QSizePolicy
)

import core.map_loader as _map_loader_module
from core.map_loader import (
    configure_raster_tiles,
    configure_render_cache,
    detect_raster_zoom_range,
    get_raster_zoom_range,
)
from utils.paths import app_resources_dir

from utils.config_manager import ConfigManager
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import LangManager, t
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox
from utils.paths import get_cache_dir, get_thumb_cache_dir


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class SettingsDialog(QDialog):
    """설정 다이얼로그"""

    BROWSER_PATHS = {
        "Microsoft Edge":  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "Google Chrome":   r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "Mozilla Firefox": r"C:\Program Files\Mozilla Firefox\firefox.exe",
    }

    settings_changed                = Signal()
    overlay_settings_changed        = Signal()
    cache_settings_changed          = Signal()
    rendering_settings_changed      = Signal()
    thumbnail_cache_clear_requested = Signal()
    map_settings_changed            = Signal()  

    # ── 초기화 ────────────────────────────────────────────────────────────────

    def __init__(self, config: ConfigManager, parent=None) -> None:
        super().__init__(parent)
        self.config = config

        self._thumb_cache_dir: Path = get_thumb_cache_dir()
        self._cache_base_dir:  Path = get_cache_dir()

        self.setWindowTitle(t('settings.title'))
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)

        self._orig_scale   = int(config.get("overlay.scale", 100))
        self._orig_opacity = float(config.get_overlay_setting("opacity", 0.8))

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.overlay_settings_changed.emit)

        self._init_ui()
        self._load_settings()

    # ── UI 구성 ───────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        tabs   = QTabWidget()

        tabs.addTab(self._create_rendering_tab(), t('settings.tab_rendering'))
        tabs.addTab(self._create_cache_tab(),     t('settings.tab_cache'))
        tabs.addTab(self._create_map_tab(),       t('settings.tab_map'))
        tabs.addTab(self._create_browser_tab(),   t('settings.tab_browser'))
        tabs.addTab(self._create_overlay_tab(),   t('settings.tab_layout'))

        layout.addWidget(tabs)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    # ── 탭 생성 ───────────────────────────────────────────────────────────────

    def _create_rendering_tab(self) -> QWidget:
        """렌더링 설정 탭 (OpenGL/GPU 가속)"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # OpenGL 그룹
        opengl_group  = QGroupBox(t('settings.rendering.group_opengl'))
        opengl_layout = QVBoxLayout(opengl_group)

        self.opengl_checkbox = QCheckBox(t('settings.rendering.opengl_checkbox'))
        self.opengl_checkbox.setToolTip(t('settings.rendering.opengl_tooltip'))
        opengl_layout.addWidget(self.opengl_checkbox)
        self.opengl_checkbox.stateChanged.connect(self._on_opengl_toggled)

        self.vsync_checkbox = QCheckBox(t('settings.rendering.vsync_checkbox'))
        self.vsync_checkbox.setToolTip(t('settings.rendering.vsync_tooltip'))
        opengl_layout.addWidget(self.vsync_checkbox)

        msaa_layout = QHBoxLayout()
        msaa_label  = QLabel(t('settings.rendering.msaa_label'))
        self.msaa_combo = QComboBox()
        self.msaa_combo.addItems([t('settings.rendering.msaa_off'), "2x", "4x", "8x", "16x"])
        self.msaa_combo.setCurrentIndex(2)
        self.msaa_combo.setToolTip(t('settings.rendering.msaa_tooltip'))
        msaa_layout.addWidget(msaa_label)
        msaa_layout.addWidget(self.msaa_combo)
        msaa_layout.addStretch()
        opengl_layout.addLayout(msaa_layout)

        warning_label = QLabel(t('settings.rendering.restart_hint'))
        warning_label.setStyleSheet("""
            QLabel {
                color: #ff9800; font-weight: bold;
                background-color: rgba(255,152,0,20);
                padding: 8px; border-radius: 4px;
                border: 1px solid rgba(255,152,0,60);
            }
        """)
        opengl_layout.addWidget(warning_label)
        layout.addWidget(opengl_group)

        # 성능 정보
        info_group  = QGroupBox(t('settings.rendering.perf_group'))
        info_layout = QVBoxLayout(info_group)
        info_text   = QLabel(t('settings.rendering.perf_text'))
        info_text.setWordWrap(True)
        info_text.setStyleSheet("""
            QLabel {
                color: #ccc;
                background-color: rgba(100,100,100,30);
                padding: 10px; border-radius: 4px; font-size: 11px;
            }
        """)
        info_layout.addWidget(info_text)
        layout.addWidget(info_group)

        # 애니메이션 설정
        anim_group = QGroupBox(t('settings.rendering.anim_group'))
        anim_form  = QFormLayout(anim_group)

        self.anim_quality_combo = QComboBox()
        self.anim_quality_combo.addItems([
            t('settings.rendering.anim_quality_low'),
            t('settings.rendering.anim_quality_medium'),
            t('settings.rendering.anim_quality_high'),
        ])
        anim_form.addRow(t('settings.rendering.anim_quality_label'), self.anim_quality_combo)

        self.anim_cache_checkbox = QCheckBox(t('settings.rendering.anim_cache_checkbox'))
        anim_form.addRow("", self.anim_cache_checkbox)

        self.webp_mode_combo = QComboBox()
        self.webp_mode_combo.addItems([
            t('settings.rendering.webp_fast'),
            t('settings.rendering.webp_quality'),
        ])
        self.webp_mode_combo.setToolTip(t('settings.rendering.webp_tooltip'))
        anim_form.addRow(t('settings.rendering.webp_label'), self.webp_mode_combo)

        layout.addWidget(anim_group)
        layout.addStretch()
        return widget


    def _create_cache_tab(self) -> QWidget:
        """캐시 설정 탭 — OFM 타일 캐시 섹션 완전 제거"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)

        BASE = get_cache_dir()

        # 이미지 뷰어 캐시
        viewer_group = QGroupBox(t('settings.cache.viewer_group'))
        viewer_form  = QFormLayout(viewer_group)

        self.ahead_spin = QSpinBox()
        self.ahead_spin.setRange(5, 50)
        self.ahead_spin.setSuffix(t('settings.cache.unit_sheets'))
        self.ahead_spin.setToolTip(t('settings.cache.ahead_tooltip'))
        viewer_form.addRow(t('settings.cache.ahead'), self.ahead_spin)

        self.behind_spin = QSpinBox()
        self.behind_spin.setRange(1, 20)
        self.behind_spin.setSuffix(t('settings.cache.unit_sheets'))
        viewer_form.addRow(t('settings.cache.behind'), self.behind_spin)

        self.memory_spin = QSpinBox()
        self.memory_spin.setRange(100, 4000)
        self.memory_spin.setSingleStep(100)
        self.memory_spin.setSuffix(" MB")
        self.memory_spin.setToolTip(t('settings.cache.memory_tooltip'))
        viewer_form.addRow(t('settings.cache.max_memory'), self.memory_spin)

        layout.addWidget(viewer_group)

        # 썸네일 하이브리드 캐시 (OFM 서브그룹 제거, 썸네일만 유지)
        hybrid_group  = QGroupBox(t('settings.cache.hybrid_group'))
        hybrid_layout = QVBoxLayout(hybrid_group)

        thumb_sub  = QGroupBox(t('settings.cache.thumb_group'))
        thumb_form = QFormLayout(thumb_sub)

        self.thumb_memory_spin = QSpinBox()
        self.thumb_memory_spin.setRange(10, 500)
        self.thumb_memory_spin.setSingleStep(10)
        self.thumb_memory_spin.setSuffix(" MB")
        self.thumb_memory_spin.setToolTip(t('settings.cache.thumb_memory_tooltip'))
        thumb_form.addRow(t('settings.cache.thumb_memory'), self.thumb_memory_spin)

        self.thumb_disk_spin = QSpinBox()
        self.thumb_disk_spin.setRange(50, 2000)
        self.thumb_disk_spin.setSingleStep(50)
        self.thumb_disk_spin.setSuffix(" MB")
        self.thumb_disk_spin.setToolTip(t('settings.cache.thumb_disk_tooltip'))
        thumb_form.addRow(t('settings.cache.thumb_disk'), self.thumb_disk_spin)

        hybrid_layout.addWidget(thumb_sub)

        restart_label = QLabel(t('settings.cache.restart_hint'))
        restart_label.setStyleSheet("""
            QLabel {
                color: #ff9800;
                background-color: rgba(255,152,0,20);
                padding: 6px 8px; border-radius: 4px;
                border: 1px solid rgba(255,152,0,60);
                font-size: 10px;
            }
        """)
        hybrid_layout.addWidget(restart_label)
        layout.addWidget(hybrid_group)

        # 캐시 폴더 관리 (썸네일만, OFM 행 제거)
        mgmt_group  = QGroupBox(t('settings.cache.mgmt_group'))
        mgmt_layout = QVBoxLayout(mgmt_group)
        mgmt_layout.setSpacing(10)

        loc_layout = QHBoxLayout()
        loc_layout.addWidget(QLabel(t('settings.cache.location_label')))
        loc_label = QLabel(str(BASE))
        loc_label.setStyleSheet("color: #4a9eff; font-family: Consolas, monospace;")
        loc_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        loc_layout.addWidget(loc_label, 1)
        open_btn = QPushButton("📁")
        open_btn.setFixedWidth(36)
        open_btn.setToolTip(t('settings.cache.open_folder_tooltip'))
        open_btn.clicked.connect(lambda: self._open_cache_folder(BASE))
        loc_layout.addWidget(open_btn)
        mgmt_layout.addLayout(loc_layout)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #444;")
        mgmt_layout.addWidget(sep)

        # 썸네일 캐시 행
        thumb_row = QHBoxLayout()
        thumb_icon = QLabel(t('settings.cache.thumb_label'))
        thumb_icon.setStyleSheet("font-weight: bold; color: #ccc;")
        thumb_icon.setMinimumWidth(130)
        thumb_row.addWidget(thumb_icon)

        self.thumb_size_label = QLabel(t('settings.cache.calculating'))
        self.thumb_size_label.setStyleSheet("color: #888;")
        thumb_row.addWidget(self.thumb_size_label, 1)

        thumb_refresh = QPushButton("🔄")
        thumb_refresh.setFixedWidth(36)
        thumb_refresh.setToolTip(t('settings.cache.refresh_tooltip'))
        thumb_refresh.clicked.connect(
            lambda: self._refresh_cache_size(self._thumb_cache_dir, self.thumb_size_label)
        )
        thumb_row.addWidget(thumb_refresh)

        thumb_clear_btn = QPushButton(t('settings.cache.clear_thumb'))
        thumb_clear_btn.setFixedWidth(110)
        thumb_clear_btn.setStyleSheet(self._danger_btn_style())
        thumb_clear_btn.clicked.connect(self._clear_thumbnail_cache)
        thumb_row.addWidget(thumb_clear_btn)

        mgmt_layout.addLayout(thumb_row)

        hint = QLabel(t('settings.cache.hint'))
        hint.setWordWrap(True)
        hint.setStyleSheet("""
            QLabel {
                color: #888;
                background-color: rgba(100,100,100,30);
                padding: 8px; border-radius: 4px; font-size: 10px;
            }
        """)
        mgmt_layout.addWidget(hint)
        layout.addWidget(mgmt_group)
        layout.addStretch()

        QTimer.singleShot(
            150,
            lambda: self._refresh_cache_size(self._thumb_cache_dir, self.thumb_size_label)
        )
        return widget


    def _create_map_tab(self) -> QWidget:
        """지도 설정 탭 — 래스터 타일 디렉터리 + 줌 범위 자동 감지"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        _DEFAULT_TILES_DIR = app_resources_dir() / "tiles"

        # ── [A] 타일 디렉터리 그룹 ──────────────────────────────────────────
        dir_group = QGroupBox(t('settings.map.dir_group'))
        dir_layout = QVBoxLayout(dir_group)
        dir_layout.setSpacing(8)

        # 안내
        guide = QLabel(t('settings.map.guide'))
        guide.setWordWrap(True)
        guide.setStyleSheet("""
            QLabel {
                color: #4a9eff;
                background: rgba(74,158,255,20);
                padding: 8px; border-radius: 4px; font-size: 10px;
                border: 1px solid rgba(74,158,255,60);
            }
        """)
        dir_layout.addWidget(guide)

        # 경로 행
        path_row = QHBoxLayout()

        self.tiles_dir_edit = QLineEdit()
        self.tiles_dir_edit.setPlaceholderText(str(_DEFAULT_TILES_DIR))
        self.tiles_dir_edit.setReadOnly(True)
        self.tiles_dir_edit.setStyleSheet("""
            QLineEdit {
                background: #2a2a3a; color: #ccc;
                border: 1px solid #555; border-radius: 4px;
                padding: 5px 8px; font-family: Consolas, monospace; font-size: 11px;
            }
            QLineEdit:focus { border-color: #4a9eff; }
        """)
        path_row.addWidget(self.tiles_dir_edit, 1)

        browse_dir_btn = QPushButton(t('settings.map.browse_btn'))
        browse_dir_btn.setFixedHeight(30)
        browse_dir_btn.setStyleSheet("""
            QPushButton {
                background: #3a5a8a; color: white;
                border: none; border-radius: 4px; padding: 5px 12px; font-weight: bold;
            }
            QPushButton:hover { background: #4a6a9a; }
            QPushButton:pressed { background: #2a4a7a; }
        """)
        browse_dir_btn.clicked.connect(self._browse_tiles_dir)
        path_row.addWidget(browse_dir_btn)

        reset_dir_btn = QPushButton("↺")
        reset_dir_btn.setFixedWidth(30)
        reset_dir_btn.setFixedHeight(30)
        reset_dir_btn.setToolTip(t('settings.map.reset_tooltip'))
        reset_dir_btn.setStyleSheet("""
            QPushButton {
                background: #4a3a2a; color: #ffaa66;
                border: none; border-radius: 4px; font-weight: bold;
            }
            QPushButton:hover { background: #5a4a3a; }
        """)
        reset_dir_btn.clicked.connect(self._reset_tiles_dir)
        path_row.addWidget(reset_dir_btn)

        open_dir_btn = QPushButton("🗁")
        open_dir_btn.setFixedWidth(30)
        open_dir_btn.setFixedHeight(30)
        open_dir_btn.setToolTip(t('settings.map.open_tooltip'))
        open_dir_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.05); color: #ccc;
                border: 1px solid rgba(255,255,255,0.10); border-radius: 4px;
            }
            QPushButton:hover { background: rgba(74,158,255,0.18); }
        """)
        open_dir_btn.clicked.connect(
            lambda: self._open_cache_folder(
                Path(self.tiles_dir_edit.text().strip() or str(_DEFAULT_TILES_DIR))
            )
        )
        path_row.addWidget(open_dir_btn)

        dir_layout.addLayout(path_row)

        # 스캔 결과 정보 라벨
        self.tiles_info_label = QLabel(t('settings.map.scan_prompt'))
        self.tiles_info_label.setWordWrap(True)
        self.tiles_info_label.setMinimumHeight(100)
        self.tiles_info_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum     
        )
        self.tiles_info_label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )        
        self.tiles_info_label.setStyleSheet("""
            QLabel {
                color: #888; font-size: 11px;
                background: rgba(80,80,80,30);
                padding: 6px 8px; border-radius: 4px;
                font-family: Consolas, monospace;
            }
        """)
        dir_layout.addWidget(self.tiles_info_label)

        # 수동 재스캔 버튼
        rescan_row = QHBoxLayout()
        rescan_row.addStretch()
        rescan_btn = QPushButton(t('settings.map.rescan_btn'))
        rescan_btn.setFixedHeight(28)
        rescan_btn.setStyleSheet("""
            QPushButton {
                background: rgba(74,158,255,0.15); color: #4a9eff;
                border: 1px solid rgba(74,158,255,0.40); border-radius: 4px;
                padding: 4px 14px; font-size: 11px;
            }
            QPushButton:hover { background: rgba(74,158,255,0.30); }
            QPushButton:pressed { background: rgba(74,158,255,0.45); }
        """)
        rescan_btn.clicked.connect(self._rescan_tiles_dir)
        rescan_row.addWidget(rescan_btn)
        dir_layout.addLayout(rescan_row)

        layout.addWidget(dir_group)

        # ── [B] 줌 설정 그룹 ────────────────────────────────────────────────
        zoom_group = QGroupBox(t('settings.map.zoom_group'))
        zoom_form = QFormLayout(zoom_group)
        zoom_form.setSpacing(8)

        # 타일 크기 (읽기 전용 표시)
        tile_size_label = QLabel(t('settings.map.tile_size'))
        tile_size_label.setStyleSheet(
            "color: #888; font-size: 11px; font-family: Consolas, monospace;"
        )
        zoom_form.addRow(t('settings.map.tile_size_label'), tile_size_label)

        # 기본 표시 줌 (저장 가능)
        self.default_zoom_spin = QSpinBox()
        self.default_zoom_spin.setRange(1, 16)  
        self.default_zoom_spin.setValue(15)
        self.default_zoom_spin.setToolTip(t('settings.map.default_zoom_tooltip'))
        zoom_form.addRow(t('settings.map.default_zoom'), self.default_zoom_spin)

        zoom_hint = QLabel(t('settings.map.zoom_hint'))
        zoom_hint.setWordWrap(True)
        zoom_hint.setStyleSheet("""
            QLabel {
                color: #888; font-size: 10px;
                background: rgba(80,80,80,20);
                padding: 6px 8px; border-radius: 4px;
            }
        """)
        zoom_form.addRow("", zoom_hint)

        layout.addWidget(zoom_group)

        # ── [C] 렌더 메모리 캐시 그룹 ─────────────────────────────────────
        render_group = QGroupBox(t('settings.map.render_cache_group'))
        render_form = QFormLayout(render_group)

        self.render_memory_spin = QSpinBox()
        self.render_memory_spin.setRange(10, 500)
        self.render_memory_spin.setSingleStep(10)
        self.render_memory_spin.setSuffix(" MB")
        self.render_memory_spin.setToolTip(t('settings.map.render_memory_tooltip'))
        render_form.addRow(t('settings.map.render_memory_label'), self.render_memory_spin)

        render_clear_row = QHBoxLayout()
        render_clear_row.addStretch()
        render_clear_btn = QPushButton(t('settings.map.render_cache_clear'))
        render_clear_btn.setFixedWidth(140)
        render_clear_btn.setStyleSheet(self._danger_btn_style())
        render_clear_btn.clicked.connect(self._clear_render_cache)
        render_clear_row.addWidget(render_clear_btn)
        render_form.addRow("", render_clear_row)

        layout.addWidget(render_group)
        layout.addStretch()
        return widget


    def _create_browser_tab(self) -> QWidget:
        """브라우저 설정 탭"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        map_group  = QGroupBox(t('settings.browser.group_map'))
        map_layout = QFormLayout(map_group)

        self.map_service_combo = QComboBox()
        self.map_service_combo.addItems([
            t('settings.browser.naver'),
            t('settings.browser.kakao'),
            t('settings.browser.google'),
        ])
        map_layout.addRow(t('settings.browser.default_map_label'), self.map_service_combo)
        layout.addWidget(map_group)

        browser_group  = QGroupBox(t('settings.browser.group_browser'))
        browser_layout = QVBoxLayout(browser_group)

        self.browser_combo = QComboBox()
        self.browser_combo.addItem(t('settings.browser.system_default'))  # 0
        self.browser_combo.addItem("Microsoft Edge")                       # 1
        self.browser_combo.addItem("Google Chrome")                        # 2
        self.browser_combo.addItem("Mozilla Firefox")                      # 3
        self.browser_combo.addItem(t('settings.browser.custom'))           # 4
        self.browser_combo.currentTextChanged.connect(self._on_browser_changed)
        browser_layout.addWidget(self.browser_combo)

        path_layout = QHBoxLayout()
        self.browser_path_edit = QLineEdit()
        self.browser_path_edit.setPlaceholderText(t('settings.browser.path_placeholder'))
        path_layout.addWidget(self.browser_path_edit)
        browse_btn = QPushButton(t('settings.browser.browse_btn'))
        browse_btn.clicked.connect(self._browse_browser)
        path_layout.addWidget(browse_btn)
        browser_layout.addLayout(path_layout)

        layout.addWidget(browser_group)
        layout.addStretch()
        return widget


    def _create_overlay_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)

        # 언어 설정
        lang_group  = QGroupBox(t('settings.language.group_title'))
        lang_layout = QHBoxLayout(lang_group)

        lang_label = QLabel(t('settings.language.label'))
        lang_label.setStyleSheet("QLabel { color: #ccc; min-width: 90px; }")
        lang_layout.addWidget(lang_label)

        self.lang_combo = QComboBox()
        self._populate_lang_combo()
        lang_layout.addWidget(self.lang_combo, 1)

        lang_hint = QLabel(t('settings.language.restart_hint'))
        lang_hint.setWordWrap(True)
        lang_hint.setStyleSheet("""
            QLabel {
                color: #ff9800; background-color: rgba(255,152,0,20);
                padding: 6px 8px; border-radius: 4px;
                border: 1px solid rgba(255,152,0,60); font-size: 10px;
            }
        """)
        lang_layout.addWidget(lang_hint)
        layout.addWidget(lang_group)

        # 안내
        info = QLabel(t('settings.overlay.info_text'))
        info.setWordWrap(True)
        info.setStyleSheet("""
            QLabel {
                color: #4a9eff; background: rgba(74,158,255,30);
                font-size: 10px; padding: 10px; border-radius: 5px;
                border: 1px solid rgba(74,158,255,100);
            }
        """)
        layout.addWidget(info)

        # 표시 항목
        show_group  = QGroupBox(t('settings.overlay.group_items'))
        show_layout = QVBoxLayout(show_group)
        self.show_file_info   = QCheckBox(t('settings.overlay.file_info'))
        self.show_camera_info = QCheckBox(t('settings.overlay.camera_info'))
        self.show_exif_info   = QCheckBox(t('settings.overlay.exif_info'))
        self.show_lens_info   = QCheckBox(t('settings.overlay.lens_info'))
        self.show_gps_info    = QCheckBox(t('settings.overlay.gps_info'))
        self.show_map         = QCheckBox(t('settings.overlay.map'))
        for cb in (self.show_file_info, self.show_camera_info, self.show_exif_info,
                   self.show_lens_info, self.show_gps_info, self.show_map):
            show_layout.addWidget(cb)
        layout.addWidget(show_group)

        # 외관
        SLIDER_STYLE = """
            QSlider::groove:horizontal {
                height: 6px; background: #3b3b3b;
                border: 1px solid #555; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #4a9eff; width: 14px; height: 14px;
                margin: -4px 0; border-radius: 7px; border: 1px solid #555;
            }
            QSlider::handle:horizontal:hover { background: #6bb4ff; }
            QSlider::sub-page:horizontal { background: #4a9eff; border-radius: 3px; }
        """
        VALUE_LABEL_STYLE = """
            QLabel {
                color: #4a9eff; font-size: 11px; font-weight: bold;
                padding: 4px; background: #3b3b3b;
                border: 1px solid #555; border-radius: 3px; min-width: 42px;
            }
            QLabel:hover { border: 1px solid rgba(74,158,255,180); background: #404040; }
        """
        FORM_LABEL_STYLE = "QLabel { color: #ccc; font-size: 11px; min-width: 80px; }"

        appear_group  = QGroupBox(t('settings.overlay.group_appearance'))
        appear_layout = QVBoxLayout(appear_group)

        def make_slider_row(label_text, slider, value_label):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet(FORM_LABEL_STYLE)
            row.addWidget(lbl)
            row.addWidget(slider, 1)
            row.addWidget(value_label)
            return row

        # 크기 슬라이더
        self.overlay_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_scale_slider.setRange(50, 200)
        self.overlay_scale_slider.setTickInterval(25)
        self.overlay_scale_slider.setStyleSheet(SLIDER_STYLE)

        self.overlay_scale_value_label = ClickableLabel("100%")
        self.overlay_scale_value_label.setFixedWidth(48)
        self.overlay_scale_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_scale_value_label.setStyleSheet(VALUE_LABEL_STYLE)
        self.overlay_scale_value_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overlay_scale_value_label.setToolTip(t('settings.overlay.scale_reset_tooltip'))
        self.overlay_scale_slider.valueChanged.connect(self._on_scale_preview)
        self.overlay_scale_value_label.clicked.connect(
            lambda: self.overlay_scale_slider.setValue(100)
        )
        appear_layout.addLayout(
            make_slider_row(t('settings.overlay.scale'),
                            self.overlay_scale_slider,
                            self.overlay_scale_value_label)
        )

        # 불투명도 슬라이더
        self.overlay_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity_slider.setRange(10, 100)
        self.overlay_opacity_slider.setTickInterval(10)
        self.overlay_opacity_slider.setStyleSheet(SLIDER_STYLE)

        self.overlay_opacity_value_label = ClickableLabel("80%")
        self.overlay_opacity_value_label.setFixedWidth(48)
        self.overlay_opacity_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_opacity_value_label.setStyleSheet(VALUE_LABEL_STYLE)
        self.overlay_opacity_value_label.setCursor(Qt.CursorShape.PointingHandCursor)
        # 리셋값 80%로 수정, 클릭 시 80으로 리셋
        self.overlay_opacity_value_label.setToolTip(t('settings.overlay.opacity_reset_tooltip'))
        self.overlay_opacity_slider.valueChanged.connect(self._on_opacity_preview)
        self.overlay_opacity_value_label.clicked.connect(
            lambda: self.overlay_opacity_slider.setValue(80)
        )
        appear_layout.addLayout(
            make_slider_row(t('settings.overlay.opacity'),
                            self.overlay_opacity_slider,
                            self.overlay_opacity_value_label)
        )

        # 위치
        pos_row = QHBoxLayout()
        pos_lbl = QLabel(t('settings.overlay.position'))
        pos_lbl.setStyleSheet(FORM_LABEL_STYLE)
        self.overlay_position = QComboBox()
        self.overlay_position.addItems([
            t('settings.overlay.pos_top_left'),
            t('settings.overlay.pos_top_right'),
            t('settings.overlay.pos_bottom_left'),
            t('settings.overlay.pos_bottom_right'),
        ])
        pos_row.addWidget(pos_lbl)
        pos_row.addWidget(self.overlay_position, 1)
        appear_layout.addLayout(pos_row)

        layout.addWidget(appear_group)
        layout.addStretch()
        return widget


    def _populate_lang_combo(self) -> None:
        self.lang_combo.clear()
        self.lang_combo.addItem(t('settings.language.auto'), userData='auto')
        manager   = LangManager.instance()
        available = manager.get_available_languages()
        for code, name in available.items():
            self.lang_combo.addItem(name, userData=code)
        saved = self.config.get('ui.language', 'auto')
        for i in range(self.lang_combo.count()):
            if self.lang_combo.itemData(i) == saved:
                self.lang_combo.setCurrentIndex(i)
                break

    # ── 지도 탭 관련 ──────────────────────────────────────────────────────────

    def _browse_tiles_dir(self) -> None:
        """래스터 타일 디렉터리 선택 다이얼로그"""
        current = self.tiles_dir_edit.text().strip()
        start_dir = current if current and Path(current).exists() else str(
            app_resources_dir() / "tiles"
        )

        chosen = QFileDialog.getExistingDirectory(
            self,
            "래스터 타일 디렉터리 선택",
            start_dir,
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
        )
        if chosen:
            self.tiles_dir_edit.setText(chosen)
            self._scan_tiles_dir(Path(chosen))


    def _reset_tiles_dir(self) -> None:
        """기본 경로(resources/tiles)로 초기화"""
        default = app_resources_dir() / "tiles"
        self.tiles_dir_edit.setText(str(default))
        self._scan_tiles_dir(default)


    def _rescan_tiles_dir(self) -> None:
        """현재 입력된 경로를 다시 스캔"""
        raw = self.tiles_dir_edit.text().strip()
        path = Path(raw) if raw else app_resources_dir() / "tiles"
        self._scan_tiles_dir(path)


    def _scan_tiles_dir(self, path: Path) -> None:
        if not path.exists():
            self._set_tiles_info_error(t('settings.map.scan_err_not_exist', path=path))
            return
        if not path.is_dir():
            self._set_tiles_info_error(t('settings.map.scan_err_not_dir', path=path))
            return

        subdirs = [c for c in path.iterdir() if c.is_dir()]
        tile_count = 0
        for d in subdirs:
            for x_dir in d.iterdir():
                if x_dir.is_dir():
                    tile_count += sum(1 for f in x_dir.iterdir()
                                    if f.suffix.lower() == ".webp")
            if tile_count > 0:
                break

        tile_status = (
            t('settings.map.scan_tiles_found')
            if tile_count > 0
            else t('settings.map.scan_tiles_empty')
        )

        info_lines = [
            t('settings.map.scan_ok'),
            t('settings.map.scan_path',       path=path),
            t('settings.map.scan_zoom_range'),
            t('settings.map.scan_tile_format'),
            tile_status,
        ]

        self._set_tiles_info_ok("\n".join(info_lines))
        info_print(f"[Settings] 타일 디렉터리 확인: {path}")


    def _set_tiles_info_ok(self, text: str) -> None:
        self.tiles_info_label.setText(text)
        self.tiles_info_label.setStyleSheet("""
            QLabel {
                color: #66cc88; font-size: 11px;
                background: rgba(50,200,100,15);
                padding: 6px 8px; border-radius: 4px;
                border: 1px solid rgba(50,200,100,40);
                font-family: Consolas, monospace;
            }
        """)


    def _set_tiles_info_error(self, text: str) -> None:
        self.tiles_info_label.setText(text)
        self.tiles_info_label.setStyleSheet("""
            QLabel {
                color: #ff8888; font-size: 11px;
                background: rgba(255,50,50,15);
                padding: 6px 8px; border-radius: 4px;
                border: 1px solid rgba(255,50,50,40);
                font-family: Consolas, monospace;
            }
        """)


    def _clear_render_cache(self) -> None:
        """메모리 렌더 캐시 즉시 삭제"""
        _confirm = _DarkMessageBox(
            self, kind='question',
            title=t('settings.map.render_clear_confirm_title'),
            body=t('settings.map.render_clear_confirm_msg'),
        )
        if _confirm.exec() != QDialog.DialogCode.Accepted:
            return
        _map_loader_module._render_cache.clear()
        _DarkMessageBox(self, kind='info', title=t('settings.map.render_cleared_title'), body=t('settings.map.render_cleared_msg')).exec()
        info_print("렌더 캐시 삭제 완료")

    # ── 렌더링 설정 관련 ──────────────────────────────────────────────────────

    def _on_opengl_toggled(self, state: int) -> None:
        enabled = (state == Qt.CheckState.Checked.value)
        self.vsync_checkbox.setEnabled(enabled)
        self.msaa_combo.setEnabled(enabled)
        self.vsync_checkbox.setStyleSheet("" if enabled else "color: #666;")
        self.msaa_combo.setStyleSheet("" if enabled else "color: #666;")

    # ── 브라우저 관련 ─────────────────────────────────────────────────────────

    def _on_browser_changed(self, text: str) -> None:
        idx       = self.browser_combo.currentIndex()
        is_custom = (idx == 4)
        self.browser_path_edit.setEnabled(is_custom)
        _keys = list(self.BROWSER_PATHS.keys())
        if 1 <= idx <= 3:
            self.browser_path_edit.setText(self.BROWSER_PATHS[_keys[idx - 1]])
        elif idx == 0:
            self.browser_path_edit.clear()


    def _browse_browser(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            t('settings.browser.select_title'),
            "",
            t('settings.browser.select_filter'),
        )
        if file_path:
            self.browser_path_edit.setText(file_path)


    def _validate_browser_path(self, browser_path: str, browser_name: str) -> bool:
        if not Path(browser_path).exists():
            _dlg = _DarkMessageBox(
                self, kind='question',
                title=t('settings.browser.no_browser_title'),
                body=t('settings.browser.no_browser_msg', name=browser_name, path=browser_path),
            )
            return _dlg.exec() == QDialog.DialogCode.Accepted
        return True

    # ── 설정 로드 / 저장 ──────────────────────────────────────────────────────

    def _load_settings(self) -> None:
        # 렌더링
        use_opengl = self.config.get_rendering_setting('use_opengl', True)
        self.opengl_checkbox.setChecked(use_opengl)
        self.vsync_checkbox.setChecked(self.config.get_rendering_setting('vsync', True))
        msaa_samples = self.config.get_rendering_setting('msaa_samples', 4)
        self.msaa_combo.setCurrentIndex({0: 0, 2: 1, 4: 2, 8: 3, 16: 4}.get(msaa_samples, 2))
        self._on_opengl_toggled(
            Qt.CheckState.Checked.value if use_opengl else Qt.CheckState.Unchecked.value
        )

        # 캐시
        self.ahead_spin.setValue(self.config.get('cache.ahead_count', 25))
        self.behind_spin.setValue(self.config.get('cache.behind_count', 5))
        self.memory_spin.setValue(self.config.get('cache.max_memory_mb', 700))
        self.thumb_memory_spin.setValue(self.config.get('cache.thumb_memory_mb', 100))
        self.thumb_disk_spin.setValue(self.config.get('cache.thumb_disk_mb', 500))

        # 애니메이션
        anim         = self.config.get('animation', {})
        quality_map  = {'low': 0, 'medium': 1, 'high': 2}
        self.anim_quality_combo.setCurrentIndex(
            quality_map.get(anim.get('scale_quality', 'high'), 2)
        )
        self.anim_cache_checkbox.setChecked(anim.get('cache_mode', True))
        self.webp_mode_combo.setCurrentIndex(
            0 if anim.get('webp_mode', 'quality') == 'fast' else 1
        )

        # AFTER:
        # 지도 (래스터 타일)
        _default_tiles = str(app_resources_dir() / "tiles")
        tiles_dir_str = self.config.get('map.tiles_dir', _default_tiles)
        self.tiles_dir_edit.setText(tiles_dir_str)

        # 저장된 기본 줌 먼저 반영 (스캔 전 초기값)
        saved_default_zoom = self.config.get_gps_map_setting('default_zoom', 15)
        self.default_zoom_spin.setValue(saved_default_zoom)

        # 타일 디렉터리 스캔 (비동기 없이 즉시 — 로컬 폴더라 빠름)
        QTimer.singleShot(
            100,
            lambda: self._scan_tiles_dir(Path(tiles_dir_str))
        )

        self.render_memory_spin.setValue(self.config.get('cache.render_memory_mb', 50))

        # 브라우저
        map_service = self.config.get('map.service', 'naver')
        self.map_service_combo.setCurrentIndex(
            {'naver': 0, 'kakao': 1, 'google': 2}.get(map_service, 0)
        )

        browser_path  = self.config.get('browser.path', 'system_default')
        path_to_index = {p: idx for idx, (_, p) in enumerate(self.BROWSER_PATHS.items(), 1)}
        path_to_index['system_default'] = 0

        if browser_path in path_to_index:
            idx = path_to_index[browser_path]
            self.browser_combo.setCurrentIndex(idx)
            if browser_path != 'system_default':
                self.browser_path_edit.setText(browser_path)
                self.browser_path_edit.setEnabled(False)
        else:
            self.browser_combo.setCurrentIndex(4)
            self.browser_path_edit.setText(browser_path)
            self.browser_path_edit.setEnabled(True)

        # 오버레이
        self.show_file_info.setChecked(self.config.get_overlay_setting("show_file_info", True))
        self.show_camera_info.setChecked(self.config.get_overlay_setting("show_camera_info", True))
        self.show_exif_info.setChecked(self.config.get_overlay_setting("show_exif_info", True))
        self.show_lens_info.setChecked(self.config.get_overlay_setting("show_lens_info", False))
        self.show_gps_info.setChecked(self.config.get_overlay_setting("show_gps_info", False))
        self.show_map.setChecked(self.config.get_overlay_setting("show_map", False))

        scale = int(self.config.get("overlay.scale", 100))
        self.overlay_scale_slider.blockSignals(True)
        self.overlay_scale_slider.setValue(max(50, min(200, scale)))
        self.overlay_scale_slider.blockSignals(False)
        self.overlay_scale_value_label.setText(f"{scale}%")

        opacity_f = float(self.config.get_overlay_setting("opacity", 0.8))
        opacity_i = max(10, min(100, int(round(opacity_f * 100))))
        self.overlay_opacity_slider.blockSignals(True)
        self.overlay_opacity_slider.setValue(opacity_i)
        self.overlay_opacity_slider.blockSignals(False)
        self.overlay_opacity_value_label.setText(f"{opacity_i}%")

        pos_map  = {"top_left": 0, "top_right": 1, "bottom_left": 2, "bottom_right": 3}
        cur_pos  = self.config.get_overlay_setting("position", "top_left")
        self.overlay_position.setCurrentIndex(pos_map.get(cur_pos, 0))


    def accept(self) -> None:
        """확인 버튼 — 모든 설정 저장"""
        cache_changed     = False
        overlay_changed   = False
        rendering_changed = False
        needs_restart     = False

        # 언어
        lang_code = self.lang_combo.currentData()
        if self.config.get('ui.language', 'auto') != lang_code:
            self.config.set('ui.language', lang_code)
            needs_restart = True

        # 렌더링
        for key, new_val, default in [
            ('use_opengl',   self.opengl_checkbox.isChecked(), True),
            ('vsync',        self.vsync_checkbox.isChecked(),  True),
            ('msaa_samples', [0, 2, 4, 8, 16][self.msaa_combo.currentIndex()], 4),
        ]:
            if self.config.get_rendering_setting(key, default) != new_val:
                rendering_changed = True
                needs_restart     = True
                self.config.set_rendering_setting(key, new_val)

        # 브라우저
        browser_index = self.browser_combo.currentIndex()
        if browser_index == 0:
            self.config.set('browser.path', 'system_default')
        elif browser_index in (1, 2, 3):
            browser_name  = self.browser_combo.currentText()
            selected_path = self.BROWSER_PATHS[browser_name]
            if not self._validate_browser_path(selected_path, browser_name):
                return
            self.config.set(
                'browser.path',
                selected_path if Path(selected_path).exists() else 'system_default'
            )
        else:
            browser_path = self.browser_path_edit.text().strip()
            if browser_path:
                if not self._validate_browser_path(browser_path, t('settings.browser.custom_name')):
                    return
                self.config.set('browser.path', browser_path)
            else:
                self.config.set('browser.path', 'system_default')

        # 캐시 (런타임 반영 가능 vs 재시작 필요 분류)
        RUNTIME_KEYS      = {'cache.ahead_count', 'cache.behind_count', 'cache.max_memory_mb'}
        cache_restart_needed = False

        for key, new_val, default in [
            ('cache.ahead_count',    self.ahead_spin.value(),         25),
            ('cache.behind_count',   self.behind_spin.value(),         5),
            ('cache.max_memory_mb',  self.memory_spin.value(),       700),
            ('cache.thumb_memory_mb', self.thumb_memory_spin.value(), 100),
            ('cache.thumb_disk_mb',   self.thumb_disk_spin.value(),   500),
        ]:
            if self.config.get(key, default) != new_val:
                cache_changed = True
                if key not in RUNTIME_KEYS:
                    cache_restart_needed = True
                    needs_restart        = True
                self.config.set(key, new_val)

        # 렌더 메모리 캐시 (런타임 반영)
        new_render_mb = self.render_memory_spin.value()
        if self.config.get('cache.render_memory_mb', 50) != new_render_mb:
            cache_changed = True
            self.config.set('cache.render_memory_mb', new_render_mb)
            # 즉시 반영
            from core.map_loader import configure_render_cache
            configure_render_cache(new_render_mb)

        # 애니메이션
        self.config.set('animation', {
            'scale_quality': ['low', 'medium', 'high'][self.anim_quality_combo.currentIndex()],
            'cache_mode':    self.anim_cache_checkbox.isChecked(),
            'webp_mode':     ['fast', 'quality'][self.webp_mode_combo.currentIndex()],
        })

        # 지도 서비스
        self.config.set(
            'map.service',
            ['naver', 'kakao', 'google'][self.map_service_combo.currentIndex()]
        )

        # 래스터 타일 설정
        _default_tiles = str(app_resources_dir() / "tiles")
        new_tiles_dir  = self.tiles_dir_edit.text().strip() or _default_tiles
        new_default_zoom = self.default_zoom_spin.value()
        old_tiles_dir    = self.config.get('map.tiles_dir', _default_tiles)
        old_default_zoom = self.config.get_gps_map_setting('default_zoom', 15)
        _map_changed = False

        if new_tiles_dir != old_tiles_dir:
            # 경로 유효성 검증
            p = Path(new_tiles_dir)
            if not p.exists():
                _confirm = _DarkMessageBox(
                    self, kind='question',
                    title=t('settings.map.invalid_path_title'),
                    body=t('settings.map.invalid_path_msg', path=new_tiles_dir),
                )
                if _confirm.exec() != QDialog.DialogCode.Accepted:
                    return
            self.config.set('map.tiles_dir', new_tiles_dir)

            # 런타임 즉시 반영 — 새 경로로 재스캔 + 렌더 캐시 무효화
            _tms_val = self.config.get('map.tms', False)
            configure_raster_tiles(p, tms=_tms_val)
            _map_loader_module._render_cache.clear()
            _map_changed = True

        if new_default_zoom != old_default_zoom:
            self.config.set_gps_map_setting('default_zoom', new_default_zoom)
            _map_changed = True

        # 오버레이
        for key, new_val, default in [
            ("show_file_info",   self.show_file_info.isChecked(),   True),
            ("show_camera_info", self.show_camera_info.isChecked(), True),
            ("show_exif_info",   self.show_exif_info.isChecked(),   True),
            ("show_lens_info",   self.show_lens_info.isChecked(),   False),
            ("show_gps_info",    self.show_gps_info.isChecked(),    False),
            ("show_map",         self.show_map.isChecked(),         False),
            ("opacity",          self.overlay_opacity_slider.value() / 100.0, 0.8),
        ]:
            if self.config.get_overlay_setting(key, default) != new_val:
                overlay_changed = True
                self.config.set_overlay_setting(key, new_val)

        new_scale = self.overlay_scale_slider.value()
        if self.config.get("overlay.scale", 100) != new_scale:
            overlay_changed = True
            self.config.set("overlay.scale", new_scale)

        pos_keys = ["top_left", "top_right", "bottom_left", "bottom_right"]
        new_pos  = pos_keys[self.overlay_position.currentIndex()]
        if self.config.get_overlay_setting("position", "top_left") != new_pos:
            overlay_changed = True
            self.config.set_overlay_setting("position", new_pos)

        # ── 저장 및 시그널 발행 ───────────────────────────────────────────────
        self.config.save()
        info_print("설정 저장 완료")

        self.settings_changed.emit()

        if cache_changed:
            self.cache_settings_changed.emit()
            debug_print("캐시 설정 변경됨")

        if overlay_changed:
            self.overlay_settings_changed.emit()

        if rendering_changed:
            self.rendering_settings_changed.emit()

        if _map_changed:
            self.map_settings_changed.emit() 
            debug_print("지도 설정 변경됨")

        # 재시작 필요 안내
        if needs_restart:
            changed_items = []
            if rendering_changed:
                changed_items.append(t('settings.restart_rendering'))
            if cache_restart_needed:
                changed_items.append(t('settings.restart_cache_disk'))
            message = t('settings.restart_body')
            if changed_items:
                message += "\n" + "\n".join(changed_items)
            _DarkMessageBox(self, kind='info', title=t('settings.restart_title'), body=message).exec()

        super().accept()


    def reject(self) -> None:
        """취소 — 라이브 프리뷰 값 원복"""
        self._preview_timer.stop()
        self.config.set("overlay.scale", self._orig_scale)
        self.config.set_overlay_setting("opacity", self._orig_opacity)
        self.overlay_settings_changed.emit()
        super().reject()

    # ── 오버레이 실시간 프리뷰 ────────────────────────────────────────────────

    def _on_scale_preview(self, v: int) -> None:
        self.overlay_scale_value_label.setText(f"{v}%")
        self.config.set("overlay.scale", v)
        self._preview_timer.start(80)


    def _on_opacity_preview(self, v: int) -> None:
        self.overlay_opacity_value_label.setText(f"{v}%")
        self.config.set_overlay_setting("opacity", v / 100.0)
        self._preview_timer.start(80)

    # ── 캐시 용량 계산 ────────────────────────────────────────────────────────

    def _calculate_folder_size(self, folder_path: Path) -> int:
        total = 0
        try:
            if not folder_path.exists():
                return 0
            for f in folder_path.rglob('*'):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except (OSError, PermissionError):
                        continue
        except Exception as e:
            error_print(f"폴더 용량 계산 실패: {e}")
        return total


    def _format_file_size(self, size_bytes: int) -> str:
        size = float(size_bytes)
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"


    def _refresh_cache_size(self, cache_dir: Path, label: QLabel) -> None:
        label.setText(t('settings.cache.calculating'))
        label.setStyleSheet("color: #888;")

        def _calc():
            size_bytes = self._calculate_folder_size(cache_dir)
            count      = len(list(cache_dir.glob("*.cache"))) if cache_dir.exists() else 0
            size_str   = self._format_file_size(size_bytes)
            label.setText(f"{size_str} ({count:,} files)")
            if size_bytes > 400 * 1024 * 1024:
                label.setStyleSheet("color: #ff5252; font-weight: bold;")
            elif size_bytes > 100 * 1024 * 1024:
                label.setStyleSheet("color: #ff9800; font-weight: bold;")
            else:
                label.setStyleSheet("color: #4caf50; font-weight: bold;")

        QTimer.singleShot(0, _calc)

    # ── 캐시 삭제 / 폴더 관리 ────────────────────────────────────────────────

    def _open_cache_folder(self, cache_dir: Path) -> None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            if platform.system() == 'Windows':
                subprocess.run(['explorer', str(cache_dir)])
            elif platform.system() == 'Darwin':
                subprocess.run(['open', str(cache_dir)])
            else:
                subprocess.run(['xdg-open', str(cache_dir)])
            info_print(f"캐시 폴더 열기: {cache_dir}")
        except Exception as e:
            error_print(f"폴더 열기 실패: {e}")
            _DarkMessageBox(
                self, kind='warning',
                title=t('dialog.folder_open_error_title'),
                body=t('dialog.folder_open_error_msg', error=e),
            ).exec()


    def _clear_thumbnail_cache(self) -> None:
        _confirm = _DarkMessageBox(
            self, kind='question',
            title=t('dialog.thumb_clear_title'),
            body=t('dialog.thumb_clear_msg'),
        )
        if _confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self.thumbnail_cache_clear_requested.emit()
        deleted = self._delete_cache_dir(self._thumb_cache_dir)
        _DarkMessageBox(
            self, kind='info',
            title=t('dialog.thumb_cleared_title'),
            body=t('dialog.thumb_cleared_msg', size=self._format_file_size(deleted)),
        ).exec()
        self._refresh_cache_size(self._thumb_cache_dir, self.thumb_size_label)
        info_print("썸네일 캐시 삭제 완료")


    def _delete_cache_dir(self, cache_dir: Path) -> int:
        """디렉토리 전체 삭제 후 빈 디렉토리 재생성. 확보된 바이트 수 반환."""
        deleted_bytes = self._calculate_folder_size(cache_dir)
        try:
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            error_print(f"캐시 디렉토리 삭제 실패: {e}")
            _DarkMessageBox(
                self, kind='danger',
                title=t('dialog.cache_delete_error_title'),
                body=t('dialog.cache_delete_error_msg', error=e),
            ).exec()
        return deleted_bytes

    # ── 스타일 유틸리티 ───────────────────────────────────────────────────────

    @staticmethod
    def _danger_btn_style() -> str:
        return """
            QPushButton {
                background-color: #c62828; color: white;
                padding: 5px 10px; border: none;
                border-radius: 4px; font-weight: bold;
            }
            QPushButton:hover   { background-color: #ef5350; }
            QPushButton:pressed { background-color: #b71c1c; }
        """

