# -*- coding: utf-8 -*-
# tools\gps_map\gps_map_data_mixin.py

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from PySide6.QtCore import QThreadPool, Slot
from tools.gps_map.gps_map_thumbs import GpsMapPhoto
from utils.paths import norm_path as _norm_path

# ── TYPE_CHECKING 전용 임포트 ───────────────────────────────────
if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _QWidgetBase
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from tools.gps_map.gps_map_thumbs import GpsThumbHttpServer, PinThumbRegistry
    from tools.gps_map.gps_map_thumbbar import GpsMapThumbBar
    from tools.gps_map.gps_map_window import _CollectorSignals, _LoadingOverlay
    _MixinBase = _QWidgetBase
else:
    _MixinBase = object


class GpsMapDataMixin(_MixinBase):  
    """GPS 사진 수집 스레드 관리 및 지도 포인트 JSON 페이로드 빌드를 담당."""

    # ── Pylance 전용: 외부(GPSMapWindow / 다른 믹스인)가 제공하는 선언 ──
    if TYPE_CHECKING:
        # ── GPSMapWindow.__init__ 에서 초기화되는 상태 ──────────
        _gps_data:          List[Dict[str, Any]]
        _current_fp:        str
        _map_ready:         bool
        _nav_connected:     bool
        _signals:           Optional[_CollectorSignals]
        _overlay:           Optional[_LoadingOverlay]
        _view:              Optional[QWebEngineView]
        _thumb_http:        GpsThumbHttpServer
        _thumb_registry:    PinThumbRegistry
        _thumbbar_enabled:  bool
        _gps_thumbbar:      Optional[GpsMapThumbBar]
        _rep_overrides_json: str    

        # ── GPSMapWindow Signal ──────────────────────────────
        navigate_to_file:   Any    

        # ── 다른 믹스인이 제공하는 메서드 ────────────────────────
        def _ensure_view(self) -> bool: ...
        def _load_html(self) -> None: ...
        def _show_empty(self) -> None: ...
        def _update_window_title(self) -> None: ...

    # ──────────────────────────────────────────────────────────
    # 퍼블릭 API
    # ──────────────────────────────────────────────────────────

    def load_photos(
        self,
        files: List[Path],
        current_file: Optional[Path] = None,
    ) -> None:
        """
        파일 목록에서 GPS 메타데이터를 비동기로 수집한다.
        내부적으로 GpsCollector(QRunnable)를 QThreadPool 에 제출한다.
        """
        from tools.gps_map.gps_map_window import _CollectorSignals, GpsCollector

        if not self._ensure_view():
            return

        self._disconnect_signals()
        self._current_fp = _norm_path(current_file) if current_file else ""
        self._map_ready = False

        if self._overlay:
            self._overlay.set_progress(0, len(files))
            self._overlay.show()
            self._overlay.raise_()

        self._signals = _CollectorSignals()
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_collected)
        QThreadPool.globalInstance().start(GpsCollector(files, self._signals))


    def set_current_file(self, filepath: Path) -> None:
        """
        현재 열린 사진을 갱신하고 JS 의 highlightCurrent 를 호출한다.
        메인 윈도우에서 사진이 바뀔 때마다 호출된다.
        """
        self._current_fp = _norm_path(filepath)
        if self._map_ready and self._view:
            self._view.page().runJavaScript(
                f"window.highlightCurrent({json.dumps(self._current_fp)})"
            )


    def connect_navigation(self, slot) -> None:
        """
        navigate_to_file 시그널에 슬롯을 1회만 연결한다.
        중복 연결을 방지하기 위해 _nav_connected 플래그를 사용한다.
        """
        if not self._nav_connected:
            self.navigate_to_file.connect(slot)
            self._nav_connected = True

    # ──────────────────────────────────────────────────────────
    # 내부 신호 관리
    # ──────────────────────────────────────────────────────────

    def _disconnect_signals(self) -> None:
        """
        진행 중인 GpsCollector 시그널을 안전하게 해제한다.
        load_photos 호출 전·closeEvent 에서 반드시 호출한다.
        """
        if self._signals is None:
            return
        for sig, slot in [
            (self._signals.progress, self._on_progress),
            (self._signals.finished, self._on_collected),
        ]:
            try:
                sig.disconnect(slot)
            except RuntimeError:
                pass
        self._signals = None

    # ──────────────────────────────────────────────────────────
    # 수집 진행 슬롯
    # ──────────────────────────────────────────────────────────

    @Slot(int, int)
    def _on_progress(self, cur: int, tot: int) -> None:
        """GpsCollector.progress 시그널 → 로딩 오버레이 진행 바 갱신."""
        if self._overlay:
            self._overlay.set_progress(cur, tot)


    @Slot(list)
    def _on_collected(self, data: list) -> None:
        """
        GpsCollector.finished 시그널 핸들러.

        수집 결과를 self._gps_data 에 저장하고:
          - 데이터 없음 → _show_empty()
          - 데이터 있음 → _load_html() + 썸네일바 갱신 + 타이틀 갱신
        """
        self._gps_data = data

        if not data:
            if self._overlay:
                self._overlay.stop()
                self._overlay.hide()
            self._show_empty() 
            return

        if self._overlay:
            self._overlay.set_loading_map()

        self._load_html()  

        self._thumb_http.register_files([d["filepath"] for d in self._gps_data])

        if self._gps_thumbbar is not None:
            photos = [self._to_thumb_photo(d) for d in self._gps_data]
            self._gps_thumbbar.set_photos(photos, self._current_fp)
            self._gps_thumbbar.setVisible(self._thumbbar_enabled)

        self._update_window_title()

    # ──────────────────────────────────────────────────────────
    # 변환 헬퍼
    # ──────────────────────────────────────────────────────────

    def _to_thumb_photo(self, d: Dict[str, Any]) -> GpsMapPhoto:
        """dict → GpsMapPhoto 변환. is_current 플래그를 자동으로 설정한다."""
        return GpsMapPhoto(
            filepath=d["filepath"],
            filename=d["filename"],
            lat=d["lat"],
            lon=d["lon"],
            date_taken=d.get("date_taken", ""),
            model=d.get("model", ""),
            is_current=d["filepath"] == self._current_fp,
        )

    # ──────────────────────────────────────────────────────────
    # 페이로드 빌드 (MapViewMixin._html_params 에서 호출)
    # ──────────────────────────────────────────────────────────

    def _build_points_payload(self) -> list:
        """
        JS 의 points 배열용 딕셔너리 리스트를 생성한다.

        사이드이펙트:
          self._rep_overrides_json 을 최신 상태로 갱신한다.
          → MapViewMixin._html_params 가 getattr(self, "_rep_overrides_json", "")
            으로 이 캐시를 읽으므로, 반드시 _build_points_payload 호출 이후에
            _html_params 가 실행되어야 한다. (_load_html 흐름 안에서 보장됨)
        """
        repmap = self._thumb_registry.representative_overrides
        result = []
        for d in self._gps_data:
            fp = d["filepath"]
            photo = GpsMapPhoto(
                filepath=fp,
                filename=d["filename"],
                lat=d["lat"],
                lon=d["lon"],
                date_taken=d.get("date_taken", ""),
                model=d.get("model", ""),
                is_current=fp == self._current_fp,
            )
            result.append(
                photo.to_map_point(thumb_url=self._thumb_http.thumb_url(fp))
            )

        self._rep_overrides_json = json.dumps(repmap, ensure_ascii=False)
        return result


    def _build_route_points(self) -> list:
        """
        MapLibre GeoJSON LineString 용 [lon, lat] 좌표 목록을 반환한다.
        date_taken 오름차순으로 정렬하고, date_taken 없는 항목은 뒤로 보낸다.
        """
        def key(d: dict) -> tuple:
            dt = d.get("date_taken", "")
            return (0, dt, d["filename"]) if dt else (1, "", d["filename"])

        return [[d["lon"], d["lat"]] for d in sorted(self._gps_data, key=key)]

