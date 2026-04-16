# -*- coding: utf-8 -*-
# tools\gps_map\gps_map_window.py
"""
GPS 포토맵 윈도우 — MapLibre GL JS 최종 안정판
"""
from __future__ import annotations

import http.server
import json
import socket
import threading
import urllib.request
import shutil

from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from PySide6.QtCore import (
    QObject, QRunnable, QTimer, Qt,
    Signal, Slot, QUrl,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont,
    QPainter, QPen, QResizeEvent,
)
from PySide6.QtWidgets import (
    QDialog, QPushButton, QWidget,
    QFileDialog, QComboBox,
    QApplication
)

from utils.debug import debug_print, error_print, info_print, warning_print
from utils.gps_handler import GPSHandler
from utils.gpx_parser import parse_gpx_file
from utils.lang_manager import t
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox

from tools.gps_map.gps_map_thumbs import (
    GpsThumbHttpServer,
    GpsThumbProvider,
    PinThumbRegistry,
)
from tools.gps_map.gps_map_thumbbar import GpsMapThumbBar
from tools.gps_map.gps_map_html import build_html, ml_asset_url
from tools.gps_map.gps_map_gpx_dialog import GpsMapGpxManagerDialog
from tools.gps_map.gps_map_ui_mixin import GpsMapUIMixin
from tools.gps_map.gps_map_cluster_mixin import GpsMapClusterMixin
from tools.gps_map.gps_map_data_mixin import GpsMapDataMixin
from tools.gps_map.gps_map_i18n import build_gps_map_i18n
from utils.paths import norm_path as _norm_path

from typing import TYPE_CHECKING

if TYPE_CHECKING: 
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage

_WE_OK: bool = False
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView 
    from PySide6.QtWebEngineCore import QWebEnginePage   
    _WE_OK = True
except ImportError:
    _WE_OK = False
    warning_print("[GpsMap] PySide6-WebEngine 없음")

_ML_VERSION = "4.7.1"
_ML_ASSETS: Dict[str, str] = {
    "maplibre-gl.js":  f"https://unpkg.com/maplibre-gl@{_ML_VERSION}/dist/maplibre-gl.js",
    "maplibre-gl.css": f"https://unpkg.com/maplibre-gl@{_ML_VERSION}/dist/maplibre-gl.css",
}


def _ensure_maplibre_assets(asset_dir: Path) -> None:
    """MapLibre 에셋 background 다운로드 (없을 때만)."""
    missing = [(n, u) for n, u in _ML_ASSETS.items()
               if not (asset_dir / n).exists()]
    if not missing:
        return

    def _dl() -> None:
        for name, url in missing:
            dest = asset_dir / name
            tmp  = dest.with_suffix(dest.suffix + ".tmp")
            try:
                info_print(f"[GpsMap] MapLibre 에셋 다운로드: {name}")
                asset_dir.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(url, tmp)
                tmp.replace(dest)
                info_print(f"[GpsMap] 완료: {name} ({dest.stat().st_size//1024} KB)")
            except Exception as e:
                error_print(f"[GpsMap] 다운로드 실패 ({name}): {e}")
                try: tmp.unlink(missing_ok=True)
                except Exception: pass

    threading.Thread(target=_dl, daemon=True, name="maplibre-dl").start()


# ──────────────────────────────────────────────────────────────
# 브라우저 열기 전용 프록시 서버
# ──────────────────────────────────────────────────────────────

class _ProxySignals(QObject):
    gpx_open_requested = Signal()


class _BrowserProxy:
    """
    외부 브라우저용 Same-Origin 프록시 서버.

    문제: 외부 브라우저가 file:// → http://127.0.0.1:{main} 타일 요청
          → Cross-Origin 차단 → ConnectionAbortedError
    해결: 프록시가 http://127.0.0.1:{proxy}/... 로 모든 리소스를 서빙
          타일/에셋은 main 서버에서 중계 → Same-Origin이므로 CORS 불필요
    """

    def __init__(self, main_port: int) -> None:
        self._main_port = main_port
        self._html      = ""
        self._port      = 0
        self._server: Optional[http.server.HTTPServer] = None
        self._lock      = threading.Lock()

        self._proxy_signals = _ProxySignals()
        self._gpx_result_event = threading.Event()
        self._gpx_result_data: Optional[Dict[str, Any]] = None

        self._start()


    @property
    def port(self) -> int:
        return self._port


    def set_html(self, html: str) -> None:
        with self._lock:
            self._html = html


    def _start(self) -> None:
        main_port = self._main_port
        proxy     = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlparse(self.path).path

                if path == '/api/open-gpx':
                    proxy._gpx_result_event.clear()
                    proxy._gpx_result_data = None
                    proxy._proxy_signals.gpx_open_requested.emit() 
                    ok = proxy._gpx_result_event.wait(timeout=30)
                    if ok and proxy._gpx_result_data is not None:
                        body = json.dumps(proxy._gpx_result_data,
                                        ensure_ascii=False).encode('utf-8')
                        self._send(200, 'application/json; charset=utf-8', body)
                    else:
                        self._send(204, 'application/json', b'') 
                    return
    
                # ① 지도 HTML 서빙
                if path in ('/', '/index.html'):
                    with proxy._lock:
                        data = proxy._html.encode('utf-8')
                    self._send(200, 'text/html; charset=utf-8', data,
                            extra={'Cache-Control': 'no-store, no-cache'}) 
                    return

                # ② 타일/에셋 프록시 → main 서버 중계
                try:
                    url = f"http://127.0.0.1:{main_port}{self.path}"
                    with urllib.request.urlopen(url, timeout=15) as resp:
                        ct   = resp.headers.get("Content-Type",
                                                "application/octet-stream")
                        data = resp.read()
                    headers = {
                        "Content-Type":  ct,
                        "Cache-Control": "public, max-age=3600",
                    }
                    self._send(200, ct, data, extra=headers)
                except Exception:
                    self.send_response(404)
                    self.end_headers()

            def _send(
                self,
                code: int,
                ct: str,
                data: bytes,
                extra: Optional[Dict[str, str]] = None,
            ) -> None:
                try:
                    self.send_response(code)
                    self.send_header("Content-Type",   ct)
                    self.send_header("Content-Length", str(len(data)))
                    if extra:
                        for k, v in extra.items():
                            self.send_header(k, v)
                    self.end_headers()
                    try:
                        self.wfile.write(data)
                    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                        pass
                except (BrokenPipeError, ConnectionResetError,
                        ConnectionAbortedError, OSError):
                    pass

            def log_message(self, format: str, *args: object) -> None:  
                pass  

        # 사용 가능한 포트 자동 할당
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self._port = s.getsockname()[1]

        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", self._port), _Handler
        )
        _proxy_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True, name="gpsmap-proxy",
        )
        _proxy_thread.start()
        info_print(f"[GpsMap] 브라우저 프록시 → http://127.0.0.1:{self._port}/")


    def shutdown(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None


# ──────────────────────────────────────────────────────────────
# GPS 수집
# ──────────────────────────────────────────────────────────────

class _CollectorSignals(QObject):
    progress = Signal(int, int)
    finished = Signal(list)


class GpsCollector(QRunnable):
    def __init__(self, files: List[Path], signals: _CollectorSignals) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._files   = files
        self._signals = signals

    def run(self) -> None:
        from core.metadata_reader import MetadataReader
        reader  = MetadataReader()
        results: List[Dict[str, Any]] = []
        total   = len(self._files)
        for i, fp in enumerate(self._files):
            try:
                meta = reader.read(fp) or {}
                gps  = meta.get("gps")
                if not gps: continue
                lat = gps.get("latitude")
                lon = gps.get("longitude")
                if lat is None or lon is None: continue
                lat, lon = float(lat), float(lon)
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                cam = meta.get("camera", {})
                results.append({
                    "filepath":   _norm_path(fp),
                    "filename":   fp.name,
                    "lat":        lat,
                    "lon":        lon,
                    "date_taken": cam.get("date_taken", ""),
                    "model":      cam.get("model", ""),
                })
            except Exception as e:
                debug_print(f"[GpsCollector] {fp.name} 처리 실패: {e}")
            if i % 30 == 0 or i == total - 1:
                try: self._signals.progress.emit(i + 1, total)
                except RuntimeError: return
        try: self._signals.finished.emit(results)
        except RuntimeError: pass


# ──────────────────────────────────────────────────────────────
# 로딩 오버레이
# ──────────────────────────────────────────────────────────────

class _LoadingOverlay(QWidget):

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._angle   = 0
        self._status  = t('gps_map.window.loading_map')
        self._current = self._total = 0
        self._timer   = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)


    def set_progress(self, cur: int, tot: int) -> None:
        self._current, self._total = cur, tot
        self._status = t('gps_map.window.loading_gps', cur=cur, tot=tot)
        self.update()


    def set_loading_map(self) -> None:
        self._current = self._total = 0
        self._status  = t('gps_map.window.loading_map')
        self.update()


    def _tick(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()


    def stop(self) -> None:
        self._timer.stop()


    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(18, 18, 18, 235))
        cx, cy, r = self.width()//2, self.height()//2, 28
        pen = QPen(QColor(55, 55, 55), 4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.drawEllipse(cx-r, cy-r, r*2, r*2)
        pen = QPen(QColor(74, 158, 255), 4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(cx-r, cy-r, r*2, r*2, (90-self._angle)*16, -960)
        if self._total > 0:
            bw = min(260, self.width()-80)
            bx, by, bh = cx-bw//2, cy+r+22, 4
            ratio = min(1.0, self._current/self._total)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(50, 50, 50))
            p.drawRoundedRect(bx, by, bw, bh, 2, 2)
            if ratio > 0:
                p.setBrush(QColor(74, 158, 255))
                p.drawRoundedRect(bx, by, int(bw*ratio), bh, 2, 2)
        font = QFont(); font.setPointSize(10)
        p.setFont(font); p.setPen(QColor(150, 150, 150))
        ty = cy+r+(40 if self._total > 0 else 20)
        p.drawText(0, ty, self.width(), 24,
                   Qt.AlignmentFlag.AlignHCenter, self._status)


    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e); self.update()

# ──────────────────────────────────────────────────────────────
# JS 콘솔 억제
# ──────────────────────────────────────────────────────────────

if _WE_OK:
    class _SilentPage(QWebEnginePage):
        _SUPPRESS = ("Expected value", "could not be loaded",
                     "unknown property", "favicon")
        def __init__(self, profile=None, parent=None):
            super().__init__(profile, parent) if profile \
                else super().__init__(parent)
        def javaScriptConsoleMessage(self, level, msg, line, src):
            if any(s in msg for s in self._SUPPRESS): return
            debug_print(f"[GpsMap JS] {msg} (L{line})")
else:
    _SilentPage = None  # type: ignore


# ──────────────────────────────────────────────────────────────
# GPS 포토맵 윈도우
# ──────────────────────────────────────────────────────────────

class GPSMapWindow(GpsMapUIMixin, GpsMapClusterMixin, GpsMapDataMixin, QDialog):

    navigate_to_file = Signal(str)
    _MAP_READY_TIMEOUT_MS: int = 15_000

    def _gpx_save_dir(self) -> Path:
        """GPX 파일 자동 저장 폴더 경로 반환"""
        from utils.paths import app_resources_dir
        d = app_resources_dir() / 'gpx'
        d.mkdir(parents=True, exist_ok=True)
        return d


    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(t('gps_map.window.title'))
        self.resize(900, 620)
        self.setMinimumSize(720, 520)

        self._view:          Optional[QWebEngineView]    = None
        self._overlay:       Optional[_LoadingOverlay]   = None
        self._signals:       Optional[_CollectorSignals] = None
        self._proxy:         Optional[_BrowserProxy]     = None
        self._gps_data:      List[Dict[str, Any]]        = []
        self._current_fp:    str  = ""
        self._map_ready:     bool = False
        self._nav_connected: bool = False
        self._port:          int  = 0
        self._tile_size:     int  = 512
        self._tms:           bool = False
        self._min_zoom:      int  = 1
        self._max_zoom:      int  = 18
        self._asset_dir:     Optional[Path] = None

        self._thumb_provider          = GpsThumbProvider(self)
        self._thumb_http              = GpsThumbHttpServer(self._thumb_provider)
        from utils.paths import app_resources_dir
        _CONFIG_PATH = app_resources_dir() / "config" / "gps_map_thumbs.json"
        self._thumb_registry = PinThumbRegistry(_CONFIG_PATH)
        self._thumbbar_enabled:   bool = True
        self._pin_thumbs_enabled: bool = False
        self._pin_singles_enabled:  bool = True  
        self._pin_clusters_enabled: bool = True   
        self._pin_thumb_zoom_threshold: int = 12
        self._gps_thumbbar: Optional[GpsMapThumbBar] = None 
        self._gps_handler = GPSHandler(map_service='naver')

        self._cluster_selection_active: bool = False
        self._active_cluster_key: str       = ""
        self._active_cluster_fps: list[str] = []

        self._gpx_data: Optional[dict[str, Any]] = None
        self._gpx_visible: bool = True
        self._gpx_elevation_visible: bool = False
        self._selected_gpx_index: int = -1

        self._time_offset_combo: Optional[QComboBox] = None
        self._ready_timer: Optional[QTimer] = None

        self._route_visible: bool = True
        self._route_btn:    Optional[QPushButton] = None
        self._gpx_btn:      Optional[QPushButton] = None
        self._gpx_ele_btn:  Optional[QPushButton] = None
        self._playback_btn: Optional[QPushButton] = None

        self._build_ui()


    def _start_ready_timer(self) -> None:
        if self._ready_timer is None:
            self._ready_timer = QTimer(self)
            self._ready_timer.setSingleShot(True)
            self._ready_timer.timeout.connect(self._on_map_ready_timeout)
        self._ready_timer.start(self._MAP_READY_TIMEOUT_MS) 


    def _stop_ready_timer(self) -> None:
        """
        gpsmap:ready / gpsmap:error: 수신 시, 또는 closeEvent에서 호출.
        이미 멈춘 타이머에 stop()을 호출해도 안전하다.
        """
        if self._ready_timer is not None:
            self._ready_timer.stop()


    def _on_map_ready_timeout(self) -> None:
        """
        _MAP_READY_TIMEOUT_MS 경과 후에도 gpsmap:ready가 오지 않은 경우 처리.
        원인: 템플릿 파일 없음, 타일 서버 미시작, WebGL 비활성화, JS 파싱 오류 등.
        """
        if self._map_ready:
            return

        error_print(
            f"[GpsMap] 지도 로딩 타임아웃 ({self._MAP_READY_TIMEOUT_MS // 1000}초 초과)"
        )

        if self._overlay:
            self._overlay.stop()
            self._overlay.hide()

        if self._view:
            timeout_msg = t('gps_map.window.error_timeout')
            webgl_msg   = t('gps_map.window.error_web_gl')
            self._view.setHtml(f"""
            <html><body style="background:#1a1a1a;color:#888;...">
            <div style="font-size:32px">⚠️</div>
            <div style="font-size:15px;color:#bbb">{timeout_msg}</div>
            <div style="font-size:11px;color:#555">{webgl_msg}</div>
            </body></html>
            """)


    def _go_to_world(self) -> None:
        """세계지도 — 줌 레벨 2로 이동"""
        if self._map_ready and self._view:
            self._view.page().runJavaScript(
                "map.flyTo({zoom:2, center:[10,25], duration:600, essential:true});"
            )


    def _calc_geo_utc_offset(self) -> Optional[float]:
        if not self._gps_data:
            return None

        first = self._gps_data[0]
        lat = first.get("lat")
        lon = first.get("lon")
        if lat is None or lon is None:
            return None

        try:
            from timezonefinder import TimezoneFinder
        except ImportError:
            warning_print(
                "[GpsMap] timezonefinder 미설치 → UTC diff 방식으로 폴백.\n"
                "         정확한 계산: pip install timezonefinder"
            )
            return None

        try:
            tf = TimezoneFinder()
            tz_name = tf.timezone_at(lat=float(lat), lng=float(lon))
            if not tz_name:
                warning_print(
                    f"[GpsMap] 타임존 조회 실패 (lat={lat:.4f}, lon={lon:.4f})"
                )
                return None

            naive_dt = self._parse_date_taken(first.get("date_taken", ""))

            from datetime import datetime
            offset_td = None

            # ── 1순위: zoneinfo (Python 3.9+)
            try:
                from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
                try:
                    tz = ZoneInfo(tz_name)
                    local_dt = naive_dt.replace(tzinfo=tz)
                    offset_td = local_dt.utcoffset()
                except ZoneInfoNotFoundError:
                    warning_print(
                        f"[GpsMap] zoneinfo '{tz_name}' 미발견 (pip install tzdata)"
                    )
            except ImportError:
                pass

            # ── 2순위: pytz
            if offset_td is None:
                try:
                    import pytz     # type: ignore[import-untyped]
                    tz = pytz.timezone(tz_name)
                    local_dt = tz.localize(naive_dt, is_dst=None)
                    offset_td = local_dt.utcoffset()
                except ImportError:
                    warning_print(
                        "[GpsMap] zoneinfo(tzdata) + pytz 모두 없음 → diff 방식 폴백.\n"
                        "         pip install tzdata  또는  pip install pytz"
                    )
                    return None
                except Exception as e:
                    warning_print(f"[GpsMap] pytz 타임존 오류 ({tz_name}): {e}")
                    return None

            if offset_td is None:
                return None

            offset_hours = offset_td.total_seconds() / 3600.0
            info_print(
                f"[GpsMap] UTC 자동 계산: {tz_name} "
                f"({naive_dt.strftime('%Y-%m-%d')}) "
                f"→ UTC{'+' if offset_hours >= 0 else ''}{offset_hours:g}h"
            )
            return offset_hours

        except Exception as e:
            warning_print(f"[GpsMap] UTC 오프셋 계산 오류: {e}")
            return None


    def _parse_date_taken(self, date_str: str):
        """
        date_taken 문자열을 naive datetime으로 파싱.
        파싱 실패 시 현재 날짜 반환 (폴백).
        """
        from datetime import datetime

        if not date_str:
            return datetime.now()

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y:%m:%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue

        warning_print(f"[GpsMap] date_taken 파싱 실패: {date_str!r} → 현재 날짜로 폴백")
        return datetime.now()

    # ── WebView 초기화 ───────────────────────────────────────────
    def _ensure_view(self) -> bool:
        if self._view is not None:
            return True
        if not _WE_OK:
            error_print("[GpsMap] WebEngine 사용 불가"); return False
        try:
            from core.map_loader import (
                _ensure_local_server, _get_web_profile,
                get_raster_zoom_range, get_raster_tile_config,
            )
            from utils.paths import app_resources_dir
            self._port       = _ensure_local_server()
            self._min_zoom, self._max_zoom = get_raster_zoom_range()
            cfg              = get_raster_tile_config()
            self._tile_size  = cfg["tile_size"]
            self._tms        = cfg["tms"]
            self._asset_dir  = app_resources_dir() / "assets"
            profile          = _get_web_profile()
        except Exception as e:
            error_print(f"[GpsMap] 초기화 실패: {e}"); return False

        if self._asset_dir is not None:
            _ensure_maplibre_assets(self._asset_dir)

        try:
            from PySide6.QtWebEngineCore import QWebEngineProfile
            from utils.paths import get_cache_dir
            cache_path = str(get_cache_dir() / "webengine_tiles")
            profile.setCachePath(cache_path)
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
            profile.setHttpCacheMaximumSize(128 * 1024 * 1024) 
            info_print(f"[GpsMap] WebEngine 캐시: {cache_path} (128MB)")
        except Exception as e:
            warning_print(f"[GpsMap] 캐시 설정 실패: {e}")

        self._view = QWebEngineView()
        self._view.setPage(_SilentPage(profile, self._view))
        self._view.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._view.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._view.page().setBackgroundColor(QColor(18, 18, 18)) 

        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            s = self._view.settings()
            def _set(name: str, val: bool) -> None:
                a = getattr(QWebEngineSettings.WebAttribute, name, None)
                if a is not None: s.setAttribute(a, val)
            _set("ScrollAnimatorEnabled",          False)
            _set("Accelerated2dCanvasEnabled",      True)
            _set("WebGLEnabled",                    True)
            _set("LocalContentCanAccessRemoteUrls", True)
            _set("ShowScrollBars",                  False)
        except Exception as e:
            warning_print(f"[GpsMap] 설정 실패: {e}")

        try:
            self._view.page().setLifecycleState(
                QWebEnginePage.LifecycleState.Active)
        except Exception: pass

        self._view.titleChanged.connect(self._on_title)
        self._map_layout.insertWidget(0, self._view)
        self._overlay = _LoadingOverlay(self._map_container)
        self._overlay.setGeometry(self._map_container.rect())
        self._overlay.show(); self._overlay.raise_()
        return True

    # ── 공개 API ────────────────────────────────────────────────

    def _apply_time_offset_combo(self) -> None:
        if not self._map_ready or not self._view or self._time_offset_combo is None:
            return
        try:
            data = self._time_offset_combo.currentData()
        except RuntimeError:
            warning_print('[GpsMap] time_offset_combo C++ 객체 소멸 — 참조 해제')
            self._time_offset_combo = None
            return
        if data is None:
            return

        data_str = str(data).strip()

        if data_str == 'AUTO':
            self._view.page().runJavaScript("window.setTimeOffsetMode('AUTO', 0);")
        else:
            try:
                hours = int(data_str)
            except (ValueError, TypeError):
                warning_print(f"[GpsMap] time_offset_combo 잘못된 값: {data!r}")
                return
            self._view.page().runJavaScript(
                f"window.setTimeOffsetMode('MANUAL', {hours});"
            )


    @Slot(int)
    def on_time_offset_changed(self, _index: int) -> None:
        self._apply_time_offset_combo()

    # ── HTML ─────────────────────────────────────────────────────

    def _gpx_has_sensors(self) -> bool:
        """GPX 데이터에 센서 채널이 하나라도 있으면 True."""
        if not self._gpx_data:
            return False
        avail = self._gpx_data.get("sensors", {}).get("available", {})
        return any(avail.values())

    # ── _gpx_has_panel_data() 헬퍼 ───────────────────────────────

    def _gpx_has_panel_data(self) -> bool:
        """고도 또는 센서 중 하나라도 있으면 패널 표시 가능."""
        if not self._gpx_data:
            return False
        has_ele = bool(self._gpx_data.get("has_elevation"))
        return has_ele or self._gpx_has_sensors()


    def _html_params(self, port_override=None, toolbar_mode='browser') -> dict:
        lats = [d["lat"] for d in self._gps_data]
        lons = [d["lon"] for d in self._gps_data]

        if self._gpx_data and self._gpx_data.get("points"):
            gb = self._gpx_data["bounds"]
            lats += [gb["south"], gb["north"]]
            lons += [gb["west"], gb["east"]]

        geo_utc = self._calc_geo_utc_offset()

        return dict(
            port=port_override if port_override else self._port,
            center_lat=(min(lats) + max(lats)) / 2.0,
            center_lon=(min(lons) + max(lons)) / 2.0,
            zoom=max(self._min_zoom, min(5, self._max_zoom)),  
            route_visible=self._route_visible, 
            toolbar_mode=toolbar_mode,                                       
            points_json=json.dumps(self._build_points_payload(), ensure_ascii=False),
            route_json=json.dumps(self._build_route_points(), ensure_ascii=False),
            min_zoom=self._min_zoom,
            max_zoom=self._max_zoom,
            tile_size=self._tile_size,
            tms=self._tms,
            asset_dir=self._asset_dir,
            thumbbar_enabled=self._thumbbar_enabled,
            pin_thumbs_enabled=self._pin_thumbs_enabled,
            pin_singles_on=self._pin_singles_enabled,   
            pin_clusters_on=self._pin_clusters_enabled,       
            pin_thumb_zoom_threshold=self._pin_thumb_zoom_threshold,
            rep_overrides_json=getattr(self, "_rep_overrides_json", "{}"),
            gpx_json=json.dumps(self._gpx_data, ensure_ascii=False) if self._gpx_data is not None else "null",
            gpx_visible=self._gpx_visible,
            gpx_has_elevation=bool(self._gpx_data and self._gpx_data.get("has_elevation")),
            gpx_has_sensors = self._gpx_has_sensors(),
            elevation_visible = self._gpx_has_panel_data(),
            geo_utc_offset_hours=geo_utc if geo_utc is not None else "null",
            i18n_json=build_gps_map_i18n(),
        )


    def _load_html(self) -> None:
        if not self._view or not self._gps_data:
            return
        try:
            html = build_html(**self._html_params(toolbar_mode='qt'))
        except Exception as e:
            error_print(f"[GpsMap] HTML 생성 오류: {e}")
            self._stop_ready_timer()
            if self._overlay:
                self._overlay.stop()
                self._overlay.hide()
            self._show_empty()
            return

        self._map_ready = False
        self._start_ready_timer()
        self._view.setHtml(html, QUrl(f"http://127.0.0.1:{self._port}"))

    # ── 브라우저로 열기 ──────────────────────────────────────────

    def _open_in_browser(self) -> None:
        if not self._gps_data or not self._port:
            warning_print("[GpsMap] 지도 로딩 완료 후 사용 가능합니다")
            return

        if self._proxy is None:
            self._proxy = _BrowserProxy(self._port)
            self._proxy._proxy_signals.gpx_open_requested.connect(
                self._on_browser_gpx_open_request,
                Qt.ConnectionType.QueuedConnection
            )

        html = build_html(**self._html_params(
            port_override=self._proxy.port, toolbar_mode='browser'))
        self._proxy.set_html(html)
        url = QUrl(f"http://127.0.0.1:{self._proxy.port}/")
        QDesktopServices.openUrl(url)


    def _on_browser_gpx_open_request(self) -> None:
        """
        브라우저에서 /api/open-gpx 요청 시 Qt 메인 스레드에서 실행.
        GpsMapGpxManagerDialog를 열고 결과를 프록시에 전달한다.
        """
        if self._proxy is None:
            return

        selected_path: list[Optional[Path]] = [None]

        def _on_selected(path: Path) -> None:
            selected_path[0] = path

        dlg = GpsMapGpxManagerDialog(self._gpx_save_dir(), self)
        dlg.file_selected.connect(_on_selected)
        dlg.exec()

        chosen = selected_path[0]

        if chosen is not None:
            try:
                data = parse_gpx_file(str(chosen))
                self._proxy._gpx_result_data = data

                if data is not None:
                    self._gpx_data    = data
                    self._gpx_visible = True
                    has_ele     = bool(self._gpx_data.get("has_elevation"))
                    has_sensors = self._gpx_has_sensors()
                    self._sync_gpx_buttons(has_ele, has_sensors)
                    self._update_window_title()
                    if self._map_ready and self._view:
                        gpx_json = json.dumps(data, ensure_ascii=False)
                        self._view.page().runJavaScript(
                            f'window._reloadGpx({gpx_json})'
                        )
            except Exception as e:
                error_print(f'[GpsMap] 브라우저 GPX 파싱 실패: {e}')
                self._proxy._gpx_result_data = None
        else:
            self._proxy._gpx_result_data = None  

        self._proxy._gpx_result_event.set()

    # ── titleChanged ─────────────────────────────────────────────

    def _on_title(self, title: str) -> None:

        if title == 'gpsmap:ready':
            self._stop_ready_timer()
            self._map_ready = True
            if self._overlay:
                self._overlay.stop()
                self._overlay.hide()
            self._apply_time_offset_combo()
            if self._gpx_data is not None:
                has_ele     = bool(self._gpx_data.get('has_elevation'))
                has_sensors = self._gpx_has_sensors()       
                self._sync_gpx_buttons(has_ele, has_sensors)  
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx'")
            return

        if title.startswith('gpsmap:error:'):
            self._stop_ready_timer()     
            msg = title[len('gpsmap:error:'):]
            error_print(f"[GpsMap] JS 오류: {msg}")
            self._map_ready = False
            if self._overlay:
                self._overlay.stop()
                self._overlay.hide()
            return

        if title.startswith("GPXSEL:"):
            try:
                idx = int(title[len("GPXSEL:"):])
                self._selected_gpx_index = idx
            except ValueError:
                pass
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx';")
            return
        
        if title.startswith("PHOTO:"):
            fp = unquote(title[len("PHOTO:"):])
            if fp: self.navigate_to_file.emit(fp)
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx';")
            return

        if title.startswith("CLUSTERSHOW:"):
            try:
                payload = title[len("CLUSTERSHOW:"):]
                parts = payload.split(":", 2)
                if len(parts) != 3:
                    raise ValueError(f"파트 수 불일치: {len(parts)}")

                cluster_key = unquote(parts[0])
                rep_fp = unquote(parts[1])
                member_fps = [unquote(x) for x in unquote(parts[2]).split("|") if x]

                if not cluster_key or not member_fps:
                    raise ValueError("cluster_key 또는 member_fps 비어 있음")

                self._cluster_selection_active = True
                self._active_cluster_key = cluster_key
                self._active_cluster_fps = member_fps
                if self._gps_thumbbar:
                    self._gps_thumbbar.set_cluster_selection(member_fps, rep_fp)

            except Exception as e:
                error_print(f"[GpsMap] CLUSTERSHOW 파싱 오류: {e}")

                self._cluster_selection_active = False
                self._active_cluster_key = ""
                self._active_cluster_fps = []
                if self._gps_thumbbar:
                    self._gps_thumbbar.clear_cluster_selection()

            finally:
                if self._view:
                    self._view.page().runJavaScript("document.title='dodoRynx';")
            return

        if title == "CLUSTERCLEAR":
            self._cluster_selection_active = False
            self._active_cluster_key = ""
            self._active_cluster_fps = []
            if self._gps_thumbbar:
                self._gps_thumbbar.clear_cluster_selection()
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx';")
            return

        if title.startswith("PINSEL:"):
            fp = unquote(title[len("PINSEL:"):])
            if fp and self._gps_thumbbar:
                self._gps_thumbbar.set_current_file(fp)
            if self._view:
                self._view.page().runJavaScript(
                    f"(function(){{"
                    f"if(window.highlightCurrent) window.highlightCurrent({json.dumps(fp)});"
                    f"document.title='dodoRynx';"
                    f"}})();"
                )
            return

        if title.startswith("EXTMAP:"):
            parts = title[len("EXTMAP:"):].split(":", 1)
            if len(parts) == 2:
                try:
                    lat, lon = float(parts[0]), float(parts[1])
                    self._gps_handler.open_map(lat, lon)
                except ValueError:
                    pass
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx';")
            return

        if title == 'ACTION:OPEN_BROWSER':
            self._open_in_browser()
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx';")
            return

        if title.startswith('ACTION:CAPTURE:'):
            mode = title[len('ACTION:CAPTURE:'):]
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx'")
                self._view.page().runJavaScript(
                    "if(window._closeCaptureMenu) window._closeCaptureMenu();"
                )
            from PySide6.QtCore import QTimer
            QTimer.singleShot(150, lambda: self._do_capture(mode))
            return

        if title.startswith('ACTION:TOGGLE_THUMBBAR:'):
            on = title.endswith(':1')
            self._thumbbar_enabled = on
            if self._gps_thumbbar:
                self._gps_thumbbar.setVisible(on and bool(self._gps_data))
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx';")
            return

        if title == 'ACTION:GOTO_CURRENT':
            self._go_to_current()
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx';")
            return

        if title == 'ACTION:GPX_LOAD':
            self._load_gpx_from_js()
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx';")
            return

        if title == 'ACTION:PLAYBACK_STOP': 
            if self._playback_btn:
                self._playback_btn.setChecked(False)
                self._playback_btn.setText(t('gps_map.toolbar_qt.play_off'))
            if self._view:
                self._view.page().runJavaScript("document.title='dodoRynx';") 
            return  

        if title == 'ACTION:OPEN_GPX_MERGER':
            self._open_gpx_merger()
        
    # ── 툴바 액션 ────────────────────────────────────────────────

    def _fit_bounds(self) -> None:
        if self._map_ready and self._view:
            self._view.page().runJavaScript("window.fitAll&&window.fitAll();")


    def _go_to_current(self) -> None:
        if not self._map_ready or not self._view: return
        for d in self._gps_data:
            if d["filepath"] == self._current_fp:
                self._view.page().runJavaScript(
                    f"window.goToPhoto&&window.goToPhoto({d['lat']},{d['lon']});")
                return


    def _open_in_external_map(self) -> None:
        """현재 사진의 GPS 좌표를 외부 지도에서 열기."""
        if not self._current_fp:
            warning_print("[GpsMap] 현재 사진이 없습니다")
            return
        for d in self._gps_data:
            if d["filepath"] == self._current_fp:
                lat, lon = d["lat"], d["lon"]
                coord_str = GPSHandler.format_coordinates(lat, lon)
                info_print(f"[GpsMap] 외부지도 열기: {coord_str}")
                self._gps_handler.open_map(lat, lon)
                return
        warning_print("[GpsMap] 현재 사진의 GPS 데이터를 찾을 수 없습니다")


    def _toggle_route(self, checked: bool) -> None:
        if self._route_btn is not None:     
            self._route_btn.setText(
                t('gps_map.toolbar_qt.route_on') if checked
                else t('gps_map.toolbar_qt.route_off')
            )
        self._route_visible = checked     
        if self._map_ready and self._view:
            v = "true" if checked else "false"
            self._view.page().runJavaScript(
                f"window.setRouteVisible&&window.setRouteVisible({v});"
            )


    def _toggle_thumbbar(self, checked: bool) -> None:
        self._thumbbar_enabled = checked
        if self._gps_thumbbar is not None:
            self._gps_thumbbar.setVisible(checked and bool(self._gps_data))


    def _toggle_pin_thumbs(self, checked: bool) -> None:
        self._pin_thumbs_enabled = checked
        if self._map_ready and self._view:
            self._view.page().runJavaScript(
                f"window.setPinThumbsEnabled&&window.setPinThumbsEnabled({str(checked).lower()});")


    def _on_thumbbar_photo_activated(self, filepath: str) -> None:
        self.navigate_to_file.emit(filepath)
        self._current_fp = filepath
        if self._gps_thumbbar:
            self._gps_thumbbar.set_current_file(filepath)

        if self._cluster_selection_active:
            return

        for d in self._gps_data:
            if d["filepath"] == filepath and self._view:
                self._view.page().runJavaScript(
                    f"window.goToPhoto&&window.goToPhoto({d['lat']},{d['lon']});")
                break


    def _on_thumbbar_photo_hovered(self, filepath: str) -> None:
        if self._map_ready and self._view:
            self._view.page().runJavaScript(
                f"window.hoverPhoto&&window.hoverPhoto({json.dumps(filepath)});")


    def _load_gpx_file(self) -> None:
        """Qt 툴바 GPX 버튼 → 관리 다이얼로그"""
        dlg = GpsMapGpxManagerDialog(self._gpx_save_dir(), self)
        dlg.file_selected.connect(self._open_gpx_from_path)
        dlg.exec()


    def _toggle_gpx(self, checked: bool) -> None:
        if self._gpx_btn is not None:  
            self._gpx_btn.setText(
                t('gps_map.toolbar_qt.gpx_on') if checked
                else t('gps_map.toolbar_qt.gpx_off')
            )        
        self._gpx_visible = checked
        if self._map_ready and self._view:
            v = "true" if checked else "false"
            self._view.page().runJavaScript(
                f"window.setGpxVisible&&window.setGpxVisible({v});"
            )


    def _toggle_gpx_elevation(self, checked: bool) -> None:
        if self._gpx_ele_btn is not None:    
            self._gpx_ele_btn.setText(
                t('gps_map.toolbar_qt.elev_en') if checked
                else t('gps_map.toolbar_qt.elev_off')
            )     
        self._gpx_elevation_visible = checked
        if self._map_ready and self._view:
            v = "true" if checked else "false"
            self._view.page().runJavaScript(
                f"window.setElevationVisible&&window.setElevationVisible({v});"
            )


    def _load_gpx_from_js(self) -> None:
        """JS 툴바 ACTION:GPXLOAD → 관리 다이얼로그 (Qt 임베디드 뷰)"""
        dlg = GpsMapGpxManagerDialog(self._gpx_save_dir(), self)
        dlg.file_selected.connect(self._open_gpx_from_path)
        dlg.exec()


    def _sync_gpx_buttons(self, has_elevation: bool, has_sensors:   bool = False,) -> None:
        """GPX 로딩 완료 후 Qt 툴바 버튼 상태 동기화"""
        has_panel = has_elevation or has_sensors
        gpx_btn     = getattr(self, '_gpx_btn',     None)
        gpx_ele_btn = getattr(self, '_gpx_ele_btn', None)

        if gpx_btn is not None:
            gpx_btn.setEnabled(True)
            gpx_btn.setChecked(True)
            gpx_btn.setText(t('gps_map.toolbar_qt.gpx_on'))

        if gpx_ele_btn is not None:
            gpx_ele_btn.setEnabled(has_panel)
            if has_elevation:
                self._gpx_elevation_visible = True
                gpx_ele_btn.setChecked(True)
                gpx_ele_btn.setText(t('gps_map.toolbar_qt.elev_en'))
            else:
                self._gpx_elevation_visible = False
                gpx_ele_btn.setChecked(False)
                gpx_ele_btn.setText(t('gps_map.toolbar_qt.elev_off'))

        for btn in [
            getattr(self, '_speed_heatmap_btn', None),
            getattr(self, '_arrows_btn',        None),
            getattr(self, '_stop_markers_btn',  None),
        ]:
            if btn is not None:
                btn.setEnabled(True)


    def _open_gpx_from_path(self, path: Path) -> None:
        """GPX 경로를 받아 파싱 후 지도 로드 (GpsMapGpxManagerDialog 콜백)"""
        try:
            gpx_data = parse_gpx_file(str(path))
            if gpx_data is None:
                _DarkMessageBox(
                    self, kind='warning',
                    title=t('gps_map.window.title'),
                    body=t('gps_map.window.error_gpx_load'),
                ).exec()
                return

            self._gpx_data    = gpx_data
            self._gpx_visible = True
            has_ele     = bool(self._gpx_data.get("has_elevation"))
            has_sensors = self._gpx_has_sensors()
            self._sync_gpx_buttons(has_ele, has_sensors)
            self._update_window_title()

            if self._map_ready and self._view:
                gpx_json = json.dumps(self._gpx_data, ensure_ascii=False)
                self._view.page().runJavaScript(f'window._reloadGpx({gpx_json})')

        except Exception as e:
            error_print(f'[GpsMap] GPX 로딩 오류: {type(e).__name__}: {e}')
            _DarkMessageBox(
                self, kind='danger',
                title=t('gps_map.window.title'),
                body=t('gps_map.window.error_gpx_generic', err=str(e)),
            ).exec()


    def _toggle_photo_playback(self, checked: bool) -> None:
        if self._playback_btn is not None:   
            self._playback_btn.setText(
                t('gps_map.toolbar_qt.play_on') if checked
                else t('gps_map.toolbar_qt.play_off')
            )
        if self._map_ready and self._view:
            if checked:
                self._view.page().runJavaScript(
                    "window.startPhotoPlayback&&window.startPhotoPlayback(2500)"
                )
            else:
                self._view.page().runJavaScript(
                    "window.stopPhotoPlayback&&window.stopPhotoPlayback()"
                )


    def _do_capture(self, mode: str) -> None:
        from datetime import datetime
        from pathlib import Path
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        if not self._view:
            return

        pixmap = self._view.grab()

        toolbar_h = 65
        if pixmap.height() > toolbar_h:
            pixmap = pixmap.copy(0, toolbar_h, pixmap.width(), pixmap.height() - toolbar_h)

        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buf, "JPEG", 92)
        ok = pixmap.save(buf, "JPEG", 92)
        buf.close()
        if not ok or ba.isEmpty():
            error_print("[GpsMap] 캡처 JPEG 인코딩 실패")
            return        
        img_bytes = self._add_capture_exif(bytes(ba.data()))  

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{ts}_dodoRynx.jpg"

        if mode == 'clipboard':
            QApplication.clipboard().setPixmap(pixmap)
            if self._view and self._map_ready:
                self._view.page().runJavaScript(
                    "if(window._captureToast) _captureToast('📋 Copied to clipboard');"
                )
            return

        save_path: Path | None = None
        if mode == 'current_folder' and self._gps_data:
            folder = Path(self._gps_data[0]['filepath']).parent
            if folder.exists():
                save_path = folder / filename

        if save_path is None:
            path_str, _ = QFileDialog.getSaveFileName(
                self, t('gps_map.capture.save_as'), filename, 'JPEG image (*.jpg *.jpeg)'
            )
            if not path_str:
                return
            save_path = Path(path_str)

        try:
            save_path.write_bytes(img_bytes)
            if self._view and self._map_ready:
                self._view.page().runJavaScript(
                    "if(window._captureToast) _captureToast('💾 Saved successfully');"
                )
        except Exception as e:
            _DarkMessageBox(self, kind='warning', title='Save failed', body=str(e)).exec()


    def _add_capture_exif(self, jpeg_bytes: bytes) -> bytes:
        try:
            import piexif
            from PIL import Image
            import io
            exif_dict = {
                "0th": {
                    piexif.ImageIFD.Software:  b"dodoRynx",
                    piexif.ImageIFD.Artist:    b"dodoRynx",
                    piexif.ImageIFD.Copyright: b"dodoRynx",
                },
                "Exif": {}, "GPS": {}, "1st": {},
            }
            img = Image.open(io.BytesIO(jpeg_bytes))
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=93, exif=piexif.dump(exif_dict))
            return out.getvalue()
        except Exception:
            return jpeg_bytes  


    def _open_gpx_merger(self) -> None:
        from tools.gpx_merger.gpx_launcher import open_gpx_merger
        open_gpx_merger(parent=self)
            
# ──────────────────────────────────────────────────────────────
# 싱글턴 팩토리
# ──────────────────────────────────────────────────────────────

_instance: Optional["GPSMapWindow"] = None

def open_gps_map(
    files: List[Path],
    current_file: Optional[Path],
    parent: Optional[QWidget] = None,
) -> Optional[GPSMapWindow]:
    global _instance
    if not _WE_OK:
        warning_print("[GpsMap] WebEngine 없음"); return None
    if _instance is None:
        _instance = GPSMapWindow(parent)
    _instance.load_photos(files, current_file)
    if not _instance.isVisible():
        _instance.show()
    _instance.raise_(); _instance.activateWindow()
    return _instance
