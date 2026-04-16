# -*- coding: utf-8 -*-
# tools\tile_downloader\tile_downloader_window.py

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
import urllib.request as req
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import qasync
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow,
    QProgressBar, QPushButton, QScrollArea, QSlider,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget, QFrame
)

from .config_manager import (
    ConfigManager, delete_history_entry,
    load_history, load_preset_json, save_preset_json,
)
from .country_presets import PRESETS, get_ordered_presets
from .downloader_engine import (
    CalcWorker, DiskMonitor, DownloadConfig,
    ETACalculator, SignalThrottler,
)
from .signals import EngineSignals
from .tile_bbox_map import TileBboxDialog
from .tile_calculator import Bbox
from .job_queue import JobQueue
from .dl_loop import _TileDownloadLoop
from .tdw_queue_mixin import TdwQueueMixin
from .tdw_engine_mixin import TdwEngineMixin
from .tdw_constants import (
    S, LEVEL_COLOR, get_state_label,
    get_fmt_options, get_size_options
)
from utils.lang_manager import t
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox
from utils.paths import get_user_data_dir 


def _t(key: str, **kw) -> str:
    return t(f"tile_downloader.{key}", **kw)


class TileDownloaderWindow(TdwEngineMixin, TdwQueueMixin, QMainWindow):

    _conn_result_sig = Signal(bool, str)

    def __init__(self, parent=None, *, main_cfg=None):

        super().__init__(parent)
        self.setWindowTitle("dodoRynx - Tile Downloader")
        self.setMinimumSize(1120, 720)

        self._cfg_mgr     = ConfigManager(main_cfg)
        self._state       = S.IDLE
        self._calc_result = None
        self._calc_worker: Optional[CalcWorker]     = None
        self._engine_sig:  Optional[EngineSignals]  = None
        self._throttler:   Optional[SignalThrottler] = None
        self._disk_mon:    Optional[DiskMonitor]     = None
        self._eta          = ETACalculator()
        self._cancel_ev:   Optional[asyncio.Event] = None
        self._user_pause:  Optional[asyncio.Event] = None
        self._disk_pause:  Optional[asyncio.Event] = None
        self._dl_loop:     Optional[_TileDownloadLoop]  = None
        self._engine_task  = None
        self._total_s = self._total_k = self._total_f = 0
        self._session_start = 0.0
        self._log_buf: deque = deque(maxlen=10_000)
        self._zoom_rows: dict[int, int] = {}
        self._zoom_done: dict[int, int] = {}
        self._history_data: list[dict] = []

        self._job_queue   = JobQueue()
        self._queue_running = False  
        self._queue_path    = get_user_data_dir() / 'job_queue.json'
        self._job_queue.load(self._queue_path)  

        self._queue_calc:     Optional[CalcWorker]      = None
        self._queue_sig:      Optional[EngineSignals]   = None
        self._queue_throttler: Optional[SignalThrottler] = None
        
        self._is_antimeridian: bool = False
        self._q_cancel_ev: asyncio.Event | None = None
        
        self._apply_theme()
        self._build_ui()
        self._load_last()
        self._apply_state(S.IDLE)
        geom = self._cfg_mgr.load_geometry()
        if geom:
            self.restoreGeometry(geom) 
        self._conn_result_sig.connect(self._apply_conn_result)


    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #1e1e1e; color: #d4d4d4; font-size: 12px;
            }
            QGroupBox {
                border: 1px solid #3a3a3a; border-radius: 5px;
                margin-top: 8px; padding-top: 6px; font-weight: bold; color: #9cdcfe;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background: #2d2d2d; border: 1px solid #3a3a3a;
                border-radius: 3px; padding: 3px 6px; color: #d4d4d4;
            }
            QLineEdit:focus, QComboBox:focus { border-color: #0e7fd4; }
            QPushButton {
                background: #2d2d2d; border: 1px solid #3a3a3a;
                border-radius: 4px; padding: 4px 10px; color: #d4d4d4;
            }
            QPushButton:hover { background: #3a3a3a; border-color: #555; }
            QPushButton:disabled { color: #555; border-color: #2a2a2a; }
            QPushButton:pressed { background: #1a1a1a; }
            QTableWidget {
                background: #1e1e1e; gridline-color: #2e2e2e;
                selection-background-color: #0e4a7a;
            }
            QHeaderView::section {
                background: #2a2a2a; border: none;
                border-bottom: 1px solid #3a3a3a; padding: 4px; color: #9cdcfe;
            }
            QTabWidget::pane { border: 1px solid #3a3a3a; }
            QTabBar::tab {
                background: #2a2a2a; padding: 6px 14px;
                border: 1px solid #3a3a3a; border-bottom: none;
            }
            QTabBar::tab:selected { background: #1e1e1e; color: #0e7fd4; }
            QProgressBar {
                background: #2d2d2d; border: 1px solid #3a3a3a;
                border-radius: 3px; text-align: center; height: 16px;
            }
            QProgressBar::chunk { background: #0e7fd4; border-radius: 2px; }
            QScrollBar:vertical {
                background: #1e1e1e; width: 8px;
            }
            QScrollBar::handle:vertical { background: #3a3a3a; border-radius: 4px; }
            QTextEdit { background: #141414; border: 1px solid #2e2e2e; }
            QSlider::groove:horizontal {
                background: #2d2d2d; height: 4px; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #0e7fd4; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px;
            }
            QStatusBar { border-top: 1px solid #2e2e2e; }
            QSpinBox, QDoubleSpinBox {
                background: #1e2228;
                border: 1px solid #3a404a;
                color: #c8d0d8;
                border-radius: 3px;
                padding-right: 18px;         
            }                           
        """)
        
    # ──────────────────────────────────────────────────────────────────────────
    # UI 구성
    # ──────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        cw = QWidget()
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        toolbar_widget = self._make_toolbar()
        toolbar_widget.setFixedHeight(40)    
        root.addWidget(toolbar_widget)

        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.addWidget(self._make_settings_panel())
        sp.addWidget(self._make_right_tabs())
        sp.setSizes([440, 680])
        root.addWidget(sp, stretch=1)    

        self._make_statusbar()

    # ── 툴바 ──────────────────────────────────────────────────────────────────

    def _make_toolbar(self) -> QWidget:        
        container = QWidget()
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(4)

        self._btn_calc   = QPushButton(_t("toolbar.btn_calc_count"))
        self._btn_save_p = QPushButton(_t("toolbar.btn_save_preset"))
        self._btn_load_p = QPushButton(_t("toolbar.btn_load_preset"))
        self._btn_start  = QPushButton(_t("toolbar.btn_start"))
        self._btn_pause  = QPushButton(_t("toolbar.btn_pause"))
        self._btn_resume = QPushButton(_t("toolbar.btn_resume"))
        self._btn_cancel = QPushButton(_t("toolbar.btn_cancel"))

        self._btn_start.setStyleSheet(
            "QPushButton{background:#1a6b2f;border-color:#2d9e4a;color:#fff;font-weight:bold;padding:5px 14px;}"
            "QPushButton:hover{background:#1f8038;}"
            "QPushButton:disabled{background:#1e1e1e;color:#555;border-color:#2a2a2a;}"
        )
        self._btn_pause.setStyleSheet(
            "QPushButton{background:#7a4e00;border-color:#c07a00;color:#fff;}"
            "QPushButton:hover{background:#8f5c00;}"
            "QPushButton:disabled{background:#1e1e1e;color:#555;border-color:#2a2a2a;}"
        )
        self._btn_resume.setStyleSheet(self._btn_pause.styleSheet())
        self._btn_cancel.setStyleSheet(
            "QPushButton{background:#6b1a1a;border-color:#9e2d2d;color:#fff;}"
            "QPushButton:hover{background:#802020;}"
            "QPushButton:disabled{background:#1e1e1e;color:#555;border-color:#2a2a2a;}"
        )

        # 구분선
        self._toolbar_sep = QFrame()
        self._toolbar_sep.setFrameShape(QFrame.Shape.VLine)
        self._toolbar_sep.setStyleSheet("color:#3a3a3a;")

        for b in (self._btn_calc, self._btn_save_p, self._btn_load_p):
            b.setFixedHeight(28)
            lay.addWidget(b)

        lay.addWidget(self._toolbar_sep)

        for b in (self._btn_start, self._btn_pause,
                self._btn_resume, self._btn_cancel):
            b.setFixedHeight(28)
            lay.addWidget(b)

        lay.addStretch()

        self._lbl_state_badge = QLabel(_t("badge.idle"))
        self._lbl_state_badge.setStyleSheet(
            "color:#555; font-size:11px; padding-right:6px;"
        )
        lay.addWidget(self._lbl_state_badge)

        # 시그널 연결
        self._btn_calc.clicked.connect(self._on_calc)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_resume.clicked.connect(self._on_resume)
        self._btn_cancel.clicked.connect(self._on_cancel)
        self._btn_save_p.clicked.connect(self._on_save_preset)
        self._btn_load_p.clicked.connect(self._on_load_preset)

        return container     
    
    # ── 설정 패널 ──────────────────────────────────────────────────────────────

    def _make_settings_panel(self) -> QScrollArea:
        area = QScrollArea(); area.setWidgetResizable(True); area.setFixedWidth(520)
        w = QWidget(); lay = QVBoxLayout(w); lay.setSpacing(5)
        lay.addWidget(self._grp_server())
        lay.addWidget(self._grp_tile())
        lay.addWidget(self._grp_area())
        lay.addWidget(self._grp_zoom())
        lay.addWidget(self._grp_output())
        lay.addWidget(self._grp_advanced())
        lay.addWidget(self._grp_estimate())
        lay.addStretch()
        area.setWidget(w); return area


    def _grp_server(self) -> QGroupBox:

        g = QGroupBox(_t("grp_server.title"))
        lay = QVBoxLayout(g)

        self._e_url   = QLineEdit("http://localhost:8080")
        self._e_style = QLineEdit("light")

        # ── Base URL 행 ─────────────────────────────────────────────
        url_row = QHBoxLayout()
        self._btn_conn_test = QPushButton(_t("grp_server.btn_conn"))
        self._btn_conn_test.setFixedHeight(26)
        self._lbl_conn = QLabel("")
        self._lbl_conn.setWordWrap(True)   
        self._lbl_conn.setMinimumHeight(18)
        self._lbl_conn.setMaximumHeight(80)
        url_row.addWidget(self._e_url)
        url_row.addWidget(self._btn_conn_test)

        lay.addWidget(QLabel(_t("grp_server.label_url")))
        lay.addLayout(url_row)
        lay.addWidget(self._lbl_conn)
        lay.addWidget(QLabel(_t("grp_server.label_style")))
        lay.addWidget(self._e_style)

        self._e_url.textChanged.connect(self._cfg_changed)
        self._e_style.textChanged.connect(self._cfg_changed)
        self._e_url.textChanged.connect(self._reset_conn_label)  
        self._btn_conn_test.clicked.connect(self._on_conn_test)
        return g


    def _reset_conn_label(self):
        self._lbl_conn.setText("")


    def _on_conn_test(self):

        url   = self._e_url.text().strip().rstrip("/")
        style = self._e_style.text().strip() or "light"

        if not url:
            self._lbl_conn.setText(_t("grp_server.conn_no_url"))
            self._lbl_conn.setStyleSheet("color:orange; font-size:11px;")
            return

        self._btn_conn_test.setEnabled(False)
        self._lbl_conn.setText(_t("grp_server.conn_testing"))
        self._lbl_conn.setStyleSheet("color:gray; font-size:11px;")

        def test():
            results = {}

            # ── 1. 헬스 체크 ──────────────────────────────────────────
            try:
                with req.urlopen(
                    req.Request(f"{url}/health",
                                headers={"User-Agent": "dodoRynx-ConnTest/1.0"}),
                    timeout=5
                ) as r:
                    results["health"] = r.status
            except Exception as e:
                self._conn_result_sig.emit(False, _t("grp_server.result_fail", e=e))
                return

            # ── 2. styles.json → 스타일 목록 ────────────────────────
            try:
                with req.urlopen(f"{url}/styles.json", timeout=5) as r:
                    styles_data = json.loads(r.read().decode())

                    if isinstance(styles_data, list):
                        available_styles = [
                            s.get("id") or s.get("name") or str(s)
                            for s in styles_data
                        ]
                    elif isinstance(styles_data, dict):
                        available_styles = list(styles_data.keys())
                    else:
                        available_styles = []

                    results["styles"] = available_styles
            except Exception as e:
                results["styles"] = [f"읽기 실패: {e}"]

            # ── 3. config.json → formatQuality / formatOptions ──────
            try:
                with req.urlopen(f"{url}/config.json", timeout=5) as r:
                    cfg = json.loads(r.read().decode())
                    opts    = cfg.get("options", {})
                    fq      = opts.get("formatQuality")  
                    fo      = opts.get("formatOptions")  
                    results["format_quality"] = fq
                    results["format_options"] = fo
            except Exception:
                results["format_quality"] = None
                results["format_options"] = None

            # ── 4. 결과 조립 ─────────────────────────────────────────
            style_ok   = style in results.get("styles", [])
            styles_str = ", ".join(results.get("styles", []))

            fq = results["format_quality"]
            fo = results["format_options"]
            if fo:
                webp_q = fo.get("webp", {}).get("quality", "?")
                jpeg_q = fo.get("jpeg", {}).get("quality", "?")
                fmt_str = _t("grp_server.fmt_new",    webp=webp_q, jpeg=jpeg_q)
            elif fq:
                webp_q = fq.get("webp", "?")
                jpeg_q = fq.get("jpeg", "?")
                fmt_str = _t("grp_server.fmt_legacy", webp=webp_q, jpeg=jpeg_q)
            else:
                fmt_str = _t("grp_server.fmt_none")

            style_mark = _t("grp_server.style_ok") if style_ok else _t("grp_server.style_miss")
            msg = _t("grp_server.result_ok", style=style, style_mark=style_mark,
                    styles=styles_str, fmt=fmt_str)
            self._conn_result_sig.emit(True, msg)

        threading.Thread(target=test, daemon=True, name="conn-test").start()


    def _apply_conn_result(self, ok: bool, msg: str):
        """메인 스레드에서 UI 업데이트."""
        self._btn_conn_test.setEnabled(True)
        if ok:
            self._lbl_conn.setText(_t("grp_server.conn_ok",   msg=msg))
            self._lbl_conn.setStyleSheet("color: #4CAF50; font-size: 11px;")
        else:
            self._lbl_conn.setText(_t("grp_server.conn_fail",  msg=msg))
            self._lbl_conn.setStyleSheet("color: #E05252; font-size: 11px;")


    def _grp_tile(self) -> QGroupBox:
        g = QGroupBox(_t("grp_tile.title")); lay = QVBoxLayout(g)
        self._cb_fmt  = QComboBox()
        self._cb_size = QComboBox()
        for v,l in get_fmt_options():  self._cb_fmt.addItem(l, v)
        for v,l in get_size_options(): self._cb_size.addItem(l, v)
        lay.addWidget(QLabel(_t("grp_tile.label_fmt")));        lay.addWidget(self._cb_fmt)
        lay.addWidget(QLabel(_t("grp_tile.label_size")));   lay.addWidget(self._cb_size)
        self._cb_fmt.currentIndexChanged.connect(self._cfg_changed)
        self._cb_size.currentIndexChanged.connect(self._cfg_changed)
        return g


    def _grp_area(self) -> QGroupBox:
        g = QGroupBox(_t("grp_area.title"))
        lay = QVBoxLayout(g)

        # ── 프리셋 선택 ─────────────────────────────────────────────────────
        row_preset = QHBoxLayout()
        self._cb_preset = QComboBox()
        for code, p in get_ordered_presets():
            label = _t(f"presets.{code}") or p.name
            self._cb_preset.addItem(label, code)
        self._btn_map_sel = QPushButton(_t("grp_area.btn_map_sel"))
        self._btn_map_sel.setToolTip(_t("grp_area.btn_map_tip"))
        self._btn_map_sel.setFixedHeight(26)
        row_preset.addWidget(self._cb_preset)
        row_preset.addWidget(self._btn_map_sel)
        lay.addWidget(QLabel(_t("grp_area.label_preset")))
        lay.addLayout(row_preset)

        # ── 좌표 스핀박스 ───────────────────────────────────────────────────
        def dsp(lo, hi):
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setDecimals(4); s.setSingleStep(0.1)
            return s

        self._sp_lon_min = dsp(-180, 180); self._sp_lat_min = dsp(-90, 90)
        self._sp_lon_max = dsp(-180, 180); self._sp_lat_max = dsp(-90, 90)

        r1 = QHBoxLayout(); r2 = QHBoxLayout()
        r1.addWidget(QLabel(_t("grp_area.label_lon_min"))); r1.addWidget(self._sp_lon_min)
        r1.addWidget(QLabel(_t("grp_area.label_lat_min"))); r1.addWidget(self._sp_lat_min)
        r2.addWidget(QLabel(_t("grp_area.label_lon_max"))); r2.addWidget(self._sp_lon_max)
        r2.addWidget(QLabel(_t("grp_area.label_lat_max"))); r2.addWidget(self._sp_lat_max)
        lay.addLayout(r1)
        lay.addLayout(r2)

        self._lbl_antimer = QLabel("")
        self._lbl_antimer.setStyleSheet("color:orange; font-size:11px;")
        lay.addWidget(self._lbl_antimer)

        # ── 이벤트 연결 ─────────────────────────────────────────────────────
        self._cb_preset.currentIndexChanged.connect(self._on_preset_changed)
        for sp in (self._sp_lon_min, self._sp_lat_min,
                   self._sp_lon_max, self._sp_lat_max):
            sp.valueChanged.connect(self._on_bbox_manual)

        self._btn_map_sel.clicked.connect(self._on_open_map_selector)
        return g


    def _on_open_map_selector(self) -> None:
        lon_min = self._sp_lon_min.value()
        lat_min = self._sp_lat_min.value()
        lon_max = self._sp_lon_max.value()
        lat_max = self._sp_lat_max.value()

        valid = (lon_min != lon_max and lat_min != lat_max and
                abs(lon_min) + abs(lat_min) > 0.001)
        init_bbox = Bbox(lon_min, lat_min, lon_max, lat_max) if valid else None

        style_id = self._e_style.text().strip() or "light"
        base_url = self._e_url.text().strip()
        dlg = TileBboxDialog.open(self, style_id=style_id, base_url=base_url, init_bbox=init_bbox)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            bbox = dlg.get_bbox()
            if bbox is None:
                return

            spins = (self._sp_lon_min, self._sp_lat_min,
                    self._sp_lon_max, self._sp_lat_max)
            for sp in spins: sp.blockSignals(True)
            try:
                self._sp_lon_min.setValue(bbox.lon_min)
                self._sp_lat_min.setValue(bbox.lat_min)
                self._sp_lon_max.setValue(bbox.lon_max)
                self._sp_lat_max.setValue(bbox.lat_max)
            finally:
                for sp in spins: sp.blockSignals(False)

            idx = self._cb_preset.findData("custom")
            if idx >= 0:
                self._cb_preset.blockSignals(True)
                try:
                    self._cb_preset.setCurrentIndex(idx)
                finally:
                    self._cb_preset.blockSignals(False)

            self._is_antimeridian = False           
            self._lbl_antimer.setText("")
            self._cfg_changed()


    def _grp_zoom(self) -> QGroupBox:
        g = QGroupBox(_t("grp_zoom.title")); lay = QHBoxLayout(g)
        self._sp_z_min = QSpinBox(); self._sp_z_min.setRange(0, 22)
        self._sp_z_max = QSpinBox(); self._sp_z_max.setRange(0, 22)
        self._sp_z_max.setValue(13)
        self._lbl_z_rec = QLabel("")
        self._lbl_z_rec.setStyleSheet("color:gray;font-size:11px;")
        lay.addWidget(QLabel(_t("grp_zoom.label_min"))); lay.addWidget(self._sp_z_min)
        lay.addWidget(QLabel(_t("grp_zoom.label_max"))); lay.addWidget(self._sp_z_max)
        lay.addWidget(self._lbl_z_rec); lay.addStretch()
        self._sp_z_min.valueChanged.connect(self._cfg_changed)
        self._sp_z_max.valueChanged.connect(self._cfg_changed)
        return g


    def _grp_output(self) -> QGroupBox:
        g = QGroupBox(_t("grp_output.title")); lay = QVBoxLayout(g)
        row = QHBoxLayout()
        self._e_out = QLineEdit()
        self._e_out.setPlaceholderText(_t("grp_output.placeholder"))
        btn = QPushButton("📁"); btn.setFixedWidth(32)
        btn.clicked.connect(self._browse_out)
        row.addWidget(self._e_out); row.addWidget(btn)
        lay.addLayout(row)
        self._lbl_path = QLabel("")
        self._lbl_path.setStyleSheet("color:gray;font-size:10px;")
        self._lbl_path.setWordWrap(True)
        lay.addWidget(self._lbl_path)
        self._e_out.textChanged.connect(self._cfg_changed)
        return g


    def _grp_advanced(self) -> QGroupBox:
        g = QGroupBox(_t("grp_advanced.title")); lay = QVBoxLayout(g)
        row = QHBoxLayout()
        self._sl_conc  = QSlider(Qt.Orientation.Horizontal)
        self._sl_conc.setRange(1, 200); self._sl_conc.setValue(50)
        self._lbl_conc = QLabel("50"); self._lbl_conc.setFixedWidth(30)
        row.addWidget(QLabel(_t("grp_advanced.label_conc")))
        row.addWidget(self._sl_conc); row.addWidget(self._lbl_conc)
        lay.addLayout(row)
        self._sl_conc.valueChanged.connect(
            lambda v: (self._lbl_conc.setText(str(v)), self._cfg_changed()))
        return g


    def _grp_estimate(self) -> QGroupBox:
        g = QGroupBox(_t("grp_estimate.title"))
        form = QFormLayout(g)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._lbl_est_msg = QLabel(_t("grp_estimate.msg_initial"))
        self._lbl_total    = QLabel("—")
        self._lbl_new      = QLabel("—")
        self._lbl_size     = QLabel("—")
        self._lbl_disk_est = QLabel("—")
        self._lbl_eta_est  = QLabel("—")
        self._lbl_tmp      = QLabel("")

        self._lbl_est_msg.setWordWrap(True)
        form.addRow("",          self._lbl_est_msg)
        form.addRow(_t("grp_estimate.label_total"), self._lbl_total)
        form.addRow(_t("grp_estimate.label_new"), self._lbl_new)
        form.addRow(_t("grp_estimate.label_size"), self._lbl_size)
        form.addRow(_t("grp_estimate.label_disk"), self._lbl_disk_est)
        form.addRow(_t("grp_estimate.label_eta"), self._lbl_eta_est)
        form.addRow(_t("grp_estimate.label_tmp"), self._lbl_tmp)
        return g

    # ── 오른쪽 탭 ─────────────────────────────────────────────────────────────

    def _make_right_tabs(self) -> QTabWidget:
        self._tabs = QTabWidget()
        self._tabs.addTab(self._tab_progress(), _t("tabs.progress"))
        self._tabs.addTab(self._tab_queue(),    _t("tabs.queue"))
        self._tabs.addTab(self._tab_log(),      _t("tabs.log"))
        self._tabs.addTab(self._tab_history(),  _t("tabs.history"))
        return self._tabs

    def _tab_progress(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        self._bar_all = QProgressBar()
        self._bar_all.setFormat("%v / %m  (%p%)")
        lay.addWidget(QLabel(_t("tab_progress.label_overall")))
        lay.addWidget(self._bar_all)
        self._card_layouts: list = []

        self._stat_frame = QWidget()
        self._stat_frame.setStyleSheet(
            "background:#252525; border-radius:5px; padding:4px;"
        )
        sr = QHBoxLayout(self._stat_frame) 
        sr.setContentsMargins(8, 6, 8, 6)
        sr.setSpacing(0)

        def _stat_card(label: str, color: str):
            card = QWidget()
            card.setStyleSheet(
                f"border-left:3px solid {color};"
                "padding-left:8px; margin-right:16px;"
            )
            vl = QVBoxLayout(card)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(1)
            self._card_layouts.append(vl)
            lbl_val = QLabel("0")
            lbl_val.setStyleSheet(
                f"color:{color}; font-size:18px; font-weight:bold;"
            )
            lbl_key = QLabel(label)
            lbl_key.setStyleSheet("color:#666; font-size:10px;")
            vl.addWidget(lbl_val)
            vl.addWidget(lbl_key)
            return card, lbl_val

        self._card_s,   self._lbl_s   = _stat_card(_t("tab_progress.card_success"), "#4CAF50")
        self._card_k,   self._lbl_k   = _stat_card(_t("tab_progress.card_skip"),    "#9cdcfe")
        self._card_f,   self._lbl_f   = _stat_card(_t("tab_progress.card_fail"),    "#E05252")
        self._card_spd, self._lbl_spd = _stat_card(_t("tab_progress.card_speed"),   "#E6A817")

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color:#333; margin:0 8px;")
        self._stat_sep = sep2   

        self._lbl_eta_run = QLabel(_t("tab_progress.eta_initial"))
        self._lbl_eta_run.setStyleSheet(
            "color:#888; font-size:12px; padding-left:12px;"
        )

        for widget in (self._card_s, self._card_k,
                    self._card_f, sep2, self._card_spd):
            sr.addWidget(widget)
        sr.addStretch()
        sr.addWidget(self._lbl_eta_run)

        lay.addWidget(self._stat_frame)

        lay.addWidget(QLabel(_t("tab_progress.label_zoom")))
        self._tbl_zoom = QTableWidget(0, 4)
        self._tbl_zoom.setHorizontalHeaderLabels([
            _t("tab_progress.zoom_col_z"), _t("tab_progress.zoom_col_bar"),
            _t("tab_progress.zoom_col_count"), _t("tab_progress.zoom_col_status"),
        ])
        self._tbl_zoom.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c, w2 in ((0,35),(2,120),(3,45)):
            self._tbl_zoom.setColumnWidth(c, w2)
        self._tbl_zoom.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self._tbl_zoom)

        self._btn_retry = QPushButton(_t("tab_progress.btn_retry"))
        self._btn_retry.setEnabled(False)
        lay.addWidget(self._btn_retry)
        self._btn_retry.clicked.connect(self._on_retry_failed)
        return w


    def _tab_queue(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # ── 툴바 ──────────────────────────────────────────────────────
        tb = QHBoxLayout()
        self._btn_q_add      = QPushButton(_t("tab_queue.btn_add"))
        self._btn_q_run_all  = QPushButton(_t("tab_queue.btn_run_all"))
        self._btn_q_stop     = QPushButton(_t("tab_queue.btn_stop"))
        self._btn_q_up       = QPushButton(_t("tab_queue.btn_up"))
        self._btn_q_down     = QPushButton(_t("tab_queue.btn_down"))
        self._btn_q_del      = QPushButton(_t("tab_queue.btn_del"))
        self._btn_q_clr_done = QPushButton(_t("tab_queue.btn_clr_done"))
        self._btn_q_retry    = QPushButton(_t("tab_queue.btn_retry"))
        self._btn_q_save     = QPushButton(_t("tab_queue.btn_save"))

        self._btn_q_stop.setEnabled(False)
        for b in (self._btn_q_up, self._btn_q_down):
            b.setFixedWidth(30)

        for b in (self._btn_q_add, self._btn_q_run_all, self._btn_q_stop,
                self._btn_q_up, self._btn_q_down, self._btn_q_del,
                self._btn_q_clr_done, self._btn_q_retry, self._btn_q_save):
            b.setFixedHeight(26)
            tb.addWidget(b)
        tb.addStretch()

        # 완료 후 동작 선택
        tb2 = QHBoxLayout()
        tb2.addWidget(QLabel(_t("tab_queue.label_after")))
        self._cb_after = QComboBox()
        for v, l in [
            ("none",     _t("tab_queue.after_none")),
            ("sleep",    _t("tab_queue.after_sleep")),
            ("shutdown", _t("tab_queue.after_shutdown")),
        ]:
            self._cb_after.addItem(l, v)
        self._cb_after.setFixedWidth(140)
        tb2.addWidget(self._cb_after)

        # 작업 간 딜레이
        tb2.addWidget(QLabel(_t("tab_queue.label_delay")))
        self._sp_q_delay = QDoubleSpinBox()
        self._sp_q_delay.setRange(0, 300)
        self._sp_q_delay.setValue(0)
        self._sp_q_delay.setSuffix("ms")
        self._sp_q_delay.setFixedWidth(90)
        tb2.addWidget(self._sp_q_delay)
        tb2.addStretch()

        lay.addLayout(tb)
        lay.addLayout(tb2)

        # ── 작업 리스트 테이블 ─────────────────────────────────────────
        self._tbl_queue = QTableWidget(0, 8)
        self._tbl_queue.setHorizontalHeaderLabels([
            _t("tab_queue.col_name"), _t("tab_queue.col_z"),    _t("tab_queue.col_fmt"),
            _t("tab_queue.col_tiles"),_t("tab_queue.col_prog"),  _t("tab_queue.col_status"),
            _t("tab_queue.col_elapsed"), _t("tab_queue.col_error"),
        ])

        hh = self._tbl_queue.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)   
        for c in range(1, self._tbl_queue.columnCount()):          
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
        for c, w2 in (
            (1,  45),   # 범위    Z0~13
            (2,  55),   # 포맷    webp/256
            (3,  80),   # 타일    1,234/5,678
            (4, 100),   # 진행    progress bar
            (5,  52),   # 상태
            (6,  52),   # 소요
            (7,  80),   # 오류
        ):
            self._tbl_queue.setColumnWidth(c, w2)

        self._tbl_queue.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl_queue.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl_queue.verticalHeader().setVisible(False)
        lay.addWidget(self._tbl_queue)

        # ── 시그널 연결 ───────────────────────────────────────────────
        self._btn_q_add.clicked.connect(self._q_add)
        self._btn_q_run_all.clicked.connect(self._q_run_all)
        self._btn_q_stop.clicked.connect(self._q_stop)
        self._btn_q_up.clicked.connect(lambda: self._q_move(-1))
        self._btn_q_down.clicked.connect(lambda: self._q_move(1))
        self._btn_q_del.clicked.connect(self._q_delete)
        self._btn_q_clr_done.clicked.connect(self._q_clear_done)
        self._btn_q_retry.clicked.connect(self._q_reset_failed)
        self._btn_q_save.clicked.connect(self._q_save)

        self._refresh_queue_table()
        return w


    def _tab_log(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        fr = QHBoxLayout()
        self._chk_info = QCheckBox(_t("tab_log.chk_info"));  self._chk_info.setChecked(True)
        self._chk_warn = QCheckBox(_t("tab_log.chk_warn"));  self._chk_warn.setChecked(True)
        self._chk_err  = QCheckBox(_t("tab_log.chk_err")); self._chk_err.setChecked(True)
        self._chk_as   = QCheckBox(_t("tab_log.chk_autoscroll")); self._chk_as.setChecked(True)
        b_clr = QPushButton(_t("tab_log.btn_clear")); b_exp = QPushButton(_t("tab_log.btn_export"))
        for x in (self._chk_info, self._chk_warn, self._chk_err,
                  self._chk_as, b_clr, b_exp):
            fr.addWidget(x)
        fr.addStretch(); lay.addLayout(fr)
        self._log_edit = QTextEdit(); self._log_edit.setReadOnly(True)
        self._log_edit.setFont(QFont("Consolas", 9))
        lay.addWidget(self._log_edit)
        for c in (self._chk_info, self._chk_warn, self._chk_err):
            c.stateChanged.connect(self._refresh_log)
        b_clr.clicked.connect(self._clear_log)
        b_exp.clicked.connect(self._export_log)
        return w


    def _tab_history(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        self._tbl_hist = QTableWidget(0, 9)
        self._tbl_hist.setHorizontalHeaderLabels([
            _t("tab_history.col_date"),    _t("tab_history.col_style"),
            _t("tab_history.col_preset"),  _t("tab_history.col_z"),
            _t("tab_history.col_fmt"),     _t("tab_history.col_total"),
            _t("tab_history.col_success"), _t("tab_history.col_fail"),
            _t("tab_history.col_elapsed"),
        ])
        self._tbl_hist.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._tbl_hist.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl_hist.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tbl_hist.doubleClicked.connect(self._hist_load)
        lay.addWidget(self._tbl_hist)
        br = QHBoxLayout()
        b1 = QPushButton(_t("tab_history.btn_load")); b2 = QPushButton(_t("tab_history.btn_del"))
        b1.clicked.connect(self._hist_load); b2.clicked.connect(self._hist_del)
        br.addWidget(b1); br.addWidget(b2); br.addStretch()
        lay.addLayout(br)
        self._load_hist_table()
        return w


    def _make_statusbar(self):
        sb = self.statusBar()
        self._lbl_st   = QLabel(_t("statusbar.idle"))
        self._lbl_disk = QLabel(_t("statusbar.disk_initial"))
        self._lbl_spd2 = QLabel(_t("statusbar.speed_initial"))
        sb.addPermanentWidget(self._lbl_st)
        sb.addPermanentWidget(self._lbl_disk)
        sb.addPermanentWidget(self._lbl_spd2)

    # ──────────────────────────────────────────────────────────────────────────
    # 상태 머신
    # ──────────────────────────────────────────────────────────────────────────

    def _apply_state(self, state: str):
        self._state = state
        idle   = state == S.IDLE
        ready  = state == S.READY
        run    = state == S.RUNNING
        paused = state == S.PAUSED
        busy   = state in (S.RUNNING, S.PAUSED, S.CANCELLING)
        calc   = state == S.CALCULATING

        self._btn_calc.setEnabled(idle or ready or calc)
        self._btn_start.setEnabled(ready)
        self._btn_pause.setEnabled(run)
        self._btn_resume.setEnabled(paused)
        self._btn_cancel.setEnabled(busy)
        self._btn_save_p.setEnabled(not busy)
        self._btn_load_p.setEnabled(idle or ready)

        self._lbl_st.setText(get_state_label(state))  
        self._btn_calc.setText(_t("toolbar.btn_calc_cancel") if calc else _t("toolbar.btn_calc_count"))
        _grp_titles = {
            _t("grp_server.title"), _t("grp_tile.title"), _t("grp_area.title"),
            _t("grp_zoom.title"),   _t("grp_output.title"), _t("grp_advanced.title"),
        }
        for grp in self.findChildren(QGroupBox):
            if grp.title() in _grp_titles:
                grp.setEnabled(not busy) 
        badge_map = {
            S.IDLE:        (_t("badge.idle"),        "#555555"),
            S.CALCULATING: (_t("badge.calculating"), "#E6A817"),
            S.READY:       (_t("badge.ready"),       "#4CAF50"),
            S.RUNNING:     (_t("badge.running"),     "#0e7fd4"),
            S.PAUSED:      (_t("badge.paused"),      "#E6A817"),
            S.CANCELLING:  (_t("badge.cancelling"),  "#E05252"),
        }
        text, color = badge_map.get(state, (_t("badge.idle"), "#555"))
        self._lbl_state_badge.setText(text)
        self._lbl_state_badge.setStyleSheet(
            f"color:{color}; font-size:11px; font-weight:bold; padding-right:6px;"
        )
        
    # ──────────────────────────────────────────────────────────────────────────
    # 설정 패널 인터랙션
    # ──────────────────────────────────────────────────────────────────────────

    def _cfg_changed(self):
        if self._state == S.READY:
            self._calc_result = None
            self._apply_state(S.IDLE)
            for l in (self._lbl_total, self._lbl_new, self._lbl_size,
                      self._lbl_disk_est, self._lbl_eta_est, self._lbl_tmp):
                l.setText("")
            self._lbl_est_msg.setText(_t("grp_estimate.msg_changed"))
        self._update_path_preview()


    def _on_preset_changed(self):
        code   = self._cb_preset.currentData()
        preset = PRESETS.get(code)
        if not preset or preset.bbox is None:
            return

        spins = (self._sp_lon_min, self._sp_lat_min,
                self._sp_lon_max, self._sp_lat_max)
        for sp in spins: sp.blockSignals(True)
        try:
            self._sp_lon_min.setValue(preset.bbox.lon_min)
            self._sp_lat_min.setValue(preset.bbox.lat_min)
            self._sp_lon_max.setValue(preset.bbox.lon_max)
            self._sp_lat_max.setValue(preset.bbox.lat_max)
        finally:
            for sp in spins: sp.blockSignals(False)

        self._sp_z_max.setValue(preset.z_max)
        self._lbl_z_rec.setText(_t("grp_zoom.rec_max", z_max=preset.z_max))
        self._is_antimeridian = preset.antimeridian     
        self._lbl_antimer.setText(
            _t("grp_area.antimer_warn") if preset.antimeridian else ""
        )
        self._cfg_changed()


    def _on_bbox_manual(self):
        idx = self._cb_preset.findData("custom")
        if idx >= 0:
            self._cb_preset.blockSignals(True)
            try:
                self._cb_preset.setCurrentIndex(idx)
            finally:
                self._cb_preset.blockSignals(False)
        self._is_antimeridian = False                    
        self._lbl_antimer.setText("")
        self._cfg_changed()


    def _browse_out(self):
        p = QFileDialog.getExistingDirectory(self, _t("grp_output.dlg_title"), self._e_out.text())
        if p: self._e_out.setText(p)


    def _update_path_preview(self):
        style  = self._e_style.text().strip() or "style"
        mode   = self._cb_size.currentData()  or "256"
        fmt    = self._cb_fmt.currentData()   or "webp"
        root   = self._e_out.text().strip()   or "/out"
        folder = {"256":"256","@2x":"512_2x","512":"512_native"}.get(mode,"256")
        self._lbl_path.setText(_t("grp_output.path_preview", path=f"{root}/{style}/{folder}/{{z}}/{{x}}/{{y}}.{fmt}"))

    # ──────────────────────────────────────────────────────────────────────────
    # 계산 (CALCULATING)
    # ──────────────────────────────────────────────────────────────────────────

    def _on_calc(self):
        if self._state == S.CALCULATING:
            if self._calc_worker:
                self._calc_worker.cancel()
            return
        cfg = self._collect_config(calc_result=None)
        if cfg is None: return

        self._apply_state(S.CALCULATING)
        self._lbl_est_msg.setText(_t("grp_estimate.msg_calculating"))

        self._calc_worker = CalcWorker(cfg)
        self._calc_worker.signals.progress.connect(self._lbl_est_msg.setText)
        self._calc_worker.signals.finished.connect(self._on_calc_done)
        self._calc_worker.signals.error.connect(self._on_calc_err)
        self._calc_worker.signals.cancelled.connect(self._on_calc_cancel)
        self._calc_worker.start()


    def _on_calc_done(self, result):
        self._calc_result = result
        total = result.tile_count; new = result.new_count
        exist = result.existing.count; tmp_n = len(result.existing.corrupt_tmp)

        try:
            root = self._e_out.text().strip()
            p = Path(root) if root else Path.home()
            if not p.exists(): p = Path(p.anchor or "/")
            free_gb = shutil.disk_usage(p).free / 1024**3
            ok = "✅" if free_gb > result.size_mb / 1024 else "⚠️"
            self._lbl_disk_est.setText(_t("grp_estimate.val_disk", icon=ok, gb=f"{free_gb:.1f}"))
        except OSError:
            self._lbl_disk_est.setText(_t("grp_estimate.val_disk_fail"))

        self._lbl_est_msg.setText(_t("grp_estimate.msg_done"))
        self._lbl_total.setText(_t("grp_estimate.val_total", count=f"{total:,}"))
        self._lbl_new.setText(_t("grp_estimate.val_new", exist=f"{exist:,}", new=f"{new:,}"))
        self._lbl_size.setText(_t("grp_estimate.val_size", mb=f"{result.size_mb:.2f}"))
        eta = str(timedelta(seconds=int(result.eta_sec)))
        self._lbl_eta_est.setText(_t("grp_estimate.val_eta", eta=eta, conc=self._sl_conc.value()))
        self._lbl_tmp.setText(_t("grp_estimate.val_tmp", n=tmp_n))

        if total > 5_000_000:
            _large_dlg = _DarkMessageBox(
                self, kind='question',
                title=_t("grp_estimate.warn_large"),
                body=_t("grp_estimate.warn_large_msg", total=f"{total:,}"),
            )
            if _large_dlg.exec() != QDialog.DialogCode.Accepted:
                self._apply_state(S.IDLE); return

        self._init_zoom_table(result.z_breakdown)
        self._apply_state(S.READY)


    def _on_calc_err(self, msg: str):
        self._lbl_est_msg.setText(_t("grp_estimate.msg_error", msg=msg))
        self._apply_state(S.IDLE)


    def _on_calc_cancel(self):
        self._lbl_est_msg.setText(_t("grp_estimate.msg_cancelled"))
        self._apply_state(S.IDLE)

    # ──────────────────────────────────────────────────────────────────────────
    # 로그
    # ──────────────────────────────────────────────────────────────────────────

    def _on_log(self, level: str, msg: str, ts: float):
        dt = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        self._log_buf.append((level, dt, msg))
        self._append_log_line(level, dt, msg)


    def _append_log_line(self, level: str, dt: str, msg: str):
        show = {"INFO": self._chk_info.isChecked(),
                "WARN": self._chk_warn.isChecked(),
                "ERROR": self._chk_err.isChecked()}.get(level, True)
        if not show: return
        color = LEVEL_COLOR.get(level, "#888888")
        html  = f'<span style="color:{color}">[{dt}] [{level}] {msg}</span><br>'
        cur   = self._log_edit.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertHtml(html)
        if self._chk_as.isChecked():
            self._log_edit.verticalScrollBar().setValue(
                self._log_edit.verticalScrollBar().maximum())


    def _refresh_log(self):
        self._log_edit.clear()
        for level, dt, msg in self._log_buf:
            self._append_log_line(level, dt, msg)


    def _clear_log(self):
        self._log_buf.clear(); self._log_edit.clear()


    def _export_log(self):
        p, _ = QFileDialog.getSaveFileName(
            self, _t("tab_log.dlg_export_title"),
                               _t("tab_log.export_filename"), "Text (*.log *.txt)")
        if not p: return
        with open(p, "w", encoding="utf-8") as fh:
            for level, dt, msg in self._log_buf:
                fh.write(f"[{dt}] [{level}] {msg}\n")

    # ──────────────────────────────────────────────────────────────────────────
    # 히스토리
    # ──────────────────────────────────────────────────────────────────────────

    def _load_hist_table(self):
        self._history_data = load_history()
        self._tbl_hist.setRowCount(0)
        for e in self._history_data:
            row = self._tbl_hist.rowCount(); self._tbl_hist.insertRow(row)
            cfg = e.get("config", {}); st = e.get("stats", {})
            dur = e.get("duration_sec", 0)
            vals = [
                e.get("started_at","")[:19],
                cfg.get("style_id",""), cfg.get("preset","custom"),
                f"Z{cfg.get('z_min',0)}~{cfg.get('z_max',0)}",
                cfg.get("tile_format",""),
                f"{st.get('total',0):,}", f"{st.get('success',0):,}",
                f"{st.get('fail',0):,}", str(timedelta(seconds=dur)),
            ]
            for c, v in enumerate(vals):
                self._tbl_hist.setItem(row, c, QTableWidgetItem(v))

    def _hist_load(self):
        idx = self._tbl_hist.currentRow()
        if idx < 0 or idx >= len(self._history_data): return
        self._apply_config_dict(self._history_data[idx].get("config", {}))


    def _hist_del(self):
        idx = self._tbl_hist.currentRow()
        if idx < 0 or idx >= len(self._history_data): return
        eid = self._history_data[idx].get("id")
        if eid: delete_history_entry(eid)
        self._load_hist_table()

    # ──────────────────────────────────────────────────────────────────────────
    # 프리셋 저장/불러오기
    # ──────────────────────────────────────────────────────────────────────────

    def _on_save_preset(self):
        from utils.dark_dialog import DarkInputDialog as _DarkInputDialog
        name, ok = _DarkInputDialog.getText(self, _t("preset.dlg_save_title"), _t("preset.dlg_save_label"))
        if not ok or not name.strip(): return
        path = save_preset_json(name.strip(), self._collect_config_dict())
        _DarkMessageBox(self, kind='info', title=_t("preset.dlg_saved_title"), body=_t("preset.dlg_saved_msg", path=path)).exec()


    def _on_load_preset(self):
        p, _ = QFileDialog.getOpenFileName(
            self, _t("preset.dlg_load_title"), str(Path.home()), "JSON (*.json)")
        if not p: return
        try:
            self._apply_config_dict(load_preset_json(Path(p)))
        except Exception as e:
            _DarkMessageBox(self, kind='danger', title=_t("preset.dlg_load_title"), body=_t("preset.dlg_load_error", e=e)).exec()

    # ──────────────────────────────────────────────────────────────────────────
    # 설정 수집 / 적용
    # ──────────────────────────────────────────────────────────────────────────

    def _collect_config(self, calc_result) -> Optional[DownloadConfig]:
        url    = self._e_url.text().strip()
        style  = self._e_style.text().strip()
        out    = self._e_out.text().strip()
        if not url or not style or not out:
            _DarkMessageBox(
                self, kind='warning',
                title=_t("tab_queue.dlg_add_error_title"),
                body=_t("tab_queue.dlg_add_error_msg"),
            ).exec()
            return None
        return DownloadConfig(
            base_url       = url, style_id = style,
            tile_format    = self._cb_fmt.currentData(),
            tile_size_mode = self._cb_size.currentData(),
            z_min          = self._sp_z_min.value(),
            z_max          = self._sp_z_max.value(),
            bbox           = Bbox(self._sp_lon_min.value(), self._sp_lat_min.value(),
                                  self._sp_lon_max.value(), self._sp_lat_max.value()),
            antimeridian   = self._is_antimeridian,
            concurrency    = self._sl_conc.value(),
            out_root       = Path(out),
            calc_result    = calc_result,
        )


    def _collect_config_dict(self) -> dict:
        return {
            "base_url": self._e_url.text().strip(),
            "style_id": self._e_style.text().strip(),
            "tile_format":    self._cb_fmt.currentData(),
            "tile_size_mode": self._cb_size.currentData(),
            "z_min": self._sp_z_min.value(), "z_max": self._sp_z_max.value(),
            "bbox": {
                "lon_min": self._sp_lon_min.value(), "lat_min": self._sp_lat_min.value(),
                "lon_max": self._sp_lon_max.value(), "lat_max": self._sp_lat_max.value(),
            },
            "antimeridian": self._is_antimeridian,
            "concurrency":  self._sl_conc.value(),
            "out_root":     self._e_out.text().strip(),
            "preset":       self._cb_preset.currentData() or "custom",
        }


    def _apply_config_dict(self, cfg: dict):
        if "base_url" in cfg: self._e_url.setText(cfg["base_url"])
        if "style_id" in cfg: self._e_style.setText(cfg["style_id"])
        if "tile_format" in cfg:
            i = self._cb_fmt.findData(cfg["tile_format"])
            if i >= 0: self._cb_fmt.setCurrentIndex(i)
        if "tile_size_mode" in cfg:
            i = self._cb_size.findData(cfg["tile_size_mode"])
            if i >= 0: self._cb_size.setCurrentIndex(i)
        if "z_min" in cfg: self._sp_z_min.setValue(int(cfg["z_min"]))
        if "z_max" in cfg: self._sp_z_max.setValue(int(cfg["z_max"]))
        b = cfg.get("bbox", {})
        if b:
            self._sp_lon_min.setValue(float(b.get("lon_min", 0)))
            self._sp_lat_min.setValue(float(b.get("lat_min", 0)))
            self._sp_lon_max.setValue(float(b.get("lon_max", 0)))
            self._sp_lat_max.setValue(float(b.get("lat_max", 0)))

        if "concurrency" in cfg: self._sl_conc.setValue(int(cfg["concurrency"]))

        self._is_antimeridian = bool(cfg.get("antimeridian", False))
        if self._is_antimeridian:
            self._lbl_antimer.setText(_t("grp_area.antimer_warn"))
        else:
            self._lbl_antimer.setText("")

        if "out_root" in cfg: self._e_out.setText(cfg["out_root"])
        p = cfg.get("preset", "custom")
        i = self._cb_preset.findData(p)
        if i >= 0:
            self._cb_preset.blockSignals(True)
            try:
                self._cb_preset.setCurrentIndex(i)
            finally:
                self._cb_preset.blockSignals(False)
        self._tabs.setCurrentIndex(0)
        self._cfg_changed()

    # ──────────────────────────────────────────────────────────────────────────
    # 마지막 설정 저장/복원
    # ──────────────────────────────────────────────────────────────────────────

    def _load_last(self):
        last = self._cfg_mgr.load_last()
        if last: self._apply_config_dict(last)


    def _save_last(self):
        self._cfg_mgr.save_last(self._collect_config_dict())

    # ──────────────────────────────────────────────────────────────────────────
    # 창 닫기
    # ──────────────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._state in (S.RUNNING, S.PAUSED):
            _close_dlg = _DarkMessageBox(
                self, kind='question',
                title=_t("close.dlg_title"),
                body=_t("close.dlg_msg"),
            )
            if _close_dlg.exec() != QDialog.DialogCode.Accepted:
                event.ignore(); return
            dl_loop    = self._dl_loop
            cancel_ev  = self._cancel_ev
            user_pause = self._user_pause
            if dl_loop is not None and cancel_ev is not None:
                dl_loop.set_event(cancel_ev)
            if dl_loop is not None and user_pause is not None:
                dl_loop.clear_event(user_pause) 
        self._save_last()
        self._cfg_mgr.save_geometry(self.saveGeometry().data()) 
        self._cfg_mgr.flush()
        if self._disk_mon:  self._disk_mon.stop()
        if self._throttler: self._throttler.stop()
        dl_loop = self._dl_loop
        if dl_loop is not None:
            dl_loop.stop()
            self._dl_loop = None
        super().closeEvent(event)


# ── 단독 실행 진입점 ───────────────────────────────────────────────────────────

def main():
    app  = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    win  = TileDownloaderWindow()
    win.show()
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
