# -*- coding: utf-8 -*-
# ui/edit_toolbar.py

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from utils.lang_manager import t


# ── 전역 크기 상수 ───────────────────────────────────────────────────
_H      = 28    # 모든 인터랙티브 위젯 통일 높이 (px)
_H_SQ   = 28    # 정사각형 버튼 (색상 스와치, 잠금 등)
_ROW_H  = 36    # 각 행 전체 높이 여백 포함


class EditToolbar(QWidget):
    """편집 모드 툴바 — 2행 레이아웃

    Row 1: [✂자르기] [⧉복사] [⬛모자이크] │ W [🔒] H [↕리사이즈] │  [✔적용] [✘취소]
    Row 2: [도형▾] [채색▣] │ [선종류▾] [선색▣] [두께 ↕] │ [폰트▾] [크기↕] B I [색▣] [+텍스트] │ [+도형]
    """

    crop_requested   = Signal()
    copy_requested   = Signal()
    mosaic_requested = Signal()
    resize_requested = Signal(int, int)
    apply_requested  = Signal()
    cancel_requested = Signal()
    shape_requested  = Signal(str, QColor, int, int)
    text_requested   = Signal(str, int, QColor, bool, bool)
    tool_changed     = Signal(str)

    SHAPE_ITEMS = [
        ('None',           'edit_toolbar.shape.None'),      
        ('rect',           'edit_toolbar.shape.rect'),
        ('rect_filled',    'edit_toolbar.shape.rect_filled'),
        ('ellipse',        'edit_toolbar.shape.ellipse'),
        ('ellipse_filled', 'edit_toolbar.shape.ellipse_filled'),
        ('line',           'edit_toolbar.shape.line'),
        ('arrow',          'edit_toolbar.shape.arrow'),
        ('cross',          'edit_toolbar.shape.cross'),
        ('triangle',       'edit_toolbar.shape.triangle'),
        ('star',           'edit_toolbar.shape.star'),
    ]

    LINE_STYLES = [
        ('solid',    Qt.PenStyle.SolidLine,   'edit_toolbar.line_style.solid'),
        ('dashed',   Qt.PenStyle.DashLine,    'edit_toolbar.line_style.dashed'),
        ('dotted',   Qt.PenStyle.DotLine,     'edit_toolbar.line_style.dotted'),
        ('dash_dot', Qt.PenStyle.DashDotLine, 'edit_toolbar.line_style.dash_dot'),
    ]

    # ── 스타일시트 — 모든 위젯 높이 _H 로 통일 ───────────────────────
    _SS = f"""
        EditToolbar {{
            background: #141414;
            border-top: 1px solid #3a3a3a;
        }}

        /* ── 기본 버튼 ── */
        QPushButton {{
            background : #282828;
            color      : #d0d0d0;
            border     : 1px solid #484848;
            border-radius : 4px;
            font-size  : 12px;
            min-height : {_H}px;
            max-height : {_H}px;
            padding    : 0px 10px;
        }}
        QPushButton:hover   {{ background: #363636; color: #ffffff; }}
        QPushButton:checked {{ background: #2563a8; color: #ffffff; border-color: #3a82d4; }}
        QPushButton:pressed {{ background: #1e4f87; }}
        QPushButton:disabled{{ color: #555; border-color: #333; }}

        /* ── 스핀박스 ── */
        QSpinBox {{
            background    : #282828;
            color         : #d0d0d0;
            border        : 1px solid #484848;
            border-radius : 3px;
            font-size     : 11px;
            min-height    : {_H}px;
            max-height    : {_H}px;
            padding       : 0px 2px;
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 16px; background: #333; border: none; border-radius: 0px;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background: #444;
        }}

        /* ── 콤보박스 ── */
        QComboBox {{
            background    : #282828;
            color         : #d0d0d0;
            border        : 1px solid #484848;
            border-radius : 3px;
            font-size     : 11px;
            min-height    : {_H}px;
            max-height    : {_H}px;
            padding       : 0px 6px;
        }}
        QComboBox::drop-down {{
            width: 18px; border: none;
        }}
        QComboBox QAbstractItemView {{
            background : #202020;
            color      : #d0d0d0;
            selection-background-color: #2563a8;
            border: 1px solid #484848;
        }}

        /* ── 폰트 콤보박스 ── */
        QFontComboBox {{
            background    : #282828;
            color         : #d0d0d0;
            border        : 1px solid #484848;
            border-radius : 3px;
            font-size     : 11px;
            min-height    : {_H}px;
            max-height    : {_H}px;
            padding       : 0px 4px;
        }}
        QFontComboBox QAbstractItemView {{
            background : #202020;
            color      : #d0d0d0;
            selection-background-color: #2563a8;
        }}

        /* ── 라벨 ── */
        QLabel {{
            color      : #909090;
            font-size  : 11px;
            background : transparent;
            min-height : {_H}px;
            max-height : {_H}px;
            padding    : 0px 2px;
        }}

        /* ── 구분선 ── */
        QFrame[frameShape="5"] {{
            color: #3a3a3a;
            min-height: {_H}px;
            max-height: {_H}px;
        }}
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(self._SS)

        self._shape_color   = QColor(255, 80, 80)
        self._line_color    = QColor(255, 80, 80)
        self._text_color    = QColor(255, 50, 50)
        self._aspect_locked = True
        self._aspect_ratio  = 1.0
        self._updating_size = False

        self._build_ui()

    # ────────────────────────────────────────────────────────────────
    # UI 구성
    # ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 5, 8, 5)
        root.setSpacing(4)
        root.addLayout(self._row1())
        root.addWidget(self._hline())
        root.addLayout(self._row2())

    # ── Row 1 ────────────────────────────────────────────────────────

    def _row1(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        row.setContentsMargins(0, 0, 0, 0)

        # 영역 작업 (exclusive checkable)
        self.btn_crop   = self._mk_btn(t('edit_toolbar.crop'),   chk=True, tip='Drag to crop')
        self.btn_copy   = self._mk_btn(t('edit_toolbar.copy'),     chk=True, tip='Drag to copy to clipboard')
        self.btn_mosaic = self._mk_btn(t('edit_toolbar.mosaic'), chk=True, tip='Drag to apply mosaic')

        self._area_grp = QButtonGroup(self)
        self._area_grp.setExclusive(True)
        for b in (self.btn_crop, self.btn_copy, self.btn_mosaic):
            self._area_grp.addButton(b)

        self.btn_crop.clicked.connect(  lambda: self.tool_changed.emit('crop_select'))
        self.btn_copy.clicked.connect(  lambda: self.tool_changed.emit('copy_select'))
        self.btn_mosaic.clicked.connect(lambda: self.tool_changed.emit('mosaic_select'))

        row.addWidget(self.btn_crop)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_mosaic)
        row.addWidget(self._vsep())

        # 리사이즈
        row.addWidget(self._lbl('W'))

        self.spin_w = self._mk_spin(1, 32000, suffix='px', w=72)
        self.spin_w.valueChanged.connect(self._on_width_changed)

        self.btn_lock = self._mk_sq_btn('🔒', chk=True, checked=True, tip='Lock aspect ratio')
        self.btn_lock.clicked.connect(self._on_lock_clicked)

        row.addWidget(self.spin_w)
        row.addWidget(self.btn_lock)
        row.addWidget(self._lbl('H'))

        self.spin_h = self._mk_spin(1, 32000, suffix='px', w=72)
        self.spin_h.valueChanged.connect(self._on_height_changed)

        self.btn_resize = self._mk_btn(t('edit_toolbar.resize_apply'))
        self.btn_resize.clicked.connect(
            lambda: self.resize_requested.emit(self.spin_w.value(), self.spin_h.value())
        )

        row.addWidget(self.spin_h)
        row.addWidget(self.btn_resize)
        row.addStretch(1)

        # 적용 / 취소
        self.btn_apply  = self._mk_btn(t('edit_toolbar.apply'))
        self.btn_cancel = self._mk_btn(t('edit_toolbar.cancel'))
        self.btn_apply.setStyleSheet(
            f'QPushButton{{background:#1e5c1e;color:#fff;border-color:#2e7d2e;'
            f'min-height:{_H}px;max-height:{_H}px;}}'
            f'QPushButton:hover{{background:#276f27;}}'
        )
        self.btn_cancel.setStyleSheet(
            f'QPushButton{{background:#5c1e1e;color:#fff;border-color:#7d2e2e;'
            f'min-height:{_H}px;max-height:{_H}px;}}'
            f'QPushButton:hover{{background:#6f2727;}}'
        )
        self.btn_apply.clicked.connect(self.apply_requested)
        self.btn_cancel.clicked.connect(self.cancel_requested)
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_cancel)
        return row

    # ── Row 2 ────────────────────────────────────────────────────────

    def _row2(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        row.setContentsMargins(0, 0, 0, 0)

        # ── 도형 선택 + 채색 ───────────────────────────────────────
        self.shape_combo = QComboBox()
        self.shape_combo.setFixedSize(128, _H)
        for _, key in self.SHAPE_ITEMS:
            self.shape_combo.addItem(t(key))

        self.shape_combo.setCurrentIndex(0)
        self.shape_combo.currentIndexChanged.connect(self._on_shape_selected)

        self.btn_fill = self._mk_color_btn(self._shape_color, tip='Fill color')
        self.btn_fill.clicked.connect(self._pick_fill)

        row.addWidget(self.shape_combo)
        row.addWidget(self.btn_fill)
        row.addWidget(self._vsep())

        # ── 선 종류 + 선 색 + 두께 ────────────────────────────────
        self.line_combo = QComboBox()
        self.line_combo.setFixedSize(88, _H)
        for _, _, key in self.LINE_STYLES:
            self.line_combo.addItem(t(key))

        self.btn_line_color = self._mk_color_btn(self._line_color, tip='Line color')
        self.btn_line_color.clicked.connect(self._pick_line)

        self.spin_lw = self._mk_spin(1, 50, val=10, suffix='px', w=58)
        self.spin_lw.setToolTip('Line thickness')

        row.addWidget(self.line_combo)
        row.addWidget(self.btn_line_color)
        row.addWidget(self._lbl(t('edit_toolbar.thickness_label')))
        row.addWidget(self.spin_lw)
        row.addWidget(self._vsep())

        # ── 텍스트 삽입 버튼 (단순화) ─────────────────────────────
        # 폰트/사이즈/Bold/Italic 위젯 완전 제거
        #    설정은 클릭 시 나타나는 다이얼로그에서 처리
        self.btn_add_text = self._mk_btn(t('edit_toolbar.text_insert'))
        self.btn_add_text.setToolTip('클릭 후 이미지를 클릭하면 텍스트 삽입')
        self.btn_add_text.setCheckable(False)  
        self.btn_add_text.clicked.connect(self._on_text_mode)
        row.addWidget(self.btn_add_text)
        row.addWidget(self._vsep())

        # ── 도형 삽입 버튼 ────────────────────────────────────────
        # 드래그 방식으로 전환 — [도형삽입] 버튼 제거
        #    shape_combo에서 선택 후 이미지에 직접 드래그
        #    콤보박스 선택 변경 시 자동으로 드래그 모드 활성화
        self.shape_combo.currentIndexChanged.connect(self._on_shape_selected)

        row.addStretch(1)

        hint = QLabel(t('edit_toolbar.hint'))
        hint.setStyleSheet('color:#484848;font-size:10px;background:transparent;')
        row.addWidget(hint)
        return row


    # ────────────────────────────────────────────────────────────────
    # 색상 선택
    # ────────────────────────────────────────────────────────────────

    def _pick_fill(self) -> None:
        c = QColorDialog.getColor(self._shape_color, self, t('edit_toolbar.color_dlg_shape'))
        if c.isValid():
            self._shape_color = c
            self._set_color_btn(self.btn_fill, c)


    def _pick_line(self) -> None:
        c = QColorDialog.getColor(self._line_color, self, t('edit_toolbar.color_dlg_line'))
        if c.isValid():
            self._line_color = c
            self._set_color_btn(self.btn_line_color, c)


    # ────────────────────────────────────────────────────────────────
    # 리사이즈 비율 잠금
    # ────────────────────────────────────────────────────────────────

    def set_image_size(self, w: int, h: int) -> None:
        for sp in (self.spin_w, self.spin_h):
            sp.blockSignals(True)
        self.spin_w.setValue(w); self.spin_h.setValue(h)
        for sp in (self.spin_w, self.spin_h):
            sp.blockSignals(False)
        self._aspect_ratio = float(w) / float(h) if h > 0 else 1.0


    def _on_lock_clicked(self, checked: bool) -> None:
        self._aspect_locked = checked
        self.btn_lock.setText('🔒' if checked else '🔓')
        if checked and self.spin_h.value() > 0:
            self._aspect_ratio = float(self.spin_w.value()) / float(self.spin_h.value())


    def _on_width_changed(self, v: int) -> None:
        if not self._aspect_locked or self._updating_size:
            return
        self._updating_size = True
        try:
            self.spin_h.blockSignals(True)
            self.spin_h.setValue(max(1, int(round(v / self._aspect_ratio))))
            self.spin_h.blockSignals(False)
        finally:
            self._updating_size = False


    def _on_height_changed(self, v: int) -> None:
        if not self._aspect_locked or self._updating_size:
            return
        self._updating_size = True
        try:
            self.spin_w.blockSignals(True)
            self.spin_w.setValue(max(1, int(round(v * self._aspect_ratio))))
            self.spin_w.blockSignals(False)
        finally:
            self._updating_size = False


    # ────────────────────────────────────────────────────────────────
    # 외부 동기화
    # ────────────────────────────────────────────────────────────────

    def reset_area_buttons(self) -> None:
        """자르기/복사/모자이크 + 텍스트 버튼 체크 해제"""
        self._area_grp.setExclusive(False)
        for b in (self.btn_crop, self.btn_copy, self.btn_mosaic):
            b.setChecked(False)
        self._area_grp.setExclusive(True)
        self.btn_add_text.setChecked(False) 

        # 도형 콤보박스 플레이스홀더로 복귀 (시그널 차단 후 설정)
        self.shape_combo.blockSignals(True)
        self.shape_combo.setCurrentIndex(0)
        self.shape_combo.blockSignals(False)


    def sync_from_shape(self, color: QColor, line_width: int) -> None:
        self._line_color = QColor(color)
        self._set_color_btn(self.btn_line_color, color)
        self.spin_lw.blockSignals(True)
        self.spin_lw.setValue(line_width)
        self.spin_lw.blockSignals(False)

    sync_from_shape_item = sync_from_shape


    def sync_from_text(self, item) -> None:
        # 툴바에 텍스트 위젯이 없으므로 아무것도 할 필요 없음
        pass

    sync_from_text_item = sync_from_text   # 호환성 유지


    # ────────────────────────────────────────────────────────────────
    # 팩토리 헬퍼 — 모든 위젯 높이 _H 강제 고정
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _mk_btn(label: str = '', *, chk: bool = False, tip: str = '') -> QPushButton:
        b = QPushButton(label)
        b.setCheckable(chk)
        b.setFixedHeight(_H)
        if tip:
            b.setToolTip(tip)
        return b


    @staticmethod
    def _mk_sq_btn(label: str = '', *, chk: bool = False,
                   checked: bool = False, tip: str = '') -> QPushButton:
        b = QPushButton(label)
        b.setCheckable(chk)
        b.setChecked(checked)
        b.setFixedSize(_H_SQ, _H_SQ)
        if tip:
            b.setToolTip(tip)
        return b


    def _mk_color_btn(self, color: QColor, *, tip: str = '') -> QPushButton:
        b = QPushButton()
        b.setFixedSize(_H_SQ, _H_SQ)
        if tip:
            b.setToolTip(tip)
        self._set_color_btn(b, color)
        return b


    @staticmethod
    def _mk_spin(lo: int, hi: int, *, val: int = 0,
                 suffix: str = '', w: int = 64) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        if val:
            s.setValue(val)
        if suffix:
            s.setSuffix(suffix)
        s.setFixedSize(w, _H)
        return s


    @staticmethod
    def _lbl(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setFixedHeight(_H)
        return lb


    @staticmethod
    def _set_color_btn(btn: QPushButton, color: QColor) -> None:
        px = QPixmap(14, 14)
        px.fill(color)
        btn.setIcon(QIcon(px))


    @staticmethod
    def _vsep() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setFrameShadow(QFrame.Shadow.Sunken)
        f.setFixedSize(6, _H)
        return f


    @staticmethod
    def _hline() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setFrameShadow(QFrame.Shadow.Sunken)
        f.setFixedHeight(1)
        return f
    

    def _on_shape_selected(self, idx: int) -> None:
        if idx < 0:
            return
        stype = self.SHAPE_ITEMS[idx][0]

        # 플레이스홀더(None) 선택 시 아무 동작 없음
        if stype is None:
            return

        self.btn_add_text.setChecked(False)
        self.tool_changed.emit(f'shape:{stype}')


    def _on_text_mode(self) -> None:
        """텍스트 버튼 클릭 → text 모드 신호만 보냄 (1회성)"""
        self.tool_changed.emit('text')
        # 버튼 상태 유지 없음 — mixin이 삽입 완료 후 즉시 select로 복귀
        

    def current_pen_color(self) -> QColor:
        return QColor(self._line_color)


    def current_fill_color(self) -> Optional[QColor]:
        """채색 도형일 때만 fill 색 반환"""
        idx   = self.shape_combo.currentIndex()
        stype = self.SHAPE_ITEMS[idx][0] if idx >= 0 else ''
        if 'filled' in stype or stype in ('triangle', 'star'):
            c = QColor(self._shape_color)
            c.setAlpha(100)
            return c
        return None


    def current_line_width(self) -> int:
        return self.spin_lw.value()


    def current_line_style(self) -> int:
        idx = self.line_combo.currentIndex()
        return self.LINE_STYLES[idx][1].value if idx >= 0 else 1