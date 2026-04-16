# -*- coding: utf-8 -*-
# ui\editor\ai_panel.py

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSlider, QPushButton,
    QVBoxLayout, QWidget, QFrame, QGroupBox,
)

from utils.lang_manager import t
from utils.drag_header import DragHeader


PANEL_W     = 220
SECTION_GAP = 8

_SS = """
/* ── 패널 컨테이너 ───────────────────────────────── */
QWidget#ai_panel {
    background: rgba(18,18,18,235);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 8px;
}

/* ── 헤더 ─────────────────────────────────────────── */
QLabel#header {
    color: #6ab4ff;
    font-size: 13px;
    font-weight: 700;
    padding: 8px 10px 4px 10px;
    background: transparent;
    letter-spacing: 0.5px;
}

/* ── 섹션 그룹박스 ───────────────────────────────── */
QGroupBox {
    font-size: 12px;
    font-weight: 700;
    color: #dddddd;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
    background: rgba(255,255,255,0.02);
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #dddddd;
    background: transparent;          /* 이전 버그 수정분 유지 */
}

/* ── 일반 레이블 (QGroupBox 내부 포함) ──────────────── */
QLabel {
    background: transparent;
    color: #aaa;
    font-size: 11px;
}

/* ── 힌트 / 브러시 수치 ────────────────────────────── */
QLabel#hint {
    color: #666;
    font-size: 9px;
    background: transparent;
}
QLabel#brush_val {
    color: #aaa;
    font-size: 11px;
    font-weight: 600;
    background: transparent;
    min-width: 26px;
}

/* ── 상태 배지 ──────────────────────────────────────── */
QLabel#status_checking {
    color: #888; font-size: 11px;
    background: rgba(40,40,40,180);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 4px; padding: 3px 8px;
}
QLabel#status_loading {
    color: #e8a020; font-size: 11px;
    background: rgba(50,35,10,180);
    border: 1px solid rgba(200,130,20,0.3);
    border-radius: 4px; padding: 3px 8px;
}
QLabel#status_ready {
    color: #50bb50; font-size: 11px;
    background: rgba(10,40,10,180);
    border: 1px solid rgba(50,180,50,0.3);
    border-radius: 4px; padding: 3px 8px;
}
QLabel#status_no_model {
    color: #6699ff; font-size: 11px;
    background: rgba(10,20,50,180);
    border: 1px solid rgba(60,100,200,0.3);
    border-radius: 4px; padding: 3px 8px;
}

/* ── 기본 실행 버튼 ─────────────────────────────────── */
QPushButton#run {
    background: #172e4e; color: #5aaaff;
    border: 1px solid #2460a0; border-radius: 5px;
    font-size: 12px; font-weight: 600;
    padding: 0 8px; min-height: 32px;
}
QPushButton#run:hover    { background: #1e4575; border-color: #3a80cc; }
QPushButton#run:checked  { background: #1a5080; border-color: #4a9eff; color: #fff; }
QPushButton#run:disabled { background: #1a1a1a; color: #3a3a3a; border-color: #252525; }

/* ── 초기화 버튼 ─────────────────────────────────────── */
QPushButton#clear {
    background: #2a1a1a; color: #cc6666;
    border: 1px solid #5a2a2a; border-radius: 5px;
    font-size: 12px; font-weight: 600;
    padding: 0 8px; min-height: 32px;
}
QPushButton#clear:hover { background: #3a2020; border-color: #883333; }

/* ── 슬라이더 ────────────────────────────────────────── */
QSlider { background: transparent; }        /* _GROUP_SS에만 있던 규칙 */
QSlider::groove:horizontal {
    background: #2a2a2a; height: 4px; border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #4a9eff; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #2a5ca8; border-radius: 2px;
}

/* ── 구분선 ──────────────────────────────────────────── */
QFrame#sep { color: rgba(255,255,255,0.07); }
"""

_ST_CHECKING = ("checking", "ai_panel.status_checking")
_ST_LOADING  = ("loading",  "ai_panel.status_loading")
_ST_READY    = ("ready",    "ai_panel.status_ready")
_ST_NO_MODEL = ("no_model", "ai_panel.status_no_model")
_ST_WORKING  = ("loading",  "ai_panel.status_working")


class AIPanel(QWidget):
    bg_remove_requested:      Signal = Signal()
    erase_activate_requested: Signal = Signal()
    erase_run_requested:      Signal = Signal()
    erase_clear_requested:    Signal = Signal()
    brush_size_changed:       Signal = Signal(int)
    preload_requested:        Signal = Signal()
    panel_closed: Signal = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ai_panel")
        self.setFixedWidth(PANEL_W)
        self.setStyleSheet(_SS)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._ben2_ready:        bool = False
        self._lama_ready:        bool = False
        self._preload_triggered: bool = False
        self._build_ui()


    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._preload_triggered:
            self._preload_triggered = True
            QTimer.singleShot(50, self.preload_requested.emit)


    def reset_preload(self) -> None:
        self._preload_triggered = False
        self._ben2_ready  = False
        self._lama_ready  = False


    def close_panel(self) -> None:
        self.setVisible(False)
        self.panel_closed.emit()

    # ── UI 구성 ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)   # 헤더가 상단 처리
        root.setSpacing(0)

        # ── 드래그 헤더 (기존 QLabel hdr + sep 대체) ──────────────
        root.addWidget(DragHeader(
            self,
            title="AI Panel",
            icon="◈",
            on_close=self.close_panel,
        ))

        # ── 나머지 컨텐츠 래퍼 (기존 마진 유지) ────────────────────
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(8, 6, 8, 10)
        bl.setSpacing(SECTION_GAP)

        # ── 배경 제거 그룹 ──────────────────────────────────────────────
        bg_group = QGroupBox(t('ai_panel.bg_group_title'))                       
        bg_layout = QVBoxLayout(bg_group)
        bg_layout.setContentsMargins(8, 8, 8, 8)
        bg_layout.setSpacing(6)

        self._status_bg = QLabel(t(_ST_CHECKING[1]))
        self._status_bg.setObjectName(f"status_{_ST_CHECKING[0]}")
        self._status_bg.setWordWrap(True)
        self._status_bg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bg_layout.addWidget(self._status_bg)

        self._btn_bg = QPushButton(t('ai_panel.btn_run_bg'))
        self._btn_bg.setObjectName("run")
        self._btn_bg.setEnabled(False)
        self._btn_bg.clicked.connect(lambda: self.bg_remove_requested.emit())
        bg_layout.addWidget(self._btn_bg)

        bl.addWidget(bg_group)

        # ── AI 지우개 그룹 ──────────────────────────────────────────────
        erase_group = QGroupBox(t('ai_panel.erase_group_title'))
        erase_layout = QVBoxLayout(erase_group)
        erase_layout.setContentsMargins(8, 8, 8, 8)
        erase_layout.setSpacing(6)

        self._status_erase = QLabel(t(_ST_CHECKING[1]))
        self._status_erase.setObjectName(f"status_{_ST_CHECKING[0]}")
        self._status_erase.setWordWrap(True)
        self._status_erase.setAlignment(Qt.AlignmentFlag.AlignCenter)
        erase_layout.addWidget(self._status_erase)

        # 브러시 크기 행
        brush_row = QHBoxLayout()
        brush_row.setContentsMargins(0, 2, 0, 2)
        brush_row.setSpacing(6)

        lbl_brush = QLabel("✏")
        lbl_brush.setObjectName("hint")
        lbl_brush.setFixedWidth(14)
        lbl_brush.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._brush_slider = QSlider(Qt.Orientation.Horizontal)
        self._brush_slider.setRange(5, 300)
        self._brush_slider.setValue(50)

        self._brush_val_lbl = QLabel("50")
        self._brush_val_lbl.setObjectName("brush_val")
        self._brush_val_lbl.setFixedWidth(26)
        self._brush_val_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._brush_slider.valueChanged.connect(self._on_brush_changed)

        brush_row.addWidget(lbl_brush)
        brush_row.addWidget(self._brush_slider, 1)
        brush_row.addWidget(self._brush_val_lbl)
        erase_layout.addLayout(brush_row)

        # 브러시 ON/OFF + 실행 버튼 한 행
        erase_btn_row = QHBoxLayout()
        erase_btn_row.setContentsMargins(0, 0, 0, 0)
        erase_btn_row.setSpacing(6)

        self._btn_erase_on = QPushButton(t('ai_panel.btn_brush'))
        self._btn_erase_on.setObjectName("run")
        self._btn_erase_on.setCheckable(True)
        self._btn_erase_on.setChecked(False)
        self._btn_erase_on.clicked.connect(self._on_erase_activate)

        self._btn_erase_run = QPushButton(t('ai_panel.btn_run_erase'))
        self._btn_erase_run.setObjectName("run")
        self._btn_erase_run.setEnabled(False)
        self._btn_erase_run.clicked.connect(lambda: self.erase_run_requested.emit())

        erase_btn_row.addWidget(self._btn_erase_on, 1)
        erase_btn_row.addWidget(self._btn_erase_run, 1)
        erase_layout.addLayout(erase_btn_row)

        # 초기화 버튼 — 전폭
        btn_clear = QPushButton(t('ai_panel.btn_clear'))
        btn_clear.setObjectName("clear")
        btn_clear.clicked.connect(self._on_erase_clear)
        erase_layout.addWidget(btn_clear)

        bl.addWidget(erase_group)
        bl.addStretch(1)

        root.addWidget(body)

    # ── 모델 상태 제어 ──────────────────────────────────────────────────

    def set_model_loading(self, name: str) -> None:
        if name == "ben2":
            self._ben2_ready = False
            self._set_status(self._status_bg, _ST_LOADING)
            self._btn_bg.setEnabled(False)
        elif name == "lama":
            self._lama_ready = False
            self._set_status(self._status_erase, _ST_LOADING)
            self._btn_erase_run.setEnabled(False)


    def set_models_ready(self, name: str) -> None:
        if name == "ben2":
            self._ben2_ready = True
            self._set_status(self._status_bg, _ST_READY)
            self._btn_bg.setEnabled(True)
            self._btn_bg.setText(t('ai_panel.btn_run_bg'))
        elif name == "lama":
            self._lama_ready = True
            self._set_status(self._status_erase, _ST_READY)
            self._btn_erase_run.setEnabled(True)
            self._btn_erase_run.setText(t('ai_panel.btn_run_erase'))


    def set_model_not_installed(self, name: str) -> None:
        if name == "ben2":
            self._set_status(self._status_bg, _ST_NO_MODEL)
            self._btn_bg.setEnabled(True)
            self._btn_bg.setText(t('ai_panel.btn_run_bg'))
        elif name == "lama":
            self._set_status(self._status_erase, _ST_NO_MODEL)
            self._btn_erase_run.setEnabled(True)
            self._btn_erase_run.setText(t('ai_panel.btn_run_erase'))


    def set_bg_task_running(self, running: bool) -> None:
        if running:
            self._set_status(self._status_bg, _ST_WORKING)
            self._btn_bg.setEnabled(False)
        else:
            if self._ben2_ready:
                self._set_status(self._status_bg, _ST_READY)
                self._btn_bg.setEnabled(True)


    def set_erase_run_enabled(self, enabled: bool) -> None:
        if enabled:
            if self._lama_ready:
                self._set_status(self._status_erase, _ST_READY)
                self._btn_erase_run.setEnabled(True)
        else:
            self._set_status(self._status_erase, _ST_WORKING)
            self._btn_erase_run.setEnabled(False)


    # ── 내부 헬퍼 ──────────────────────────────────────────────────────

    def _set_status(self, label: QLabel, state: tuple) -> None:
        style_name, lang_key = state  
        label.setText(t(lang_key))    
        label.setObjectName(f"status_{style_name}")
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()


    def _on_brush_changed(self, v: int) -> None:
        self._brush_val_lbl.setText(str(v))
        self.brush_size_changed.emit(v)


    def _on_erase_activate(self, checked: bool) -> None:
        self.erase_activate_requested.emit()


    def _on_erase_clear(self) -> None:
        self._btn_erase_on.setChecked(False)
        self.erase_clear_requested.emit()


    def set_brush_active(self, active: bool) -> None:
        self._btn_erase_on.setChecked(active)


    def set_brush_size(self, size: int) -> None:
        self._brush_slider.blockSignals(True)
        self._brush_slider.setValue(size)
        self._brush_val_lbl.setText(str(size))
        self._brush_slider.blockSignals(False)


    def _sep(self) -> QFrame:
        f = QFrame()
        f.setObjectName("sep")
        f.setFrameShape(QFrame.Shape.HLine)
        return f


    def _label_hint(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("hint")
        return lbl
    