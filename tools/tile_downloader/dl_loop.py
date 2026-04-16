# -*- coding: utf-8 -*-
# tools\tile_downloader\dl_loop.py

"""
백그라운드 asyncio 이벤트 루프 래퍼.
tile_downloader_window.py 와 tdw_queue_mixin.py 에서 공동 사용.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading

from utils.debug import error_print


class _TileDownloadLoop:
    """
    백그라운드 스레드에서 전용 asyncio 루프 실행.
    메인 앱이 qasync를 쓰지 않아도 독립 동작.
    Qt Signal 은 cross-thread queued connection 으로 안전하게 전달.
    """

    def __init__(self) -> None:
        self._loop   = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="tile-dl-loop"
        )
        self._thread.start()


    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()


    def make_event(self) -> asyncio.Event:
        fut = asyncio.run_coroutine_threadsafe(self._new_event(), self._loop)
        try:
            return fut.result(timeout=5)
        except concurrent.futures.TimeoutError:
            error_print("[TileDownloadLoop] make_event 타임아웃 — 루프 응답 없음")
            raise RuntimeError("TileDownloadLoop: The event loop is not responding.")


    async def _new_event(self) -> asyncio.Event:
        return asyncio.Event()


    def submit(self, coro) -> "concurrent.futures.Future":
        """코루틴을 이 루프에서 실행."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)


    def set_event(self, ev: asyncio.Event) -> None:
        self._loop.call_soon_threadsafe(ev.set)


    def clear_event(self, ev: asyncio.Event) -> None:
        self._loop.call_soon_threadsafe(ev.clear)


    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3)     
        if self._thread.is_alive():
            from utils.debug import warning_print
            warning_print("[TileDownloadLoop] 루프 스레드가 3초 내 종료되지 않음")