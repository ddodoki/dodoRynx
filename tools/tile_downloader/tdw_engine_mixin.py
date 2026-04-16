# -*- coding: utf-8 -*-
# tools/tile_downloader/tdw_engine_mixin.py

"""
TileDownloaderWindow — 다운로드 엔진 제어 믹스인.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PySide6.QtWidgets import (
    QDialog,
    QProgressBar,
    QTableWidgetItem,
    QWidget,
)

from .config_manager import append_history
from .dl_loop import _TileDownloadLoop
from .downloader_engine import (
    CalcResult, DiskMonitor, DownloadConfig,
    ETACalculator, SignalThrottler, run_engine,
)
from .signals import EngineSignals
from .tile_calculator import Bbox, ExistingScanResult
from .tdw_constants import S

from utils.debug import error_print, info_print, warning_print
from utils.lang_manager import t
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox

if TYPE_CHECKING:
    from PySide6.QtWidgets import (
        QLabel,
        QLineEdit,
        QComboBox,
        QDoubleSpinBox,
        QProgressBar as _QProgressBar,
        QPushButton,
        QSlider,
        QSpinBox,
        QTableWidget,
    )
    from .downloader_engine import CalcWorker


# ── 단축 함수 ─────────────────────────────────────────────────────────────────

def _t(key: str, **kw) -> str:
    return t(f"tile_downloader.{key}", **kw)


class TdwEngineMixin:
    """다운로드 엔진 시작/정지/재개/취소 및 진행 업데이트 믹스인."""

    if TYPE_CHECKING:
        # ── 엔진 상태 ───────────────────────────────────────────────────
        _state:         str
        from .tile_calculator import CalcResult as _CalcResult
        _calc_result: _CalcResult | None
        _session_start: float
        _total_s:       int
        _total_k:       int
        _total_f:       int
        _eta:           ETACalculator
        _dl_loop:       _TileDownloadLoop | None
        _cancel_ev:     asyncio.Event | None
        _user_pause:    asyncio.Event | None
        _disk_pause:    asyncio.Event | None
        _engine_sig:    EngineSignals | None
        _throttler:     SignalThrottler | None
        _disk_mon:      DiskMonitor | None
        _zoom_rows:     "dict[int, int]"
        _zoom_done:     "dict[int, int]"

        # ── UI 위젯 ────────────────────────────────────────────────────
        _bar_all:       "_QProgressBar"
        _tbl_zoom:      "QTableWidget"
        _btn_retry:     "QPushButton"
        _lbl_s:         "QLabel"
        _lbl_k:         "QLabel"
        _lbl_f:         "QLabel"
        _lbl_spd:       "QLabel"
        _lbl_spd2:      "QLabel"
        _lbl_eta_run:   "QLabel"
        _lbl_disk:      "QLabel"
        _e_out:         "QLineEdit"
        _e_style:       "QLineEdit"
        _cb_fmt:        "QComboBox"
        _cb_size:       "QComboBox"
        _cb_preset:     "QComboBox"
        _sp_z_min:      "QSpinBox"
        _sp_z_max:      "QSpinBox"
        _sp_lon_min:    "QDoubleSpinBox"
        _sp_lat_min:    "QDoubleSpinBox"
        _sp_lon_max:    "QDoubleSpinBox"
        _sp_lat_max:    "QDoubleSpinBox"
        _is_antimeridian: bool
        _lbl_antimer:   "QLabel"
        _sl_conc:       "QSlider"

        # ── 메서드 (다른 믹스인 / 메인 클래스 제공) ──────────────────
        def _apply_state(self, state: str) -> None: ...
        def _collect_config(self, calc_result) -> DownloadConfig | None : ...
        def _on_log(self, level: str, msg: str, ts: float) -> None: ...
        def _load_hist_table(self) -> None: ...


    def _as_widget(self) -> "QWidget":
        """Pylance용 QWidget 캐스트 헬퍼."""
        return cast(QWidget, self)

    # ══════════════════════════════════════════════════════════════════════
    # 다운로드 시작/제어
    # ══════════════════════════════════════════════════════════════════════

    def _launch_engine(self, cfg: DownloadConfig, total: int) -> None:
        """엔진 초기화·제출 공통 로직."""
        self._dl_loop    = _TileDownloadLoop()
        self._cancel_ev  = self._dl_loop.make_event()
        self._user_pause = self._dl_loop.make_event()
        self._disk_pause = self._dl_loop.make_event()

        self._engine_sig = EngineSignals()
        self._engine_sig.log_emitted.connect(self._on_log)
        self._engine_sig.disk_checked.connect(self._on_disk_chk)
        self._engine_sig.disk_full.connect(self._on_disk_full)
        self._engine_sig.disk_restored.connect(self._on_disk_restore)
        self._engine_sig.engine_finished.connect(self._on_done)
        self._engine_sig.engine_cancelled.connect(self._on_done)

        self._throttler = SignalThrottler(self._flush_progress)
        self._throttler.start()

        self._disk_mon = DiskMonitor(
            self._engine_sig, self._disk_pause,
            Path(self._e_out.text().strip()),
            loop=self._dl_loop._loop,
        )
        self._disk_mon.start()

        self._engine_future = self._dl_loop.submit(
            run_engine(cfg, self._cancel_ev, self._user_pause,
                    self._disk_pause, self._engine_sig, self._throttler)
        )
        self._engine_future.add_done_callback(self._on_future_done)


    def _on_future_done(self, fut) -> None:
        try:
            exc = fut.exception()
            if exc:
                tb = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
                error_print(f"[엔진] ★ 예외 ★\n{tb}")
                sig = self._engine_sig
                if sig is not None:
                    sig.log_emitted.emit("ERROR", f"엔진 예외: {exc}", time.time())
        except Exception:
            pass


    def _on_start(self) -> None:
        if self._calc_result is None:
            return
        cfg = self._collect_config(self._calc_result)
        if cfg is None:
            return

        for tmp in self._calc_result.existing.corrupt_tmp:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

        self._total_s = self._total_k = self._total_f = 0
        self._session_start = time.time()
        self._eta = ETACalculator()

        nc = self._calc_result.new_count
        self._bar_all.setMaximum(nc if nc > 0 else 1)
        self._bar_all.setValue(0 if nc > 0 else 1)

        info_print("[엔진] 전용 asyncio 루프 시작")
        self._apply_state(S.RUNNING)
        self._launch_engine(cfg, self._calc_result.tile_count)
        info_print(f"[엔진] 제출 완료: {self._engine_future}")


    def _on_pause(self) -> None:
        dl_loop    = self._dl_loop
        user_pause = self._user_pause
        if dl_loop is None or user_pause is None:
            return
        dl_loop.set_event(user_pause)
        self._eta.on_pause()
        self._apply_state(S.PAUSED)


    def _on_resume(self) -> None:
        dl_loop    = self._dl_loop
        user_pause = self._user_pause
        if dl_loop is None or user_pause is None:
            return
        dl_loop.clear_event(user_pause)
        self._eta.on_resume()
        self._apply_state(S.RUNNING)


    def _on_cancel(self) -> None:
        dl_loop    = self._dl_loop
        cancel_ev  = self._cancel_ev
        user_pause = self._user_pause
        if dl_loop is None or cancel_ev is None or user_pause is None:
            return
        self._apply_state(S.CANCELLING)
        dl_loop.set_event(cancel_ev)
        dl_loop.clear_event(user_pause)

    # ══════════════════════════════════════════════════════════════════════
    # 진행 업데이트
    # ══════════════════════════════════════════════════════════════════════

    def _flush_progress(self, s: int, k: int, f: int, z_delta: dict) -> None:
        """SignalThrottler flush 콜백."""
        self._total_s += s
        self._total_k += k
        self._total_f += f

        done      = self._total_s + self._total_f
        new_count = self._calc_result.new_count if self._calc_result else 0

        self._bar_all.setValue(min(done, new_count))

        self._lbl_s.setText(_t("tab_progress.lbl_success", n=f"{self._total_s:,}"))
        self._lbl_k.setText(_t("tab_progress.lbl_skip",    n=f"{self._total_k:,}"))
        self._lbl_f.setText(_t("tab_progress.lbl_fail",    n=f"{self._total_f:,}"))

        if z_delta:
            last_z = max(z_delta)
            self._eta.record(done, last_z)
            for z, delta in z_delta.items():
                self._update_zoom_row(z, delta)

        spd = self._eta.get_speed()
        remaining = max(0, new_count - done)
        eta = self._eta.get_eta(remaining)

        self._lbl_spd.setText( _t("tab_progress.lbl_speed", speed=f"{spd:.1f}"))
        self._lbl_spd2.setText(_t("statusbar.speed",         speed=f"{spd:.1f}"))
        if eta < float("inf"):
            self._lbl_eta_run.setText(
                _t("tab_progress.lbl_eta", eta=str(timedelta(seconds=int(eta))))
            )


    def _init_zoom_table(self, z_breakdown: dict[int, int]) -> None:
        self._tbl_zoom.setRowCount(0)
        self._zoom_rows.clear()
        self._zoom_done.clear()
        for z, total in sorted(z_breakdown.items()):
            row = self._tbl_zoom.rowCount()
            self._tbl_zoom.insertRow(row)
            self._tbl_zoom.setItem(row, 0, QTableWidgetItem(str(z)))
            bar = QProgressBar()
            bar.setMaximum(total)
            bar.setValue(0)
            bar.setFormat("%p%")
            self._tbl_zoom.setCellWidget(row, 1, bar)
            self._tbl_zoom.setItem(row, 2, QTableWidgetItem(f"0 / {total:,}"))
            self._tbl_zoom.setItem(row, 3, QTableWidgetItem(_t("tab_progress.zoom_running")))
            self._zoom_rows[z] = row
            self._zoom_done[z] = 0


    def _update_zoom_row(self, z: int, delta: int) -> None:
        if z not in self._zoom_rows:
            return
        row  = self._zoom_rows[z]
        self._zoom_done[z] = self._zoom_done.get(z, 0) + delta
        done = self._zoom_done[z]
        bar  = cast(QProgressBar, self._tbl_zoom.cellWidget(row, 1))
        if bar is None:
            return
        total = bar.maximum()
        bar.setValue(min(done, total))
        item = self._tbl_zoom.item(row, 2)
        if item:
            item.setText(f"{done:,} / {total:,}")
        if done >= total:
            status = self._tbl_zoom.item(row, 3)
            if status:
                status.setText(_t("tab_progress.zoom_done"))
            bar.setStyleSheet("QProgressBar::chunk{background:#4CAF50;}")

    # ══════════════════════════════════════════════════════════════════════
    # 엔진 완료
    # ══════════════════════════════════════════════════════════════════════

    def _on_done(self, stats: dict) -> None:
        if self._dl_loop is None:
            return
                
        if self._disk_mon:
            self._disk_mon.stop()
        if self._throttler:
            self._throttler.stop()
        dl = self._dl_loop
        if dl is not None:
            dl.stop()
            self._dl_loop = None

        cancelled = bool(self._cancel_ev and self._cancel_ev.is_set())
        self._apply_state(S.IDLE)
        self._calc_result = None

        dur = int(time.time() - self._session_start)

        title   = _t("engine.dlg_cancelled_title") if cancelled else _t("engine.dlg_done_title")
        msg_key = "engine.dlg_cancelled_msg"       if cancelled else "engine.dlg_done_msg"
        _DarkMessageBox(
            self._as_widget(), kind='info',
            title=title,
            body=_t(msg_key,
               success=f"{stats['success']:,}",
               skip=f"{stats['skip']:,}",
               fail=f"{stats['fail']:,}",
               dur=str(timedelta(seconds=dur))),
        ).exec()

        if stats["fail"] > 0:
            self._btn_retry.setEnabled(True)
            self._btn_retry.setProperty("failed_tiles", stats["failed_tiles"])

        preset = self._cb_preset.currentData() or "custom"
        append_history({
            "started_at":   datetime.fromtimestamp(stats["started_at"]).isoformat(),
            "duration_sec": dur,
            "config": {
                "style_id":       self._e_style.text(),
                "preset":         preset,
                "tile_format":    self._cb_fmt.currentData(),
                "tile_size_mode": self._cb_size.currentData(),
                "z_min":          self._sp_z_min.value(),
                "z_max":          self._sp_z_max.value(),
                "bbox": {
                    "lon_min": self._sp_lon_min.value(),
                    "lat_min": self._sp_lat_min.value(),
                    "lon_max": self._sp_lon_max.value(),
                    "lat_max": self._sp_lat_max.value(),
                },
                "antimeridian": self._is_antimeridian,
                "concurrency":  self._sl_conc.value(),
                "out_root":     self._e_out.text(),
            },
            "stats": {
                "total":   stats["success"] + stats["skip"] + stats["fail"],
                "success": stats["success"],
                "skip":    stats["skip"],
                "fail":    stats["fail"],
            },
        })
        self._load_hist_table()
        self._engine_sig = None 
        self._throttler = None   


    def _on_retry_failed(self) -> None:
        """실패 타일 목록으로 동일 설정 재시도."""
        failed_tiles = self._btn_retry.property("failed_tiles")
        if not failed_tiles:
            return

        count = len(failed_tiles)

        _retry_dlg = _DarkMessageBox(
            self._as_widget(), kind='question',
            title=_t("engine.dlg_retry_title"),
            body=_t("engine.dlg_retry_msg", count=f"{count:,}"),
        )
        if _retry_dlg.exec() != QDialog.DialogCode.Accepted:
            return

        self._btn_retry.setEnabled(False)
        self._btn_retry.setProperty("failed_tiles", [])

        z_breakdown: dict[int, int] = {}
        for tile in failed_tiles:
            z_breakdown[tile["z"]] = z_breakdown.get(tile["z"], 0) + 1

        fake_result = CalcResult(
            tile_count   = count,
            new_count    = count,
            size_mb      = 0.0,
            eta_sec      = 0.0,
            existing     = ExistingScanResult(count=0, size_bytes=0, corrupt_tmp=[]),
            z_breakdown  = z_breakdown,
            bbox_list    = [Bbox(-180.0, -90.0, 180.0, 90.0)],
            antimeridian = False,
        )

        cfg = self._collect_config(fake_result)
        if cfg is None:
            return

        # ── 세션 초기화 ──────────────────────────────────────────────────
        self._calc_result    = fake_result
        self._total_s = self._total_k = self._total_f = 0
        self._session_start  = time.time()
        self._eta            = ETACalculator()
        self._bar_all.setValue(0)
        self._bar_all.setMaximum(count)

        self._init_zoom_table(z_breakdown)
        self._apply_state(S.RUNNING)
        self._launch_engine(cfg, count) 

    # ══════════════════════════════════════════════════════════════════════
    # 디스크 모니터 콜백
    # ══════════════════════════════════════════════════════════════════════

    def _on_disk_chk(self, free: float, total: float) -> None:
        self._lbl_disk.setText(_t("statusbar.disk", gb=f"{free / 1024 ** 3:.1f}"))


    def _on_disk_full(self) -> None:
        self._lbl_disk.setStyleSheet("color:red;")
        if self._state == S.RUNNING:
            self._eta.on_pause()
            self._apply_state(S.PAUSED)


    def _on_disk_restore(self) -> None:
        self._lbl_disk.setStyleSheet("")
        if self._state == S.PAUSED:
            try:
                user_still_paused = (
                    self._user_pause is not None
                    and self._user_pause.is_set()
                )
            except Exception:
                user_still_paused = False
            if not user_still_paused:
                self._eta.on_resume()
                self._apply_state(S.RUNNING)
