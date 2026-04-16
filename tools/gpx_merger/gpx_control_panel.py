# -*- coding: utf-8 -*-
# tools\gpx_merger\gpx_control_panel.py

"""GPX 유틸리티 좌측 컨트롤 패널"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore    import Qt, Signal
from PySide6.QtGui import (
    QColor, QDragEnterEvent, QDropEvent, QIcon, QPainter, QPixmap
)
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QRadioButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget, QDialog
)
from .gpx_logic import MergeOptions
from utils.lang_manager import t
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox


SCROLLBAR_STYLE = """
    QScrollArea {
        background-color: #202020;
        border: none;
        border-top: 1px solid rgba(255, 255, 255, 0.06);
    }

    /* ── 수평 ── */
    QScrollBar:horizontal {
        height: 6px;
        background: transparent;
        margin: 0px;
    }
    QScrollBar::handle:horizontal {
        background: rgba(255, 255, 255, 0.18);
        border-radius: 3px;
        min-width: 30px;
    }
    QScrollBar::handle:horizontal:hover   { background: rgba(255, 255, 255, 0.30); }
    QScrollBar::handle:horizontal:pressed { background: rgba( 74, 158, 255, 0.60); }
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal { width: 0px; }
    QScrollBar::add-page:horizontal,
    QScrollBar::sub-page:horizontal { background: none; }

    /* ── 수직 ── */
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
    QScrollBar::handle:vertical:hover   { background: rgba(255, 255, 255, 0.30); }
    QScrollBar::handle:vertical:pressed { background: rgba( 74, 158, 255, 0.60); }
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical { height: 0px; }
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical { background: none; }
"""

# ── UTC -12:00 ~ +14:00, 15분 단위 ─────────────────
_HINTS = {
    0: 'GMT',   60: 'CET',  120: 'EET',
    330: 'IST',  345: 'NPT',  480: 'CST/HKT',
    525: 'ACST', 540: 'KST/JST', 570: 'ACST',
    600: 'AEST', 765: 'NZST',
-300: 'EST',  -360: 'CST', -420: 'MST',
-480: 'PST',  -600: 'HST',
}


class _Sep(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setStyleSheet(
            'background: rgba(255,255,255,0.07); max-height:1px;'
            ' border:none; margin: 3px 0;')


class GpxControlPanel(QWidget):
    """
    좌측 컨트롤 패널.

    Signals
    -------
    files_changed(list[Path])     파일 목록 변경
    operation_changed(str)        'merge' | 'split'
    split_mode_changed(str)       'gap'|'date'|'dist'|'points'|'manual'
    manual_split_toggled(bool)    수동 분할 모드 on/off
    gaps_apply_requested()        감지 갭으로 분할 지점 일괄 설정
    options_changed()             옵션 변경 (미리보기 재계산 필요)
    preview_requested()
    save_requested()
    clear_splits_requested()
    """

    files_changed          = Signal(list) 
    operation_changed      = Signal(str)
    split_mode_changed     = Signal(str)
    manual_split_toggled   = Signal(bool)
    gaps_apply_requested   = Signal()
    options_changed        = Signal()
    preview_requested      = Signal()
    save_requested         = Signal()
    clear_splits_requested = Signal()

    # ── split_mode 라디오버튼 → 문자열 키 매핑
    _SPLIT_KEYS = ('gap', 'date', 'dist', 'points', 'manual')


    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._files:      List[Path]     = []
        self._output_dir: Optional[Path] = None
        self.setAcceptDrops(True)
        self.setMinimumWidth(265)
        self.setMaximumWidth(320)
        self.setStyleSheet("""
            /* ── 기본 ── */
            QWidget { background: #1e1e1e; color: #d4d4d4; font-size: 12px; }

            /* ── GroupBox ── */
            QGroupBox {
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 6px;
                font-weight: bold;
                font-size: 11px;
                color: #9cdcfe;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }

            /* ── 라벨 ── */
            QLabel { color: #c8c8c8; background: transparent; }

            /* ── 입력 위젯 ── */
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background: #2d2d2d;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                padding: 3px 6px;
                color: #d4d4d4;
            }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border-color: #0e7fd4;
            }
            QSpinBox, QDoubleSpinBox { padding-right: 18px; }
            QComboBox::drop-down     { border: none; width: 18px; }
            QComboBox::down-arrow {
                border-left:  4px solid transparent;
                border-right: 4px solid transparent;
                border-top:   5px solid #888;
            }

            /* ── 버튼 ── */
            QPushButton {
                background: #2d2d2d;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 4px 10px;
                color: #d4d4d4;
            }
            QPushButton:hover    { background: #3a3a3a; border-color: #555; }
            QPushButton:pressed  { background: #1a1a1a; }
            QPushButton:disabled { color: #555; border-color: #2a2a2a; background: #1e1e1e; }
            QPushButton:checked  { background: #1a5a2a; border-color: #2d9e4a; color: #fff; }
            QPushButton:checked:hover { background: #1f7032; }

            /* ── 체크박스 / 라디오 ── */
            QRadioButton, QCheckBox { color: #c8d0d8; spacing: 6px; }
            QRadioButton::indicator, QCheckBox::indicator {
                width: 14px; height: 14px;
                border: 2px solid #555;
                border-radius: 7px;
                background: #2d2d2d;
            }
            QCheckBox::indicator { border-radius: 3px; }
            QRadioButton::indicator:checked { background: #0e7fd4; border-color: #4a9eff; }
            QCheckBox::indicator:checked    { background: #0e7fd4; border-color: #4a9eff; }
            QRadioButton::indicator:hover,
            QCheckBox::indicator:hover      { border-color: #8ab4f8; }

            /* ── 리스트 ── */
            QListWidget {
                background: #1a1a1a;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                color: #d4d4d4;
            }
            QListWidget::item            { padding: 2px 4px; }
            QListWidget::item:selected   { background: #0e4a7a; color: #fff; }
            QListWidget::item:hover      { background: #2a2a2a; }
        """)

        self._file_colors: dict[str, List[str]] = {}
        
        self._build_ui()
        self._connect_signals()
        self._refresh_op_visibility()

    # ────────────────────────────────────────────────────────
    # UI 구성
    # ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # 스크롤 영역 (옵션 많아도 화면 벗어나지 않음)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(SCROLLBAR_STYLE)
        inner     = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.setSpacing(5)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        self._build_file_section(inner_lay)
        inner_lay.addWidget(_Sep())
        self._build_display_section(inner_lay)
        inner_lay.addWidget(_Sep())
        self._build_operation_section(inner_lay)
        self._build_merge_options(inner_lay)
        self._build_split_options(inner_lay)
        self._build_gap_detection(inner_lay)
        self._build_quality_filter(inner_lay)
        inner_lay.addWidget(_Sep())
        self._build_output_section(inner_lay)
        inner_lay.addStretch(1)

        self._build_action_buttons(root)

    # ── 파일 목록 ────────────────────────────────────────────

    def _build_file_section(self, lay: QVBoxLayout) -> None:
        grp = QGroupBox(t('gpx_merger.panel.file_section'))
        g_lay = QVBoxLayout(grp)
        g_lay.setSpacing(4)

        self._file_list = QListWidget()
        self._file_list.setMaximumHeight(120)
        self._file_list.setToolTip(t('gpx_merger.panel.file_drop_tip'))
        g_lay.addWidget(self._file_list)

        btn_row = QHBoxLayout()
        self._btn_add    = QPushButton(t('gpx_merger.panel.btn_add'))
        self._btn_remove = QPushButton(t('gpx_merger.panel.btn_remove'))
        self._btn_clear  = QPushButton(t('gpx_merger.panel.btn_clear'))
        self._btn_up     = QPushButton(t('gpx_merger.panel.btn_up'))
        self._btn_down   = QPushButton(t('gpx_merger.panel.btn_down'))
        self._btn_add.setToolTip(t('gpx_merger.panel.btn_add_tip'))
        self._btn_remove.setToolTip(t('gpx_merger.panel.btn_remove_tip'))
        self._btn_clear.setToolTip(t('gpx_merger.panel.btn_clear_tip'))
        self._btn_up.setToolTip(t('gpx_merger.panel.btn_up_tip'))
        self._btn_down.setToolTip(t('gpx_merger.panel.btn_down_tip'))
        self._btn_clear.setFixedWidth(28) 
        for b in (self._btn_up, self._btn_down):
            b.setFixedWidth(30)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_remove)
        btn_row.addWidget(self._btn_clear)
        btn_row.addWidget(self._btn_up)
        btn_row.addWidget(self._btn_down)
        g_lay.addLayout(btn_row)

        self._lbl_file_info = QLabel(t('gpx_merger.panel.file_none'))
        self._lbl_file_info.setStyleSheet(
            'color: #9cdcfe; font-size: 10px; font-weight: 600; background: transparent;')
        g_lay.addWidget(self._lbl_file_info)

        lay.addWidget(grp)

    # ── 작업 선택 ─────────────────────────────────────────────

    def _build_operation_section(self, lay: QVBoxLayout) -> None:
        grp = QGroupBox(t('gpx_merger.panel.op_section'))
        g_lay = QVBoxLayout(grp)

        self._rb_merge = QRadioButton(t('gpx_merger.panel.op_merge'))
        self._rb_split = QRadioButton(t('gpx_merger.panel.op_split'))
        self._rb_merge.setChecked(True)

        self._op_group = QButtonGroup(self)
        self._op_group.addButton(self._rb_merge, 0)
        self._op_group.addButton(self._rb_split, 1)

        g_lay.addWidget(self._rb_merge)
        g_lay.addWidget(self._rb_split)
        lay.addWidget(grp)

    # ── 합치기 옵션 ──────────────────────────────────────────

    def _build_merge_options(self, lay: QVBoxLayout) -> None:
        self._grp_merge = QGroupBox(t('gpx_merger.panel.merge_section'))
        m_lay = QVBoxLayout(self._grp_merge)
        m_lay.setSpacing(4)

        self._cb_sort_time = QCheckBox(t('gpx_merger.panel.cb_sort_time'))
        self._cb_sort_time.setChecked(True)
        m_lay.addWidget(self._cb_sort_time)

        m_lay.addWidget(QLabel(t('gpx_merger.panel.merge_as_label')))
        self._rb_as_single = QRadioButton(t('gpx_merger.panel.rb_single_track'))
        self._rb_as_multi  = QRadioButton(t('gpx_merger.panel.rb_multi_track'))
        self._rb_as_seg    = QRadioButton(t('gpx_merger.panel.rb_seg'))
        self._rb_as_single.setChecked(True)

        self._merge_as_group = QButtonGroup(self)
        self._merge_as_group.addButton(self._rb_as_single, 0)
        self._merge_as_group.addButton(self._rb_as_multi,  1)
        self._merge_as_group.addButton(self._rb_as_seg,    2)

        for rb in (self._rb_as_single, self._rb_as_multi, self._rb_as_seg):
            m_lay.addWidget(rb)

        m_lay.addWidget(_Sep())
        self._cb_merge_wpt = QCheckBox(t('gpx_merger.panel.cb_merge_wpt'))
        self._cb_merge_wpt.setChecked(True)
        self._cb_dedup_wpt = QCheckBox(t('gpx_merger.panel.cb_dedup_wpt'))
        self._cb_dedup_wpt.setChecked(True)
        m_lay.addWidget(self._cb_merge_wpt)
        m_lay.addWidget(self._cb_dedup_wpt)

        lay.addWidget(self._grp_merge)

    # ── 쪼개기 옵션 ──────────────────────────────────────────

    def _build_split_options(self, lay: QVBoxLayout) -> None:
        self._grp_split  = QGroupBox(t('gpx_merger.panel.split_section'))
        s_lay = QVBoxLayout(self._grp_split)
        s_lay.setSpacing(4)

        self._rb_gap     = QRadioButton(t('gpx_merger.panel.rb_gap'))
        self._rb_date    = QRadioButton(t('gpx_merger.panel.rb_date'))
        self._rb_dist    = QRadioButton(t('gpx_merger.panel.rb_dist'))
        self._rb_points  = QRadioButton(t('gpx_merger.panel.rb_points'))
        self._rb_manual  = QRadioButton(t('gpx_merger.panel.rb_manual'))
        self._rb_gap.setChecked(True)

        self._split_group = QButtonGroup(self)
        for i, rb in enumerate((self._rb_gap, self._rb_date,
                                 self._rb_dist, self._rb_points,
                                 self._rb_manual)):
            self._split_group.addButton(rb, i)
            s_lay.addWidget(rb)

        s_lay.addWidget(_Sep())

        # 시간 갭 값
        gap_row = QHBoxLayout()
        gap_row.addWidget(QLabel(t('gpx_merger.panel.gap_label')))
        self._spin_gap = QDoubleSpinBox()
        self._spin_gap.setRange(1.0, 1440.0)
        self._spin_gap.setValue(30.0)
        self._spin_gap.setSuffix(t('gpx_merger.panel.gap_suffix'))
        self._spin_gap.setDecimals(0)
        gap_row.addWidget(self._spin_gap)
        s_lay.addLayout(gap_row)

        # 거리 값
        dist_row = QHBoxLayout()
        dist_row.addWidget(QLabel(t('gpx_merger.panel.dist_label')))
        self._spin_dist = QDoubleSpinBox()
        self._spin_dist.setRange(0.1, 10_000.0)
        self._spin_dist.setValue(10.0)
        self._spin_dist.setSuffix(t('gpx_merger.panel.dist_suffix'))
        dist_row.addWidget(self._spin_dist)
        s_lay.addLayout(dist_row)

        # 포인트 수 값
        cnt_row = QHBoxLayout()
        cnt_row.addWidget(QLabel(t('gpx_merger.panel.points_label')))
        self._spin_points = QSpinBox()
        self._spin_points.setRange(10, 1_000_000)
        self._spin_points.setValue(1000)
        self._spin_points.setSuffix(t('gpx_merger.panel.points_suffix'))
        cnt_row.addWidget(self._spin_points)
        s_lay.addLayout(cnt_row)

        s_lay.addWidget(_Sep())

        # 수동 분할 전용 버튼
        self._btn_manual_mode  = QPushButton(t('gpx_merger.panel.btn_manual_mode'))
        self._btn_manual_mode.setCheckable(True)
        self._btn_manual_mode.setStyleSheet("""
            QPushButton {
                text-align: left; padding: 4px 8px;
                background: #2d2d2d; border: 1px solid #3a3a3a;
                border-radius: 4px; color: #d4d4d4;
            }
            QPushButton:hover   { background: #3a3a3a; border-color: #555; }
            QPushButton:checked {
                background: #1a5a2a; border-color: #2d9e4a; color: #fff;
            }
            QPushButton:checked:hover { background: #1f7032; }
        """)
        self._btn_clear_splits = QPushButton(t('gpx_merger.panel.btn_clear_splits'))
        self._lbl_split_count  = QLabel(t('gpx_merger.panel.split_count', n=0))
        self._lbl_split_count.setStyleSheet('color:#888;font-size:10px;')

        s_lay.addWidget(self._btn_manual_mode)
        s_lay.addWidget(self._btn_clear_splits)
        s_lay.addWidget(self._lbl_split_count)

        self._grp_split.setVisible(False)
        lay.addWidget(self._grp_split)

    # ── 갭 감지 결과 ─────────────────────────────────────────

    def _build_gap_detection(self, lay: QVBoxLayout) -> None:
        self._grp_gaps = QGroupBox(t('gpx_merger.panel.gap_section'))
        g_lay = QVBoxLayout(self._grp_gaps)
        g_lay.setSpacing(4)

        self._gap_list = QListWidget()
        self._gap_list.setMaximumHeight(90)
        self._gap_list.setToolTip(t('gpx_merger.panel.gap_list_tip'))
        g_lay.addWidget(self._gap_list)

        self._btn_apply_gaps = QPushButton(t('gpx_merger.panel.btn_apply_gaps'))
        g_lay.addWidget(self._btn_apply_gaps)

        self._grp_gaps.setVisible(False)
        lay.addWidget(self._grp_gaps)

    # ── 품질 필터 ────────────────────────────────────────────

    def _build_quality_filter(self, lay: QVBoxLayout) -> None:
        grp = QGroupBox(t('gpx_merger.panel.quality_section'))
        f_lay = QVBoxLayout(grp)
        f_lay.setSpacing(4)

        self._cb_rm_anomaly = QCheckBox(t('gpx_merger.panel.cb_rm_anomaly'))
        self._cb_rm_anomaly.setToolTip(t('gpx_merger.panel.cb_rm_anomaly_tip'))
        f_lay.addWidget(self._cb_rm_anomaly)

        spd_row = QHBoxLayout()
        spd_row.addWidget(QLabel(t('gpx_merger.panel.max_spd_label')))
        self._spin_maxspd = QDoubleSpinBox()
        self._spin_maxspd.setRange(10.0, 3000.0)
        self._spin_maxspd.setValue(300.0)
        self._spin_maxspd.setSuffix(t('gpx_merger.panel.max_spd_suffix'))
        self._spin_maxspd.setEnabled(False)
        spd_row.addWidget(self._spin_maxspd)
        f_lay.addLayout(spd_row)

        self._cb_smooth_ele = QCheckBox(t('gpx_merger.panel.cb_smooth_ele'))
        f_lay.addWidget(self._cb_smooth_ele)

        smooth_row = QHBoxLayout()
        smooth_row.addWidget(QLabel(t('gpx_merger.panel.smooth_label')))
        self._spin_smooth = QSpinBox()
        self._spin_smooth.setRange(3, 31)
        self._spin_smooth.setSingleStep(2)
        self._spin_smooth.setValue(5)
        self._spin_smooth.setSuffix(t('gpx_merger.panel.smooth_suffix'))
        self._spin_smooth.setEnabled(False)
        smooth_row.addWidget(self._spin_smooth)
        f_lay.addLayout(smooth_row)

        lay.addWidget(grp)

    # ── 출력 설정 ────────────────────────────────────────────

    def _build_display_section(self, lay: QVBoxLayout) -> None:
        grp = QGroupBox(t('gpx_merger.panel.display_section'))
        g_lay = QHBoxLayout(grp)
        g_lay.setContentsMargins(8, 4, 8, 4)

        g_lay.addWidget(QLabel(t('gpx_merger.panel.timezone_label')))

        self._cmb_utc = QComboBox()
        self._cmb_utc.setToolTip(t('gpx_merger.panel.timezone_tip'))
        self._cmb_utc.setFixedWidth(180)

        _default_idx = 0
        for i, mins in enumerate(range(-720, 841, 15)):
            sign    = '+' if mins >= 0 else '-'
            abs_m   = abs(mins)
            h, m    = abs_m // 60, abs_m % 60
            label   = f'UTC{sign}{h}:{m:02d}'
            hint    = _HINTS.get(mins)
            if hint:
                label += f'  ({hint})'
            self._cmb_utc.addItem(label, mins)  
            if mins == 540:           
                _default_idx = i
        self._cmb_utc.setCurrentIndex(_default_idx)

        g_lay.addWidget(self._cmb_utc)
        g_lay.addStretch()
        lay.addWidget(grp)


    def _build_output_section(self, lay: QVBoxLayout) -> None:
        grp = QGroupBox(t('gpx_merger.panel.output_section'))
        o_lay = QVBoxLayout(grp)
        o_lay.setSpacing(4)

        dir_row = QHBoxLayout()
        self._lbl_outdir = QLabel(t('gpx_merger.panel.outdir_default'))
        self._lbl_outdir.setWordWrap(True)
        self._lbl_outdir.setStyleSheet(
            'color: #888; font-size: 10px; background: transparent;')
        self._btn_outdir = QPushButton(t('gpx_merger.panel.btn_outdir'))
        self._btn_outdir.setFixedWidth(60)
        dir_row.addWidget(self._lbl_outdir, 1)
        dir_row.addWidget(self._btn_outdir)
        o_lay.addLayout(dir_row)

        # 출력 파일명 템플릿
        o_lay.addWidget(QLabel(t('gpx_merger.panel.tpl_label')))
        tpl_row = QHBoxLayout()
        self._lbl_tpl_hint = QLabel(t('gpx_merger.panel.tpl_hint'))
        self._lbl_tpl_hint.setStyleSheet(
            'color: #666; font-size: 10px; background: transparent;')
        tpl_row.addWidget(self._lbl_tpl_hint)
        o_lay.addLayout(tpl_row)

        # 덮어쓰기 경고
        self._cb_overwrite = QCheckBox(t('gpx_merger.panel.cb_overwrite'))
        self._cb_overwrite.setStyleSheet(
            'QCheckBox { color: #E6A817; font-size: 11px; }')
        o_lay.addWidget(self._cb_overwrite)

        lay.addWidget(grp)

    # ── 액션 버튼 ────────────────────────────────────────────

    def _build_action_buttons(self, root: QVBoxLayout) -> None:
        self._btn_preview = QPushButton(t('gpx_merger.panel.btn_preview'))
        self._btn_save    = QPushButton(t('gpx_merger.panel.btn_save'))
        self._btn_save.setStyleSheet("""
            QPushButton {
                background: #1a6b2f; color: #fff;
                font-weight: bold; border: none;
                border-radius: 4px; padding: 5px 10px;
            }
            QPushButton:hover    { background: #1f8038; }
            QPushButton:pressed  { background: #145522; }
            QPushButton:disabled {
                background: #1e1e1e; color: #555;
                border: 1px solid #2a2a2a;
            }
        """)
        self._btn_save.setEnabled(False)

        self._btn_preview.setStyleSheet("""
            QPushButton {
                background: #0e4a7a; color: #9cdcfe;
                font-weight: 600; border: 1px solid #0e7fd4;
                border-radius: 4px; padding: 5px 10px;
            }
            QPushButton:hover   { background: #0e5a8a; }
            QPushButton:pressed { background: #093a5e; }
        """)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self._btn_preview)
        btn_row.addWidget(self._btn_save)
        root.addLayout(btn_row)

    # ────────────────────────────────────────────────────────
    # 시그널 연결
    # ────────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        # 파일 목록
        self._btn_add.clicked.connect(self._on_add_files)
        self._btn_remove.clicked.connect(self._on_remove_file)
        self._btn_clear.clicked.connect(self._on_clear_all)
        self._btn_up.clicked.connect(self._on_move_up)
        self._btn_down.clicked.connect(self._on_move_down)

        # 작업 선택
        self._rb_merge.toggled.connect(self._refresh_op_visibility)
        self._rb_split.toggled.connect(self._refresh_op_visibility)
        self._op_group.buttonClicked.connect(
            lambda _btn: self.operation_changed.emit(self._current_operation()))

        # 합치기 옵션
        self._cb_sort_time.toggled.connect(lambda _: self.options_changed.emit())
        self._cb_merge_wpt.toggled.connect(lambda _: self.options_changed.emit())
        self._cb_dedup_wpt.toggled.connect(lambda _: self.options_changed.emit())
        self._merge_as_group.buttonClicked.connect(
            lambda _btn: self.options_changed.emit())

        # 쪼개기 기준
        self._split_group.buttonClicked.connect(self._on_split_mode_changed)
        self._spin_gap.valueChanged.connect(lambda _: self.options_changed.emit())
        self._spin_dist.valueChanged.connect(lambda _: self.options_changed.emit())
        self._spin_points.valueChanged.connect(lambda _: self.options_changed.emit())

        # 수동 분할
        self._btn_manual_mode.toggled.connect(self.manual_split_toggled)
        self._btn_clear_splits.clicked.connect(self.clear_splits_requested)

        # 갭 감지
        self._btn_apply_gaps.clicked.connect(self.gaps_apply_requested)

        # 품질 필터
        self._cb_rm_anomaly.toggled.connect(
            lambda v: (self._spin_maxspd.setEnabled(v),
                       self.options_changed.emit()))
        self._cb_smooth_ele.toggled.connect(
            lambda v: (self._spin_smooth.setEnabled(v),
                       self.options_changed.emit()))

        # 출력 폴더
        self._btn_outdir.clicked.connect(self._on_choose_outdir)
        self._cb_overwrite.toggled.connect(lambda _: self.options_changed.emit())

        # 액션
        self._btn_preview.clicked.connect(self.preview_requested)
        self._btn_save.clicked.connect(self.save_requested)

        self._spin_smooth.valueChanged.connect(self._on_smooth_window_changed)
        self._spin_maxspd.valueChanged.connect(
            lambda _: self.options_changed.emit())        
        self._cmb_utc.currentIndexChanged.connect(lambda _: self.options_changed.emit())

    # ────────────────────────────────────────────────────────
    # 공개 API
    # ────────────────────────────────────────────────────────

    def get_utc_offset(self) -> float:
        mins = self._cmb_utc.currentData() or 0
        return mins / 60.0


    def set_files(self, paths: list[Path]) -> None:
        self._files = list(paths)
        self._refresh_file_list()


    def get_files(self) -> list[Path]:
        return list(self._files)


    def get_operation(self) -> str:
        return self._current_operation()


    def get_split_mode(self) -> str:
        return self._SPLIT_KEYS[self._split_group.checkedId()]


    def get_gap_minutes(self) -> float:
        return float(self._spin_gap.value())


    def get_dist_km(self) -> float:
        return float(self._spin_dist.value())


    def get_point_count(self) -> int:
        return int(self._spin_points.value())


    def get_merge_options(self):
        """MergeOptions 반환"""
        as_map = {0: 'single_track', 1: 'multi_track', 2: 'segments'}
        return MergeOptions(
            sort_by_time=self._cb_sort_time.isChecked(),
            merge_as=as_map.get(self._merge_as_group.checkedId(), 'single_track'),
            merge_waypoints=self._cb_merge_wpt.isChecked(),
            deduplicate_waypoints=self._cb_dedup_wpt.isChecked(),
        )


    def get_output_dir(self) -> Optional[Path]:
        return self._output_dir


    def get_overwrite(self) -> bool:
        return self._cb_overwrite.isChecked()


    def get_remove_anomalies(self) -> bool:
        return self._cb_rm_anomaly.isChecked()


    def get_max_speed(self) -> float:
        return float(self._spin_maxspd.value())


    def get_smooth_elevation(self) -> bool:
        return self._cb_smooth_ele.isChecked()


    def get_smooth_window(self) -> int:
        return int(self._spin_smooth.value())


    def set_split_count_label(self, count: int) -> None:
        self._lbl_split_count.setText(t('gpx_merger.panel.split_count', n=count))


    def set_save_enabled(self, enabled: bool) -> None:
        self._btn_save.setEnabled(enabled)


    def populate_gaps(self, gaps: list) -> None:
        """GapInfo 목록을 갭 감지 그룹에 표시"""
        self._gap_list.clear()
        if not gaps:
            self._grp_gaps.setVisible(False)
            return
        for g in gaps:
            mins  = g.gap_seconds / 60.0
            label = t('gpx_merger.panel.gap_item',
                    idx=g.split_index, mins=f'{mins:.0f}',
                    lat=f'{g.lat:.4f}', lon=f'{g.lon:.4f}')
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, g.split_index)
            self._gap_list.addItem(item)
        self._grp_gaps.setVisible(True)


    def set_file_info(self, text: str) -> None:
        self._lbl_file_info.setText(text)

    # ────────────────────────────────────────────────────────
    # 내부 슬롯
    # ────────────────────────────────────────────────────────

    def _on_add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, t('gpx_merger.panel.add_files_title'),
            '', t('gpx_merger.panel.file_filter'))
        if not paths:
            return
        existing = {str(p) for p in self._files}
        added = [Path(p) for p in paths if str(Path(p)) not in existing]
        if not added:
            return
        self._files.extend(added)
        self._refresh_file_list()
        self.files_changed.emit(self._files)


    def _on_clear_all(self) -> None:
        if not self._files:
            return
        _dlg = _DarkMessageBox(
            self, kind='question',
            title=t('gpx_merger.panel.clear_all_title'),
            body=t('gpx_merger.panel.clear_all_msg', n=len(self._files)),
        )
        if _dlg.exec() == QDialog.DialogCode.Accepted:
            self._files.clear()
            self._refresh_file_list()
            self.files_changed.emit(self._files)


    def _on_remove_file(self) -> None:
        row = self._file_list.currentRow()
        if row < 0 or row >= len(self._files):
            return
        self._files.pop(row)
        self._refresh_file_list()
        new_row = min(row, len(self._files) - 1)  
        if new_row >= 0:
            self._file_list.setCurrentRow(new_row)
        self.files_changed.emit(self._files)


    def _on_move_up(self) -> None:
        row = self._file_list.currentRow()
        if row <= 0:
            return
        self._files[row - 1], self._files[row] = (
            self._files[row], self._files[row - 1])
        self._refresh_file_list()
        self._file_list.setCurrentRow(row - 1)
        self.files_changed.emit(self._files)


    def _on_move_down(self) -> None:
        row = self._file_list.currentRow()
        if row < 0 or row >= len(self._files) - 1:
            return
        self._files[row], self._files[row + 1] = (
            self._files[row + 1], self._files[row])
        self._refresh_file_list()
        self._file_list.setCurrentRow(row + 1)
        self.files_changed.emit(self._files)


    def _on_split_mode_changed(self) -> None:
        mode = self.get_split_mode()

        if mode != 'manual' and self._btn_manual_mode.isChecked():
            self._btn_manual_mode.setChecked(False)
        self._refresh_split_widgets()
        self.split_mode_changed.emit(mode)
        self.options_changed.emit()


    def _on_choose_outdir(self) -> None:
        start = str(self._output_dir or (
            self._files[0].parent if self._files else Path.home()))
        folder = QFileDialog.getExistingDirectory(self, t('gpx_merger.panel.outdir_chooser_title'), start)
        if folder:
            self._output_dir = Path(folder)
            short = folder if len(folder) <= 38 else '…' + folder[-35:]
            self._lbl_outdir.setText(short)


    def _refresh_op_visibility(self) -> None:
        is_merge = self._rb_merge.isChecked()
        self._grp_merge.setVisible(is_merge)
        self._grp_split.setVisible(not is_merge)
        if is_merge:
            self._grp_gaps.setVisible(False)   
        else:
            self._refresh_split_widgets()


    def _refresh_split_widgets(self) -> None:
        mode = self.get_split_mode()
        self._spin_gap.setEnabled(mode == 'gap')
        self._spin_dist.setEnabled(mode == 'dist')
        self._spin_points.setEnabled(mode == 'points')
        self._btn_manual_mode.setEnabled(mode == 'manual')
        self._btn_clear_splits.setEnabled(mode == 'manual')

        if mode in ('gap', 'date') and self._gap_list.count() > 0:
            self._grp_gaps.setVisible(True)
        else:
            self._grp_gaps.setVisible(False) 


    def _refresh_file_list(self) -> None:
        self._file_list.clear()
        for p in self._files:
            item = QListWidgetItem(f'  {p.name}')
            colors = self._file_colors.get(str(p))
            if colors:
                item.setIcon(self._make_color_icon(colors))
            self._file_list.addItem(item)
        n = len(self._files)
        self._lbl_file_info.setText(t('gpx_merger.panel.file_count', n=n) if n else t('gpx_merger.panel.file_none'))


    def _current_operation(self) -> str:
        return 'merge' if self._rb_merge.isChecked() else 'split'


    def _on_smooth_window_changed(self, value: int) -> None:
        if value % 2 == 0:
            self._spin_smooth.blockSignals(True)
            try:
                self._spin_smooth.setValue(value + 1)
            finally:
                self._spin_smooth.blockSignals(False)
        self.options_changed.emit()
            
    # ────────────────────────────────────────────────────────
    # 드래그앤드롭
    # ────────────────────────────────────────────────────────

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            urls = e.mimeData().urls()
            if any(u.toLocalFile().lower().endswith('.gpx') for u in urls):
                e.acceptProposedAction()
                return
        e.ignore()


    def dropEvent(self, e: QDropEvent) -> None:
        paths = [
            Path(u.toLocalFile())
            for u in e.mimeData().urls()
            if u.toLocalFile().lower().endswith('.gpx')
        ]
        if not paths:
            return
        existing = {str(p) for p in self._files}
        added    = [p for p in paths if str(p) not in existing]
        if added:
            self._files.extend(added)
            self._refresh_file_list()
            self.files_changed.emit(self._files)
        e.acceptProposedAction()


    def set_file_items(self, items: List[Tuple[str, List[str]]]) -> None:
        self._file_colors = {str(p): colors for p, colors in zip(self._files, [c for _, c in items])}
        self._file_list.clear()
        for name, colors in items:
            item = QListWidgetItem(f'  {name}')
            item.setIcon(self._make_color_icon(colors))
            self._file_list.addItem(item)
        n = len(items)
        self._lbl_file_info.setText(t('gpx_merger.panel.file_count', n=n) if n else t('gpx_merger.panel.file_none'))


    def _make_color_icon(self, colors: List[str]) -> QIcon:
        """트랙 색상 스트라이프 아이콘 (14×14 px) 생성."""
        px = QPixmap(14, 14)
        px.fill(Qt.GlobalColor.transparent)
        painter = QPainter(px)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            n = max(1, len(colors))
            w = max(1, 14 // n)
            for i, c in enumerate(colors[:14]):
                painter.fillRect(i * w, 2, w, 10, QColor(c))
            if colors:
                painter.fillRect(n * w, 2, 14 - n * w, 10, QColor(colors[-1]))
        finally:
            painter.end()
        return QIcon(px)