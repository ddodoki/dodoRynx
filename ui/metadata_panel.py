# -*- coding: utf-8 -*-
# ui/metadata_panel.py

"""
메타데이터 패널 - EXIF 정보 표시
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from core.map_loader import OFMMapLoader
from core.metadata_reader import MetadataReader

from utils.config_manager import ConfigManager
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t


_ZOOM_DEBOUNCE_MS = 600


class ClickableLabel(QLabel):
    """
    클릭 가능한 라벨.
    """

    def __init__(
        self,
        display_text: str = "",
        copy_value: str = "",
        parent=None,
    ) -> None:
        super().__init__(display_text, parent)
        self._copy_value = copy_value
        self._original_style = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self._restore_style)

    # ── 외부에서 스타일 지정 시 원본 자동 보관 ──────────────────────
    def setStyleSheet(self, style: str) -> None:
        self._original_style = style
        super().setStyleSheet(style)

    def set_copy_value(self, value: str) -> None:
        self._copy_value = value

    # ── 마우스 클릭 ────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._copy_value:
            QApplication.clipboard().setText(self._copy_value)
            self._show_feedback(event.globalPosition().toPoint())
        super().mousePressEvent(event)

    # ── 시각적 피드백 ───────────────────────────────────────────────
    def _show_feedback(self, global_pos) -> None:
        """색상을 녹색으로 변경하고 툴팁으로 복사 완료 알림"""
        import re
        feedback_style = re.sub(
            r"(?<!-)color\s*:\s*[^;]+",
            "color: #4CAF50",
            self._original_style,
        )
        super().setStyleSheet(feedback_style)
        QToolTip.showText(global_pos, t('metadata_panel.copy_feedback'), self, self.rect(), 1200)
        self._feedback_timer.start(700)

    def _restore_style(self) -> None:
        super().setStyleSheet(self._original_style)


class MetadataPanel(QWidget):
    """메타데이터 패널"""

    MIN_ZOOM = 1
    MAX_ZOOM = 18
    DEFAULT_ZOOM = 15

    gps_clicked = Signal(float, float) 
    map_zoom_changed = Signal(int) 
    
    # ============================================
    # 초기화
    # ============================================

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.config = config
        self.metadata_reader = MetadataReader()
        self.current_metadata: Dict[str, Any] = {}
        self.metadata_widgets: List[QWidget] = [] 
        
        # 지도 관련
        self.map_loader: Optional[OFMMapLoader] = None
        self.current_gps: Optional[tuple] = None
        self.current_zoom: int = self.DEFAULT_ZOOM

        self._zoom_debounce_timer = QTimer(self)
        self._zoom_debounce_timer.setSingleShot(True)
        self._zoom_debounce_timer.timeout.connect(self._on_zoom_debounce_timeout)
        
        # 설정 저장 디바운싱 타이머
        self.config_save_timer = QTimer(self)
        self.config_save_timer.setSingleShot(True)
        self.config_save_timer.timeout.connect(self._save_config_delayed)

        self._init_ui()
        
        # 폭 고정
        self.setMinimumWidth(300)
        self.setMaximumWidth(300)
    

    def _init_ui(self) -> None:
        """UI 초기화"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
               
        # 스크롤 영역
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # 메타데이터 컨테이너
        self.metadata_container = QWidget()
        self.metadata_layout = QVBoxLayout(self.metadata_container)
        self.metadata_layout.setContentsMargins(10, 10, 10, 10)
        self.metadata_layout.setSpacing(5)
        self.metadata_layout.addStretch()
        
        scroll_area.setWidget(self.metadata_container)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #202020;
                border: none;
                border-top: 1px solid rgba(255, 255, 255, 0.06);
            }

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

            /* 위/아래 버튼 제거 */
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
                border: none;
                background: none;
            }

            /* 페이지 영역 제거 */
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }
        """)

        layout.addWidget(scroll_area)
        

    # ============================================
    # 메타데이터 로딩
    # ============================================

    def load_metadata(self, file_path: Optional[Path]) -> Dict[str, Any]:
        """메타데이터 로딩"""

        self.stop_map_loader()
        self._clear_widgets()

        def _finalize_empty() -> Dict[str, Any]:
            """빈 상태 공통 종료 처리"""
            self.metadata_layout.addStretch()
            return {}

        if file_path is None:
            self.current_metadata = {}
            self.current_gps = None
            return _finalize_empty()

        if not file_path.exists():
            warning_print(f"파일이 존재하지 않음: {file_path}")
            self.current_metadata = {}
            self.current_gps = None
            return _finalize_empty()

        debug_print(f"메타데이터 로딩 시작: {file_path.name}")

        metadata_result = self.metadata_reader.read(file_path)
        self.current_metadata = metadata_result if metadata_result else {}

        if not self.current_metadata:
            warning_print(f"메타데이터 없음: {file_path.name}")
            return _finalize_empty() 

        file_info = self.current_metadata.get('file', {})
        if file_info:
            self._add_section(t('metadata_panel.section_file'), file_info)

        camera_info = self.current_metadata.get('camera', {})
        if camera_info:
            self._add_section(t('metadata_panel.section_camera'), camera_info)

        exif_info = self.current_metadata.get('exif', {})
        if exif_info:
            self._add_section(t('metadata_panel.section_exif'), exif_info)

        gps_info = self.current_metadata.get('gps')
        if gps_info:
            debug_print(f"GPS 정보 있음: {gps_info.get('display', 'N/A')}")
            self._add_gps_section(gps_info)
        else:
            debug_print("GPS 정보 없음")
            self.current_gps = None

        self.metadata_layout.addStretch()

        debug_print("메타데이터 로딩 완료")

        return self.current_metadata


    def get_current_metadata(self) -> Dict[str, Any]:
        """현재 메타데이터 반환"""
        return self.current_metadata


    # ============================================
    # 섹션 생성
    # ============================================

    def _add_section(self, title: str, data: Dict[str, Any]) -> None:
        """섹션 추가"""
        if not data:
            return
        
        # 섹션 제목
        title_label = QLabel(title)
        title_label.setFont(QFont("", 10, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #4a9eff; margin-top: 5px;")
        self.metadata_layout.addWidget(title_label)
        self.metadata_widgets.append(title_label)
        
        # 데이터
        for key, value in data.items():
            str_value = str(value)

            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)

            # 내부 영문 키 → 번역 레이블 (없으면 키 그대로)
            display_key = t(f'metadata_labels.{key}', default=key)            

            # Key 라벨
            key_label = ClickableLabel(
                f"{display_key}:",
                copy_value=f"{display_key}: {str_value}",
            )
            key_label.setStyleSheet("color: #aaa; font-size: 10px;")
            key_label.setFixedWidth(100)
            key_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft) 
            row.addWidget(key_label)
            
            # Value 라벨
            value_label = ClickableLabel(str_value, copy_value=str_value)
            value_label.setStyleSheet("color: #fff; font-size: 10px;")
            value_label.setWordWrap(True) 
            value_label.setMaximumWidth(160) 
            value_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            
            value_label.setSizePolicy(
                QSizePolicy.Policy.Expanding,  
                QSizePolicy.Policy.Minimum  
            )
            
            if key == "filename":
                value_label.setMinimumHeight(20)  

            row.addWidget(value_label, 1) 
            
            self.metadata_layout.addWidget(row_widget)
            self.metadata_widgets.append(row_widget)


    def _add_gps_section(self, gps_info: Dict[str, Any]) -> None:

        # ── 공통 디자인 토큰 ──────────────────────────────────────
        _C_BG        = "#1e1e1e"
        _C_BG_RAISED = "#252525"
        _C_BORDER    = "rgba(255,255,255,0.08)"
        _C_BTN_BG    = "rgba(255,255,255,0.05)"
        _C_BTN_BOR   = "rgba(255,255,255,0.10)"
        _C_HOVER_BG  = "rgba(74,158,255,0.18)"
        _C_HOVER_BOR = "rgba(74,158,255,0.60)"
        _C_PRESS_BG  = "rgba(74,158,255,0.32)"
        _C_ACCENT    = "#4a9eff"
        _C_TEXT      = "#cccccc"
        _C_TEXT_DIM  = "#888888"
        _RADIUS      = "4px"
        _BTN_H       = 26

        # ── 섹션 제목 ─────────────────────────────────────────────
        title_label = QLabel("🌍 GPS")
        title_label.setFont(QFont("", 10, QFont.Weight.Bold))
        title_label.setStyleSheet(f"""
            QLabel {{
                color: {_C_ACCENT};
                font-size: 11px;
                font-weight: bold;
                padding: 6px 0 2px 0;
                background: transparent;
            }}
        """)
        self.metadata_layout.addWidget(title_label)
        self.metadata_widgets.append(title_label)

        # ── GPS 좌표 버튼 ─────────────────────────────────────────
        gps_display = f"📍 {gps_info.get('display', '위치 정보')}"
        if 'altitude' in gps_info:
            gps_display += f"   ⛰ {gps_info['altitude']}"

        gps_btn = QPushButton(gps_display)
        gps_btn.setFixedHeight(_BTN_H)
        gps_btn.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 0 8px;
                background: {_C_BTN_BG};
                color: {_C_ACCENT};
                border: 1px solid {_C_BTN_BOR};
                border-radius: {_RADIUS};
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: {_C_HOVER_BG};
                border-color: {_C_HOVER_BOR};
                color: #ffffff;
            }}
            QPushButton:pressed {{
                background: {_C_PRESS_BG};
            }}
        """)
        gps_btn.clicked.connect(
            lambda: self.gps_clicked.emit(
                gps_info['latitude'],
                gps_info['longitude']
            )
        )
        self.metadata_layout.addWidget(gps_btn)
        self.metadata_widgets.append(gps_btn)

        self.current_gps  = (gps_info['latitude'], gps_info['longitude'])
        self.current_zoom = self.config.get_gps_map_setting("default_zoom", 15)

        # ── 자동 로드 체크박스 ────────────────────────────────────
        auto_load_checkbox = QCheckBox(t('metadata_panel.auto_load_map'))
        auto_load_checkbox.setStyleSheet(f"""
            QCheckBox {{
                color: {_C_TEXT};
                font-size: 11px;
                spacing: 6px;
                background: transparent;
            }}
            QCheckBox::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {_C_BTN_BOR};
                border-radius: 3px;
                background: {_C_BTN_BG};
            }}
            QCheckBox::indicator:checked {{
                background: rgba(74,158,255,0.40);
                border-color: {_C_ACCENT};
            }}
            QCheckBox::indicator:hover {{
                border-color: {_C_HOVER_BOR};
            }}
        """)
        auto_load = self.config.get_gps_map_setting("auto_load", False)
        auto_load_checkbox.setChecked(auto_load)
        auto_load_checkbox.stateChanged.connect(self._on_auto_load_changed)
        self.metadata_layout.addWidget(auto_load_checkbox)
        self.metadata_widgets.append(auto_load_checkbox)

        # ── 지도 컨테이너 ─────────────────────────────────────────
        map_container = QWidget()
        map_container.setStyleSheet(f"""
            QWidget {{
                background: transparent;
            }}
        """)
        map_layout = QVBoxLayout(map_container)
        map_layout.setContentsMargins(0, 4, 0, 0)
        map_layout.setSpacing(4)

        # 진행률 바
        self.map_progress = QProgressBar()
        self.map_progress.setMaximum(100)
        self.map_progress.setValue(0)
        self.map_progress.setTextVisible(True)
        self.map_progress.setFormat(t('metadata_panel.map_progress_format'))
        self.map_progress.setFixedHeight(18)
        self.map_progress.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {_C_BTN_BOR};
                border-radius: {_RADIUS};
                background: {_C_BTN_BG};
                text-align: center;
                color: {_C_TEXT};
                font-size: 10px;
            }}
            QProgressBar::chunk {{
                background: rgba(74,158,255,0.50);
                border-radius: 3px;
            }}
        """)
        self.map_progress.setVisible(False)
        map_layout.addWidget(self.map_progress)

        # 지도 이미지
        self.map_label = QLabel(t('metadata_panel.map_placeholder'))
        self.map_label.setFixedSize(280, 200)
        self.map_label.setScaledContents(False)
        self.map_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_label.setStyleSheet(f"""
            QLabel {{
                background: {_C_BG};
                color: {_C_TEXT_DIM};
                border: 1px solid {_C_BORDER};
                border-radius: {_RADIUS};
                font-size: 11px;
            }}
        """)
        map_layout.addWidget(self.map_label)

        # ── 지도 컨트롤 버튼 ─────────────────────────────────────
        _btn_style = f"""
            QPushButton {{
                background: {_C_BTN_BG};
                color: {_C_TEXT};
                border: 1px solid {_C_BTN_BOR};
                border-radius: {_RADIUS};
                font-size: 13px;
                padding: 0;
            }}
            QPushButton:hover {{
                background: {_C_HOVER_BG};
                border-color: {_C_HOVER_BOR};
                color: #ffffff;
            }}
            QPushButton:pressed {{
                background: {_C_PRESS_BG};
            }}
        """

        zoom_in_btn  = QPushButton("🔍+")
        zoom_out_btn = QPushButton("🔍-")
        reset_btn    = QPushButton("↺")

        for btn, tip, slot in (
            (zoom_in_btn,  t('metadata_panel.zoom_in_tooltip'),  self._zoom_in),
            (zoom_out_btn, t('metadata_panel.zoom_out_tooltip'), self._zoom_out),
            (reset_btn,    t('metadata_panel.reset_tooltip'),    self._reset_zoom),
        ):
            btn.setFixedHeight(_BTN_H)
            btn.setToolTip(tip)
            btn.setStyleSheet(_btn_style)
            btn.clicked.connect(slot)

        # 줌 레벨 표시 라벨
        self.zoom_level_label = QLabel(
            t('metadata_panel.zoom_label', zoom=self.current_zoom)
        )
        self.zoom_level_label.setFixedSize(60, _BTN_H)
        self.zoom_level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_level_label.setStyleSheet(f"""
            QLabel {{
                color: {_C_TEXT_DIM};
                font-size: 10px;
                background: transparent;
                border: none;
                padding: 0;
            }}
        """)

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(4)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addWidget(zoom_in_btn)
        controls_layout.addWidget(zoom_out_btn)
        controls_layout.addWidget(self.zoom_level_label)
        controls_layout.addWidget(reset_btn)
        map_layout.addLayout(controls_layout)

        self.metadata_layout.addWidget(map_container)
        self.metadata_widgets.append(map_container)

        if auto_load:
            self._load_map()

        # ── Attribution ───────────────────────────────────────────
        attr_label = QLabel(
            '<a href="https://openfreemap.org/">© OpenFreeMap</a>'
            '&nbsp;·&nbsp;'
            '<a href="https://www.openmaptiles.org/">© OpenMapTiles</a>'
            '&nbsp;·&nbsp;'
            '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a>'
        )
        attr_label.setTextFormat(Qt.TextFormat.RichText)
        attr_label.setOpenExternalLinks(True)
        attr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        attr_label.setWordWrap(True)
        attr_label.setStyleSheet(f"""
            QLabel {{
                color: rgba(255,255,255,0.20);
                font-size: 9px;
                padding: 2px 0;
                background: transparent;
            }}
            QLabel a {{
                color: rgba(74,158,255,0.50);
                text-decoration: none;
            }}
            QLabel a:hover {{
                color: {_C_ACCENT};
                text-decoration: underline;
            }}
        """)
        map_layout.addWidget(attr_label)
        

    # ============================================
    # 위젯 정리
    # ============================================

    def _clear_widgets(self) -> None:
        """
        metadata_widgets 추적 리스트 제거 → layout 단일 경로로만 삭제.
        layout의 takeAt()으로 위젯을 꺼내면 부모-자식 관계가 해제되어
        deleteLater() 1회만 호출됨.
        """
        self.metadata_widgets.clear()

        while self.metadata_layout.count() > 0:
            item = self.metadata_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater() 
            else:
                layout = item.layout()
                if layout is not None:
                    self._clear_layout(layout)
                        

    def _clear_layout(self, layout) -> None:
        """레이아웃 재귀적으로 클리어"""
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                else:
                    # 중첩 레이아웃
                    nested_layout = item.layout()
                    if nested_layout is not None:
                        self._clear_layout(nested_layout)


    # ============================================
    # 지도 관리
    # ============================================

    def stop_map_loader(self) -> None:
        """
        OFMMapLoader 취소 및 참조 해제 (단일 진입점).

        OFMMapLoader(QObject) 아키텍처:
        - cancel() : _cancelled 플래그 + QWebEngineView hide/deleteLater
        - deleteLater() : QObject C++ 메모리 이벤트 루프에서 안전 해제
        - self.map_loader = None 로 지연 콜백(QueuedConnection) 무효화
        """
        if self.map_loader is None:
            return

        loader = self.map_loader
        self.map_loader = None  

        try:
            loader.map_loaded.disconnect(self._on_map_loaded)
        except RuntimeError:
            pass
        try:
            loader.load_failed.disconnect(self._on_map_failed)
        except RuntimeError:
            pass
        try:
            loader.progress.disconnect(self._on_map_progress)
        except RuntimeError:
            pass

        loader.cancel()      
        loader.deleteLater()    
        debug_print("OFMMapLoader 취소 완료")


    def _apply_attribution_overlay(self, img: QImage) -> QPixmap:
        """
        QImage에 attribution 텍스트 오버레이를 그린 후 QPixmap 반환.
        _load_map(캐시 HIT)과 _on_map_loaded(캐시 MISS) 공용.
        """
        crop_x = max(0, (img.width()  - 280) // 2)
        crop_y = max(0, (img.height() - 200) // 2)
        crop_w = min(280, img.width())
        crop_h = min(200, img.height())
        cropped = img.copy(crop_x, crop_y, crop_w, crop_h)

        painter = QPainter(cropped)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            font = QFont()
            font.setPointSize(7)
            painter.setFont(font)

            text = "OpenFreeMap © OpenMapTiles Data from OpenStreetMap"
            metrics = painter.fontMetrics()
            text_w  = metrics.horizontalAdvance(text)
            text_h  = metrics.height()
            margin  = 4

            x = crop_w - text_w - margin
            y = crop_h - margin

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 200))
            painter.drawRoundedRect(
                x - 2, y - text_h - 2,
                text_w + 6, text_h + 4,
                3, 3,
            )
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(x, y, text)
        finally:
            painter.end()

        return QPixmap.fromImage(cropped)


    def _load_map(self) -> None:
        """
        지도 로드 시작.
        캐시 HIT: 로딩 UI 없이 즉시 표시.
        캐시 MISS: 프로그레스바 표시 후 QWebEngineView 렌더링.
        """
        if not self.current_gps:
            return

        lat, lon = self.current_gps

        # ── 렌더 캐시 선행 확인 ───────────────────────────────────────────
        pix = OFMMapLoader.get_cached_pixmap(lat, lon, self.current_zoom, 400, 300)

        if pix is not None:
            self.stop_map_loader()
            try:
                pixmap = self._apply_attribution_overlay(pix.toImage())
                self.map_progress.setVisible(False)
                self.map_label.setScaledContents(False)
                self.map_label.setPixmap(pixmap)
                self.map_label.setFixedSize(280, 200)
                self.map_label.show()
                debug_print(f"OFM 패널 캐시 HIT: z={self.current_zoom}")
            except RuntimeError:
                pass
            return

        # ── 캐시 MISS → 프로그레스바 + WebView 시작 ──────────────────────
        self.stop_map_loader() 

        try:
            self.map_progress.setVisible(False)
            self.map_progress.setValue(0)
            self.map_progress.setFormat("렌더링 중...")
            self.map_label.setPixmap(QPixmap())
            self.map_label.setText(t("metadata_panel.map_loading_zoom", zoom=self.current_zoom))
        except RuntimeError:
            return 

        self.map_loader = OFMMapLoader(
            lat, lon,
            zoom   = self.current_zoom,
            width  = 400,
            height = 300,
        )
        # QueuedConnection: 캐시 HIT 동기 emit 재진입 방지
        self.map_loader.map_loaded.connect(
            self._on_map_loaded, Qt.ConnectionType.QueuedConnection
        )
        self.map_loader.load_failed.connect(
            self._on_map_failed, Qt.ConnectionType.QueuedConnection
        )
        self.map_loader.progress.connect(
            self._on_map_progress, Qt.ConnectionType.QueuedConnection
        )
        self.map_loader.start()
        info_print(f"OFM 지도 로딩 시작: ({lat:.6f}, {lon:.6f}), 줌: {self.current_zoom}")


    def clear_map(self) -> None:
        """지도 클리어"""
        debug_print("clear_map() 호출")

        self.stop_map_loader() 

        try:
            if self.map_label:
                self.map_label.clear()
                self.map_label.hide()
        except RuntimeError as e:
            warning_print(f"map_label 이미 삭제됨: {e}")

        self.current_gps = None


    def _on_map_loaded(self, q_image: QImage) -> None:
        """지도 로딩 완료 콜백 (GUI 스레드)"""
        debug_print("_on_map_loaded 호출")

        if self.map_loader is None:
            debug_print("_on_map_loaded: 로더 취소됨 — 무시")
            return

        loader = self.map_loader
        self.map_loader = None

        try:
            loader.map_loaded.disconnect(self._on_map_loaded)
        except RuntimeError:
            pass
        try:
            loader.load_failed.disconnect(self._on_map_failed)
        except RuntimeError:
            pass
        try:
            loader.progress.disconnect(self._on_map_progress)
        except RuntimeError:
            pass

        loader.cancel() 
        loader.deleteLater()

        if q_image is None or q_image.isNull():
            warning_print("유효하지 않은 QImage")
            return

        try:
            if not hasattr(self, "map_label") or self.map_label is None:
                return
            self.map_label.isVisible()
        except RuntimeError as e:
            warning_print(f"map_label 이미 삭제됨: {e}")
            return

        try:
            if hasattr(self, "map_progress"):
                self.map_progress.setVisible(False)
        except RuntimeError:
            pass

        pixmap = self._apply_attribution_overlay(q_image)
        self.map_label.setPixmap(pixmap)
        self.map_label.setFixedSize(280, 200)
        self.map_label.show()
        debug_print("OFM 패널 지도 표시 완료: 280x200 + attribution 오버레이")


    def _on_map_failed(self, error: str) -> None:
        """지도 로딩 실패 콜백 (GUI 스레드)"""
        if self.map_loader is None:
            return

        loader = self.map_loader
        self.map_loader = None

        try:
            loader.map_loaded.disconnect(self._on_map_loaded)
        except RuntimeError:
            pass
        try:
            loader.load_failed.disconnect(self._on_map_failed)
        except RuntimeError:
            pass
        try:
            loader.progress.disconnect(self._on_map_progress)
        except RuntimeError:
            pass

        loader.cancel()
        loader.deleteLater()

        try:
            if hasattr(self, 'map_progress'):
                self.map_progress.setVisible(False)
        except RuntimeError:
            pass

        try:
            self.map_label.setPixmap(QPixmap())
            self.map_label.setText(f"❌ {error}")
        except RuntimeError as e:
            warning_print(f"map_label 이미 삭제됨 (_on_map_failed): {e}")


    def _on_map_progress(self, current: int, total: int) -> None:
        if self.map_loader is None:
            return
        try:
            if not hasattr(self, "map_progress"):
                return
            self.map_progress.isVisible()
        except RuntimeError:
            return

        if total > 0:
            percentage = int((current / total) * 100)
            self.map_progress.setValue(percentage)

            if current < total:
                self.map_progress.setFormat(
                    t('metadata_panel.map_progress_format') 
                )
            else:
                self.map_progress.setFormat("완료")
                # 완료 시 잠시 후 숨김
                QTimer.singleShot(500, lambda: self._hide_progress_safe())


    def _hide_progress_safe(self) -> None:
        try:
            if hasattr(self, 'map_progress'):
                self.map_progress.setVisible(False)
        except RuntimeError:
            pass
        

# ============================================
# 지도 줌 컨트롤
# ============================================

    def _change_zoom(self, new_zoom: int, log_message: str = "") -> None:
        self.current_zoom = new_zoom
        # config 저장은 디바운싱 후에만 (즉시 호출 제거)
        self._update_zoom_label()
        self.map_zoom_changed.emit(self.current_zoom)
        if log_message:
            info_print(f"{log_message}: {self.current_zoom}")
        # 연속 클릭 시 마지막 값만 반영
        self._zoom_debounce_timer.stop()
        self._zoom_debounce_timer.start(_ZOOM_DEBOUNCE_MS)


    def _on_zoom_debounce_timeout(self) -> None:
        """줌 디바운스 완료 — 설정 저장 + 지도 로드"""
        # 설정 저장은 여기서만
        self.config.set_gps_map_setting("default_zoom", self.current_zoom)
        self.config_save_timer.stop()
        self.config_save_timer.start(200) 
        self._load_map()            


    def _zoom_in(self) -> None:
        """확대"""
        if self.current_zoom < self.MAX_ZOOM:
            self._change_zoom(self.current_zoom + 1, "줌 인")


    def _zoom_out(self) -> None:
        """축소"""
        if self.current_zoom > self.MIN_ZOOM:
            self._change_zoom(self.current_zoom - 1, "줌 아웃")


    def _reset_zoom(self) -> None:
        """리셋"""
        self._change_zoom(self.DEFAULT_ZOOM, "줌 리셋")


    def _update_zoom_label(self) -> None:
        """줌 레벨 라벨 업데이트"""
        if hasattr(self, 'zoom_level_label'):
            self.zoom_level_label.setText(t('metadata_panel.zoom_label', zoom=self.current_zoom))


# ============================================
# 설정 관리
# ============================================

    def _on_auto_load_changed(self, state: int) -> None:
        auto_load = (state == Qt.CheckState.Checked.value)
        self.config.set_gps_map_setting("auto_load", auto_load)
        self.config.save()

        if auto_load and self.current_gps:
            self._load_map()
        elif not auto_load:
            self.stop_map_loader()

            try:
                if hasattr(self, 'map_label'):
                    self.map_label.setPixmap(QPixmap())
                    self.map_label.setText(t('metadata_panel.map_placeholder'))
            except RuntimeError:
                pass


    def _save_config_delayed(self) -> None:
        """설정 저장 (디바운싱 후)"""
        self.config.save()
        info_print(f"설정 저장됨 (디바운싱)")


    def hideEvent(self, event) -> None:
        self.stop_map_loader()
        super().hideEvent(event)


