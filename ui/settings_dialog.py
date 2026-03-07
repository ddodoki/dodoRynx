# -*- coding: utf-8 -*-
# ui/settings_dialog.py

"""
설정 다이얼로그
"""

import platform
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.map_loader import _render_cache

from utils.config_manager import ConfigManager
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import LangManager, t
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
        "Microsoft Edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "Google Chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "Mozilla Firefox": r"C:\Program Files\Mozilla Firefox\firefox.exe"
    }
    
    settings_changed = Signal() 
    overlay_settings_changed = Signal()
    cache_settings_changed = Signal() 
    rendering_settings_changed = Signal() 
    thumbnail_cache_clear_requested = Signal()
    tile_cache_clear_requested = Signal()

    # ============================================
    # 초기화 / UI 구성
    # ============================================

    def __init__(self, config: ConfigManager, parent=None) -> None:       
        super().__init__(parent)
        self.config = config

        # 인스턴스 속성으로 명시 선언 (Pylance 인식)
        self._thumb_cache_dir: Path = get_thumb_cache_dir()
        self._cache_base_dir: Path  = get_cache_dir()
        self._ofm_cache_dir:   Path = get_cache_dir() / "ofm_rendered"

        self.setWindowTitle(t('settings.title'))
        self.setMinimumWidth(550)
        self.setMinimumHeight(450)
        # 라이브 프리뷰 취소 복원을 위해 원본 값 저장
        self._orig_scale   = int(config.get("overlay.scale", 100))
        self._orig_opacity = float(config.get_overlay_setting("opacity", 0.8))

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.overlay_settings_changed.emit)
        self._init_ui()
        self._load_settings()
    

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # 탭 위젯
        tabs = QTabWidget()
        
        # 렌더링 설정 탭
        rendering_tab = self._create_rendering_tab()
        tabs.addTab(rendering_tab, t('settings.tab_rendering'))
        
        # 캐시 설정 탭
        cache_tab = self._create_cache_tab()
        tabs.addTab(cache_tab,     t('settings.tab_cache'))
        
        # 브라우저 설정 탭
        browser_tab = self._create_browser_tab()
        tabs.addTab(browser_tab,   t('settings.tab_browser'))
        
        # 오버레이 설정 탭
        tabs.addTab(self._create_overlay_tab(), t('settings.tab_layout'))
        
        layout.addWidget(tabs)
        
        # 버튼
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    # ============================================
    # 탭 생성
    # ============================================

    def _create_rendering_tab(self) -> QWidget:
        """렌더링 설정 탭 (OpenGL/GPU 가속)"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # OpenGL 설정 그룹
        opengl_group = QGroupBox(t('settings.rendering.group_opengl'))
        opengl_layout = QVBoxLayout(opengl_group)
        
        # OpenGL 사용 여부
        self.opengl_checkbox = QCheckBox(t('settings.rendering.opengl_checkbox'))
        self.opengl_checkbox.setToolTip(t('settings.rendering.opengl_tooltip'))
        opengl_layout.addWidget(self.opengl_checkbox)
        
        # OpenGL 상태 연결
        self.opengl_checkbox.stateChanged.connect(self._on_opengl_toggled)
        
        # V-Sync
        self.vsync_checkbox = QCheckBox(t('settings.rendering.vsync_checkbox'))
        self.vsync_checkbox.setToolTip(t('settings.rendering.vsync_tooltip'))
        opengl_layout.addWidget(self.vsync_checkbox)
        
        # MSAA
        msaa_layout = QHBoxLayout()
        msaa_label = QLabel(t('settings.rendering.msaa_label'))
        self.msaa_combo = QComboBox()
        self.msaa_combo.addItems([t('settings.rendering.msaa_off'), "2x", "4x", "8x", "16x"])
        self.msaa_combo.setCurrentIndex(2)
        self.msaa_combo.setToolTip(t('settings.rendering.msaa_tooltip')) 
        msaa_layout.addWidget(msaa_label)
        msaa_layout.addWidget(self.msaa_combo)
        msaa_layout.addStretch()
        opengl_layout.addLayout(msaa_layout)
        
        # 경고 메시지
        warning_label = QLabel(t('settings.rendering.restart_hint'))
        warning_label.setStyleSheet("""
            QLabel {
                color: #ff9800;
                font-weight: bold;
                background-color: rgba(255, 152, 0, 20);
                padding: 8px;
                border-radius: 4px;
                border: 1px solid rgba(255, 152, 0, 60);
            }
        """)
        opengl_layout.addWidget(warning_label)
        
        layout.addWidget(opengl_group)
        
        # 성능 정보
        info_group = QGroupBox(t('settings.rendering.perf_group'))
        info_layout = QVBoxLayout(info_group)
        info_text = QLabel(t('settings.rendering.perf_text'))
        info_text.setWordWrap(True)
        info_text.setWordWrap(True)
        info_text.setStyleSheet("""
            QLabel {
                color: #ccc;
                background-color: rgba(100, 100, 100, 30);
                padding: 10px;
                border-radius: 4px;
                font-size: 11px;
            }
        """)
        info_layout.addWidget(info_text)

        layout.addWidget(info_group)

        # 애니메이션 설정
        anim_group = QGroupBox(t('settings.rendering.anim_group'))
        anim_form = QFormLayout(anim_group)

        self.anim_quality_combo = QComboBox()
        self.anim_quality_combo.addItems([
            t('settings.rendering.anim_quality_low'),
            t('settings.rendering.anim_quality_medium'),
            t('settings.rendering.anim_quality_high'),
        ])
        anim_form.addRow(t('settings.rendering.anim_quality_label'), self.anim_quality_combo)

        self.anim_cache_checkbox = QCheckBox(t('settings.rendering.anim_cache_checkbox'))
        anim_form.addRow("", self.anim_cache_checkbox)

        # WebP 재생 방식
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
        """캐시 설정 탭"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)

        self.thumb_cache_dir = get_thumb_cache_dir()
        BASE = get_cache_dir()

        # 이미지 뷰어 캐시
        viewer_group = QGroupBox(t('settings.cache.viewer_group'))
        viewer_form = QFormLayout(viewer_group)

        self.ahead_spin = QSpinBox()
        self.ahead_spin.setRange(5, 50)
        self.ahead_spin.setSuffix(" 장")
        self.ahead_spin.setToolTip(t('settings.cache.ahead_tooltip'))
        viewer_form.addRow(t('settings.cache.ahead'), self.ahead_spin)

        self.behind_spin = QSpinBox()
        self.behind_spin.setRange(1, 20)
        self.behind_spin.setSuffix(" 장")
        viewer_form.addRow(t('settings.cache.behind'), self.behind_spin)

        self.memory_spin = QSpinBox()
        self.memory_spin.setRange(100, 4000)
        self.memory_spin.setSingleStep(100)
        self.memory_spin.setSuffix(" MB")
        self.memory_spin.setToolTip(t('settings.cache.memory_tooltip'))
        viewer_form.addRow(t('settings.cache.max_memory'), self.memory_spin)

        layout.addWidget(viewer_group)

        # 하이브리드 캐시 용량 설정
        hybrid_group = QGroupBox(t('settings.cache.hybrid_group'))
        hybrid_layout = QVBoxLayout(hybrid_group)

        # 썸네일 캐시
        thumb_sub = QGroupBox(t('settings.cache.thumb_group'))
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

        # 지도 타일 캐시
        ofm_sub = QGroupBox(t('settings.cache.ofm_group'))
        ofm_form = QFormLayout(ofm_sub)

        self.ofm_memory_spin = QSpinBox()
        self.ofm_memory_spin.setRange(10, 500)
        self.ofm_memory_spin.setSingleStep(10)
        self.ofm_memory_spin.setSuffix(" MB")
        self.ofm_memory_spin.setToolTip(t('settings.cache.ofm_memory_tooltip'))
        ofm_form.addRow(t('settings.cache.ofm_memory'), self.ofm_memory_spin)

        self.ofm_disk_spin = QSpinBox()
        self.ofm_disk_spin.setRange(50, 2000)
        self.ofm_disk_spin.setSingleStep(50)
        self.ofm_disk_spin.setSuffix(" MB")
        self.ofm_disk_spin.setToolTip(t('settings.cache.ofm_disk_tooltip'))
        ofm_form.addRow(t('settings.cache.ofm_disk'), self.ofm_disk_spin)

        self.ofm_expiry_spin = QSpinBox()
        self.ofm_expiry_spin.setRange(15, 365)
        self.ofm_expiry_spin.setSuffix(t('settings.cache.ofm_expiry_suffix'))
        self.ofm_expiry_spin.setToolTip(t('settings.cache.ofm_expiry_tooltip'))
        ofm_form.addRow(t('settings.cache.ofm_expiry'), self.ofm_expiry_spin)

        hybrid_layout.addWidget(ofm_sub)

        restart_label = QLabel(t('settings.cache.restart_hint'))
        restart_label.setStyleSheet("""
            QLabel {
                color: #ff9800;
                background-color: rgba(255,152,0,20);
                padding: 6px 8px;
                border-radius: 4px;
                border: 1px solid rgba(255,152,0,60);
                font-size: 10px;
            }
        """)
        hybrid_layout.addWidget(restart_label)
        layout.addWidget(hybrid_group)

        # 캐시 폴더 관리
        mgmt_group = QGroupBox(t('settings.cache.mgmt_group'))
        mgmt_layout = QVBoxLayout(mgmt_group)
        mgmt_layout.setSpacing(10)

        # 저장 위치 표시
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

        # 구분선
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

        # 지도 타일 캐시 행
        ofm_row = QHBoxLayout()
        ofm_icon = QLabel(t('settings.cache.ofm_label'))  
        ofm_icon.setStyleSheet("font-weight: bold; color: #ccc;")
        ofm_icon.setMinimumWidth(130)
        ofm_row.addWidget(ofm_icon)

        self.ofm_size_label = QLabel(t('settings.cache.calculating'))
        self.ofm_size_label.setStyleSheet("color: #888;")
        ofm_row.addWidget(self.ofm_size_label, 1)

        ofm_refresh = QPushButton("🔄")
        ofm_refresh.setFixedWidth(36)
        ofm_refresh.clicked.connect(self._refresh_tile_cache_size)
        ofm_row.addWidget(ofm_refresh)

        ofm_clear_btn = QPushButton(t('settings.cache.clear_ofm')) 
        ofm_clear_btn.setFixedWidth(110)
        ofm_clear_btn.setStyleSheet(self._danger_btn_style())
        ofm_clear_btn.clicked.connect(self._clear_tile_cache)
        ofm_row.addWidget(ofm_clear_btn)

        mgmt_layout.addLayout(ofm_row)

        # 안내 메시지
        hint = QLabel(t('settings.cache.hint'))
        hint.setWordWrap(True)
        hint.setStyleSheet("""
            QLabel {
                color: #888;
                background-color: rgba(100,100,100,30);
                padding: 8px;
                border-radius: 4px;
                font-size: 10px;
            }
        """)
        mgmt_layout.addWidget(hint)
        layout.addWidget(mgmt_group)

        layout.addStretch()

        # 초기 용량 계산
        QTimer.singleShot(
            150,
            lambda: (
                self._refresh_cache_size(self._thumb_cache_dir, self.thumb_size_label),
                self._refresh_tile_cache_size(),
            )
        )
        return widget


    def _create_browser_tab(self) -> QWidget:
        """브라우저 설정 탭"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 지도 서비스 선택
        map_group = QGroupBox(t('settings.browser.group_map'))
        map_layout = QFormLayout(map_group)
        
        self.map_service_combo = QComboBox()
        self.map_service_combo.addItems([
            t('settings.browser.naver'),
            t('settings.browser.kakao'),
            t('settings.browser.google'),
        ])
        map_layout.addRow(t('settings.browser.default_map_label'), self.map_service_combo)
        
        layout.addWidget(map_group)
        
        # 브라우저 선택
        browser_group = QGroupBox(t('settings.browser.group_browser'))
        browser_layout = QVBoxLayout(browser_group)
        
        self.browser_combo = QComboBox()
        self.browser_combo.addItem(t('settings.browser.system_default'))  # index 0
        self.browser_combo.addItem("Microsoft Edge")                        # index 1
        self.browser_combo.addItem("Google Chrome")                         # index 2
        self.browser_combo.addItem("Mozilla Firefox")                       # index 3
        self.browser_combo.addItem(t('settings.browser.custom'))            # index 4
        self.browser_combo.currentTextChanged.connect(self._on_browser_changed)
        browser_layout.addWidget(self.browser_combo)
        
        # 사용자 지정 경로
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

        # 언어 설정 그룹
        lang_group = QGroupBox(t('settings.language.group_title'))
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
                color: #ff9800;
                background-color: rgba(255,152,0,20);
                padding: 6px 8px;
                border-radius: 4px;
                border: 1px solid rgba(255,152,0,60);
                font-size: 10px;
            }
        """)
        lang_layout.addWidget(lang_hint)
        layout.addWidget(lang_group) 

        # 안내 라벨
        info = QLabel(t('settings.overlay.info_text'))
        info.setWordWrap(True)
        info.setStyleSheet("""
            QLabel { color: #4a9eff; background: rgba(74,158,255,30);
                    font-size:10px; padding:10px; border-radius:5px;
                    border:1px solid rgba(74,158,255,100); }
        """)
        layout.addWidget(info)

        # 표시 항목 그룹
        show_group = QGroupBox(t('settings.overlay.group_items'))
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

        # 외관 그룹
        SLIDER_STYLE = """
            QSlider::groove:horizontal {
                height:6px; background:#3b3b3b;
                border:1px solid #555; border-radius:3px;
            }
            QSlider::handle:horizontal {
                background:#4a9eff; width:14px; height:14px;
                margin:-4px 0; border-radius:7px; border:1px solid #555;
            }
            QSlider::handle:horizontal:hover { background:#6bb4ff; }
            QSlider::sub-page:horizontal { background:#4a9eff; border-radius:3px; }
        """

        VALUE_LABEL_CLICK_STYLE = """
            QLabel { color:#4a9eff; font-size:11px; font-weight:bold;
                    padding:4px; background:#3b3b3b; border:1px solid #555;
                    border-radius:3px; min-width:42px; }
            QLabel:hover { border:1px solid rgba(74,158,255,180); background:#404040; }
        """

        FORM_LABEL_STYLE = "QLabel { color:#ccc; font-size:11px; min-width:80px; }"

        appear_group = QGroupBox(t('settings.overlay.group_appearance'))
        appear_layout = QVBoxLayout(appear_group)

        def make_slider_row(label_text, slider, value_label):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet(FORM_LABEL_STYLE)
            row.addWidget(lbl)
            row.addWidget(slider, 1)
            row.addWidget(value_label)
            return row

        self.overlay_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_scale_slider.setRange(50, 200)
        self.overlay_scale_slider.setTickInterval(25)
        self.overlay_scale_slider.setStyleSheet(SLIDER_STYLE)

        self.overlay_scale_value_label = ClickableLabel("100%")
        self.overlay_scale_value_label.setFixedWidth(48)
        self.overlay_scale_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_scale_value_label.setStyleSheet(VALUE_LABEL_CLICK_STYLE)
        self.overlay_scale_value_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overlay_scale_value_label.setToolTip("Reset to 100% on click")        

        self.overlay_scale_slider.valueChanged.connect(self._on_scale_preview)
        self.overlay_scale_value_label.clicked.connect(
            lambda: self.overlay_scale_slider.setValue(100)
        )
        appear_layout.addLayout(
            make_slider_row(t('settings.overlay.scale'), self.overlay_scale_slider,
                            self.overlay_scale_value_label))

        self.overlay_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity_slider.setRange(10, 100)
        self.overlay_opacity_slider.setTickInterval(10)
        self.overlay_opacity_slider.setStyleSheet(SLIDER_STYLE)

        self.overlay_opacity_value_label = ClickableLabel("80%")
        self.overlay_opacity_value_label.setFixedWidth(48)
        self.overlay_opacity_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_opacity_value_label.setStyleSheet(VALUE_LABEL_CLICK_STYLE)
        self.overlay_opacity_value_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overlay_opacity_value_label.setToolTip("Reset to 100% on click")      

        self.overlay_opacity_slider.valueChanged.connect(self._on_opacity_preview)
        self.overlay_opacity_value_label.clicked.connect(
            lambda: self.overlay_opacity_slider.setValue(100)
        )
        appear_layout.addLayout(make_slider_row(t('settings.overlay.opacity'), self.overlay_opacity_slider,
                            self.overlay_opacity_value_label))

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
        """langs/ 폴더 기반으로 언어 콤보박스 채우기"""

        self.lang_combo.clear()

        # "자동" 항목
        self.lang_combo.addItem(t('settings.language.auto'), userData='auto')

        # 언어팩 목록 (코드 → 표시명)
        manager = LangManager.instance()
        available = manager.get_available_languages()

        for code, name in available.items():
            self.lang_combo.addItem(name, userData=code)

        # 현재 설정값 선택
        saved = self.config.get('ui.language', 'auto')
        for i in range(self.lang_combo.count()):
            if self.lang_combo.itemData(i) == saved:
                self.lang_combo.setCurrentIndex(i)
                break

    # ============================================
    # 렌더링 설정 관련
    # ============================================    

    def _on_opengl_toggled(self, state: int):
        """OpenGL 체크박스 상태 변경 시"""
        enabled = (state == Qt.CheckState.Checked.value)
        
        # V-Sync와 MSAA는 OpenGL 활성화 시에만 의미가 있음
        self.vsync_checkbox.setEnabled(enabled)
        self.msaa_combo.setEnabled(enabled)
        
        if not enabled:
            self.vsync_checkbox.setStyleSheet("color: #666;")
            self.msaa_combo.setStyleSheet("color: #666;")
        else:
            self.vsync_checkbox.setStyleSheet("")
            self.msaa_combo.setStyleSheet("")

    # ============================================
    # 브라우저 관련
    # ============================================

    def _on_browser_changed(self, text: str):
        """브라우저 선택 변경"""
        idx = self.browser_combo.currentIndex()
        is_custom = (idx == 4)
        self.browser_path_edit.setEnabled(is_custom)
        _keys = list(self.BROWSER_PATHS.keys())   # ["Microsoft Edge", "Google Chrome", "Mozilla Firefox"]
        if 1 <= idx <= 3:
            self.browser_path_edit.setText(self.BROWSER_PATHS[_keys[idx - 1]])
        elif idx == 0:
            self.browser_path_edit.clear()
    

    def _browse_browser(self):
        """브라우저 실행 파일 선택"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            t('settings.browser.select_title'),
            "",
            t('settings.browser.select_filter'),
        )
        if file_path:
            self.browser_path_edit.setText(file_path)
    

    def _validate_browser_path(self, browser_path: str, browser_name: str) -> bool:
        """
        브라우저 경로 검증
        
        Args:
            browser_path: 검증할 브라우저 경로
            browser_name: 브라우저 이름 (오류 메시지용)
        
        Returns:
            True: 검증 통과 또는 사용자가 계속 진행 선택
            False: 사용자가 취소 선택
        """
        if not Path(browser_path).exists():
            reply = QMessageBox.warning(
                self,
                t('settings.browser.no_browser_title'),
                 t('settings.browser.no_browser_msg', name=browser_name, path=browser_path),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            return reply == QMessageBox.StandardButton.Yes
        
        return True

    # ============================================
    # 설정 로드/저장
    # ============================================

    def _load_settings(self):
        """설정 불러오기"""
        # 렌더링 설정 로드
        use_opengl = self.config.get_rendering_setting('use_opengl', True)
        self.opengl_checkbox.setChecked(use_opengl)
        
        vsync = self.config.get_rendering_setting('vsync', True)
        self.vsync_checkbox.setChecked(vsync)
        
        msaa_samples = self.config.get_rendering_setting('msaa_samples', 4)
        msaa_index = {0: 0, 2: 1, 4: 2, 8: 3, 16: 4}.get(msaa_samples, 2)
        self.msaa_combo.setCurrentIndex(msaa_index)

        # V-Sync/MSAA 활성화 상태 설정
        self._on_opengl_toggled(Qt.CheckState.Checked.value if use_opengl else Qt.CheckState.Unchecked.value)
        
        # 캐시
        self.ahead_spin.setValue(self.config.get('cache.ahead_count', 25))
        self.behind_spin.setValue(self.config.get('cache.behind_count', 5))
        self.memory_spin.setValue(self.config.get('cache.max_memory_mb', 700))
        
        self.thumb_memory_spin.setValue(self.config.get('cache.thumb_memory_mb', 100))
        self.thumb_disk_spin.setValue(self.config.get('cache.thumb_disk_mb', 500))
        self.ofm_memory_spin.setValue(self.config.get('cache.ofm_memory_mb',   50))
        self.ofm_disk_spin.setValue(  self.config.get('cache.ofm_disk_mb',    200))
        self.ofm_expiry_spin.setValue(self.config.get('cache.ofm_expiry_days',  28))

        # 애니메이션
        anim_quality = self.config.get('animation', {}).get('scale_quality', 'high')
        quality_map = {'low': 0, 'medium': 1, 'high': 2}
        self.anim_quality_combo.setCurrentIndex(quality_map.get(anim_quality, 2))

        anim_cache = self.config.get('animation', {}).get('cache_mode', True)
        self.anim_cache_checkbox.setChecked(anim_cache)
        
        webp_mode = self.config.get('animation', {}).get('webp_mode', 'quality')
        self.webp_mode_combo.setCurrentIndex(0 if webp_mode == 'fast' else 1)
        
        # 지도
        map_service = self.config.get('map.service', 'naver')
        map_index = {'naver': 0, 'kakao': 1, 'google': 2}.get(map_service, 0)
        self.map_service_combo.setCurrentIndex(map_index)
        
        browser_path = self.config.get('browser.path', 'system_default')
        
        # 역 매핑 생성 (경로 → 인덱스)
        path_to_index = {p: idx for idx, (_, p) in enumerate(self.BROWSER_PATHS.items(), 1)}
        path_to_index['system_default'] = 0
        
        if browser_path in path_to_index:
            # 시스템 기본 또는 미리 정의된 브라우저
            index = path_to_index[browser_path]
            self.browser_combo.setCurrentIndex(index)
            if browser_path != 'system_default':
                self.browser_path_edit.setText(browser_path)
            self.browser_path_edit.setEnabled(False)
        else:
            # 사용자 지정
            self.browser_combo.setCurrentIndex(4)
            self.browser_path_edit.setText(browser_path)
            self.browser_path_edit.setEnabled(True)
        
        # 오버레이
        self.show_file_info.setChecked(
            self.config.get_overlay_setting("show_file_info", True))
        self.show_camera_info.setChecked(
            self.config.get_overlay_setting("show_camera_info", True))
        self.show_exif_info.setChecked(
            self.config.get_overlay_setting("show_exif_info", True))
        self.show_lens_info.setChecked(
            self.config.get_overlay_setting("show_lens_info", False))
        self.show_gps_info.setChecked(
            self.config.get_overlay_setting("show_gps_info", False))
        self.show_map.setChecked(
            self.config.get_overlay_setting("show_map", False))

        # 슬라이더 로드
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

        pos_map = {"top_left": 0, "top_right": 1, "bottom_left": 2, "bottom_right": 3}
        cur_pos = self.config.get_overlay_setting("position", "top_left")
        self.overlay_position.setCurrentIndex(pos_map.get(cur_pos, 0))


    def accept(self):
        """확인 버튼 - 모든 설정 저장"""

        cache_changed = False
        overlay_changed = False
        rendering_changed = False
        needs_restart = False

        # 언어 설정 저장
        lang_code = self.lang_combo.currentData()
        old_lang = self.config.get('ui.language', 'auto')
        if old_lang != lang_code:
            self.config.set('ui.language', lang_code)
            needs_restart = True   # 언어 변경은 항상 재시작 필요
            
        # 렌더링 설정 저장
        rendering_settings = [
            ('use_opengl', self.opengl_checkbox.isChecked(), True),
            ('vsync', self.vsync_checkbox.isChecked(), True),
            ('msaa_samples', [0, 2, 4, 8, 16][self.msaa_combo.currentIndex()], 4)
        ]

        for key, new_value, default in rendering_settings:
            old_value = self.config.get_rendering_setting(key, default)
            if old_value != new_value:
                rendering_changed = True
                needs_restart = True
            self.config.set_rendering_setting(key, new_value)

        browser_index = self.browser_combo.currentIndex()
        
        if browser_index == 0:
            # 시스템 기본
            self.config.set('browser.path', 'system_default')
        elif browser_index in [1, 2, 3]:
            # Edge, Chrome, Firefox
            browser_name = self.browser_combo.currentText()
            selected_path = self.BROWSER_PATHS[browser_name]
            
            if not self._validate_browser_path(selected_path, browser_name):
                return  # 사용자가 취소 선택
            
            if Path(selected_path).exists():
                self.config.set('browser.path', selected_path)
            else:
                self.config.set('browser.path', 'system_default')
        else:
            # 사용자 지정 (index 4)
            browser_path = self.browser_path_edit.text().strip()
            if browser_path:
                if not self._validate_browser_path(browser_path, "사용자 지정 브라우저"):
                    return
                self.config.set('browser.path', browser_path)
            else:
                self.config.set('browser.path', 'system_default')
        
        # _save_settings() — 기본값 통일 + 재시작 필요 분류
        RUNTIME_KEYS = {'cache.ahead_count', 'cache.behind_count', 'cache.max_memory_mb'}

        cache_settings = [
            ('cache.ahead_count',     self.ahead_spin.value(),         25),
            ('cache.behind_count',    self.behind_spin.value(),         5),
            ('cache.max_memory_mb',   self.memory_spin.value(),        700), 
            ('cache.thumb_memory_mb', self.thumb_memory_spin.value(), 100),  
            ('cache.thumb_disk_mb',   self.thumb_disk_spin.value(),   500), 
            ('cache.ofm_memory_mb',   self.ofm_memory_spin.value(),   50),
            ('cache.ofm_disk_mb',     self.ofm_disk_spin.value(),    200),
            ('cache.ofm_expiry_days', self.ofm_expiry_spin.value(),   28),
        ]

        # needs_restart 플래그를 세분화
        cache_runtime_changed = False   # 런타임 반영 가능 (재시작 불필요)
        cache_restart_needed  = False   # 재시작 필요

        for key, new_value, default in cache_settings:
            old_value = self.config.get(key, default)
            if old_value != new_value:
                cache_changed = True
                if key in RUNTIME_KEYS:
                    cache_runtime_changed = True   # 런타임 반영
                else:
                    cache_restart_needed = True    # 재시작 필요
                    needs_restart = True
                self.config.set(key, new_value)
        
        quality_values = ['low', 'medium', 'high']
        webp_mode_values = ['fast', 'quality']
        self.config.set('animation', {
            'scale_quality': quality_values[self.anim_quality_combo.currentIndex()],
            'cache_mode': self.anim_cache_checkbox.isChecked(),
            'webp_mode': webp_mode_values[self.webp_mode_combo.currentIndex()],
        })
        
        map_services = ['naver', 'kakao', 'google']
        self.config.set('map.service', map_services[self.map_service_combo.currentIndex()])
        
        overlay_settings = [
            ("show_file_info",    self.show_file_info.isChecked(),    True),
            ("show_camera_info",  self.show_camera_info.isChecked(),  True),
            ("show_exif_info",    self.show_exif_info.isChecked(),    True),
            ("show_lens_info",    self.show_lens_info.isChecked(),    False),
            ("show_gps_info",     self.show_gps_info.isChecked(),     False),
            ("show_map",          self.show_map.isChecked(),          False),
            # 슬라이더 값
            ("opacity", self.overlay_opacity_slider.value() / 100.0, 0.8),
        ]
        for key, new_val, default in overlay_settings:
            old_val = self.config.get_overlay_setting(key, default)
            if old_val != new_val:
                overlay_changed = True
                self.config.set_overlay_setting(key, new_val)

        # scale 저장
        new_scale = self.overlay_scale_slider.value()
        if self.config.get("overlay.scale", 100) != new_scale:
            overlay_changed = True
            self.config.set("overlay.scale", new_scale)

        pos_map = ["top_left", "top_right", "bottom_left", "bottom_right"]
        new_pos = pos_map[self.overlay_position.currentIndex()]
        old_pos = self.config.get_overlay_setting("position", "top_left")
        if old_pos != new_pos:
            overlay_changed = True
            self.config.set_overlay_setting("position", new_pos)
        
        self.config.save()
        info_print(f"설정 저장 완료")
        
        self.settings_changed.emit()
        
        if cache_changed:
            self.cache_settings_changed.emit()
            debug_print(f"캐시 설정 변경됨")
        
        if overlay_changed:
            self.overlay_settings_changed.emit()
            debug_print(f"오버레이 설정 변경됨")
        
        # 렌더링 설정 변경 시그널
        if rendering_changed:
            self.rendering_settings_changed.emit()
            debug_print(f"렌더링 설정 변경됨")
        
        if needs_restart:
            changed_items = []
            if rendering_changed:
                 changed_items.append(t('settings.restart_rendering'))
            if cache_restart_needed:                                         
                changed_items.append(t('settings.restart_cache_disk'))
            # cache_runtime_changed (선행/후행/뷰어메모리)는 재시작 불필요 → 메시지 제외
            message = t('settings.restart_body')
            if changed_items:
                message += "\n".join(changed_items)
            QMessageBox.information(self, t('settings.restart_title'), message)

        super().accept()


    def reject(self) -> None:
        """취소 클릭 시 라이브 프리뷰로 바뀐 값을 원복"""
        self._preview_timer.stop()
        self.config.set("overlay.scale", self._orig_scale)
        self.config.set_overlay_setting("opacity", self._orig_opacity)
        # 원복 신호 → MainWindow가 즉시 재적용
        self.overlay_settings_changed.emit()
        super().reject()       

    # ============================================
    # 오버레이 실시간 프리뷰
    # ============================================

    def _on_scale_preview(self, v: int) -> None:
        """크기 슬라이더 실시간 반영 (debounce 80ms)"""
        self.overlay_scale_value_label.setText(f"{v}%")
        self.config.set("overlay.scale", v) 
        self._preview_timer.start(80)  


    def _on_opacity_preview(self, v: int) -> None:
        """투명도 슬라이더 실시간 반영 (debounce 80ms)"""
        self.overlay_opacity_value_label.setText(f"{v}%")
        self.config.set_overlay_setting("opacity", v / 100.0)
        self._preview_timer.start(80)

    # ============================================
    # 캐시 용량 계산 / 표시
    # ============================================

    def _calculate_folder_size(self, folder_path: Path) -> int:

        total_size = 0
        try:
            if not folder_path.exists():
                return 0
            
            for file in folder_path.rglob('*'):
                if file.is_file():
                    try:
                        total_size += file.stat().st_size
                    except (OSError, PermissionError):
                        continue
        except Exception as e:
            error_print(f"폴더 용량 계산 실패: {e}")
        
        return total_size


    def _format_file_size(self, size_bytes: int) -> str:

        size = float(size_bytes)
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        
        return f"{size:.2f} PB"


    def _refresh_cache_size(self, cache_dir: Path, label: QLabel) -> None:
        """특정 캐시 디렉토리 용량을 label 에 표시"""

        label.setText(t('settings.cache.calculating'))
        label.setStyleSheet("color: #888;")

        def _calc():
            size_bytes = self._calculate_folder_size(cache_dir)
            # .cache 파일만 카운트 (DB 제외)
            count = len(list(cache_dir.glob("*.cache"))) if cache_dir.exists() else 0
            size_str = self._format_file_size(size_bytes)
            label.setText(f"{size_str}  ({count:,} files)")

            if size_bytes > 400 * 1024 * 1024:
                label.setStyleSheet("color: #ff5252; font-weight: bold;")
            elif size_bytes > 100 * 1024 * 1024:
                label.setStyleSheet("color: #ff9800; font-weight: bold;")
            else:
                label.setStyleSheet("color: #4caf50; font-weight: bold;")

        QTimer.singleShot(0, _calc)


    def _refresh_tile_cache_size(self) -> None:
        """ofm_rendered/ 크기를 ofm_size_label에 표시"""
        self.ofm_size_label.setText(t('settings.cache.calculating'))
        self.ofm_size_label.setStyleSheet("color: #888;")

        def _calc():
            ofm_bytes = self._calculate_folder_size(self._ofm_cache_dir)
            ofm_files = len(list(self._ofm_cache_dir.glob("*.cache"))) \
                        if self._ofm_cache_dir.exists() else 0

            size_str = self._format_file_size(ofm_bytes)
            self.ofm_size_label.setText(f"{size_str}  ({ofm_files:,} files)")

            if ofm_bytes > 400 * 1024 * 1024:
                self.ofm_size_label.setStyleSheet("color: #ff5252; font-weight: bold;")
            elif ofm_bytes > 100 * 1024 * 1024:
                self.ofm_size_label.setStyleSheet("color: #ff9800; font-weight: bold;")
            else:
                self.ofm_size_label.setStyleSheet("color: #4caf50; font-weight: bold;")

        QTimer.singleShot(0, _calc)

    # ============================================
    # 캐시 삭제 / 폴더 관리
    # ============================================

    def _open_cache_folder(self, cache_dir: Path) -> None:

        try:
            # 폴더가 없으면 생성
            if not cache_dir.exists():
                cache_dir.mkdir(parents=True, exist_ok=True)
            
            if platform.system() == 'Windows':
                subprocess.run(['explorer', str(cache_dir)])
            elif platform.system() == 'Darwin':  # macOS
                subprocess.run(['open', str(cache_dir)])
            else:  # Linux
                subprocess.run(['xdg-open', str(cache_dir)])
            
            info_print(f"캐시 폴더 열기: {cache_dir}")
        except Exception as e:
            error_print(f"폴더 열기 실패: {e}")
            QMessageBox.warning(
                self,
                t('dialog.folder_open_error_title'),
                t('dialog.folder_open_error_msg', error=e),
            )


    def _clear_thumbnail_cache(self) -> None:
        reply = QMessageBox.question(
            self,
            t('dialog.thumb_clear_title'),
            t('dialog.thumb_clear_msg'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 메모리 캐시 해제 (running 인스턴스)
        self.thumbnail_cache_clear_requested.emit()

        # 디스크 캐시 삭제
        deleted = self._delete_cache_dir(self._thumb_cache_dir)

        QMessageBox.information(
            self,
            t('dialog.thumb_cleared_title'),
            t('dialog.thumb_cleared_msg', size=self._format_file_size(deleted)),
        )
        self._refresh_cache_size(self._thumb_cache_dir, self.thumb_size_label)
        info_print("썸네일 캐시 삭제 완료")


    def _clear_tile_cache(self) -> None:
        reply = QMessageBox.question(
            self,
            t('dialog.tile_clear_title'),
            t('dialog.tile_clear_msg'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        _render_cache.clear()
        self.tile_cache_clear_requested.emit()

        deleted = self._delete_cache_dir(self._ofm_cache_dir)  

        _render_cache._ensure_db()

        QMessageBox.information(
            self,
            t('dialog.tile_cleared_title'),
            t('dialog.tile_cleared_msg', size=self._format_file_size(deleted)),
        )
        self._refresh_tile_cache_size()
        info_print("OFM 렌더 캐시 삭제 완료")


    # 디렉토리 삭제 + 재생성
    def _delete_cache_dir(self, cache_dir: Path) -> int:
        """캐시 디렉토리 내용 전체 삭제 후 빈 디렉토리 재생성. 확보된 바이트 반환."""

        deleted_bytes = self._calculate_folder_size(cache_dir)
        try:
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            error_print(f"캐시 디렉토리 삭제 실패: {e}")
            QMessageBox.critical(self, t('dialog.cache_delete_error_title'), t('dialog.cache_delete_error_msg', error=e),)
        return deleted_bytes

    # ============================================
    # 스타일 유틸리티
    # ============================================

    def _danger_btn_style(self) -> str:
        return """
            QPushButton {
                background-color: #d32f2f; color: white;
                padding: 5px 10px; border: none;
                border-radius: 4px; font-weight: bold;
            }
            QPushButton:hover   { background-color: #f44336; }
            QPushButton:pressed { background-color: #b71c1c; }
        """

