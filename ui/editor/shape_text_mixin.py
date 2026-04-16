# -*- coding: utf-8 -*-
# ui\editor\shape_text_mixin.py

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup, QColorDialog, QFontComboBox, QFrame,
    QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QVBoxLayout, QWidget, QSlider
)

from ui.editor.shape_item import ResizableShapeItem
from ui.editor.text_item  import TextShapeItem

from utils.debug   import debug_print
from utils.lang_manager import t
from utils.panel_opacity import apply_hover_opacity
from utils.drag_header import DragHeader


_OVERLAY_W = 230

_SS = """
QWidget#st_overlay {
    background: rgba(18, 18, 18, 220);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 7px;
}
QLabel#sec {
    color: #555; font-size: 9px; font-weight: 700;
    letter-spacing: 0.5px; background: transparent;
}
QLabel { color: #aaa; font-size: 10px; background: transparent; }
QLabel#op_label {
    color: #888; font-size: 10px; background: transparent;
    min-width: 62px; max-width: 62px;
}

/* 도형 버튼 공통 */
QPushButton {
    background: #252525; color: #ccc;
    border: 1px solid #404040; border-radius: 3px;
    font-size: 13px;
    min-width:  26px; max-width:  26px;
    min-height: 26px; max-height: 26px;
    padding: 0px;
}
QPushButton:hover   { background: #333; color: #fff; border-color: #666; }
QPushButton:checked { background: #1a3f6b; color: #6ab4ff; border-color: #2a68b0; }
QPushButton:pressed { background: #152e50; }

QPushButton#colbtn {
    min-width: 26px; max-width: 26px;
    min-height: 26px; max-height: 26px;
    border-radius: 3px;
}
QPushButton#wide {
    min-width: 0px; max-width: 9999px;
    padding: 0px 10px; font-size: 11px;
}
QPushButton#insert {
    min-width: 0px; max-width: 9999px;
    padding: 0px 8px; font-size: 11px; font-weight: 700;
    background: #1a4a1a; color: #7ddd7d; border-color: #2a6e2a;
}
QPushButton#insert:hover { background: #215c21; color: #a0f0a0; }

QSpinBox {
    background: #222; color: #eee; border: 1px solid #404040;
    border-radius: 3px; font-size: 11px;
    min-height: 26px; max-height: 26px; min-width: 50px;
    padding: 0px 2px;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 14px; background: #2e2e2e; border: none;
}

QFontComboBox {
    background: #222; color: #eee; border: 1px solid #404040;
    border-radius: 3px; font-size: 11px;
    min-height: 26px; max-height: 26px;
    min-width: 80px; max-width: 9999px;
    padding: 0px 4px;
}
QFontComboBox QAbstractItemView {
    background: #1e1e1e; color: #eee;
    selection-background-color: #1a3f6b;
}

QSlider::groove:horizontal { background: #333; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #4a9eff; width: 12px; height: 12px;
    margin: -4px 0; border-radius: 6px;
}
QSlider::sub-page:horizontal { background: #2a5ca8; border-radius: 2px; }

QFrame[frameShape="4"] { color: rgba(255,255,255,0.07); margin: 2px 0; }
"""

_SHAPES: list[tuple[str, str, str]] = [
    ("none",       "∅",  "shape_text_mixin.shape_none"),
    ("rect",       "▭",  "shape_text_mixin.shape_rect"),
    ("rect_round", "▢",  "shape_text_mixin.shape_rect_round"),
    ("ellipse",    "○",  "shape_text_mixin.shape_ellipse"),
    ("line",       "╱",  "shape_text_mixin.shape_line"),
    ("arrow",      "↗",  "shape_text_mixin.shape_arrow"),
    ("cross",      "✕",  "shape_text_mixin.shape_cross"),
    ("triangle",   "△",  "shape_text_mixin.shape_triangle"),
    ("star",       "★",  "shape_text_mixin.shape_star"),
    ("heart",      "♥",  "shape_text_mixin.shape_heart"),
    ("diamond",    "◇",  "shape_text_mixin.shape_diamond"),
    ("pentagon",   "⬠",  "shape_text_mixin.shape_pentagon"),
]

_LINE_STYLES: list[tuple[Qt.PenStyle, str, str]] = [
    (Qt.PenStyle.SolidLine, "━", "shape_text_mixin.line_solid"),
    (Qt.PenStyle.DashLine,  "╌", "shape_text_mixin.line_dash"),
    (Qt.PenStyle.DotLine,   "┄", "shape_text_mixin.line_dot"),
]


def _make_color_btn(color: QColor, tip: str = "") -> QPushButton:
    b = QPushButton()
    b.setObjectName("colbtn")
    if tip:
        b.setToolTip(tip)
    _update_color_btn(b, color)
    return b


def _update_color_btn(btn: QPushButton, color: QColor) -> None:
    btn.setStyleSheet(
        f"QPushButton#colbtn {{ background:{color.name()}; "
        f"border:1px solid #555; border-radius:3px; }}"
        f"QPushButton#colbtn:hover {{ border-color:#aaa; }}"
    )


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    return f


class ShapeTextMixin:
    """도형 · 텍스트 삽입 Mixin. 단독 인스턴스화 불가."""

    # ------------------------------------------------------------------
    # 초기화
    # ------------------------------------------------------------------

    def _init_shape_text(self) -> None:
        self._st_shape_type:     str        = "none"
        self._st_pen_color:      QColor     = QColor(255, 80, 80)
        self._st_line_width:     int        = 10
        self._st_line_style:     Qt.PenStyle = Qt.PenStyle.SolidLine
        self._st_stroke_opacity: int        = 100
        self._st_fill_opacity:   int        = 80

        self._st_font_family:    str    = "맑은 고딕"
        self._st_font_size:      int    = 80
        self._st_text_color:     QColor = QColor(255, 50, 50)
        self._st_bold:           bool   = False
        self._st_italic:         bool   = False

        self._st_overlay:    Optional[QWidget]      = None
        self._st_shape_btns: dict[str, QPushButton] = {}
        self._st_style_btns: dict[Qt.PenStyle, QPushButton] = {}
        self._st_lw_spin:    Optional[QSpinBox]     = None
        self._st_pen_btn:    Optional[QPushButton]  = None
        self._st_stroke_sl:  Optional[QSlider]      = None
        self._st_fill_sl:    Optional[QSlider]      = None

        self._st_font_cb:    Optional[QFontComboBox] = None
        self._st_fs_spin:    Optional[QSpinBox]      = None
        self._st_bold_btn:   Optional[QPushButton]   = None
        self._st_italic_btn: Optional[QPushButton]   = None
        self._st_tcol_btn:   Optional[QPushButton]   = None

    # ------------------------------------------------------------------
    # 툴 진입 / 이탈
    # ------------------------------------------------------------------

    def _on_shape_text_tool_enter(self) -> None:
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)  # type: ignore[attr-defined]
        self._show_shape_text_overlay()


    def _on_shape_text_tool_leave(self) -> None:
        self._cancel_shape_preview_st()
        self._hide_shape_text_overlay()
        self.viewport().unsetCursor()  # type: ignore[attr-defined]


    def _cleanup_shape_text(self) -> None:
        self._cancel_shape_preview_st()
        if self._st_overlay is not None:
            self._st_overlay.setVisible(False)

    # ------------------------------------------------------------------
    # 오버레이 생성
    # ------------------------------------------------------------------

    def _show_shape_text_overlay(self) -> None:
        if self._st_overlay is not None:
            self._st_overlay.setVisible(True)
            self._reposition_shape_text_overlay()
            self._st_overlay.raise_()
            return

        vp = self.viewport()  # type: ignore[attr-defined]
        ov = QWidget(vp)
        ov.setObjectName("st_overlay")
        ov.setStyleSheet(_SS)
        ov.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ov.setCursor(Qt.CursorShape.ArrowCursor)

        ov.setFixedWidth(_OVERLAY_W)

        root = QVBoxLayout(ov)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(4)

        # ── SHAPE 섹션 + 닫기 버튼 (같은 행) ───────────────────
        sec_row = QHBoxLayout()
        sec_row.setContentsMargins(0, 0, 0, 0)
        sec_row.setSpacing(4)
        sec_s = QLabel(t('shape_text_mixin.sec_shape'))
        sec_s.setObjectName("sec")
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(16, 16)
        btn_close.setStyleSheet(
            "QPushButton{background:transparent;color:#4a4a4a;"
            "border:none;font-size:11px;padding:0;}"
            "QPushButton:hover{color:#e35b5b;}"
        )
        btn_close.clicked.connect(self._hide_shape_text_overlay)
        sec_row.addWidget(sec_s)
        sec_row.addStretch()
        sec_row.addWidget(btn_close)
        root.addLayout(sec_row)

        shape_grp = QButtonGroup(ov)
        shape_grp.setExclusive(True)

        for row_shapes in (_SHAPES[:6], _SHAPES[6:]):
            row = QHBoxLayout()
            row.setSpacing(0)
            row.setContentsMargins(0, 0, 0, 0)
            for i, (key, icon, tip_key) in enumerate(row_shapes):
                if i > 0:
                    row.addStretch(1)
                btn = QPushButton(icon)
                btn.setCheckable(True)
                btn.setToolTip(t(tip_key))        
                btn.setChecked(key == self._st_shape_type)
                shape_grp.addButton(btn)
                row.addWidget(btn)
                self._st_shape_btns[key] = btn
                btn.clicked.connect(lambda _, k=key: self._on_shape_btn_clicked(k))
            root.addLayout(row)

        # ── 선 속성 행 ───────────────────────────────────────────────
        prop_row = QHBoxLayout()
        prop_row.setSpacing(4)

        pen_btn = _make_color_btn(self._st_pen_color, t('shape_text_mixin.pen_color_tip'))
        def _pick_pen():
            c = QColorDialog.getColor(
                self._st_pen_color, ov, t('shape_text_mixin.pen_color_dialog')
            )
            if c.isValid():
                self._st_pen_color = c
                _update_color_btn(pen_btn, c)
        pen_btn.clicked.connect(_pick_pen)
        prop_row.addWidget(pen_btn)

        lw_spin = QSpinBox()
        lw_spin.setRange(1, 50)
        lw_spin.setValue(self._st_line_width)
        lw_spin.setSuffix(" px")
        lw_spin.valueChanged.connect(lambda v: setattr(self, '_st_line_width', v))
        prop_row.addWidget(lw_spin)

        style_grp = QButtonGroup(ov)
        style_grp.setExclusive(True)
        for sty, icon, tip_key in _LINE_STYLES:
            sb = QPushButton(icon)
            sb.setCheckable(True)
            sb.setToolTip(t(tip_key)) 
            sb.setChecked(sty == self._st_line_style)
            style_grp.addButton(sb)
            prop_row.addWidget(sb)
            self._st_style_btns[sty] = sb
            sb.clicked.connect(
                lambda _, s=sty: setattr(self, '_st_line_style', s) if _ else None
            )
        prop_row.addStretch()
        root.addLayout(prop_row)

        def _make_opacity_row(
            label: str, init: int, setter
        ) -> tuple[QWidget, QSlider, QLabel]:
            w   = QWidget()
            w.setStyleSheet("background:transparent;")
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(4)

            lbl = QLabel(label)
            lbl.setObjectName("op_label")  

            sl  = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(0, 100)
            sl.setValue(init)  

            val = QLabel(f"{init}%")
            val.setFixedWidth(30)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            sl.valueChanged.connect(lambda v: (setter(v), val.setText(f"{v}%")))
            lay.addWidget(lbl)
            lay.addWidget(sl, 1) 
            lay.addWidget(val)
            return w, sl, val

        stroke_w, stroke_sl, _ = _make_opacity_row(
            t('shape_text_mixin.stroke_opacity'),
            self._st_stroke_opacity,
            lambda v: setattr(self, '_st_stroke_opacity', v)
        )
        fill_w, fill_sl, _ = _make_opacity_row(
            t('shape_text_mixin.fill_opacity'),
            self._st_fill_opacity,
            lambda v: setattr(self, '_st_fill_opacity', v)
        )
        root.addWidget(stroke_w)
        root.addWidget(fill_w)

        self._st_stroke_sl = stroke_sl
        self._st_fill_sl   = fill_sl

        # ── 구분선 ───────────────────────────────────────────────────
        root.addWidget(_sep())

        # ── TEXT 섹션 ────────────────────────────────────────────────
        sec_t = QLabel(t('shape_text_mixin.sec_text'))
        sec_t.setObjectName("sec")
        root.addWidget(sec_t)

        # 폰트 행 1: 폰트 콤보 + 크기
        font_row1 = QHBoxLayout()
        font_row1.setSpacing(4)

        font_cb = QFontComboBox()
        font_cb.setCurrentFont(QFont(self._st_font_family))
        font_cb.setToolTip(t('shape_text_mixin.font_tip'))
        font_cb.currentFontChanged.connect(
            lambda f: setattr(self, '_st_font_family', f.family())
        )

        fs_spin = QSpinBox()
        fs_spin.setRange(8, 2000)
        fs_spin.setValue(self._st_font_size)
        fs_spin.setSuffix(" px")
        fs_spin.setToolTip(t('shape_text_mixin.font_size_tip'))
        fs_spin.valueChanged.connect(lambda v: setattr(self, '_st_font_size', v))

        font_row1.addWidget(font_cb, 1) 
        font_row1.addWidget(fs_spin)
        root.addLayout(font_row1)

        # 폰트 행 2: B / I / 색상 / 삽입 버튼
        font_row2 = QHBoxLayout()
        font_row2.setSpacing(4)

        btn_bold   = QPushButton("B")
        btn_italic = QPushButton("I")
        for b in (btn_bold, btn_italic):
            b.setCheckable(True)
            b.setMinimumWidth(26)
            b.setMaximumWidth(26)
        btn_bold.setChecked(self._st_bold)
        btn_bold.setStyleSheet("""
            QPushButton { font-weight: 700; }
            QPushButton:checked {
                background: #1a3f6b;
                color: #6ab4ff;
                border-color: #2a68b0;
            }
            QPushButton:checked:hover { background: #1e4d82; }
        """)

        btn_italic.setChecked(self._st_italic)
        btn_italic.setStyleSheet("""
            QPushButton { font-style: italic; }
            QPushButton:checked {
                background: #1a3f6b;
                color: #6ab4ff;
                border-color: #2a68b0;
            }
            QPushButton:checked:hover { background: #1e4d82; }
        """)   

        btn_bold.clicked.connect(  lambda c: setattr(self, '_st_bold',   c))
        btn_italic.clicked.connect(lambda c: setattr(self, '_st_italic', c))

        tcol_btn = _make_color_btn(self._st_text_color, t('shape_text_mixin.text_color_tip'))
        def _pick_tcol():
            c = QColorDialog.getColor(
                self._st_text_color, ov, t('shape_text_mixin.text_color_dialog')
            )
            if c.isValid():
                self._st_text_color = c
                _update_color_btn(tcol_btn, c)
        tcol_btn.clicked.connect(_pick_tcol)

        btn_insert = QPushButton(t('shape_text_mixin.btn_insert_text'))
        btn_insert.setObjectName("insert")
        btn_insert.clicked.connect(self._st_insert_text)

        font_row2.addWidget(btn_bold)
        font_row2.addWidget(btn_italic)
        font_row2.addWidget(tcol_btn)
        font_row2.addWidget(btn_insert, 1) 
        root.addLayout(font_row2)

        # ── 참조 저장 ────────────────────────────────────────────────
        self._st_overlay    = ov
        self._st_lw_spin    = lw_spin
        self._st_pen_btn    = pen_btn
        self._st_font_cb    = font_cb
        self._st_fs_spin    = fs_spin
        self._st_bold_btn   = btn_bold
        self._st_italic_btn = btn_italic
        self._st_tcol_btn   = tcol_btn

        apply_hover_opacity(ov, idle=0.18, hover=0.96)

        ov.adjustSize()
        self._reposition_shape_text_overlay()
        ov.show()
        ov.raise_()


    def _hide_shape_text_overlay(self) -> None:
        if self._st_overlay is not None:
            self._st_overlay.setVisible(False)
        # 툴바 버튼 체크 해제
        tb = getattr(self, '_edit_toolbar', None)
        if tb is not None and hasattr(tb, 'uncheck_shapes'):
            tb.uncheck_shapes()
            

    def _reposition_shape_text_overlay(self) -> None:
        ov = self._st_overlay
        if ov is None:
            return
        tb   = getattr(self, '_edit_toolbar', None)
        tb_h = tb.height() if tb is not None else 90
        ov.adjustSize()
        ov.move(10, tb_h + 10)

    # ------------------------------------------------------------------
    # 오버레이 ↔ 아이템 속성 동기화
    # ------------------------------------------------------------------

    def _st_sync_from_shape_item(self, item: ResizableShapeItem) -> None:
        self._st_pen_color  = QColor(item.pen.color())
        self._st_pen_color.setAlpha(255)
        self._st_line_width  = item.pen.width()
        self._st_line_style  = item.pen.style()
        self._st_shape_type  = item.shape_type
        self._st_stroke_opacity = int(item.pen.color().alpha() * 100 / 255)
        self._st_fill_opacity   = int(item.fill_color.alpha() * 100 / 255) \
                                  if item.fill_color else self._st_fill_opacity

        if self._st_pen_btn:
            _update_color_btn(self._st_pen_btn, self._st_pen_color)
        if self._st_lw_spin:
            self._st_lw_spin.blockSignals(True)
            self._st_lw_spin.setValue(self._st_line_width)
            self._st_lw_spin.blockSignals(False)
        for sty, btn in self._st_style_btns.items():
            btn.setChecked(sty == self._st_line_style)
        for key, btn in self._st_shape_btns.items():
            btn.setChecked(key == self._st_shape_type)
        if self._st_stroke_sl:
            self._st_stroke_sl.blockSignals(True)
            self._st_stroke_sl.setValue(self._st_stroke_opacity)
            self._st_stroke_sl.blockSignals(False)
        if self._st_fill_sl:
            self._st_fill_sl.blockSignals(True)
            self._st_fill_sl.setValue(self._st_fill_opacity)
            self._st_fill_sl.blockSignals(False)


    def _st_sync_from_text_item(self, item: TextShapeItem) -> None:
        self._st_text_color  = QColor(item._color)
        self._st_font_family = item._font_family
        self.st_font_size    = item._font_size
        self._st_bold        = item._bold
        self._st_italic      = item._italic

        if self._st_tcol_btn:
            _update_color_btn(self._st_tcol_btn, self._st_text_color)
        if self._st_font_cb:
            self._st_font_cb.blockSignals(True)
            self._st_font_cb.setCurrentFont(QFont(self._st_font_family))
            self._st_font_cb.blockSignals(False)
        if self._st_fs_spin:
            self._st_fs_spin.blockSignals(True)
            self._st_fs_spin.setValue(self._st_font_size)
            self._st_fs_spin.blockSignals(False)
        if self._st_bold_btn:
            self._st_bold_btn.setChecked(self._st_bold)
        if self._st_italic_btn:
            self._st_italic_btn.setChecked(self._st_italic)

    # ------------------------------------------------------------------
    # 도형 삽입
    # ------------------------------------------------------------------

    def _st_add_shape_at_center(self) -> None:
        pi   = self.pixmap_item  # type: ignore[attr-defined]
        base = max(200.0, min(pi.boundingRect().width(),
                              pi.boundingRect().height()) * 0.20) if pi else 200.0
        vp   = self.viewport()  # type: ignore[attr-defined]
        sc   = self.mapToScene(int(vp.width() / 2), int(vp.height() / 2))  # type: ignore[attr-defined]
        rect = QRectF(sc.x() - base / 2, sc.y() - base * 0.375, base, base * 0.75)
        self._st_commit_shape(rect)


    def _st_commit_shape(self, rect: QRectF) -> None:
        stype = self._st_shape_type
        if stype == 'none':
            return

        pen_color = QColor(self._st_pen_color)
        pen_color.setAlpha(int(self._st_stroke_opacity * 255 / 100))

        fill: Optional[QColor] = None
        if stype not in ('line', 'arrow', 'cross'):
            fill = QColor(self._st_pen_color)
            fill.setAlpha(int(self._st_fill_opacity * 255 / 100))

        self._push_undo()  # type: ignore[attr-defined]
        item = ResizableShapeItem(
            stype, rect,
            pen_color  = pen_color,
            fill_color = fill,
            line_width = self._st_line_width,
        )
        item.set_line_style(self._st_line_style)
        item.about_to_change.connect(self._push_undo)       # type: ignore[attr-defined]
        item.properties_needed.connect(self._on_st_item_properties)
        self.graphics_scene.addItem(item)                   # type: ignore[attr-defined]
        self._edit_shapes.append(item)                      # type: ignore[attr-defined]
        item.setSelected(True)

    # ------------------------------------------------------------------
    # 텍스트 삽입
    # ------------------------------------------------------------------

    def _st_insert_text(self) -> None:
        item = TextShapeItem(
            text        = t('shape_text_mixin.default_text'),
            font_family = self._st_font_family,
            font_size   = self._st_font_size,
            color       = QColor(self._st_text_color),
            bold        = self._st_bold,
            italic      = self._st_italic,
        )
        vp = self.viewport()  # type: ignore[attr-defined]
        sc = self.mapToScene(int(vp.width() / 2), int(vp.height() / 2))  # type: ignore[attr-defined]
        br = item._rect
        item.setPos(sc.x() - br.width() / 2, sc.y() - br.height() / 2)

        self._push_undo()  # type: ignore[attr-defined]
        item.about_to_change.connect(self._push_undo)       # type: ignore[attr-defined]
        item.properties_needed.connect(self._on_st_item_properties)
        self.graphics_scene.addItem(item)                   # type: ignore[attr-defined]
        self._edit_shapes.append(item)                      # type: ignore[attr-defined]
        item.setSelected(True)
        self._st_after_insert()
        debug_print("[ShapeText] 텍스트 삽입")

    # ------------------------------------------------------------------
    # 도형 드래그 프리뷰
    # ------------------------------------------------------------------

    def _st_after_insert(self) -> None:
        """삽입 완료 후: shape_type만 none으로 리셋, edit_tool은 shapes 유지."""
        self._cancel_shape_preview_st()
        self._drag_start_scene = None     # type: ignore[attr-defined]
        # _edit_tool을 바꾸지 않는다 → 이벤트 라우팅(shapes 분기) 유지
        # shape_type = 'none' 으로만 리셋 → 캔버스 클릭 시 select처럼 동작
        self._st_reset_shape_selection()  # _st_shape_type='none' + 버튼 업데이트
        self.viewport().unsetCursor()     # type: ignore[attr-defined]
        

    def _deactivate_shapes_tool(self) -> None:
        self._hide_shape_text_overlay()
        tb = getattr(self, '_edit_toolbar', None)
        if tb is not None and hasattr(tb, 'btn_shapes'):
            tb.btn_shapes.blockSignals(True)
            tb.btn_shapes.setChecked(False)
            tb.btn_shapes.blockSignals(False)
        self._on_edit_tool_changed('select')  # type: ignore[attr-defined]


    def _cancel_shape_preview_st(self) -> None:
        item = getattr(self, '_shape_preview_item', None)
        if item is not None:
            self.graphics_scene.removeItem(item)  # type: ignore[attr-defined]
            self._shape_preview_item = None        # type: ignore[attr-defined]


    def _begin_shape_preview_st(self, start: QPointF) -> None:
        pen_color = QColor(self._st_pen_color)
        fill: Optional[QColor] = None
        stype = self._st_shape_type
        if "filled" in stype or stype in ("triangle", "star"):
            fill = QColor(pen_color)
            fill.setAlpha(80)

        from PySide6.QtWidgets import QGraphicsItem
        item = ResizableShapeItem(
            stype, QRectF(start, start),
            pen_color  = pen_color,
            fill_color = fill,
            line_width = self._st_line_width,
        )
        item.set_line_style(self._st_line_style)  # type: ignore[attr-defined]
        item.setOpacity(0.65)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable,    False)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.graphics_scene.addItem(item)  # type: ignore[attr-defined]
        self._shape_preview_item = item    # type: ignore[attr-defined]


    def _handle_shape_text_event(self, event: QMouseEvent, et: QEvent.Type) -> bool:
        if et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                if self._st_shape_type == 'none':
                    return False
                sp = self.mapToScene(event.pos().x(), event.pos().y())  # type: ignore[attr-defined]
                self._drag_start_scene = sp  # type: ignore[attr-defined]
            return self._st_shape_type != 'none'

        if et == QEvent.Type.MouseMove:
            if self._st_shape_type == 'none':
                return False
            if event.buttons() & Qt.MouseButton.LeftButton:
                start = getattr(self, '_drag_start_scene', None)
                if start is None:
                    return True
                sp      = self.mapToScene(event.pos().x(), event.pos().y())  # type: ignore[attr-defined]
                rect    = QRectF(start, sp).normalized()
                preview = getattr(self, '_shape_preview_item', None)
                if preview is None:
                    self._begin_shape_preview_st(start)
                    preview = getattr(self, '_shape_preview_item', None)
                if preview is not None:
                    preview.update_rect(rect)  # type: ignore[attr-defined]
            return True

        if et == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                if self._st_shape_type == 'none':
                    return False
                start = getattr(self, '_drag_start_scene', None)
                sp    = self.mapToScene(event.pos().x(), event.pos().y())  # type: ignore[attr-defined]
                self._cancel_shape_preview_st()
                self._drag_start_scene = None  # type: ignore[attr-defined]

                if start is not None:
                    rect = QRectF(start, sp).normalized()
                    if rect.width() > 8 and rect.height() > 8:
                        self._st_commit_shape(rect)
                    else:
                        self._st_add_shape_at_center()

                self._st_reset_shape_selection()
            return True

        return False

    # ------------------------------------------------------------------
    # 더블클릭 → 속성 편집
    # ------------------------------------------------------------------

    def _on_st_item_properties(self, item: object) -> None:
        if isinstance(item, ResizableShapeItem):
            self._st_sync_from_shape_item(item)
        elif isinstance(item, TextShapeItem):
            self._st_show_text_edit_dialog(item)


    def _st_show_text_edit_dialog(self, item: "TextShapeItem") -> None:
        from utils.dark_dialog import DarkTextEditDialog as _DTE
        from PySide6.QtWidgets import QDialog
        from PySide6.QtGui import QColor

        dlg = _DTE(
            self.window(),                          # type: ignore[attr-defined]
            title=t('shape_text_mixin.text_edit_title'),
            text=item._text,
            font_family=item._font_family,
            font_size=item._font_size,             
            bold=item._bold,
            italic=item._italic,
            color=QColor(item._color),
            text_height=120,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            props = dlg.result_props()
            self._push_undo()                       # type: ignore[attr-defined]
            item.update_properties(**props)
            self._st_sync_from_text_item(item)

    # ------------------------------------------------------------------
    # 유틸
    # ------------------------------------------------------------------

    def _st_to_scene_size(self, viewport_px: int) -> int:
        try:
            sc = self.graphics_scene.views()[0].transform().m11()  # type: ignore[attr-defined]
            if sc > 0:
                return max(8, int(viewport_px / sc))
        except Exception:
            pass
        return viewport_px


    def _st_to_viewport_size(self, scene_px: int) -> int:
        try:
            sc = self.graphics_scene.views()[0].transform().m11()  # type: ignore[attr-defined]
            if sc > 0:
                return max(8, int(scene_px * sc))
        except Exception:
            pass
        return scene_px


    def _st_reset_shape_selection(self) -> None:
        self._st_shape_type = 'none'
        for key, btn in self._st_shape_btns.items():
            btn.setChecked(key == 'none')


    def _on_shape_btn_clicked(self, key: str) -> None:
        self._st_shape_type = key
