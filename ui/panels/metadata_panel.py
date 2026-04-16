# -*- coding: utf-8 -*-
# ui\panels\metadata_panel.py

"""
메타데이터 패널 - EXIF 정보 표시
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from core.metadata_reader import MetadataReader
from core.map_loader import RasterTileMapLoader

from utils.config_manager import ConfigManager
from utils.debug import debug_print, info_print, warning_print
from utils.lang_manager import t

_ZOOM_DEBOUNCE_MS = 600
_MAP_MAX_RETRY    = 3
_MAP_RETRY_DELAYS = [400, 800, 1600]


# ================================================================
# ClickableLabel
# ================================================================

class ClickableLabel(QLabel):
    """클릭하면 copy_value 를 클립보드에 복사하는 라벨."""


    def __init__(self, display_text: str = "", copy_value: str = "", parent=None) -> None:
        super().__init__(display_text, parent)
        self._copy_value     = copy_value
        self._original_style = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self._restore_style)


    def setStyleSheet(self, style: str) -> None:
        self._original_style = style
        super().setStyleSheet(style)


    def set_copy_value(self, value: str) -> None:
        self._copy_value = value


    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._copy_value:
            QApplication.clipboard().setText(self._copy_value)
            self._show_feedback(event.globalPosition().toPoint())
        super().mousePressEvent(event)


    def _show_feedback(self, global_pos) -> None:
        feedback_style = re.sub(
            r"(color\s*:\s*)([^;\"'}\n]+)", r"\g<1>#4caf50",
            self._original_style, count=1,
        )
        super().setStyleSheet(feedback_style)
        QToolTip.showText(global_pos, t("metadata_panel.copied", default="복사됨"), self)
        self._feedback_timer.start(800)


    def _restore_style(self) -> None:
        super().setStyleSheet(self._original_style)


# ================================================================
# CollapsibleSection
# ================================================================

class CollapsibleSection(QWidget):
    """클릭으로 접기/펼치기 + 우측 인디케이터 토글 지원."""

    def __init__(self, title: str, icon: str = "",
                 design: Optional[Dict[str, str]] = None, parent=None) -> None:
        super().__init__(parent)
        D = design or {}
        self._design    = D
        self._collapsed = False
        self._indicator_btn: Optional[QPushButton] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(0)

        header_w = QWidget()
        header_w.setStyleSheet("background: transparent;")
        self._header_layout = QHBoxLayout(header_w)
        self._header_layout.setContentsMargins(0, 0, 0, 0)
        self._header_layout.setSpacing(0)

        parts = [p for p in ["▾", icon, title] if p]
        self.toggle_btn = QPushButton("  ".join(parts))
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet(f"""
            QPushButton {{
                text-align: left; padding: 5px 10px;
                background: rgba(74,158,255,0.07);
                color: {D.get('accent', '#4a9eff')};
                border: none;
                border-left: 3px solid {D.get('accent', '#4a9eff')};
                border-bottom: 1px solid rgba(74,158,255,0.12);
                border-radius: 0px; font-size: 11px; font-weight: bold;
            }}
            QPushButton:hover {{ background: rgba(74,158,255,0.13); }}
            """)
        self.toggle_btn.clicked.connect(self._toggle)
        self._header_layout.addWidget(self.toggle_btn, 1)
        outer.addWidget(header_w)

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("background: transparent;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(10, 6, 0, 8)
        self.content_layout.setSpacing(3)
        outer.addWidget(self.content_widget)


    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        self.content_widget.setVisible(not self._collapsed)
        text = self.toggle_btn.text()
        self.toggle_btn.setText(
            text.replace("▾", "▸", 1) if self._collapsed
            else text.replace("▸", "▾", 1)
        )


    def add_widget(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)


    def set_indicator(self, active: bool, callback) -> None:
        D = self._design
        self._indicator_btn = QPushButton("●")
        self._indicator_btn.setFixedSize(28, 28)
        self._indicator_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._indicator_btn.setToolTip(
            t('metadata_panel.indicator_on_tooltip') if active
            else t('metadata_panel.indicator_off_tooltip')
        )
        self._indicator_btn.clicked.connect(callback)
        self._apply_indicator_style(active)
        self._header_layout.addWidget(self._indicator_btn)


    def update_indicator(self, active: bool) -> None:
        if self._indicator_btn is None:
            return
        self._apply_indicator_style(active)
        self._indicator_btn.setToolTip(
            t('metadata_panel.indicator_on_tooltip') if active
            else t('metadata_panel.indicator_off_tooltip')
        )


    def _apply_indicator_style(self, active: bool) -> None:
        if self._indicator_btn is None:
            return        
        D     = self._design
        color = D.get('accent', '#4a9eff') if active else "rgba(255,255,255,0.20)"
        bg    = "rgba(74,158,255,0.15)"    if active else "transparent"
        self._indicator_btn.setStyleSheet(f"""
            QPushButton {{
                color: {color}; background: {bg};
                border: none; border-radius: 4px;
                font-size: 11px; padding: 0;
            }}
            QPushButton:hover {{
                background: rgba(74,158,255,0.20);
                color: {D.get('accent', '#4a9eff')};
            }}
            QPushButton:pressed {{ background: rgba(74,158,255,0.35); }}
            """)

# ================================================================
# MetadataPanel
# ================================================================

class MetadataPanel(QWidget):
    """메타데이터 패널"""

    PANEL_WIDTH:  int = 340
    MIN_ZOOM:     int = 1
    MAX_ZOOM:     int = 16
    DEFAULT_ZOOM: int = 15
    MAP_WIDTH:    int = 275
    MAP_HEIGHT:   int = 200

    _D: Dict[str, str] = {
        "bg":           "#202020",
        "bg_card":      "#1e1e1e",
        "border":       "rgba(255,255,255,0.08)",
        "btn_bg":       "rgba(255,255,255,0.05)",
        "btn_border":   "rgba(255,255,255,0.10)",
        "hover_bg":     "rgba(74,158,255,0.18)",
        "hover_border": "rgba(74,158,255,0.60)",
        "press_bg":     "rgba(74,158,255,0.32)",
        "accent":       "#4a9eff",
        "text":         "#cccccc",
        "text_key":     "#aaaaaa",
        "text_value":   "#ffffff",
        "text_dim":     "#888888",
        "radius":       "4px",
        "btn_h":        "26",
    }

    gps_clicked      = Signal(float, float)
    map_zoom_changed = Signal(int)

    # ============================================================
    # 초기화
    # ============================================================

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.config           = config
        self.metadata_reader  = MetadataReader()
        self.current_metadata: Dict[str, Any] = {}
        self.metadata_widgets: List[QWidget]  = []

        self.MIN_ZOOM     = 1
        self.MAX_ZOOM     = 16
        _saved            = config.get_gps_map_setting("default_zoom", 15)
        self.DEFAULT_ZOOM = max(1, min(int(_saved), 16))

        self.map_loader:        Optional[RasterTileMapLoader] = None
        self.current_gps:       Optional[tuple] = None
        self.current_zoom:      int  = self.DEFAULT_ZOOM
        self._map_retry_count:  int  = 0
        self._map_load_gen:     int  = 0
        self._gps_dms_mode:     bool = False
        self._gps_info_cache:   Dict[str, Any] = {}

        self._map_gps_section:  Optional[CollapsibleSection] = None
        self._map_btn_style:    str  = ""
        self._map_widget_ready: bool = False

        self._zoom_debounce_timer = QTimer(self)
        self._zoom_debounce_timer.setSingleShot(True)
        self._zoom_debounce_timer.timeout.connect(self._on_zoom_debounce_timeout)

        self.config_save_timer = QTimer(self)
        self.config_save_timer.setSingleShot(True)
        self.config_save_timer.timeout.connect(self._save_config_delayed)

        self._init_ui()
        self.setFixedWidth(self.PANEL_WIDTH)


    def _init_ui(self) -> None:
        D = self._D
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.metadata_container = QWidget()
        self.metadata_layout    = QVBoxLayout(self.metadata_container)
        self.metadata_layout.setContentsMargins(10, 10, 4, 10)
        self.metadata_layout.setSpacing(6)
        self.metadata_layout.addStretch()

        scroll_area.setWidget(self.metadata_container)
        scroll_area.setStyleSheet(f"""
            QScrollArea {{
                background-color: {D['bg']};
                border: none;
                border-top: 1px solid rgba(255,255,255,0.06);
            }}
            QScrollBar:vertical {{
                width: 6px; background: transparent; margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.18); border-radius: 3px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover  {{ background: rgba(255,255,255,0.30); }}
            QScrollBar::handle:vertical:pressed {{ background: rgba(74,158,255,0.60); }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px; border: none; background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
            """)
        layout.addWidget(scroll_area)

    # ============================================================
    # 메타데이터 로딩
    # ============================================================

    def load_metadata(self, file_path: Optional[Path]) -> Dict[str, Any]:
        self.stop_map_loader()
        self._clear_widgets()

        self._map_gps_section  = None
        self._map_widget_ready = False

        def _finalize_empty(msg: str = "") -> Dict[str, Any]:
            self._show_empty_state(msg)
            self.metadata_layout.addStretch()
            return {}

        if file_path is None:
            self.current_metadata = {}
            self.current_gps      = None
            return _finalize_empty()

        if not file_path.exists():
            warning_print(f"파일이 존재하지 않음: {file_path}")
            self.current_metadata = {}
            self.current_gps      = None
            return _finalize_empty(
                t("metadata_panel.file_not_found", default="파일을 찾을 수 없습니다")
            )

        debug_print(f"메타데이터 로딩 시작: {file_path.name}")
        metadata_result       = self.metadata_reader.read(file_path)
        self.current_metadata = metadata_result if metadata_result else {}

        if not self.current_metadata:
            warning_print(f"메타데이터 없음: {file_path.name}")
            return _finalize_empty(
                t("metadata_panel.no_metadata", default="메타데이터가 없습니다")
            )

        for key, label, icon in (
            ('file',   t('metadata_panel.section_file'),   ""),
            ('camera', t('metadata_panel.section_camera'), ""),
            ('exif',   t('metadata_panel.section_exif'),   ""),
        ):
            info = self.current_metadata.get(key, {})
            if info:
                self._add_section(label, info, icon=icon)

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
        return self.current_metadata

    # ============================================================
    # 빈 상태
    # ============================================================

    def _show_empty_state(self, message: str = "") -> None:
        D   = self._D
        msg = message or t("metadata_panel.empty_state", default="이미지를 선택하세요")
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 40, 0, 0)
        vbox.setSpacing(10)
        vbox.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        icon_lbl = QLabel("🖼️")
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet(
            "font-size: 36px; color: rgba(255,255,255,0.12); background: transparent;"
        )
        vbox.addWidget(icon_lbl)
        msg_lbl = QLabel(msg)
        msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(
            f"color: {D['text_dim']}; font-size: 11px; background: transparent;"
        )
        vbox.addWidget(msg_lbl)
        self.metadata_layout.addWidget(container)
        self.metadata_widgets.append(container)

    # ============================================================
    # 일반 섹션
    # ============================================================

    def _add_section(self, title: str, data: Dict[str, Any], icon: str = "") -> None:
        if not data:
            return
        D = self._D
        section = CollapsibleSection(title, icon=icon, design=D)

        for key, value in data.items():
            str_value   = str(value)
            display_key = t(f'metadata_labels.{key}', default=key)
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 1, 0, 1)
            row.setSpacing(6)

            key_lbl = ClickableLabel(f"{display_key}:",
                                     copy_value=f"{display_key}: {str_value}")
            key_lbl.setStyleSheet(
                f"color: {D['text_key']}; font-size: 10px; background: transparent;"
            )
            key_lbl.setFixedWidth(95)
            key_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            row.addWidget(key_lbl)

            display_value = str_value
            if key in ("datetime", "datetime_original", "datetime_digitized"):
                rel = self._relative_time(str_value)
                if rel:
                    display_value = f"{str_value}  ({rel})"

            val_lbl = ClickableLabel(display_value, copy_value=str_value)
            val_lbl.setStyleSheet(
                f"color: {D['text_value']}; font-size: 10px; background: transparent;"
            )
            val_lbl.setWordWrap(True)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            val_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            row.addWidget(val_lbl, 1)
            section.add_widget(row_w)

        self.metadata_layout.addWidget(section)
        self.metadata_widgets.append(section)


    @staticmethod
    def _relative_time(exif_date: str) -> str:
        try:
            dt   = datetime.strptime(exif_date[:19], "%Y:%m:%d %H:%M:%S")
            days = (datetime.now() - dt).days
            if days < 0:   return ""
            if days == 0:  return "오늘"
            if days < 7:   return f"{days}일 전"
            if days < 30:  return f"{days // 7}주 전"
            if days < 365: return f"{days // 30}개월 전"
            return f"{days // 365}년 전"
        except Exception:
            return ""

    # ============================================================
    # GPS 섹션
    # ============================================================

    def _add_gps_section(self, gps_info: Dict[str, Any]) -> None:
        D     = self._D
        BTN_H = int(D['btn_h'])

        _btn_style = f"""
QPushButton {{
    background: {D['btn_bg']}; color: {D['text']};
    border: 1px solid {D['btn_border']}; border-radius: {D['radius']};
    font-size: 13px; padding: 0;
}}
QPushButton:hover {{
    background: {D['hover_bg']}; border-color: {D['hover_border']}; color: #ffffff;
}}
QPushButton:pressed {{ background: {D['press_bg']}; }}
"""
        self._map_btn_style = _btn_style

        lat = gps_info.get('latitude',  0.0)
        lon = gps_info.get('longitude', 0.0)
        self.current_gps     = (lat, lon)
        self._gps_info_cache = gps_info

        gps_section = CollapsibleSection("GPS", icon="🌍", design=D)
        self._map_gps_section = gps_section

        # ── 좌표 버튼 + DMS 토글 ────────────────────────────────────
        coord_row_w = QWidget()
        coord_row_w.setStyleSheet("background: transparent;")
        coord_row = QHBoxLayout(coord_row_w)
        coord_row.setSpacing(4)
        coord_row.setContentsMargins(0, 0, 0, 0)

        self._gps_coord_btn = QPushButton()
        self._gps_coord_btn.setFixedHeight(BTN_H)
        self._gps_coord_btn.setStyleSheet(f"""
QPushButton {{
    text-align: left; padding: 0 8px;
    background: {D['btn_bg']}; color: {D['accent']};
    border: 1px solid {D['btn_border']}; border-radius: {D['radius']};
    font-size: 11px;
}}
QPushButton:hover {{
    background: {D['hover_bg']}; border-color: {D['hover_border']}; color: #ffffff;
}}
QPushButton:pressed {{ background: {D['press_bg']}; }}
""")
        self._gps_coord_btn.clicked.connect(lambda: self.gps_clicked.emit(lat, lon))
        self._update_coord_btn_text()
        coord_row.addWidget(self._gps_coord_btn, 1)

        dms_btn = QPushButton("DMS")
        dms_btn.setFixedSize(40, BTN_H)
        dms_btn.setToolTip(t('metadata_panel.dms_tooltip'))
        dms_btn.setCheckable(True)
        dms_btn.setStyleSheet(f"""
QPushButton {{
    background: {D['btn_bg']}; color: {D['text_dim']};
    border: 1px solid {D['btn_border']}; border-radius: {D['radius']};
    font-size: 9px; font-weight: bold; padding: 0;
}}
QPushButton:checked {{
    background: rgba(74,158,255,0.25); border-color: {D['accent']}; color: {D['accent']};
}}
QPushButton:hover {{ border-color: {D['hover_border']}; color: #ffffff; }}
""")
        dms_btn.toggled.connect(self._on_dms_toggled)
        coord_row.addWidget(dms_btn)
        gps_section.add_widget(coord_row_w)

        # ── 자동 로드 체크박스 ──────────────────────────────────────
        auto_load = self.config.get_gps_map_setting("auto_load", False)
        gps_section.set_indicator(auto_load, self._toggle_auto_load)

        # 줌 초기화
        _raw = self.config.get_gps_map_setting("default_zoom", self.DEFAULT_ZOOM)
        self.current_zoom = max(self.MIN_ZOOM, min(int(_raw), self.MAX_ZOOM))
        self.map_zoom_changed.emit(self.current_zoom)

        self.metadata_layout.addWidget(gps_section)
        self.metadata_widgets.append(gps_section)

        # auto_load ON → 즉시 지도 생성
        if auto_load:
            self._ensure_map_widget()
            self._load_map()

    # ============================================================
    # lazy 지도 위젯 생성 — setVisible 없이 insertWidget으로만 삽입
    # ============================================================

    def _ensure_map_widget(self) -> bool:
        """
        map_container가 없으면 GPS 섹션 Attribution 바로 앞에 삽입.
        부모 없는 위젯에 setVisible() 호출 없음 — insertWidget 으로 부모 설정과 동시에 배치.
        """
        if self._map_widget_ready:
            return True
        if self._map_gps_section is None:
            return False

        D     = self._D
        BTN_H = int(D['btn_h'])

        map_container = QWidget()
        map_container.setStyleSheet("background: transparent;")
        map_layout = QVBoxLayout(map_container)
        map_layout.setContentsMargins(0, 4, 0, 0)
        map_layout.setSpacing(4)

        self.map_label = QLabel(t('metadata_panel.map_placeholder'))
        self.map_label.setFixedSize(self.MAP_WIDTH, self.MAP_HEIGHT)
        self.map_label.setScaledContents(False)
        self.map_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_label.setStyleSheet(f"""
            QLabel {{
                background: {D['bg_card']}; color: {D['text_dim']};
                border: 1px solid {D['border']}; border-radius: {D['radius']};
                font-size: 11px;
            }}
            """)
        map_layout.addWidget(self.map_label)

        zoom_in_btn  = QPushButton("🔍+")
        zoom_out_btn = QPushButton("🔍-")
        reset_btn    = QPushButton("↺")
        for btn, tip, slot in (
            (zoom_in_btn,  t('metadata_panel.zoom_in_tooltip'),  self._zoom_in),
            (zoom_out_btn, t('metadata_panel.zoom_out_tooltip'), self._zoom_out),
            (reset_btn,    t('metadata_panel.reset_tooltip'),    self._reset_zoom),
        ):
            btn.setFixedHeight(BTN_H)
            btn.setToolTip(tip)
            btn.setStyleSheet(self._map_btn_style)
            btn.clicked.connect(slot)

        self.zoom_in_btn      = zoom_in_btn
        self.zoom_level_label = QLabel("")
        self.zoom_level_label.setFixedSize(72, BTN_H)
        self.zoom_level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_level_label.setStyleSheet(
            f"QLabel {{ color: {D['text_dim']}; font-size: 10px;"
            " background: transparent; border: none; padding: 0; }}"
        )

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(4)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addWidget(zoom_in_btn)
        controls_layout.addWidget(zoom_out_btn)
        controls_layout.addWidget(self.zoom_level_label)
        controls_layout.addWidget(reset_btn)
        map_layout.addLayout(controls_layout)

        attr_label = QLabel(
            '<a href="https://github.com/protomaps/basemaps">Protomaps</a>'
            ' · '
            '<a href="https://leafletjs.com">Leaflet</a>'
            ' · '
            '© <a href="https://openstreetmap.org">OpenStreetMap</a>'
        )
        attr_label.setTextFormat(Qt.TextFormat.RichText)
        attr_label.setOpenExternalLinks(True)
        attr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        attr_label.setWordWrap(True)
        attr_label.setStyleSheet("""
        QLabel {
            color: rgba(255,255,255,0.20);
            font-size: 9px;
            padding: 3px 0 1px 0;
            background: transparent;
        }
        QLabel a {
            color: rgba(74,158,255,0.50);
            text-decoration: none;
        }
        QLabel a:hover {
            color: #4a9eff;
            text-decoration: underline;
        }
        """)
        map_layout.addWidget(attr_label)

        self._update_zoom_label()

        # Attribution 바로 앞에 삽입 (부모 없는 상태에서 show/setVisible 없이)
        cl    = self._map_gps_section.content_layout
        count = cl.count()
        cl.addWidget(map_container)

        self._map_widget_ready = True
        return True

    # ============================================================
    # GPS 좌표 포맷
    # ============================================================

    def _on_dms_toggled(self, checked: bool) -> None:
        self._gps_dms_mode = checked
        self._update_coord_btn_text()


    def _update_coord_btn_text(self) -> None:
        if not hasattr(self, '_gps_coord_btn') or not self.current_gps:
            return
        lat, lon = self.current_gps
        gps_info = self._gps_info_cache
        if self._gps_dms_mode:
            coord_text = f"📍 {self._format_dms(lat, lon)}"
        else:
            coord_text = f"📍 {gps_info.get('display', f'{lat:.6f}, {lon:.6f}')}"
        if 'altitude' in gps_info:
            coord_text += f"  ⛰ {gps_info['altitude']}"
        self._gps_coord_btn.setText(coord_text)


    @staticmethod
    def _format_dms(lat: float, lon: float) -> str:
        def to_dms(d: float) -> str:
            deg = int(abs(d)); m = int((abs(d)-deg)*60)
            s = ((abs(d)-deg)*60-m)*60
            return f"{deg}°{m}'{s:.1f}\""
        return (f"{to_dms(lat)}{'N' if lat>=0 else 'S'}, "
                f"{to_dms(lon)}{'E' if lon>=0 else 'W'}")

    # ============================================================
    # 위젯 정리 — hide() 후 deleteLater()
    # ============================================================

    def _clear_widgets(self) -> None:
        self.metadata_widgets.clear()
        while self.metadata_layout.count() > 0:
            item = self.metadata_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
            else:
                layout = item.layout()
                if layout is not None:
                    self._clear_layout(layout)


    def _clear_layout(self, layout) -> None:
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
                else:
                    nested = item.layout()
                    if nested is not None:
                        self._clear_layout(nested)

    # ============================================================
    # 지도 관리
    # ============================================================

    def _toggle_auto_load(self) -> None:
        current   = self.config.get_gps_map_setting("auto_load", False)
        new_state = not current
        self._on_auto_load_changed(
            Qt.CheckState.Checked.value if new_state
            else Qt.CheckState.Unchecked.value
        )
        if self._map_gps_section is not None:
            self._map_gps_section.update_indicator(new_state)
            

    def stop_map_loader(self) -> None:
        if self.map_loader is None:
            return
        loader          = self.map_loader
        self.map_loader = None
        try:    loader.map_loaded.disconnect()
        except RuntimeError: pass
        try:    loader.load_failed.disconnect()
        except RuntimeError: pass
        loader.cancel()
        loader.deleteLater()
        debug_print("[RasterTiles] MetadataPanel 맵 로더 취소 완료")


    def _apply_attribution_overlay(self, img: QImage) -> QPixmap:
        return QPixmap.fromImage(img)


    def _load_map(self) -> None:
        if not self.current_gps:
            return
        
        if not hasattr(self, 'map_label'):
            return

        self._map_load_gen   += 1
        self._map_retry_count = 0
        lat, lon = self.current_gps

        pix = RasterTileMapLoader.get_cached_pixmap(
            lat, lon, self.current_zoom, self.MAP_WIDTH, self.MAP_HEIGHT
        )
        if pix is not None:
            self.stop_map_loader()
            try:
                self.map_label.setScaledContents(False)
                self.map_label.setPixmap(self._apply_attribution_overlay(pix.toImage()))
                self.map_label.show()
                debug_print(f"[RasterTiles] 패널 캐시 HIT: z={self.current_zoom}")
            except RuntimeError:
                pass
            return

        self.stop_map_loader()
        try:
            self.map_label.setPixmap(QPixmap())
            self.map_label.setText(
                t("metadata_panel.map_loading_zoom", zoom=self.current_zoom)
            )
        except RuntimeError:
            return

        gen = self._map_load_gen
        self.map_loader = RasterTileMapLoader(
            lat, lon, zoom=self.current_zoom,
            width=self.MAP_WIDTH, height=self.MAP_HEIGHT,
        )
        self.map_loader.map_loaded.connect(
            lambda img, g=gen: self._on_map_loaded(img, g),
            Qt.ConnectionType.QueuedConnection,
        )
        self.map_loader.load_failed.connect(
            lambda err, g=gen: self._on_map_failed(err, g),
            Qt.ConnectionType.QueuedConnection,
        )
        self.map_loader.start()


    def clear_map(self) -> None:
        debug_print("clear_map() 호출")
        self.stop_map_loader()
        try:
            if hasattr(self, 'map_label') and self.map_label:
                self.map_label.clear()
                self.map_label.hide()
        except RuntimeError as e:
            warning_print(f"map_label 이미 삭제됨: {e}")
        self.current_gps = None


    def _on_map_loaded(self, q_image: QImage, gen: int) -> None:
        if gen != self._map_load_gen:
            debug_print(f"[MetadataPanel] stale map 콜백 무시 (gen={gen})")
            return
        loader          = self.map_loader
        self.map_loader = None
        if loader:
            try:
                loader.map_loaded.disconnect()
                loader.load_failed.disconnect()
            except RuntimeError: pass
            loader.cancel()
            loader.deleteLater()
        if q_image is None or q_image.isNull():
            return
        try:
            if not hasattr(self, 'map_label') or self.map_label is None:
                return
            self.map_label.setPixmap(self._apply_attribution_overlay(q_image))
            self.map_label.show()
        except RuntimeError as e:
            warning_print(f"[MetadataPanel] map_label 접근 오류: {e}")


    def _on_map_failed(self, error: str, gen: int) -> None:
        if gen != self._map_load_gen:
            return
        if self.map_loader is None:
            return
        loader          = self.map_loader
        self.map_loader = None
        try:    loader.map_loaded.disconnect()
        except RuntimeError: pass
        try:    loader.load_failed.disconnect()
        except RuntimeError: pass
        loader.cancel()
        loader.deleteLater()

        is_fetch = "Failed to fetch" in error or "failed to fetch" in error.lower()
        if is_fetch and self._map_retry_count < _MAP_MAX_RETRY:
            delay = _MAP_RETRY_DELAYS[self._map_retry_count]
            self._map_retry_count += 1
            debug_print(f"[RasterTiles] 재시도 ({self._map_retry_count}/{_MAP_MAX_RETRY}), {delay}ms")
            QTimer.singleShot(delay, self._load_map)
            return

        self._map_retry_count = 0
        try:
            self.map_label.setPixmap(QPixmap())
            self.map_label.setText(f"❌ {error}")
        except RuntimeError as e:
            warning_print(f"map_label 이미 삭제됨 (_on_map_failed): {e}")


    def refresh_map(self) -> None:
        if not self.current_gps:
            debug_print("refresh_map: GPS 정보 없음 — 스킵")
            return
        # map_label이 아직 생성되지 않은 경우 먼저 위젯 생성
        if not self._map_widget_ready:
            if not self._ensure_map_widget():
                debug_print("refresh_map: 지도 위젯 미생성 — 스킵")
                return
        debug_print(f"refresh_map: 지도 재요청 z={self.current_zoom}")
        self._load_map()

    # ============================================================
    # 줌 컨트롤
    # ============================================================

    def _change_zoom(self, new_zoom: int, log_message: str = "") -> None:
        self.current_zoom = new_zoom
        self._update_zoom_label()
        self.map_zoom_changed.emit(self.current_zoom)
        if log_message:
            info_print(f"{log_message}: {self.current_zoom}")
        self._zoom_debounce_timer.stop()
        self._zoom_debounce_timer.start(_ZOOM_DEBOUNCE_MS)


    def _on_zoom_debounce_timeout(self) -> None:
        self.config.set_gps_map_setting("default_zoom", self.current_zoom)
        self.config_save_timer.stop()
        self.config_save_timer.start(200)
        self._load_map()


    def _zoom_in(self) -> None:
        if self.current_zoom < self.MAX_ZOOM:
            self._change_zoom(self.current_zoom + 1, "줌 인")


    def _zoom_out(self) -> None:
        if self.current_zoom > self.MIN_ZOOM:
            self._change_zoom(self.current_zoom - 1, "줌 아웃")


    def _reset_zoom(self) -> None:
        self._change_zoom(self.MIN_ZOOM, "줌 리셋")


    def _update_zoom_label(self) -> None:
        if not hasattr(self, 'zoom_level_label'):
            return
        zoom   = self.current_zoom
        at_max = (zoom >= self._effective_max_zoom)
        if at_max:
            self.zoom_level_label.setText(t('metadata_panel.zoom_max_label', zoom=zoom))
            self.zoom_level_label.setStyleSheet(
                "QLabel { color: #ff9800; font-size: 9px; font-weight: bold;"
                " background: transparent; border: none; padding: 0; }"
            )
        else:
            self.zoom_level_label.setText(t('metadata_panel.zoom_label', zoom=zoom))
            self.zoom_level_label.setStyleSheet(
                "QLabel { color: #888888; font-size: 10px;"
                " background: transparent; border: none; padding: 0; }"
            )
        if hasattr(self, 'zoom_in_btn'):
            self.zoom_in_btn.setEnabled(not at_max)


    @property
    def _effective_max_zoom(self) -> int:
        return self.MAX_ZOOM


    def refresh_max_zoom(self) -> None:
        eff_max = self._effective_max_zoom
        if self.current_zoom > eff_max:
            self._change_zoom(eff_max)
        else:
            self._update_zoom_label()

    # ============================================================
    # 설정 관리
    # ============================================================

    def _on_auto_load_changed(self, state: int) -> None:
        auto_load = (state == Qt.CheckState.Checked.value)
        self.config.set_gps_map_setting("auto_load", auto_load)
        self.config.save()

        if auto_load and self.current_gps:
            self._ensure_map_widget()
            self._load_map()
        elif not auto_load:
            self.stop_map_loader()
            if self._map_widget_ready:
                try:
                    self.map_label.setPixmap(QPixmap())
                    self.map_label.setText(t('metadata_panel.map_placeholder'))
                except RuntimeError:
                    pass


    def _save_config_delayed(self) -> None:
        self.config.save()
        info_print("설정 저장됨 (디바운싱)")


    def hideEvent(self, event) -> None:
        self.stop_map_loader()
        super().hideEvent(event)

