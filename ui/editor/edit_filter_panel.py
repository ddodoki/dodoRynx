# -*- coding: utf-8 -*-
# ui\editor\edit_filter_panel.py

from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QPushButton,
    QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from core.image_filters import BasicParams
from utils.lang_manager import t 
from utils.panel_opacity import apply_hover_opacity


_TAB_H  = 26
_BTN_H  = 26
_ROW_H  = 22
_LBL_W  = 52  
_VAL_W  = 34
_ROW_SP =  4
_BTN_SP =  4

_CONTENT_H_BASIC = 10 + 5 * _ROW_H + 4 * _ROW_SP   
_CONTENT_H: int  = _CONTENT_H_BASIC

PANEL_W:       int = 270   
PANEL_TOTAL_H: int = 4 + 4 + _TAB_H + 1 + _CONTENT_H 

_STYLE_KEYS: Tuple[str, ...] = ("none", "grayscale", "sepia", "vintage")
_PRO_KEYS:   Tuple[str, ...] = ("none", "vignette", "clarity", "grain", "fade", "glow")

_BASIC_SPECS: Tuple[Tuple[str, str], ...] = (
    ("brightness",  "edit_filter_panel.basic.brightness"),
    ("contrast",    "edit_filter_panel.basic.contrast"),
    ("saturation",  "edit_filter_panel.basic.saturation"),
    ("sharpness",   "edit_filter_panel.basic.sharpness"),
    ("temperature", "edit_filter_panel.basic.temperature"),
)


_SS = """
EditFilterPanel {
    background    : rgba(16, 16, 16, 215);
    border        : 1px solid rgba(70, 70, 70, 160);
    border-radius : 8px;
}
QPushButton#tab {
    background    : transparent;
    color         : #5a5a5a;
    border        : none;
    border-bottom : 2px solid transparent;
    border-radius : 0px;
    font-size     : 11px;
    font-weight   : 600;
    min-height    : 24px;
    max-height    : 24px;
    padding       : 0px 14px;
}
QPushButton#tab:hover   { color: #aaaaaa; }
QPushButton#tab:checked { color: #4a9eff; border-bottom: 2px solid #4a9eff; }
QPushButton#reset {
    background    : transparent;
    color         : #4a4a4a;
    border        : 1px solid #333333;
    border-radius : 3px;
    font-size     : 10px;
    min-height    : 20px;
    max-height    : 20px;
    padding       : 0px 8px;
}
QPushButton#reset:hover {
    color: #cccccc; border-color: #585858; background: rgba(40,40,40,180);
}
QPushButton#filt {
    background    : rgba(28, 28, 28, 200);
    color         : #686868;
    border        : 1px solid rgba(55, 55, 55, 180);
    border-radius : 4px;
    font-size     : 10px;
    min-height    : 26px;
    max-height    : 26px;
    padding       : 0px;
}
QPushButton#filt:hover   { background: rgba(46,46,46,200); color: #b0b0b0; }
QPushButton#filt:checked {
    background: rgba(37,99,168,220); color: #ffffff;
    border: 1px solid rgba(74,158,255,200); font-weight: 600;
}
QPushButton#filt:disabled { color: #363636; border-color: rgba(40,40,40,100); }
QFrame#hsep { background: rgba(60,60,60,120); min-height:1px; max-height:1px; border:none; }
QWidget#page { background: transparent; }
QLabel#lbl {
    color: #7a7a7a; font-size: 11px; background: transparent;
    min-height: 22px; max-height: 22px;
}
QLabel#lbl:hover { color: #b0b0b0; }
QLabel#val {
    color: #c8c8c8; font-size: 11px; font-weight: 600;
    background: transparent; min-height: 22px; max-height: 22px;
}
QSlider::groove:horizontal  { background: rgba(55,55,55,200); height:3px; border-radius:1px; }
QSlider::sub-page:horizontal { background: #2563a8; height:3px; border-radius:1px; }
QSlider::add-page:horizontal { background: rgba(55,55,55,200); height:3px; border-radius:1px; }
QSlider::handle:horizontal   {
    background:#5aaaff; border:1.5px solid #2563a8;
    width:12px; height:12px; margin:-5px 0; border-radius:6px;
}
QSlider::handle:horizontal:hover   { background: #7fc0ff; }
QSlider::handle:horizontal:pressed { background: #1e5c9e; }
QSlider::handle:horizontal:disabled { background:#3a3a3a; border-color:#2a2a2a; }
"""

# ──────────────────────────────────────────────────────────────────
# 더블클릭 라벨
# ──────────────────────────────────────────────────────────────────

class _ClickableLabel(QLabel):
    double_clicked: Signal = Signal(str)   # type: ignore[assignment]

    def __init__(self, text: str, key: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._key = key
        self.setObjectName("lbl")
        if key:
            self.setToolTip(t('edit_filter_panel.tip_reset_val'))  
            self.setCursor(Qt.CursorShape.PointingHandCursor)


    def mouseDoubleClickEvent(self, event) -> None:   # type: ignore[override]
        if self._key:
            self.double_clicked.emit(self._key)
        super().mouseDoubleClickEvent(event)


# ──────────────────────────────────────────────────────────────────
# EditFilterPanel
# ──────────────────────────────────────────────────────────────────

class EditFilterPanel(QWidget):
    basic_changed:   Signal = Signal(object)    # type: ignore[assignment]
    style_changed:   Signal = Signal(str, int)  # type: ignore[assignment]
    pro_changed:     Signal = Signal(str, int)  # type: ignore[assignment]
    reset_requested: Signal = Signal()          # type: ignore[assignment]
    panel_closed:    Signal = Signal()

    PANEL_TOTAL_H: int = PANEL_TOTAL_H


    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(_SS)
        self.setFixedSize(PANEL_W, PANEL_TOTAL_H)

        self._basic              = BasicParams()
        self._style_name         = "none"
        self._style_intensity    = 0
        self._pro_name           = "none"
        self._pro_intensity      = 0
        self._basic_sliders: Dict[str, QSlider] = {}
        self._basic_vals:    Dict[str, QLabel]  = {}

        self._build()

        apply_hover_opacity(self, idle=0.18, hover=0.96)

    # ──────────────────────────────────────────────────────────────
    # 빌드
    # ──────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(0)
        root.addLayout(self._build_tab_row())
        root.addWidget(self._make_hsep())
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_basic_page())
        self._stack.addWidget(self._build_style_page())
        self._stack.addWidget(self._build_pro_page())
        root.addWidget(self._stack, 1)

    # ── 탭 행 ─────────────────────────────────────────────────────

    def _build_tab_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(0)

        self._tab_grp = QButtonGroup(self)
        self._tab_grp.setExclusive(True)

        tab_keys = (
            'edit_filter_panel.tab_basic',
            'edit_filter_panel.tab_style',
            'edit_filter_panel.tab_pro',
        )
        for idx, key in enumerate(tab_keys):
            btn = QPushButton(t(key))
            btn.setObjectName("tab")
            btn.setCheckable(True)
            btn.setFixedHeight(_TAB_H)
            btn.clicked.connect(lambda _, i=idx: self._stack.setCurrentIndex(i))
            self._tab_grp.addButton(btn, idx)
            row.addWidget(btn)

        self._tab_grp.button(0).setChecked(True)
        row.addStretch(1)

        btn_reset = QPushButton(t('edit_filter_panel.btn_reset'))   
        btn_reset.setObjectName("reset")
        btn_reset.clicked.connect(self._do_reset)
        row.addWidget(btn_reset)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("close")
        btn_close.setFixedSize(20, 20)
        btn_close.setStyleSheet(
            "QPushButton#close{"
            "  background:transparent; color:#4a4a4a;"
            "  border:none; font-size:12px; padding:0;"
            "}"
            "QPushButton#close:hover{ color:#e35b5b; }"
        )
        btn_close.clicked.connect(self.close_panel)
        row.addWidget(btn_close)

        return row

    # ── 기본 탭 ───────────────────────────────────────────────────

    def _build_basic_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        g = QGridLayout(page)
        g.setContentsMargins(10, 6, 10, 4)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(_ROW_SP)

        for row, (key, t_key) in enumerate(_BASIC_SPECS):
            lbl = _ClickableLabel(t(t_key), key)  
            lbl.setFixedWidth(_LBL_W)
            lbl.double_clicked.connect(self._reset_basic_key)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(-100, 100)
            slider.setValue(0)
            slider.setFixedHeight(_ROW_H)

            val = QLabel("0")
            val.setObjectName("val")
            val.setFixedWidth(_VAL_W)
            val.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

            slider.valueChanged.connect(
                lambda v, k=key, vl=val: self._on_basic_slider(k, v, vl)
            )

            g.addWidget(lbl,    row, 0)
            g.addWidget(slider, row, 1)
            g.addWidget(val,    row, 2)

            self._basic_sliders[key] = slider
            self._basic_vals[key]    = val

        g.setColumnStretch(1, 1)
        return page


    def _on_basic_slider(self, key: str, v: int, lbl: QLabel) -> None:
        sign = "+" if v > 0 else ""
        lbl.setText(f"{sign}{v}")
        setattr(self._basic, key, v)
        self.basic_changed.emit(asdict(self._basic))


    def _reset_basic_key(self, key: str) -> None:
        slider = self._basic_sliders.get(key)
        if slider:
            slider.setValue(0)

    # ── 스타일 탭 ─────────────────────────────────────────────────

    def _build_style_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 6, 10, 4)
        layout.setSpacing(_BTN_SP)

        self._style_btn_grp = QButtonGroup(page)
        self._style_btn_grp.setExclusive(True)
        layout.addWidget(
            self._build_filter_grid(_STYLE_KEYS, 'style', self._style_btn_grp)
        )
        layout.addWidget(self._make_hsep())

        int_lbl = _ClickableLabel(
            t('edit_filter_panel.intensity'), "style_intensity"  
        )
        int_lbl.setFixedWidth(_LBL_W)
        int_lbl.setToolTip(t('edit_filter_panel.tip_reset_int')) 
        int_lbl.double_clicked.connect(lambda _: self._reset_intensity("style"))

        self.s_style = QSlider(Qt.Orientation.Horizontal)
        self.s_style.setRange(0, 100)
        self.s_style.setValue(0)
        self.s_style.setFixedHeight(_ROW_H)
        self.s_style.setEnabled(False)

        self.lb_style_v = QLabel("0")
        self.lb_style_v.setObjectName("val")
        self.lb_style_v.setFixedWidth(_VAL_W)
        self.lb_style_v.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        int_row = QHBoxLayout()
        int_row.setSpacing(8)
        int_row.addWidget(int_lbl)
        int_row.addWidget(self.s_style, 1)
        int_row.addWidget(self.lb_style_v)
        layout.addLayout(int_row)
        layout.addStretch(1)

        self._style_btn_grp.idClicked.connect(self._on_style_btn)
        self.s_style.valueChanged.connect(self._on_style_slider)
        self._style_btn_grp.button(0).setChecked(True)
        return page

    # ── 전문가 탭 ─────────────────────────────────────────────────

    def _build_pro_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 6, 10, 4)
        layout.setSpacing(_BTN_SP)

        self._pro_btn_grp = QButtonGroup(page)
        self._pro_btn_grp.setExclusive(True)
        layout.addWidget(
            self._build_filter_grid(_PRO_KEYS, 'pro', self._pro_btn_grp)
        )
        layout.addWidget(self._make_hsep())

        int_lbl = _ClickableLabel(
            t('edit_filter_panel.intensity'), "pro_intensity"    
        )
        int_lbl.setFixedWidth(_LBL_W)
        int_lbl.setToolTip(t('edit_filter_panel.tip_reset_int'))
        int_lbl.double_clicked.connect(lambda _: self._reset_intensity("pro"))

        self.s_pro = QSlider(Qt.Orientation.Horizontal)
        self.s_pro.setRange(0, 100)
        self.s_pro.setValue(0)
        self.s_pro.setFixedHeight(_ROW_H)
        self.s_pro.setEnabled(False)

        self.lb_pro_v = QLabel("0")
        self.lb_pro_v.setObjectName("val")
        self.lb_pro_v.setFixedWidth(_VAL_W)
        self.lb_pro_v.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        int_row = QHBoxLayout()
        int_row.setSpacing(8)
        int_row.addWidget(int_lbl)
        int_row.addWidget(self.s_pro, 1)
        int_row.addWidget(self.lb_pro_v)
        layout.addLayout(int_row)
        layout.addStretch(1)

        self._pro_btn_grp.idClicked.connect(self._on_pro_btn)
        self.s_pro.valueChanged.connect(self._on_pro_slider)
        self._pro_btn_grp.button(0).setChecked(True)
        return page

    # ── 공통: 필터 선택 그리드 ────────────────────────────────────

    def _build_filter_grid(
        self,
        keys:      Tuple[str, ...],
        group_key: str,            
        grp:       QButtonGroup,
    ) -> QWidget:
        """키 목록 → 2열 토글 버튼 그리드. 레이블은 t()로 가져옴."""
        container = QWidget()
        container.setObjectName("page")
        gl = QGridLayout(container)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setHorizontalSpacing(_BTN_SP)
        gl.setVerticalSpacing(_BTN_SP)

        for idx, key in enumerate(keys):
            label = t(f'edit_filter_panel.{group_key}.{key}') 
            btn   = QPushButton(label)
            btn.setObjectName("filt")
            btn.setCheckable(True)
            btn.setFixedHeight(_BTN_H)
            grp.addButton(btn, idx)
            gl.addWidget(btn, idx // 2, idx % 2)

        gl.setColumnStretch(0, 1)
        gl.setColumnStretch(1, 1)
        return container

    # ── 슬롯 ──────────────────────────────────────────────────────

    def _on_style_btn(self, idx: int) -> None:
        name    = _STYLE_KEYS[idx]
        is_none = (name == "none")
        self._style_name = name
        self.s_style.setEnabled(not is_none)

        if is_none:
            self._set_slider(self.s_style, self.lb_style_v, 0)
            self._style_intensity = 0
        elif self._style_intensity == 0:
            self._set_slider(self.s_style, self.lb_style_v, 50) 
            self._style_intensity = 50

        self.style_changed.emit(self._style_name, self._style_intensity) 
        

    def _on_style_slider(self, v: int) -> None:
        self.lb_style_v.setText(str(v))
        self._style_intensity = v
        self.style_changed.emit(self._style_name, self._style_intensity)


    def _on_pro_btn(self, idx: int) -> None:
        name    = _PRO_KEYS[idx]
        is_none = (name == "none")
        self._pro_name = name
        self.s_pro.setEnabled(not is_none)

        if is_none:
            self._set_slider(self.s_pro, self.lb_pro_v, 0)
            self._pro_intensity = 0
        elif self._pro_intensity == 0:
            self._set_slider(self.s_pro, self.lb_pro_v, 50)  
            self._pro_intensity = 50

        self.pro_changed.emit(self._pro_name, self._pro_intensity) 


    def _on_pro_slider(self, v: int) -> None:
        self.lb_pro_v.setText(str(v))
        self._pro_intensity = v
        self.pro_changed.emit(self._pro_name, self._pro_intensity)


    def _reset_intensity(self, which: str) -> None:
        if which == "style":
            self._set_slider(self.s_style, self.lb_style_v, 0)
        else:
            self._set_slider(self.s_pro, self.lb_pro_v, 0)

    # ──────────────────────────────────────────────────────────────
    # 공개 API
    # ──────────────────────────────────────────────────────────────

    def reset_all(self) -> None:
        for key, slider in self._basic_sliders.items():
            slider.blockSignals(True)
            slider.setValue(0)
            slider.blockSignals(False)
            self._basic_vals[key].setText("0") 
        self._basic = BasicParams()

        self._style_name = "none"
        self._style_intensity = 0
        self._style_btn_grp.button(0).setChecked(True)
        self._set_slider(self.s_style, self.lb_style_v, 0)
        self.s_style.setEnabled(False)

        self._pro_name = "none"
        self._pro_intensity = 0
        self._pro_btn_grp.button(0).setChecked(True)
        self._set_slider(self.s_pro, self.lb_pro_v, 0)
        self.s_pro.setEnabled(False)

        self._tab_grp.button(0).setChecked(True)
        self._stack.setCurrentIndex(0)

    # ──────────────────────────────────────────────────────────────
    # 내부 헬퍼
    # ──────────────────────────────────────────────────────────────

    def _do_reset(self) -> None:
        self.reset_all()
        self.basic_changed.emit(asdict(self._basic))
        self.style_changed.emit(self._style_name, self._style_intensity)
        self.pro_changed.emit(self._pro_name,      self._pro_intensity)
        self.reset_requested.emit()


    def close_panel(self) -> None:
        self.setVisible(False)
        self.panel_closed.emit()


    @staticmethod
    def _set_slider(slider: QSlider, lbl: QLabel, v: int) -> None:
        slider.blockSignals(True)
        slider.setValue(v)
        slider.blockSignals(False)
        lbl.setText(str(v))


    @staticmethod
    def _make_hsep() -> QFrame:
        sep = QFrame()
        sep.setObjectName("hsep")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        return sep
