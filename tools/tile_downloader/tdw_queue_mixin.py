# -*- coding: utf-8 -*-
# tools\tile_downloader\tdw_queue_mixin.py

"""
TileDownloaderWindow — 작업 큐(Job Queue) 관련 로직 믹스인.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QProgressBar,
    QTableWidgetItem,
    QWidget,
)

from .dl_loop import _TileDownloadLoop
from .downloader_engine import (
    CalcWorker,
    DownloadConfig,
    EngineSignals,
    SignalThrottler,
    run_engine,
)
from .job_queue import DownloadJob, JobQueue, JobStatus
from .tile_calculator import Bbox

from utils.debug import error_print, info_print
from utils.lang_manager import t
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox

if TYPE_CHECKING:
    from .tile_downloader_window import TileDownloaderWindow


def _t(key: str, **kw) -> str:
    return t(f"tile_downloader.{key}", **kw)


class TdwQueueMixin:
    """작업 큐 관련 모든 메서드를 제공하는 믹스인."""

    _STATUS_COLOR = {
        JobStatus.PENDING:   "#888888",
        JobStatus.RUNNING:   "#4CAF50",
        JobStatus.DONE:      "#2196F3",
        JobStatus.FAILED:    "#E05252",
        JobStatus.CANCELLED: "#FF9800",
        JobStatus.SKIPPED:   "#9cdcfe",
    }
    
    if TYPE_CHECKING:
        from PySide6.QtWidgets import (
            QComboBox, QDoubleSpinBox, QLabel, QLineEdit,
            QPushButton, QSlider, QSpinBox, QTableWidget
        )
        import asyncio

        _job_queue:       "JobQueue"
        _queue_running:   bool
        _queue_path:      Path
        _q_cancel_ev:     "asyncio.Event | None"
        _dl_loop:         "_TileDownloadLoop | None"
        _queue_calc:      "CalcWorker | None"
        _queue_sig:       "EngineSignals | None"
        _queue_throttler: "SignalThrottler | None"
        _is_antimeridian:    bool 
        
        # UI 위젯
        _tbl_queue:    "QTableWidget"
        _btn_q_run_all:"QPushButton"
        _btn_q_stop:   "QPushButton"
        _cb_after:     "QComboBox"
        _e_url:        "QLineEdit"
        _e_style:      "QLineEdit"
        _e_out:        "QLineEdit"
        _cb_fmt:       "QComboBox"
        _cb_size:      "QComboBox"
        _cb_preset:    "QComboBox"
        _sp_z_min:     "QSpinBox"
        _sp_z_max:     "QSpinBox"
        _sp_lon_min:   "QDoubleSpinBox"
        _sp_lat_min:   "QDoubleSpinBox"
        _sp_lon_max:   "QDoubleSpinBox"
        _sp_lat_max:   "QDoubleSpinBox"
        _lbl_antimer:  "QLabel"
        _sl_conc:      "QSlider"
        _sp_q_delay:   "QDoubleSpinBox"


        def _on_log(self, level: str, msg: str, ts: float) -> None: ...


    def _as_widget(self) -> "QWidget":
        """Pylance용 QWidget 캐스트 헬퍼 — 런타임 오버헤드 없음."""
        return cast(QWidget, self)

    # ══════════════════════════════════════════════════════════════════════
    # 큐 버튼 핸들러
    # ══════════════════════════════════════════════════════════════════════

    def _q_add(self) -> None:
        """현재 설정을 작업 큐에 추가."""
        url   = self._e_url.text().strip()
        style = self._e_style.text().strip()
        out   = self._e_out.text().strip()
        if not url or not style or not out:
            _DarkMessageBox(self._as_widget(), kind='warning', title=_t("tab_queue.dlg_add_error_title"), body=_t("tab_queue.dlg_add_error_msg")).exec()
            return

        preset_name = self._cb_preset.currentText() or "custom"
        job = DownloadJob(
            base_url       = url,
            style_id       = style,
            tile_format    = self._cb_fmt.currentData(),
            tile_size_mode = self._cb_size.currentData(),
            z_min          = self._sp_z_min.value(),
            z_max          = self._sp_z_max.value(),
            lon_min        = self._sp_lon_min.value(),
            lat_min        = self._sp_lat_min.value(),
            lon_max        = self._sp_lon_max.value(),
            lat_max        = self._sp_lat_max.value(),
            antimeridian   = self._is_antimeridian,
            concurrency    = self._sl_conc.value(),
            out_root       = out,
            delay_after    = self._sp_q_delay.value(),
            preset_name    = preset_name,
        )
        self._job_queue.add(job)
        self._refresh_queue_table()
        self._q_save()


    def _q_run_all(self) -> None:
        """대기 중인 모든 작업을 순차 실행."""
        if self._job_queue.pending_count == 0:
            _DarkMessageBox(self._as_widget(), kind='info', title=_t("tab_queue.dlg_no_pending_title"), body=_t("tab_queue.dlg_no_pending_msg")).exec()
            return
        self._queue_running = True
        self._btn_q_run_all.setEnabled(False)
        self._btn_q_stop.setEnabled(True)
        self._run_next_job()


    def _q_stop(self):
        self._queue_running = False

        if self._queue_calc is not None:
            self._queue_calc.cancel()
        q_cancel = self._q_cancel_ev
        dl = self._dl_loop
        if q_cancel is not None and dl is not None:
            dl.set_event(q_cancel)
        self._btn_q_run_all.setEnabled(True)
        self._btn_q_stop.setEnabled(False)


    def _q_move(self, direction: int) -> None:
        """선택된 작업을 위/아래로 이동."""
        row = self._tbl_queue.currentRow()
        if row < 0:
            return
        ok = (self._job_queue.move_up(row)
              if direction == -1 else self._job_queue.move_down(row))
        if ok:
            self._refresh_queue_table()
            self._tbl_queue.selectRow(row + direction)


    def _q_delete(self) -> None:
        """선택된 작업을 큐에서 삭제."""
        row = self._tbl_queue.currentRow()
        if row < 0:
            return
        job = self._job_queue.jobs[row]
        if job.status == JobStatus.RUNNING:
            _DarkMessageBox(
                self._as_widget(), kind='warning',
                title=_t("tab_queue.dlg_del_running_title"),
                body=_t("tab_queue.dlg_del_running_msg"),
            ).exec()
            return
        self._job_queue.remove(job.job_id)
        self._refresh_queue_table()
        self._q_save()


    def _q_clear_done(self) -> None:
        """완료/취소/실패 작업을 모두 제거."""
        self._job_queue.clear_done()
        self._refresh_queue_table()
        self._q_save()


    def _q_reset_failed(self) -> None:
        """실패·취소된 작업을 PENDING 상태로 초기화."""
        n = self._job_queue.reset_failed()
        m = self._job_queue.reset_cancelled()  
        self._refresh_queue_table()
        self._q_save()                        
        if n or m:
            info_print(
                f"[큐] 재시도 대기 전환 → 실패 {n}개 / 취소 {m}개"
            )


    def _q_save(self) -> None:
        """큐 상태를 JSON 파일에 저장."""
        self._job_queue.save(self._queue_path)

    # ══════════════════════════════════════════════════════════════════════
    # 큐 실행 엔진
    # ══════════════════════════════════════════════════════════════════════

    def _run_next_job(self) -> None:
        """다음 PENDING 작업을 꺼내 실행. 없으면 전체 완료 처리."""
        if not self._queue_running:
            return

        job = self._job_queue.next_pending()
        if job is None:
            self._queue_running = False
            self._btn_q_run_all.setEnabled(True)
            self._btn_q_stop.setEnabled(False)
            self._refresh_queue_table()
            self._q_save()
            self._on_queue_all_done()
            return

        job.status     = JobStatus.RUNNING
        job.started_at = time.time()
        job.progress   = 0
        job.tiles_done = 0
        job.tiles_fail = 0
        self._refresh_queue_table()

        QTimer.singleShot(500, lambda: self._run_job(job))


    def _run_job(self, job: DownloadJob) -> None:
        """단일 큐 작업 실행 — CalcWorker 계산 후 엔진 시작."""
        
        if not self._queue_running: 
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
            self._refresh_queue_table()
            self._q_save()
            return
                
        cfg = DownloadConfig(
            base_url       = job.base_url,
            style_id       = job.style_id,
            tile_format    = job.tile_format,
            tile_size_mode = job.tile_size_mode,
            z_min          = job.z_min,
            z_max          = job.z_max,
            bbox           = Bbox(
                                 job.lon_min, job.lat_min,
                                 job.lon_max, job.lat_max,
                             ),
            antimeridian   = job.antimeridian,
            concurrency    = job.concurrency,
            out_root       = Path(job.out_root),
            calc_result    = None,
        )

        self._queue_calc = CalcWorker(cfg)
        self._queue_calc.signals.finished.connect(
            lambda res: self._on_queue_calc_done(job, cfg, res)
        )
        self._queue_calc.signals.error.connect(
            lambda e: self._on_queue_job_failed(job, _t("tab_queue.calc_error", e=e))
        )
        self._queue_calc.signals.cancelled.connect(
            lambda: self._on_queue_calc_cancelled(job)
        )
        self._queue_calc.start()


    def _on_queue_calc_done(
        self,
        job:         DownloadJob,
        cfg:         DownloadConfig,
        calc_result,
    ) -> None:
        """CalcWorker 완료 — 엔진 실행."""

        if not self._queue_running:
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
            self._refresh_queue_table()
            self._q_save()
            return
            
        cfg.calc_result = calc_result
        job.tiles_total = calc_result.tile_count

        sig       = EngineSignals()
        throttler = SignalThrottler(
            lambda s, k, f, z: self._on_queue_progress(job, s, k, f)
        )
        throttler.start()
        self._queue_throttler = throttler
        self._queue_sig       = sig

        _thr = throttler
        sig.engine_finished.connect(
            lambda stats, _thr=_thr: self._on_queue_job_done(job, stats, _thr)
        )
        sig.engine_cancelled.connect(
            lambda stats, _thr=_thr: self._on_queue_job_cancelled(job, stats, _thr)
        )
        sig.log_emitted.connect(self._on_log)

        self._dl_loop     = _TileDownloadLoop()
        cancel_ev         = self._dl_loop.make_event()
        user_pause        = self._dl_loop.make_event()
        disk_pause        = self._dl_loop.make_event()
        self._q_cancel_ev = cancel_ev

        fut = self._dl_loop.submit(
            run_engine(cfg, cancel_ev, user_pause, disk_pause, sig, throttler)
        )

        def _done_cb(f):
            try:
                exc = f.exception()
                if exc:
                    self._on_queue_job_failed(job, str(exc))
            except Exception:
                pass

        fut.add_done_callback(_done_cb)


    def _on_queue_calc_cancelled(self, job: DownloadJob) -> None:
        """CalcWorker 취소 완료 — job 상태를 CANCELLED로 정리."""
        if job.status == JobStatus.RUNNING: 
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
            self._refresh_queue_table()
            self._q_save()
            info_print(f"[큐] CalcWorker 취소 완료: {job.name}")
            

    def _on_queue_progress(
        self,
        job: DownloadJob,
        s: int, k: int, f: int,
    ) -> None:
        job.tiles_done += s + k   
        job.tiles_fail += f      
        if job.tiles_total > 0:
            job.progress = int(job.tiles_done / job.tiles_total * 100)
        self._update_queue_row(job)


    def _on_queue_job_done(
        self,
        job:      DownloadJob,
        stats:    dict,
        throttler: SignalThrottler,
    ) -> None:
        """큐 작업 정상 완료."""
        throttler.stop()
        dl = self._dl_loop
        if dl is not None:
            dl.stop()
            self._dl_loop = None

        job.status      = JobStatus.DONE
        job.progress    = 100
        job.tiles_done = job.tiles_total
        job.finished_at = time.time()
        self._refresh_queue_table()
        self._q_save()

        info_print(
            f"[큐] 완료: {job.name}  "
            f"성공={stats.get('success', 0):,}  "
            f"실패={stats.get('fail', 0):,}"
        )

        delay_ms = max(100, int((job.delay_after or 0) * 1000))
        QTimer.singleShot(delay_ms, self._run_next_job)


    def _on_queue_job_cancelled(
        self,
        job:      DownloadJob,
        stats:    dict,
        throttler: SignalThrottler,
    ) -> None:
        """큐 작업 취소 — 큐 일괄 실행도 중단."""
        throttler.stop()
        dl = self._dl_loop
        if dl is not None:
            dl.stop()
            self._dl_loop = None

        job.status      = JobStatus.CANCELLED
        job.finished_at = time.time()
        self._queue_running = False
        self._btn_q_run_all.setEnabled(True)
        self._btn_q_stop.setEnabled(False)
        self._refresh_queue_table()
        self._q_save()


    def _on_queue_job_failed(
        self,
        job:   DownloadJob,
        error: str,
    ) -> None:
        """큐 작업 실패."""
        if self._queue_throttler is not None:
            self._queue_throttler.stop()
            self._queue_throttler = None

        dl = self._dl_loop
        if dl is not None:
            dl.stop()
            self._dl_loop = None

        job.status      = JobStatus.FAILED
        job.error_msg   = error
        job.finished_at = time.time()
        self._queue_running = False
        self._btn_q_run_all.setEnabled(True)
        self._btn_q_stop.setEnabled(False)
        self._refresh_queue_table()
        self._q_save()
        error_print(f"[큐] 실패: {job.name}  {error}")


    def _on_queue_all_done(self) -> None:
        """모든 큐 작업 완료 — 완료 후 동작 실행."""
        action = self._cb_after.currentData()
        if action == "none":
            _DarkMessageBox(self._as_widget(), kind='info', title=_t("tab_queue.dlg_all_done_title"), body=_t("tab_queue.dlg_all_done_msg")).exec()
        elif action == "sleep":
            _DarkMessageBox(self._as_widget(), kind='info', title=_t("tab_queue.dlg_sleep_title"), body=_t("tab_queue.dlg_sleep_msg")).exec()
            subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
            )
        elif action == "shutdown":

            _DarkMessageBox(self._as_widget(), kind='info', title=_t("tab_queue.dlg_shutdown_title"), body=_t("tab_queue.dlg_shutdown_msg")).exec()
            subprocess.run(["shutdown", "/s", "/t", "30"])

    # ══════════════════════════════════════════════════════════════════════
    # 큐 테이블 UI
    # ══════════════════════════════════════════════════════════════════════

    def _refresh_queue_table(self) -> None:
        """큐 전체 테이블 재구성."""
        self._tbl_queue.setRowCount(0)
        for job in self._job_queue.jobs:
            self._insert_queue_row(job)


    def _insert_queue_row(self, job: DownloadJob) -> None:
        """테이블 끝에 작업 행 삽입."""
        row = self._tbl_queue.rowCount()
        self._tbl_queue.insertRow(row)

        # 열 0: 이름 (job_id 를 UserRole 에 보관)
        name_item = QTableWidgetItem(job.name)
        name_item.setData(Qt.ItemDataRole.UserRole, job.job_id)
        self._tbl_queue.setItem(row, 0, name_item)

        # 열 1~3: 메타 정보
        self._tbl_queue.setItem(
            row, 1, QTableWidgetItem(f"Z{job.z_min}~{job.z_max}"))
        self._tbl_queue.setItem(
            row, 2, QTableWidgetItem(f"{job.tile_format}/{job.tile_size_mode}"))
        self._tbl_queue.setItem(
            row, 3, QTableWidgetItem(
                f"{job.tiles_done:,}/{job.tiles_total:,}"
                if job.tiles_total else "--"
            ))

        # 열 4: 진행 막대
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(job.progress)
        bar.setFormat(f"{job.progress}%")
        self._tbl_queue.setCellWidget(row, 4, bar)

        # 열 5: 상태 (색상 구분)

        status_item = QTableWidgetItem(job.status.value)
        status_item.setForeground(
            QColor(self._STATUS_COLOR.get(job.status, "#888888"))
        )
        self._tbl_queue.setItem(row, 5, status_item)

        # 열 6~7: 소요 시간 / 오류 요약
        self._tbl_queue.setItem(row, 6, QTableWidgetItem(job.elapsed_str()))
        self._tbl_queue.setItem(
            row, 7, QTableWidgetItem((job.error_msg or "")[:30]))


    def _update_queue_row(self, job: DownloadJob) -> None:
        """job_id 가 일치하는 행만 부분 갱신 (다운로드 중 호출)."""
        for row in range(self._tbl_queue.rowCount()):
            item = self._tbl_queue.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == job.job_id:
                bar = cast(QProgressBar, self._tbl_queue.cellWidget(row, 4))
                if bar is not None:
                    bar.setValue(job.progress)
                    bar.setFormat(f"{job.progress}%")
                col3 = self._tbl_queue.item(row, 3)
                if col3:
                    col3.setText(
                        f"{job.tiles_done:,}/{job.tiles_total:,}"
                        if job.tiles_total else "--"
                    )
                break

