# -*- coding: utf-8 -*-
# ui/edit_toolbar.py
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel,
    QPushButton, QSlider, QVBoxLayout, QWidget, QGraphicsOpacityEffect
)
from ui.edit_filter_panel import EditFilterPanel
from utils.lang_manager import t

_H    = 25 
_W_SQ = 28   
_W_FMT = 34  

_SS = f"""
EditToolbar {{
    background : #161616;
    border-top : 1px solid #2e2e2e;
}}
QPushButton {{
    background   : #252525;
    color        : #c8c8c8;
    border       : 1px solid #404040;
    border-radius: 4px;
    font-size    : 14px;
    min-height   : {_H}px;
    max-height   : {_H}px;
    min-width    : {_W_SQ}px;
    max-width    : {_W_SQ}px;
    padding      : 0px;
}}
QPushButton:hover   {{ background: #313131; color: #ffffff; border-color: #585858; }}
QPushButton:checked {{ background: #1a3f6b; color: #6ab4ff; border-color: #2a68b0; }}
QPushButton:pressed {{ background: #152e50; }}
QPushButton:disabled{{ color: #484848; border-color: #2a2a2a; background: #1c1c1c; }}

QPushButton#apply {{
    background : #1a4a1a; color: #7ddd7d;
    border-color: #2a6e2a; font-size: 16px; font-weight: 700;
}}
QPushButton#apply:hover   {{ background: #215c21; color: #a0f0a0; }}
QPushButton#apply:pressed {{ background: #163d16; }}

QPushButton#cancel {{
    background : #3a1818; color: #e07070;
    border-color: #5c2a2a; font-size: 16px; font-weight: 700;
}}
QPushButton#cancel:hover   {{ background: #4a1e1e; color: #f09090; }}
QPushButton#cancel:pressed {{ background: #2e1212; }}

QPushButton#ai_btn {{
    background : #252530; color: #9090e0;
    border: 1px solid #404068; border-radius: 4px;
    font-size: 14px; font-weight: 600;
}}
QPushButton#ai_btn:hover   {{ background: #2e2e42; color: #b0b0ff; border-color: #6060a0; }}
QPushButton#ai_btn:checked {{ background: #1a1a3a; color: #a0a0ff; border-color: #5050a0; }}
QPushButton#ai_btn:disabled{{ color: #444; border-color: #2a2a2a; background: #1c1c1c; }}

QPushButton#fmt {{
    background  : #1e1e1e; color: #505050;
    border: 1px solid #363636; border-radius: 3px;
    font-size: 10px; font-weight: 700;
    min-height: {_H}px; max-height: {_H}px;
    min-width: {_W_FMT}px; max-width: {_W_FMT}px;
    padding: 0px;
}}
QPushButton#fmt:hover   {{ background: #282828; color: #808080; }}
QPushButton#fmt:checked {{ background: #142840; color: #4a9eff; border-color: #1e5090; }}

QSlider#quality::groove:horizontal  {{ background: #282828; height: 3px; border-radius: 1px; }}
QSlider#quality::sub-page:horizontal{{ background: #2563a8; height: 3px; border-radius: 1px; }}
QSlider#quality::add-page:horizontal{{ background: #282828; height: 3px; border-radius: 1px; }}
QSlider#quality::handle:horizontal  {{
    background: #4a9eff; border: 1px solid #1e5090;
    width: 10px; height: 10px; margin: -4px 0; border-radius: 5px;
}}
QSlider#quality::handle:horizontal:hover {{ background: #70c0ff; }}

QLabel {{
    color: #787878; font-size: 10px; background: transparent;
    min-height: {_H}px; max-height: {_H}px; padding: 0px 1px;
}}
QLabel#qlbl {{
    color: #b0b0b0; font-size: 11px; font-weight: 600; min-width: 26px;
}}
QFrame[frameShape="5"] {{
    color: #2e2e2e;
    min-width: 1px; max-width: 1px;
    min-height: 16px; max-height: 16px;
    margin: 0px 2px;
}}
"""


class EditToolbar(QWidget):
    """편집 모드 툴바 — 단일 행 아이콘 레이아웃"""

    # ── 시그널 ──────────────────────────────────────────────────────────────
    crop_requested             = Signal()
    copy_requested             = Signal()
    mosaic_requested           = Signal()
    apply_requested            = Signal()
    cancel_requested           = Signal()
    shape_requested            = Signal(str, QColor, int, int)
    text_requested             = Signal(str, int, QColor, bool, bool)
    tool_changed               = Signal(str)
    filters_visibility_changed = Signal(bool)
    format_changed             = Signal(str)
    quality_changed            = Signal(int)
    ai_panel_requested         = Signal(bool)
    ai_preload_requested       = Signal()
    eraser_size_changed: Signal = Signal(int)

    _BASE_H: int = 38


    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(_SS)

        self._shape_color = QColor(255, 80, 80)
        self._line_color  = QColor(255, 80, 80)
        self._text_color  = QColor(255, 50, 50)
        self._line_width  = 2
        self._line_style  = Qt.PenStyle.SolidLine

        self._build_ui()
        self.setFixedHeight(self._BASE_H)

        # ── 반투명 페이드 효과 ──────────────────────────────────────────
        self._opacity_fx = QGraphicsOpacityEffect(self)
        self._opacity_fx.setOpacity(0.30) 
        self.setGraphicsEffect(self._opacity_fx)

        self._fade = QPropertyAnimation(self._opacity_fx, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)


    def enterEvent(self, event) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._opacity_fx.opacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        super().enterEvent(event)


    def leaveEvent(self, event) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._opacity_fx.opacity())
        self._fade.setEndValue(0.30)
        self._fade.start()
        super().leaveEvent(event)


    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 5, 8, 5)
        root.setSpacing(0)
        root.addLayout(self._row())


    def _row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(3)
        row.setContentsMargins(0, 0, 0, 0)

        self.btn_crop    = self._mk_btn('✂', chk=True, tip=t('edit_toolbar.crop_tip'))
        self.btn_copy    = self._mk_btn('⧉', chk=True, tip=t('edit_toolbar.copy_tip'))
        self.btn_mosaic  = self._mk_btn('▦', chk=True, tip=t('edit_toolbar.mosaic_tip'))
        self.btn_eraser  = self._mk_btn('🧽', chk=True, tip=t('edit_toolbar.eraser_tip'))
        self.btn_filters = self._mk_btn('🎨', chk=True, tip=t('edit_toolbar.filters_tip'))

        self.btn_crop.clicked.connect(   lambda: self.tool_changed.emit('crop_select'))
        self.btn_copy.clicked.connect(   lambda: self.tool_changed.emit('copy_select'))
        self.btn_mosaic.clicked.connect( lambda: self.tool_changed.emit('mosaic_select'))
        self.btn_eraser.clicked.connect( self._on_eraser_toggled)
        self.btn_filters.clicked.connect(self._on_filters_toggled)

        for b in (self.btn_crop, self.btn_copy, self.btn_mosaic,
                  self.btn_eraser):
            row.addWidget(b)

        row.addWidget(self._vsep())
        row.addWidget(self.btn_filters)

        self.btn_ai = QPushButton('✨')
        self.btn_ai.setObjectName('ai_btn')
        self.btn_ai.setFixedSize(_W_SQ, _H)
        self.btn_ai.setCheckable(True)
        self.btn_ai.setToolTip(t('edit_toolbar.ai_panel_tip'))
        self.btn_ai.clicked.connect(self._on_ai_panel_toggled)
        row.addWidget(self.btn_ai)

        self.btn_watermark = QPushButton("💧")
        self.btn_watermark.setObjectName("ai_btn")  
        self.btn_watermark.setFixedHeight(_H)
        self.btn_watermark.setCheckable(True)
        self.btn_watermark.setToolTip(t('edit_toolbar.watermark_tip'))
        self.btn_watermark.clicked.connect(self._on_watermark_toggled)
        row.addWidget(self.btn_watermark)

        row.addWidget(self._vsep())

        self._area_grp = QButtonGroup(self)
        self._area_grp.setExclusive(True)
        for b in (self.btn_crop, self.btn_copy, self.btn_mosaic):
            self._area_grp.addButton(b)

        self.btn_resize = self._mk_btn('⤡', chk=True, tip=t('edit_toolbar.resize_tip'))
        self.btn_shapes = self._mk_btn('⬜', chk=True, tip=t('edit_toolbar.shapes_tip'))
        self.btn_resize.clicked.connect(self._on_resize_toggled)
        self.btn_shapes.clicked.connect(self._on_shapes_toggled)
        row.addWidget(self.btn_resize)
        row.addWidget(self.btn_shapes)

        row.addWidget(self._vsep())
        row.addStretch(1)

        self.btn_fmt_jpg  = QPushButton('JPG')
        self.btn_fmt_webp = QPushButton('WEBP')
        for btn in (self.btn_fmt_jpg, self.btn_fmt_webp):
            btn.setObjectName('fmt')
            btn.setCheckable(True)
        self.btn_fmt_jpg.setChecked(True)

        self._fmt_grp = QButtonGroup(self)
        self._fmt_grp.setExclusive(True)
        self._fmt_grp.addButton(self.btn_fmt_jpg,  0)
        self._fmt_grp.addButton(self.btn_fmt_webp, 1)
        self._fmt_grp.idClicked.connect(self._on_fmt_changed)

        self._quality_slider = QSlider(Qt.Orientation.Horizontal)
        self._quality_slider.setObjectName('quality')
        self._quality_slider.setRange(1, 100)
        self._quality_slider.setValue(85)
        self._quality_slider.setFixedWidth(64)
        self._quality_slider.setToolTip(t('edit_toolbar.quality_tip'))
        self._quality_slider.valueChanged.connect(self._on_quality_changed)

        self._quality_lbl = QLabel('85')
        self._quality_lbl.setObjectName('qlbl')
        self._quality_lbl.setFixedWidth(26)

        row.addWidget(self.btn_fmt_jpg)
        row.addWidget(self.btn_fmt_webp)
        row.addWidget(self._quality_slider)
        row.addWidget(self._quality_lbl)
        row.addWidget(self._vsep())

        self.btn_apply  = QPushButton('✔')
        self.btn_cancel = QPushButton('✕')
        self.btn_apply.setObjectName('apply')
        self.btn_cancel.setObjectName('cancel')
        self.btn_apply.setFixedSize(_W_SQ, _H)
        self.btn_cancel.setFixedSize(_W_SQ, _H)
        self.btn_apply.setToolTip(t('edit_toolbar.apply'))
        self.btn_cancel.setToolTip(t('edit_toolbar.cancel'))
        self.btn_apply.clicked.connect( lambda: self.apply_requested.emit())
        self.btn_cancel.clicked.connect(lambda: self.cancel_requested.emit())
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_cancel)

        return row

    # ────────────────────────────────────────────────────────────────────────
    # 이벤트 핸들러
    # ────────────────────────────────────────────────────────────────────────

    def _on_filters_toggled(self, checked: bool) -> None:
        self.filters_visibility_changed.emit(checked)


    def _on_fmt_changed(self, btn_id: int) -> None:
        fmt = 'webp' if btn_id == 1 else 'jpg'
        default_q = 82 if fmt == 'webp' else 85
        self._quality_slider.blockSignals(True)
        self._quality_slider.setValue(default_q)
        self._quality_slider.blockSignals(False)
        self._quality_lbl.setText(str(default_q))
        self.format_changed.emit(fmt)


    def _on_quality_changed(self, v: int) -> None:
        self._quality_lbl.setText(str(v))
        self.quality_changed.emit(v)


    def _on_ai_panel_toggled(self, checked: bool) -> None:
        self.ai_panel_requested.emit(checked)
        if checked:
            self.ai_preload_requested.emit()


    def _on_watermark_toggled(self, checked: bool) -> None:
        self.tool_changed.emit('watermark' if checked else 'select')


    def _on_eraser_toggled(self, checked: bool) -> None:
        self.tool_changed.emit('eraser' if checked else 'select')


    def _on_resize_toggled(self, checked: bool) -> None:
        if checked:
            self._uncheck_area_grp()
            self.btn_shapes.blockSignals(True)
            self.btn_shapes.setChecked(False)
            self.btn_shapes.blockSignals(False)
            self.tool_changed.emit('resize')
        else:
            self.tool_changed.emit('select')


    def _on_shapes_toggled(self, checked: bool) -> None:
        if checked:
            self._uncheck_area_grp()
            self.btn_resize.blockSignals(True)
            self.btn_resize.setChecked(False)
            self.btn_resize.blockSignals(False)
            self.tool_changed.emit('shapes')
        else:
            self.tool_changed.emit('select')

    # ────────────────────────────────────────────────────────────────────────
    # 외부 동기화
    # ────────────────────────────────────────────────────────────────────────

    def sync_from_shape(self, color: QColor, line_width: int) -> None:
        self._line_color = QColor(color)
        self._line_width = line_width

    sync_from_shape_item = sync_from_shape


    def sync_from_text(self, item) -> None:
        pass

    sync_from_text_item = sync_from_text

    # ────────────────────────────────────────────────────────────────────────
    # 외부 getter
    # ────────────────────────────────────────────────────────────────────────

    def current_pen_color(self) -> QColor:
        return QColor(self._line_color)


    def current_fill_color(self) -> Optional[QColor]:
        return None 

    def current_line_width(self) -> int:
        return self._line_width


    def current_line_style(self) -> int:
        return self._line_style.value if hasattr(self._line_style, 'value') else 1


    def current_format(self) -> str:
        return 'webp' if self.btn_fmt_webp.isChecked() else 'jpg'


    def current_quality(self) -> int:
        return self._quality_slider.value()

    # ────────────────────────────────────────────────────────────────────────
    # 상태 리셋
    # ────────────────────────────────────────────────────────────────────────

    def _uncheck_area_grp(self) -> None:
        self._area_grp.setExclusive(False)
        for b in (self.btn_crop, self.btn_copy, self.btn_mosaic):
            b.setChecked(False)
        self._area_grp.setExclusive(True)


    def reset_area_buttons(self) -> None:
        self._uncheck_area_grp()

    # ────────────────────────────────────────────────────────────────────────
    # 팩토리 헬퍼
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _mk_btn(label: str = '', *, chk: bool = False, tip: str = '') -> QPushButton:
        b = QPushButton(label)
        b.setCheckable(chk)
        b.setFixedSize(_W_SQ, _H)
        if tip:
            b.setToolTip(tip)
        return b


    @staticmethod
    def _vsep() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setFrameShadow(QFrame.Shadow.Sunken)
        f.setFixedSize(5, _H)
        return f
