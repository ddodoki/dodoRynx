# -*- coding: utf-8 -*-
# ui/watermark_panel.py

from __future__ import annotations

import re
from typing import Dict, List, Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup, QColorDialog, QFontComboBox, QFrame,
    QHBoxLayout, QLabel, QPushButton, QRadioButton,
    QScrollArea, QSlider, QSpinBox, QTextEdit,
    QVBoxLayout, QWidget,
)
from utils.lang_manager import t
from utils.watermark_utils import flatten_watermark_metadata, resolve_template


PANEL_W = 314

_TOKEN_ORDER: List[str] = [
    "filename", "resolution", "size", "format", "modified",
    "make", "model", "lens_model", "date_taken", "focal_length",
    "f_stop", "exposure_time", "iso", "flash", "software",
    "white_balance", "metering_mode", "exposure_program",
    "gps_display", "gps_altitude",
]

_SS = """
QWidget#wm_panel {
    background: rgba(18,18,18,240);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
}
QWidget#wm_header {
    background: rgba(26,26,26,255);
    border-bottom: 1px solid rgba(255,255,255,0.07);
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}
QLabel#title_lbl {
    color: #6e6e6e;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1.3px;
    background: transparent;
}
QLabel#sec_lbl {
    color: #5a5a5a;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.8px;
    background: transparent;
    padding-top: 1px;
}
QLabel {
    color: #b8b8b8;
    font-size: 10px;
    background: transparent;
}
QLabel#hint_lbl  { color: #5e5e5e; font-size: 9px; }
QLabel#preview_lbl {
    color: #e0e0e0;
    font-size: 10px;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 6px;
    padding: 6px 9px;
    min-height: 28px;
}

/* ── 일반 버튼 ── */
QPushButton {
    background: #252525;
    color: #c0c0c0;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 10px;
}
QPushButton:hover  { background: #333; color: #fff; border-color: #555; }
QPushButton:checked { background: #4a9eff; color: #fff; border-color: #4a9eff; }
QPushButton#close_btn {
    background: transparent; color: #5e5e5e;
    border: none; font-size: 13px; padding: 0;
    min-width: 18px; max-width: 18px; min-height: 18px;
}
QPushButton#close_btn:hover { color: #e35b5b; background: transparent; }

/* ── 칩 버튼 ── */
QPushButton#chip_btn {
    text-align: left; padding: 3px 8px; border-radius: 10px;
    background: rgba(74,158,255,0.10);
    border: 1px solid rgba(74,158,255,0.22);
    color: #aecdff; font-size: 10px;
}
QPushButton#chip_btn:hover {
    background: rgba(74,158,255,0.22);
    border-color: rgba(74,158,255,0.50); color: #fff;
}

/* ── 앵커 버튼 ── */
QPushButton#anchor_btn {
    min-width: 28px; max-width: 28px;
    min-height: 22px; max-height: 22px;
    padding: 0; font-size: 11px;
}

/* ── 색상 버튼 ── */
QPushButton#col_btn {
    min-width: 22px; max-width: 22px;
    min-height: 22px; max-height: 22px;
    padding: 0; border-radius: 4px;
}
QPushButton#col_btn:hover { border: 1px solid rgba(255,255,255,0.50); }

/* ── 적용 버튼: 기본을 어두운 파랑으로 → 호버가 확연히 밝아짐 ── */
QPushButton#apply_btn {
    background: #1e538a;
    color: #d0e8ff;
    border: 1px solid #2a6dc9;
    border-radius: 5px;
    min-height: 33px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.4px;
}
QPushButton#apply_btn:hover {
    background: #4a9eff;
    color: #ffffff;
    border-color: #6ab5ff;
}
QPushButton#apply_btn:pressed {
    background: #134772;
    border-color: #1e538a;
}
QPushButton#apply_btn:disabled {
    background: #1a1a1a; color: #484848;
    border: 1px solid #2e2e2e;
}

/* ── 텍스트 편집 ── */
QTextEdit {
    background: #141414; color: #eeeeee;
    border: 1px solid #303030;
    border-radius: 6px; padding: 6px; font-size: 11px;
}
QTextEdit:focus { border: 1px solid rgba(74,158,255,0.55); }

/* ── 스핀박스: 위 버튼 클릭 영역 명시 ── */
QSpinBox, QFontComboBox {
    background: #161616; color: #d8d8d8;
    border: 1px solid #303030;
    border-radius: 4px;
    padding: 2px 20px 2px 6px;
    font-size: 10px;
    min-width: 70px;
}
QSpinBox:focus, QFontComboBox:focus { border-color: rgba(74,158,255,0.50); }
QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid #303030;
    border-bottom: 1px solid #222;
    border-top-right-radius: 4px;
    background: #1e1e1e;
}
QSpinBox::up-button:hover   { background: #2d2d2d; }
QSpinBox::up-button:pressed { background: rgba(74,158,255,0.25); }
QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    border-left: 1px solid #303030;
    border-bottom-right-radius: 4px;
    background: #1e1e1e;
}
QSpinBox::down-button:hover   { background: #2d2d2d; }
QSpinBox::down-button:pressed { background: rgba(74,158,255,0.25); }
QSpinBox::up-arrow {
    width: 0; height: 0;
    border-left:  4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid #909090;
}
QSpinBox::up-arrow:disabled  { border-bottom-color: #404040; }
QSpinBox::down-arrow {
    width: 0; height: 0;
    border-left:  4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #909090;
}
QSpinBox::down-arrow:disabled { border-top-color: #404040; }

/* ── 라디오 버튼 (다크 테마) ── */
QRadioButton {
    color: #b8b8b8; font-size: 10px; spacing: 5px;
    background: transparent; padding: 2px 4px;
}
QRadioButton::indicator {
    width: 13px; height: 13px;
    border-radius: 7px;
    border: 2px solid #4a4a4a;
    background: #1a1a1a;
}
QRadioButton::indicator:hover  { border-color: #4a9eff; }
QRadioButton::indicator:checked {
    background: #4a9eff;
    border-color: #4a9eff;
    image: none;
}
QRadioButton::indicator:checked:hover { background: #5ab0ff; border-color: #5ab0ff; }

/* ── 슬라이더 ── */
QSlider::groove:horizontal {
    background: #2a2a2a; height: 4px; border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #efefef; width: 12px; height: 12px;
    margin: -4px 0; border-radius: 6px; border: 1px solid #777;
}
QSlider::sub-page:horizontal { background: #4a9eff; border-radius: 2px; }

/* ── 스크롤 ── */
/* Scroll Area */
QScrollArea {
    border: none;
    background: transparent;
}

/* ---------------------------
   Vertical Scrollbar
----------------------------*/
QScrollBar:vertical {
    background: rgba(255,255,255,0.05);
    width: 9px;
    border-radius: 4px;
    margin: 3px 1px;
}

QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.30);
    border-radius: 4px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(74,158,255,0.60);
}

QScrollBar::handle:vertical:pressed {
    background: rgba(74,158,255,0.85);
}

/* remove arrow buttons */
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}

/* make track rounded */
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: none;
}

/* ---------------------------
   Horizontal Scrollbar
----------------------------*/
QScrollBar:horizontal {
    background: rgba(255,255,255,0.05);
    height: 9px;
    border-radius: 4px;
    margin: 1px 3px;
}

QScrollBar::handle:horizontal {
    background: rgba(255,255,255,0.30);
    border-radius: 4px;
    min-width: 28px;
}

QScrollBar::handle:horizontal:hover {
    background: rgba(74,158,255,0.60);
}

QScrollBar::handle:horizontal:pressed {
    background: rgba(74,158,255,0.85);
}

/* remove arrow buttons */
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    width: 0;
}

/* make track rounded */
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {
    background: none;
}
"""

def _hsep() -> QFrame:
    f = QFrame(); f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet("background: rgba(255,255,255,0.07);")
    return f


def _vsep() -> QFrame:
    f = QFrame(); f.setFrameShape(QFrame.Shape.VLine)
    f.setFixedWidth(1)
    f.setStyleSheet("background: rgba(255,255,255,0.07);")
    return f


def _set_color_btn(btn: QPushButton, color: QColor) -> None:
    btn.setStyleSheet(
        f"QPushButton#col_btn {{ background:{color.name()};"
        "border:1px solid rgba(255,255,255,0.18); border-radius:4px; }}"
    )


class _DragHeader(QWidget):

    def __init__(self, panel: "WatermarkPanel") -> None:
        super().__init__(panel)
        self.setObjectName("wm_header")
        self.setFixedHeight(28)
        self._panel = panel
        self._drag_offset: Optional[QPoint] = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(6)

        icon = QLabel("◈")
        icon.setStyleSheet("color:#4a9eff; font-size:10px; background:transparent;")
        title = QLabel(t('watermark_panel.header_title'))
        title.setObjectName("title_lbl")
        close = QPushButton("✕"); close.setObjectName("close_btn")
        close.clicked.connect(panel.close_panel)

        lay.addWidget(icon); lay.addWidget(title); lay.addStretch(); lay.addWidget(close)
        self.setCursor(Qt.CursorShape.SizeAllCursor)


    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = self._panel.mapFromGlobal(event.globalPosition().toPoint())
            event.accept(); return
        super().mousePressEvent(event)


    def mouseMoveEvent(self, event) -> None:
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_offset:
            p = self._panel.parent()
            if p is None: return
            loc = p.mapFromGlobal(event.globalPosition().toPoint())  # type: ignore[attr-defined]
            nx = max(0, min(loc.x() - self._drag_offset.x(), p.width()  - self._panel.width()))   # type: ignore[attr-defined]
            ny = max(0, min(loc.y() - self._drag_offset.y(), p.height() - self._panel.height()))  # type: ignore[attr-defined]
            self._panel.move(nx, ny)
            self._panel.drag_moved.emit()
            event.accept(); return
        super().mouseMoveEvent(event)


    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class WatermarkPanel(QWidget):
    
    config_changed  = Signal(dict)
    apply_requested = Signal(dict)
    panel_closed    = Signal()
    drag_moved      = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("wm_panel")
        self.setStyleSheet(_SS)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(PANEL_W)

        self._metadata_flat: Dict[str, str] = {}
        self._text_color = QColor(255, 255, 255)
        self._band_color = QColor(0, 0, 0)
        self._alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft
        self._anchor: str = "br"

        self._build_ui()

    # ──────────────────────────────── UI BUILD ────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(_DragHeader(self))

        body = QWidget(); body.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(9, 8, 9, 9)
        lay.setSpacing(6)

        # ── TEMPLATE ─────────────────────────────────────────────────
        lay.addWidget(self._sec(t('watermark_panel.sec_template')))

        self.template_edit = QTextEdit()
        self.template_edit.setPlaceholderText(t('watermark_panel.template_placeholder'))
        self.template_edit.setFixedHeight(86)
        self.template_edit.textChanged.connect(self._on_template_changed)
        lay.addWidget(self.template_edit)

        hint = QLabel(t('watermark_panel.template_hint'))
        hint.setObjectName("hint_lbl")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # ── INSERT TOKENS ────────────────────────────────────────────
        lay.addWidget(self._sec(t('watermark_panel.sec_tokens')))

        self.token_scroll = QScrollArea()
        self.token_scroll.setWidgetResizable(True)
        self.token_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.token_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.token_scroll.setFixedHeight(92)

        self.token_container = QWidget()
        self.token_container.setStyleSheet("background:transparent;")
        self.token_layout = QVBoxLayout(self.token_container)
        self.token_layout.setContentsMargins(0, 2, 0, 4)
        self.token_layout.setSpacing(4)
        self.token_scroll.setWidget(self.token_container)
        lay.addWidget(self.token_scroll)

        # ── RESOLVED PREVIEW ─────────────────────────────────────────
        lay.addWidget(self._sec(t('watermark_panel.sec_preview')))

        self.preview_label = QLabel("—")
        self.preview_label.setObjectName("preview_lbl")
        self.preview_label.setWordWrap(True)
        lay.addWidget(self.preview_label)

        lay.addWidget(_hsep())

        # ── STYLE ────────────────────────────────────────────────────
        lay.addWidget(self._sec(t('watermark_panel.sec_style')))

        # 1행: 폰트 선택
        self.font_cb = QFontComboBox()
        self.font_cb.setCurrentFont(QFont("맑은 고딕"))
        self.font_cb.currentFontChanged.connect(lambda _: self._emit_config())
        lay.addWidget(self.font_cb)

        # 2행: [서식] 
        row_fmt = QHBoxLayout(); row_fmt.setSpacing(4)

        fmt_lbl = QLabel(t('watermark_panel.fmt_label'))
        fmt_lbl.setObjectName("sec_lbl")

        self.bold_btn = QPushButton("B")
        self.bold_btn.setCheckable(True); self.bold_btn.setFixedSize(26, 22)
        self.bold_btn.setStyleSheet(
            "QPushButton{font-weight:700;}"
            "QPushButton:checked{background:#4a9eff;color:#fff;border-color:#4a9eff;}"
        )
        self.bold_btn.clicked.connect(self._emit_config)

        self.italic_btn = QPushButton("I")
        self.italic_btn.setCheckable(True); self.italic_btn.setFixedSize(26, 22)
        self.italic_btn.setStyleSheet(
            "QPushButton{font-style:italic;}"
            "QPushButton:checked{background:#4a9eff;color:#fff;border-color:#4a9eff;}"
        )
        self.italic_btn.clicked.connect(self._emit_config)

        self.text_col_btn = QPushButton()
        self.text_col_btn.setObjectName("col_btn"); self.text_col_btn.setFixedSize(22, 22)
        _set_color_btn(self.text_col_btn, self._text_color)
        self.text_col_btn.clicked.connect(self._pick_text_color)

        align_lbl = QLabel(t('watermark_panel.align_label'))
        align_lbl.setObjectName("sec_lbl")

        align_group = QButtonGroup(self); align_group.setExclusive(True)
        self.align_left_btn   = QPushButton("L")
        self.align_center_btn = QPushButton("C")
        self.align_right_btn  = QPushButton("R")
        for btn, al in (
            (self.align_left_btn,   Qt.AlignmentFlag.AlignLeft),
            (self.align_center_btn, Qt.AlignmentFlag.AlignHCenter),
            (self.align_right_btn,  Qt.AlignmentFlag.AlignRight),
        ):
            btn.setCheckable(True); btn.setFixedSize(26, 22)
            btn.setProperty("_align", al)
            btn.clicked.connect(self._on_align_clicked)
            align_group.addButton(btn)
        self.align_left_btn.setChecked(True)

        row_fmt.addWidget(fmt_lbl)
        row_fmt.addWidget(self.bold_btn)
        row_fmt.addWidget(self.italic_btn)
        row_fmt.addWidget(self.text_col_btn)
        row_fmt.addStretch(1)
        row_fmt.addWidget(align_lbl)
        row_fmt.addWidget(self.align_left_btn)
        row_fmt.addWidget(self.align_center_btn)
        row_fmt.addWidget(self.align_right_btn)
        lay.addLayout(row_fmt)

        # 3행: 폰트 사이즈 + 줄간격
        row_size = QHBoxLayout(); row_size.setSpacing(6)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 2000); self.font_size_spin.setValue(80)
        self.font_size_spin.setSuffix(" px"); self.font_size_spin.setMinimumWidth(80)
        self.font_size_spin.valueChanged.connect(self._emit_config)

        self.line_spacing_spin = QSpinBox()
        self.line_spacing_spin.setRange(100, 300); self.line_spacing_spin.setValue(150)
        self.line_spacing_spin.setSuffix(" %"); self.line_spacing_spin.setMinimumWidth(80)
        self.line_spacing_spin.valueChanged.connect(self._emit_config)

        row_size.addWidget(QLabel(t('watermark_panel.size_label')))
        row_size.addWidget(self.font_size_spin)
        row_size.addStretch(1)
        row_size.addWidget(QLabel(t('watermark_panel.line_spacing_label')))
        row_size.addWidget(self.line_spacing_spin)
        lay.addLayout(row_size)

        lay.addWidget(_hsep())

        # ── POSITION ─────────────────────────────────────────────────
        lay.addWidget(self._sec(t('watermark_panel.sec_position')))

        pos_row = QHBoxLayout(); pos_row.setSpacing(14); pos_row.setContentsMargins(0,0,0,0)

        # 앵커 3×3
        anchor_col = QVBoxLayout(); anchor_col.setSpacing(2)
        anchor_group = QButtonGroup(self); anchor_group.setExclusive(True)
        self._anchor_btns: Dict[str, QPushButton] = {}
        for row_data in [
            [("tl","↖"),("tc","↑"),("tr","↗")],
            [("ml","←"),("mc","·"),("mr","→")],
            [("bl","↙"),("bc","↓"),("br","↘")],
        ]:
            rw = QHBoxLayout(); rw.setSpacing(2)
            for key, icon in row_data:
                btn = QPushButton(icon); btn.setObjectName("anchor_btn")
                btn.setCheckable(True); btn.setProperty("_anchor", key)
                btn.clicked.connect(self._on_anchor_clicked)
                anchor_group.addButton(btn)
                self._anchor_btns[key] = btn; rw.addWidget(btn)
            anchor_col.addLayout(rw)
        self._anchor_btns["br"].setChecked(True)

        # 여백 (앵커 오른쪽)
        margin_col = QVBoxLayout(); margin_col.setSpacing(4)
        margin_col.addStretch()
        ml = QLabel(t('watermark_panel.margin_label'))
        ml.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.margin_spin = QSpinBox()
        self.margin_spin.setRange(0, 500); self.margin_spin.setValue(20)
        self.margin_spin.setSuffix(" px"); self.margin_spin.setMinimumWidth(80)
        self.margin_spin.valueChanged.connect(self._emit_config)
        margin_col.addWidget(ml); margin_col.addWidget(self.margin_spin)
        margin_col.addStretch()

        pos_row.addLayout(anchor_col)
        pos_row.addLayout(margin_col)
        pos_row.addStretch()
        lay.addLayout(pos_row)

        lay.addWidget(_hsep())

        # ── BAND ─────────────────────────────────────────────────────
        lay.addWidget(self._sec(t('watermark_panel.sec_band')))

        self.band_enable_btn = QPushButton(t('watermark_panel.band_enable'))
        self.band_enable_btn.setCheckable(True)
        self.band_enable_btn.clicked.connect(self._on_band_enable_clicked)
        lay.addWidget(self.band_enable_btn)

        # 밴드 옵션은 항상 표시 (토글 상태만 build_config에 반영)
        band_box = QWidget(); band_box.setStyleSheet("background:transparent;")
        bl = QVBoxLayout(band_box); bl.setContentsMargins(0, 5, 0, 0); bl.setSpacing(7)

        # 모드 행 ─ QButtonGroup 으로 자동배타 충돌 방지
        mode_row = QHBoxLayout(); mode_row.setSpacing(0)
        self.band_inside_radio  = QRadioButton(t('watermark_panel.band_inside'))
        self.band_outside_radio = QRadioButton(t('watermark_panel.band_outside'))
        self.band_inside_radio.setChecked(True)
        # autoExclusive 끄고 QButtonGroup 으로 관리
        self.band_inside_radio.setAutoExclusive(False)
        self.band_outside_radio.setAutoExclusive(False)
        self._band_mode_group = QButtonGroup(self)
        self._band_mode_group.setExclusive(True)
        self._band_mode_group.addButton(self.band_inside_radio)
        self._band_mode_group.addButton(self.band_outside_radio)
        self.band_inside_radio.toggled.connect(self._emit_config)
        mode_row.addWidget(self.band_inside_radio)
        mode_row.addWidget(self.band_outside_radio)
        mode_row.addStretch()
        bl.addLayout(mode_row)

        # 색상 + 불투명도
        color_row = QHBoxLayout(); color_row.setSpacing(6)
        self.band_col_btn = QPushButton(); self.band_col_btn.setObjectName("col_btn")
        self.band_col_btn.setFixedSize(22, 22)
        _set_color_btn(self.band_col_btn, self._band_color)
        self.band_col_btn.clicked.connect(self._pick_band_color)
        self.band_alpha_sl = QSlider(Qt.Orientation.Horizontal)
        self.band_alpha_sl.setRange(0, 100); self.band_alpha_sl.setValue(60)
        self.band_alpha_lbl = QLabel("60%")
        self.band_alpha_lbl.setFixedWidth(30)
        self.band_alpha_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.band_alpha_sl.valueChanged.connect(self._on_band_alpha_changed)
        color_row.addWidget(QLabel(t('watermark_panel.band_color_label')))
        color_row.addWidget(self.band_col_btn)
        color_row.addWidget(QLabel(t('watermark_panel.band_opacity_label')))
        color_row.addWidget(self.band_alpha_sl, 1)
        color_row.addWidget(self.band_alpha_lbl)
        bl.addLayout(color_row)

        # 높이 행 ─ 별도 QButtonGroup 으로 모드 그룹과 충돌 방지
        height_row = QHBoxLayout(); height_row.setSpacing(6)
        self.band_auto_radio   = QRadioButton(t('watermark_panel.band_height_auto'))
        self.band_manual_radio = QRadioButton(t('watermark_panel.band_height_manual'))
        self.band_auto_radio.setChecked(True)
        self.band_auto_radio.setAutoExclusive(False)
        self.band_manual_radio.setAutoExclusive(False)
        self._band_height_group = QButtonGroup(self)
        self._band_height_group.setExclusive(True)
        self._band_height_group.addButton(self.band_auto_radio)
        self._band_height_group.addButton(self.band_manual_radio)
        self.band_height_spin = QSpinBox()
        self.band_height_spin.setRange(20, 3000); self.band_height_spin.setValue(120)
        self.band_height_spin.setSuffix(" px"); self.band_height_spin.setMinimumWidth(80)
        self.band_height_spin.setEnabled(False)
        self.band_auto_radio.toggled.connect(self._on_band_height_mode_changed)
        self.band_manual_radio.toggled.connect(self._emit_config)
        self.band_height_spin.valueChanged.connect(self._emit_config)
        height_row.addWidget(QLabel(t('watermark_panel.band_height_label')))
        height_row.addWidget(self.band_auto_radio)
        height_row.addWidget(self.band_manual_radio)
        height_row.addWidget(self.band_height_spin)
        height_row.addStretch()
        bl.addLayout(height_row)

        lay.addWidget(band_box)
        lay.addWidget(_hsep())

        # ── APPLY ────────────────────────────────────────────────────
        self.apply_btn = QPushButton(t('watermark_panel.apply_btn'))
        self.apply_btn.setObjectName("apply_btn")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        lay.addWidget(self.apply_btn)

        root.addWidget(body)

    # ──────────────────────────────── Public ──────────────────────────

    def load_metadata(self, metadata: dict) -> None:
        self._metadata_flat = self._flatten_metadata(metadata)
        self._rebuild_token_buttons()

        if not self.template_edit.toPlainText().strip():
            tpl = self._build_default_template()
            if tpl:
                self.template_edit.blockSignals(True)
                self.template_edit.setPlainText(tpl)
                self.template_edit.blockSignals(False)

        self._update_preview_label()
        self._refresh_apply_enabled()
        self._emit_config() 


    def build_config(self) -> dict:
        return {
            "template_text":    self.template_edit.toPlainText(),
            "font_family":      self.font_cb.currentFont().family(),
            "font_size":        self.font_size_spin.value(),
            "bold":             self.bold_btn.isChecked(),
            "italic":           self.italic_btn.isChecked(),
            "text_color":       QColor(self._text_color),
            "alignment":        self._alignment,
            "line_spacing":     self.line_spacing_spin.value() / 100.0,
            "anchor":           self._anchor,
            "margin":           self.margin_spin.value(),
            "band_enabled":     self.band_enable_btn.isChecked(),
            "band_mode":        "outside" if self.band_outside_radio.isChecked() else "inside",
            "band_color":       QColor(self._band_color),
            "band_alpha":       int(self.band_alpha_sl.value() * 255 / 100),
            "band_height_auto": self.band_auto_radio.isChecked(),
            "band_height":      self.band_height_spin.value(),
            "band_padding":     18,
        }


    def close_panel(self) -> None:
        self.setVisible(False)
        self.panel_closed.emit()

    # ──────────────────────────────── Helpers ─────────────────────────

    def _sec(self, text: str) -> QLabel:
        lbl = QLabel(text); lbl.setObjectName("sec_lbl"); return lbl


    def _flatten_metadata(self, metadata: dict) -> Dict[str, str]:
        return flatten_watermark_metadata(metadata)


    def _build_default_template(self) -> str:
        flat = self._metadata_flat
        lines: List[str] = []

        make  = flat.get("make",  "")
        model = flat.get("model", "")
        if make or model:
            parts = []
            if make:  parts.append("{make}")
            if model: parts.append("{model}")
            lines.append(" ".join(parts))

        if "lens_model"  in flat: lines.append("{lens_model}")
        if "date_taken"  in flat: lines.append("{date_taken}")

        cam = ["{" + k + "}" for k in ("focal_length","f_stop","exposure_time","iso") if k in flat]
        if cam: lines.append("  ".join(cam))

        if not lines and "filename" in flat:
            lines.append("{filename}")

        return "\n".join(lines)


    def _clear_layout(self, layout) -> None:
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item is None: continue
            w = item.widget()
            if w is not None: w.deleteLater(); continue
            c = item.layout()
            if c is not None: self._clear_layout(c)


    def _rebuild_token_buttons(self) -> None:
        self._clear_layout(self.token_layout)

        keys: List[str] = []
        for k in _TOKEN_ORDER:
            if k in self._metadata_flat:
                keys.append(k)
        for k in sorted(self._metadata_flat):
            if "." not in k and k not in keys:
                keys.append(k)

        if not keys:
            e = QLabel(t('watermark_panel.no_metadata'))
            e.setObjectName("hint_lbl")
            self.token_layout.addWidget(e)
            self.token_layout.addStretch()
            return

        row_lay: Optional[QHBoxLayout] = None
        for i, key in enumerate(keys):
            if i % 3 == 0:
                row_lay = QHBoxLayout()
                row_lay.setSpacing(4); row_lay.setContentsMargins(0, 0, 0, 0)
                self.token_layout.addLayout(row_lay)

            if key in _TOKEN_ORDER:
                label = t(f'watermark_panel.token_{key}')
            else:
                label = key                         

            btn = QPushButton(f"{label}  " + "{" + key + "}")
            btn.setObjectName("chip_btn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, tk=key: self._insert_token(tk))
            row_lay.addWidget(btn)                           # type: ignore[union-attr]

        self.token_layout.addStretch()


    def _insert_token(self, token: str) -> None:
        cur = self.template_edit.textCursor()
        cur.insertText("{" + token + "}")
        self.template_edit.setTextCursor(cur)
        self.template_edit.setFocus()


    def _resolve_preview_text(self, text: str) -> str:
        lines = resolve_template(text, self._metadata_flat, max_lines=5)
        return "\n".join(lines) if lines else "—"


    def _update_preview_label(self) -> None:
        txt = self.template_edit.toPlainText()
        self.preview_label.setText(self._resolve_preview_text(txt) if txt.strip() else "—")


    def _refresh_apply_enabled(self) -> None:
        self.apply_btn.setEnabled(bool(self.template_edit.toPlainText().strip()))

    # ──────────────────────────────── Slots ───────────────────────────

    def _emit_config(self, *_) -> None:
        self._update_preview_label()
        self._refresh_apply_enabled()
        self.config_changed.emit(self.build_config())


    def _on_template_changed(self) -> None:
        self._emit_config()


    def _on_apply_clicked(self) -> None:
        self.apply_requested.emit(self.build_config())


    def _pick_text_color(self) -> None:
        c = QColorDialog.getColor(self._text_color, self, t('watermark_panel.color_dialog_text'))
        if c.isValid():
            self._text_color = c; _set_color_btn(self.text_col_btn, c); self._emit_config()


    def _pick_band_color(self) -> None:
        c = QColorDialog.getColor(self._band_color, self, t('watermark_panel.color_dialog_band'))
        if c.isValid():
            self._band_color = c; _set_color_btn(self.band_col_btn, c); self._emit_config()


    def _on_align_clicked(self) -> None:
        btn = self.sender()
        if btn: self._alignment = btn.property("_align") or Qt.AlignmentFlag.AlignLeft
        self._emit_config()


    def _on_anchor_clicked(self) -> None:
        btn = self.sender()
        if btn:
            new_anchor = btn.property("_anchor") or "br"
            anchor_changed = (new_anchor != self._anchor)  
            self._anchor = new_anchor
        else:
            anchor_changed = False

        if self.band_enable_btn.isChecked() and anchor_changed:
            self._sync_alignment_from_anchor()

        self._emit_config()


    def _on_band_enable_clicked(self) -> None:
        """밴드 토글 전용 핸들러 — 켤 때 앵커 수평 성분을 정렬에 동기화."""
        if self.band_enable_btn.isChecked():
            self._sync_alignment_from_anchor()
        self._emit_config()


    def _sync_alignment_from_anchor(self) -> None:
        """앵커 키 끝 글자(l/c/r)를 읽어 정렬 버튼 + 내부 상태를 동기화."""
        _MAP = {
            "l": (Qt.AlignmentFlag.AlignLeft,    self.align_left_btn),
            "c": (Qt.AlignmentFlag.AlignHCenter, self.align_center_btn),
            "r": (Qt.AlignmentFlag.AlignRight,   self.align_right_btn),
        }
        h = self._anchor[-1] if self._anchor else "r"
        align, target_btn = _MAP.get(h, _MAP["r"])

        if self._alignment == align:
            return 

        self._alignment = align
        for b in (self.align_left_btn, self.align_center_btn, self.align_right_btn):
            b.blockSignals(True)
            b.setChecked(b is target_btn)
            b.blockSignals(False)


    def _on_band_alpha_changed(self, value: int) -> None:
        self.band_alpha_lbl.setText(f"{value}%")
        self._emit_config()


    def _on_band_height_mode_changed(self, checked: bool) -> None:
        self.band_height_spin.setEnabled(not checked)
        self._emit_config()
