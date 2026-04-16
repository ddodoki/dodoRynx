# tools\tile_downloader\downloader_engine.py

from __future__ import annotations

import asyncio
import random
import shutil
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import aiofiles
import aiohttp

from PySide6.QtCore import QObject, QThread, QTimer


from .signals import CalcSignals, EngineSignals
from .tile_calculator import (
    Bbox,
    CalcResult,
    SIZE_FOLDER_MAP,
    build_out_path,
    build_tile_generator,
    build_tile_url,
    estimate_disk_size_mb,
    estimate_z_breakdown,
    scan_existing,
    split_antimeridian,
)

from utils.debug import debug_print, error_print, info_print
from utils.lang_manager import t

def _t(key: str, **kw) -> str:
    return t(f"tile_downloader.{key}", **kw)


RESULT_SUCCESS = 0
RESULT_SKIP    = 1
RESULT_FAIL    = 2
SENTINEL       = None      
DISK_WARN_BYTES = 500 * 1024 * 1024   # 500 MB


async def _cancel_aware_sleep(seconds: float, cancel_event: asyncio.Event) -> None:
    """cancel_event 세팅 시 즉시 반환, 아니면 seconds 후 반환."""
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass  

# ── DownloadConfig ─────────────────────────────────────────────────────────────

@dataclass
class DownloadConfig:
    base_url:       str
    style_id:       str
    tile_format:    str
    tile_size_mode: str
    z_min:          int
    z_max:          int
    bbox:           Bbox
    antimeridian:   bool
    concurrency:    int
    out_root:       Path
    calc_result:    CalcResult | None = None

    def to_dict(self) -> dict:
        return {
            "base_url": self.base_url, "style_id": self.style_id,
            "tile_format": self.tile_format, "tile_size_mode": self.tile_size_mode,
            "z_min": self.z_min, "z_max": self.z_max,
            "bbox": self.bbox.to_dict(), "antimeridian": self.antimeridian,
            "concurrency": self.concurrency, "out_root": str(self.out_root),
        }


# ── CalcWorker (QThread) ───────────────────────────────────────────────────────

class CalcWorker(QThread):
    """
    사전 계산 전담 스레드.
    1) 줌별 타일 수 수식 계산  2) 기존 완료 파일 스캔
    """

    def __init__(self, config: DownloadConfig):
        super().__init__()
        self.config  = config
        self.signals = CalcSignals()
        self._cancel = False


    def cancel(self):
        self._cancel = True


    def run(self):
        try:
            cfg = self.config
            self.signals.progress.emit(_t("grp_estimate.calc_bbox"))
            bbox_list = split_antimeridian(cfg.bbox)

            self.signals.progress.emit(_t("grp_estimate.calc_z"))
            z_breakdown = estimate_z_breakdown(cfg.z_min, cfg.z_max, bbox_list)
            if self._cancel:
                self.signals.cancelled.emit()
                return

            total = sum(z_breakdown.values())
            self.signals.progress.emit(_t("grp_estimate.calc_scan", total=f"{total:,}"))

            existing = scan_existing(
                cfg.out_root, cfg.style_id, cfg.tile_size_mode,
                cfg.tile_format, z_breakdown,
                cancel_check=lambda: self._cancel,
            )
            if self._cancel:
                self.signals.cancelled.emit()
                return

            new_count = max(0, total - existing.count)
            size_mb   = estimate_disk_size_mb(new_count, cfg.tile_format, cfg.tile_size_mode)
            avg_tps   = max(1.0, cfg.concurrency * 0.4)
            eta_sec   = new_count / avg_tps

            self.signals.finished.emit(CalcResult(
                tile_count=total, new_count=new_count, size_mb=size_mb,
                eta_sec=eta_sec, existing=existing,
                z_breakdown=z_breakdown, bbox_list=bbox_list,
                antimeridian=cfg.antimeridian,
            ))
        except Exception as e:
            self.signals.error.emit(str(e))


# ── SignalThrottler ────────────────────────────────────────────────────────────

class SignalThrottler:

    def __init__(self, flush_callback: Callable, interval_ms: int = 200):
        self._cb      = flush_callback
        self._success = 0
        self._skip    = 0
        self._fail    = 0
        self._z_delta: dict[int, int] = {}
        self._lock    = threading.Lock() 
        self._timer   = QTimer()
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._flush)


    def start(self):
        self._timer.start()


    def record(self, result_type: int, z: int):
        with self._lock:               
            self._z_delta[z] = self._z_delta.get(z, 0) + 1
            if   result_type == RESULT_SUCCESS: self._success += 1
            elif result_type == RESULT_SKIP:    self._skip    += 1
            else:                               self._fail    += 1


    def _flush(self):
        with self._lock:  
            if self._success + self._skip + self._fail == 0:
                return
            s, k, f = self._success, self._skip, self._fail
            zd = dict(self._z_delta)
            self._success = 0
            self._skip    = 0
            self._fail    = 0
            self._z_delta.clear()
        self._cb(s, k, f, zd)      


    def stop(self):
        self._timer.stop()
        self._flush()


# ── ETACalculator ──────────────────────────────────────────────────────────────

class ETACalculator:
    """
    슬라이딩 윈도우 기반 ETA. Pause 시간 제외, 줌 레벨 전환 시 윈도우 리셋.
    """

    def __init__(self, window_size: int = 50):
        self._window:       deque = deque(maxlen=window_size)
        self._paused_at:    float | None = None
        self._total_paused: float = 0.0
        self._prev_z:       int | None = None


    def record(self, completed_total: int, current_z: int):
        if self._prev_z is not None and current_z != self._prev_z:
            self._window.clear() 
        self._prev_z = current_z
        now = time.monotonic() - self._total_paused
        self._window.append((now, completed_total))


    def get_eta(self, remaining: int) -> float:
        if len(self._window) < 2:
            return float("inf")
        t0, c0 = self._window[0]
        t1, c1 = self._window[-1]
        dt = t1 - t0
        if dt <= 0:
            return float("inf")
        speed = (c1 - c0) / dt
        return remaining / speed if speed > 0 else float("inf")


    def get_speed(self) -> float:
        if len(self._window) < 2:
            return 0.0
        t0, c0 = self._window[0]
        t1, c1 = self._window[-1]
        dt = t1 - t0
        return (c1 - c0) / dt if dt > 0 else 0.0


    def on_pause(self):
        if self._paused_at is None:
            self._paused_at = time.monotonic()


    def on_resume(self):
        if self._paused_at is not None:
            self._total_paused += time.monotonic() - self._paused_at
            self._paused_at = None


# ══════════════════════════════════════════════════════════════════════════════
# 핵심 비동기 엔진
# ══════════════════════════════════════════════════════════════════════════════

async def run_engine(config, cancel_event, user_pause, disk_pause, signals, throttler):

    info_print(f'[엔진] run_engine 진입: z={config.z_min}~{config.z_max}, '
               f'concurrency={config.concurrency}, fmt={config.tile_format}')
    info_print(f'[엔진] out_root 존재 여부: {config.out_root} → '
               f'{Path(config.out_root).exists()}')
    info_print(f'[엔진] bbox_list 개수: {len(config.calc_result.bbox_list)}')
    debug_print(f'[엔진] bbox_list: {config.calc_result.bbox_list}')

    stats = {
        'success': 0, 'skip': 0, 'fail': 0,
        'started_at': time.time(), 'finished_at': None,
        'failed_tiles': [],
    }
    connector = aiohttp.TCPConnector(limit=config.concurrency, force_close=False)
    queue: asyncio.Queue = asyncio.Queue(maxsize=config.concurrency * 4)

    try:
        async with aiohttp.ClientSession(connector=connector,
                                         connector_owner=True) as session:
            info_print('[엔진] aiohttp 세션 생성 완료')

            producer_task = asyncio.ensure_future(
                _producer(config, queue, cancel_event, signals)
            )
            consumer_tasks = [
                asyncio.ensure_future(
                    _consumer(i, queue, session, config, signals,
                              throttler, stats, cancel_event,
                              user_pause, disk_pause)
                )
                for i in range(config.concurrency)
            ]
            info_print(f'[엔진] 프로듀서 1개 + 컨슈머 {config.concurrency}개 시작')

            await producer_task
            info_print(f'[엔진] 프로듀서 완료, 큐 소진 대기 중...')

            if cancel_event.is_set():
                drained = 0
                while True:
                    try:
                        queue.get_nowait()
                        queue.task_done()
                        drained += 1
                    except asyncio.QueueEmpty:
                        break
                info_print(f'[엔진] 취소 드레인: {drained}개')

            await queue.join()
            info_print(f'[엔진] 큐 소진 완료')

            for task in consumer_tasks:
                task.cancel()
            await asyncio.gather(*consumer_tasks, return_exceptions=True)

    except Exception as e:
        tb = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
        error_print(f'[엔진] run_engine 예외:\n{tb}')
        signals.log_emitted.emit('ERROR', _t("engine.log_error", e=e), time.time())
    finally:
        stats['finished_at'] = time.time()
        info_print(f'[엔진] 완료: success={stats["success"]}, '
                   f'skip={stats["skip"]}, fail={stats["fail"]}')
        if cancel_event.is_set():
            signals.engine_cancelled.emit(stats)
        else:
            signals.engine_finished.emit(stats)


# ── Producer ───────────────────────────────────────────────────────────────────

async def _producer(config, queue, cancel_event, signals):

    gen = build_tile_generator(
        config.z_min, config.z_max, config.calc_result.bbox_list
    )
    count = 0
    info_print(f'[프로듀서] 시작: z={config.z_min}~{config.z_max}')

    try:
        for tile in gen:
            if cancel_event.is_set():
                info_print(f'[프로듀서] 취소 감지, {count}개 투입 후 중단')
                break
            count += 1
            if count <= 5:   
                debug_print(f'[프로듀서] 타일 샘플: {tile}')
            while True:
                try:
                    await asyncio.wait_for(queue.put(tile), timeout=1.0)
                    break
                except asyncio.TimeoutError:
                    if cancel_event.is_set():
                        return
        info_print(f'[프로듀서] 제너레이터 소진: 총 {count}개 투입')
    finally:
        placed = 0
        for _ in range(config.concurrency):
            for _ in range(10):
                try:
                    await asyncio.wait_for(queue.put(SENTINEL), timeout=2.0)
                    placed += 1
                    break
                except asyncio.TimeoutError:
                    pass
        info_print(f'[프로듀서] SENTINEL 투입: {placed}/{config.concurrency}')
        if placed < config.concurrency:
            error_print(f'[프로듀서] SENTINEL 부족! {placed}/{config.concurrency}')
            cancel_event.set()
            signals.log_emitted.emit(
                'ERROR',
                _t("engine.log_sentinel_fail", placed=placed, concurrency=config.concurrency),
                time.time(),
            )


# ── Consumer ───────────────────────────────────────────────────────────────────

async def _consumer(
    worker_id:    int,
    queue:        asyncio.Queue,
    session:      aiohttp.ClientSession,
    config:       DownloadConfig,
    signals:      EngineSignals,
    throttler:    SignalThrottler,
    stats:        dict,
    cancel_event: asyncio.Event,
    user_pause:   asyncio.Event,
    disk_pause:   asyncio.Event,
):
    while True:
        try:
            tile = await asyncio.wait_for(queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            if cancel_event.is_set():
                return
            continue

        if tile is SENTINEL:
            queue.task_done()
            return

        try:
            while user_pause.is_set() or disk_pause.is_set():
                await asyncio.sleep(0.1)
                if cancel_event.is_set():
                    break  

            if cancel_event.is_set():
                pass     
            else:
                z, x, y = tile
                await _process_tile(
                    worker_id, z, x, y,
                    session, config, signals, throttler, stats,
                    cancel_event,
                )

        except Exception as e:
            signals.log_emitted.emit(
                "ERROR", f"Worker-{worker_id} 예외: {e}", time.time()
            )
        finally:
            queue.task_done() 


# ── 타일 처리 ──────────────────────────────────────────────────────────────────

async def _process_tile(
    worker_id: int, z: int, x: int, y: int,
    session:   aiohttp.ClientSession,
    config:    DownloadConfig,
    signals:   EngineSignals,
    throttler: SignalThrottler,
    stats:     dict,
    cancel_event: asyncio.Event, 
):
    if cancel_event.is_set():
        return

    out_path = build_out_path(
        config.out_root, config.style_id,
        config.tile_size_mode, config.tile_format, z, x, y
    )

    try:
        if out_path.exists() and out_path.stat().st_size > 0:
            stats["skip"] += 1
            throttler.record(RESULT_SKIP, z)
            return
    except OSError:
        pass

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        signals.log_emitted.emit("ERROR", _t("engine.log_io", e=e), time.time())
        stats["fail"] += 1
        throttler.record(RESULT_FAIL, z)
        return

    url = build_tile_url(
        config.base_url, config.style_id,
        config.tile_size_mode, config.tile_format, z, x, y
    )
    await _download_with_retry(
        url, out_path, session, config, signals, throttler, stats, z, x, y,
        cancel_event, 
    )


async def _download_with_retry(
    url:       str,
    out_path:  Path,
    session:   aiohttp.ClientSession,
    config:    DownloadConfig,
    signals:   EngineSignals,
    throttler: SignalThrottler,
    stats:     dict,
    z: int, x: int, y: int,
    cancel_event: asyncio.Event,
):
    tmp_path = out_path.with_suffix(".tmp")

    for attempt in range(3):
        if cancel_event.is_set():
            break

        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(connect=10, total=30),
            ) as r:

                if r.status == 200:
                    await _stream_to_file(r, tmp_path)

                    try:
                        tmp_path.rename(out_path)
                    except OSError:
                        shutil.move(str(tmp_path), str(out_path))
                    stats["success"] += 1
                    throttler.record(RESULT_SUCCESS, z)
                    return

                elif r.status in (400, 404):
                    signals.log_emitted.emit(
                        "WARN",
                        _t("engine.log_http_skip", status=r.status, url=url),
                        time.time(),
                    )
                    stats["skip"] += 1
                    throttler.record(RESULT_SKIP, z)
                    return

                else:
                    signals.log_emitted.emit(
                        "WARN",
                        _t("engine.log_http_retry", status=r.status,
                           attempt=attempt + 1, url=url),
                        time.time(),
                    )

        except asyncio.TimeoutError:
            signals.log_emitted.emit(
                "WARN",
                _t("engine.log_timeout", attempt=attempt + 1, url=url),
                time.time(),
            )
        except OSError as e:
            if e.errno == 28: 
                signals.disk_full.emit()
                await asyncio.sleep(5)
                continue
            signals.log_emitted.emit(
                "ERROR", _t("engine.log_io", e=e), time.time()
            )
            break
        except aiohttp.ClientError as e:
            signals.log_emitted.emit(
                "WARN",
                _t("engine.log_net", e=e, attempt=attempt + 1),
                time.time(),
            )

        if attempt < 2:
            await _cancel_aware_sleep(
                (2 ** attempt) + random.uniform(0.0, 0.5),
                cancel_event,
            )

    # ─── 루프 탈출 (재시도 소진 또는 취소) ───────────────────────────
    tmp_path.unlink(missing_ok=True)

    if cancel_event.is_set():
        return

    stats["fail"] += 1
    stats["failed_tiles"].append({"url": url, "z": z, "x": x, "y": y})
    throttler.record(RESULT_FAIL, z)
    signals.log_emitted.emit("ERROR", _t("engine.log_fail", url=url), time.time())


async def _stream_to_file(response: aiohttp.ClientResponse, tmp_path: Path):
    """청크 스트리밍 저장 — 이벤트 루프 블로킹 없이 aiofiles 사용."""
    async with aiofiles.open(tmp_path, "wb") as f:
        async for chunk in response.content.iter_chunked(8192):
            await f.write(chunk)


# ── DiskMonitor ────────────────────────────────────────────────────────────────

class DiskMonitor(QObject):
    """
    5초마다 디스크 여유 공간 점검.
    부족 시 disk_pause 이벤트 세트 + 자동 재개.
    """
    def __init__(self, signals: EngineSignals,
                disk_pause: asyncio.Event,
                out_root: Path,
                loop: asyncio.AbstractEventLoop | None = None,  
                parent=None):
        super().__init__(parent)
        self._signals    = signals
        self._disk_pause = disk_pause
        self._out_root   = out_root
        self._loop       = loop    
        self._timer      = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._check)


    def start(self):
        self._timer.start()
        self._check()


    def stop(self):
        self._timer.stop()


    def _check(self):
        try:
            root = self._out_root
            if not root.exists():
                root = Path(root.anchor) if root.anchor else Path("/")
            usage = shutil.disk_usage(root)
            free, total = usage.free, usage.total
            self._signals.disk_checked.emit(float(free), float(total))

            if free < DISK_WARN_BYTES:
                if not self._disk_pause.is_set():
                    if self._loop:
                        self._loop.call_soon_threadsafe(self._disk_pause.set)
                    else:
                        self._disk_pause.set()
                    self._signals.disk_full.emit()
                    self._signals.log_emitted.emit(
                        "WARN",
                        _t("engine.log_disk_low", mb=free // 1048576),
                        time.time(),
                    )
            else:
                if self._disk_pause.is_set():
                    if self._loop:
                        self._loop.call_soon_threadsafe(self._disk_pause.clear)
                    else:
                        self._disk_pause.clear()
                    self._signals.disk_restored.emit()
                    self._signals.log_emitted.emit(
                        "INFO",
                        _t("engine.log_disk_ok", mb=free // 1048576),
                        time.time(),
                    )
        except OSError as e:
            self._signals.log_emitted.emit("ERROR", _t("engine.log_disk_err", e=e), time.time())
            
            