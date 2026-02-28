# -*- coding: utf-8 -*-
# core/map_loader.py

from __future__ import annotations

import time
from pathlib import Path
from threading import Lock
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage,
    QPainter, QPainterPath, QPen,
    QPixmap, QRadialGradient,
)

from core.hybrid_cache import HybridCache
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.paths import app_resources_dir, ensure_dir

if TYPE_CHECKING:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage

WEBENGINE_AVAILABLE: bool
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView   # type: ignore[no-redef]
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage  # type: ignore[no-redef]
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False
    warning_print("PySide6-WebEngine 없음 — pip install PySide6-Addons")


_ASSET_DIR = app_resources_dir() / "assets"
_STYLE_URL   = "https://tiles.openfreemap.org/styles/liberty"

_render_cache = HybridCache(
    namespace    = "ofm_rendered",
    max_memory_mb= 50,
    max_disk_mb  = 200,
    expiry_days  = 28,
)

_RENDER_TIMEOUT_MS = 30_000
_GRAB_DELAY_MS     = 100


# ============================================
# 모듈 수준 — 설정 / 자산
# ============================================

def configure_ofm_cache(
    memory_mb:   int = 50,
    disk_mb:     int = 200,
    expiry_days: int = 28,
) -> None:
    """
    앱 시작 시 config 값으로 캐시를 재구성한다.
    반드시 OFMMapLoader 첫 사용 이전에 호출할 것.
    """
    global _render_cache
    _render_cache = HybridCache(
        namespace    = "ofm_rendered",
        max_memory_mb= memory_mb,
        max_disk_mb  = disk_mb,
        expiry_days  = expiry_days,
    )
    info_print(f"OFM 캐시 구성: 메모리={memory_mb}MB 디스크={disk_mb}MB 만료={expiry_days}일")


def download_maplibre_assets() -> bool:
    """MapLibre GL JS / CSS 를 assets/ 에 다운로드한다. 이미 존재하면 건너뜀."""
    import urllib.request
    ensure_dir(_ASSET_DIR)
    files = {
        "maplibre-gl.min.js": "https://unpkg.com/maplibre-gl/dist/maplibre-gl.js",
        "maplibre-gl.css":    "https://unpkg.com/maplibre-gl/dist/maplibre-gl.css",
    }
    ok = True
    for fname, url in files.items():
        dest = _ASSET_DIR / fname
        if dest.exists():
            debug_print(f"[OFM] assets/{fname} 이미 존재 — 건너뜀")
            continue
        try:
            info_print(f"[OFM] 다운로드 중: {fname} ...")
            urllib.request.urlretrieve(url, dest)
            info_print(f"[OFM] 저장 완료: {dest} ({dest.stat().st_size // 1024} KB)")
        except Exception as e:
            error_print(f"[OFM] 다운로드 실패 ({fname}): {e}")
            ok = False
    return ok


# ============================================
# _RateLimiter
# ============================================

class _RateLimiter:
    """
    연속 렌더 요청 간 최소 간격 보장.
    min_interval_ms: 요청 사이 최소 대기 시간 (밀리초)
    """

    def __init__(self, min_interval_ms: int = 1000) -> None:
        self._min_interval = min_interval_ms / 1000.0
        self._last_request = 0.0
        self._lock = Lock()


    def acquire(self) -> float:
        """
        필요한 대기 시간(초)을 반환한다.
        호출 즉시 _last_request를 갱신하므로 스레드 안전.
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request
            wait = max(0.0, self._min_interval - elapsed)
            self._last_request = now + wait   # 예약 시점 기준으로 갱신
            return wait


    def release(self) -> None:
        """cancel() 호출 시 타임스탬프를 되돌려 다음 요청이 즉시 가능하게"""
        with self._lock:
            self._last_request = 0.0


_rate_limiter = _RateLimiter(min_interval_ms=1000)


# ============================================
# _SilentPage(QWebEnginePage)
# ============================================

class _SilentPage(QWebEnginePage):
    """MapLibre 스타일 JS 경고 억제"""
    _SUPPRESS = (
        "Expected value to be of type number",
        "could not be loaded",          # 스프라이트 누락 이미지
    )

    def javaScriptConsoleMessage(self, level, message, line, source):
        if any(s in message for s in self._SUPPRESS):
            return
        debug_print(f"[JS] {message} (line {line})")


# ============================================
# OFMMapLoader(QObject)
# ============================================

class OFMMapLoader(QObject):
    """
    OpenFreeMap 기반 지도 이미지 로더.

    외부 인터페이스:
      map_loaded  Signal(QImage)  : 완성된 지도 이미지 (마커 + attribution 포함)
      load_failed Signal(str)     : 실패 메시지
      progress    Signal(int,int) : (0,1) 로딩 시작, (1,1) 완료

    ⚠️ 반드시 GUI 스레드에서 생성 및 호출할 것.
    ⚠️ cancel() 후 반드시 deleteLater() 호출.
    """

    map_loaded  = Signal(QImage)
    load_failed = Signal(str)
    progress    = Signal(int, int)

    # ── 캐시 (classmethod) ──────────────────────
    @classmethod
    def get_cache_size(cls) -> int:
        return _render_cache.memory_count()


    @classmethod
    def clear_cache(cls) -> None:
        _render_cache.clear()


    @classmethod
    def is_cached(cls, lat: float, lon: float, zoom: int, width: int, height: int) -> bool:
        return not _render_cache.is_stale(f"ofm_{lat:.4f}_{lon:.4f}_{zoom}_{width}x{height}")


    @classmethod
    def get_cached_pixmap(
        cls, lat: float, lon: float, zoom: int, width: int, height: int
    ) -> Optional[QPixmap]:
        key = f"ofm_{lat:.4f}_{lon:.4f}_{zoom}_{width}x{height}"
        if _render_cache.is_stale(key):
            return None
        return _render_cache.get(key)


    # ── 초기화 ──────────────────────────────────

    def __init__(
        self,
        latitude:  float,
        longitude: float,
        zoom:      int   = 15,
        width:     int   = 275,
        height:    int   = 200,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._lat    = latitude
        self._lon    = longitude
        self._zoom   = zoom
        self._width  = width
        self._height = height

        # from __future__ import annotations 덕분에 QWebEngineView 어노테이션이
        # 문자열로 처리되어 WEBENGINE_AVAILABLE=False 시에도 NameError 없음
        self._view:          Optional[QWebEngineView]  = None
        self._timeout_timer: Optional[QTimer]          = None
        self._running   = False
        self._cancelled = False
        self._retry_count = 0


    # ── 공개 API ──────────────────────────────────

    def start(self) -> None:
        key = self._cache_key()
        if not _render_cache.is_stale(key):
            cached = _render_cache.get(key)
            if cached is not None:
                img = cached.toImage().convertToFormat(QImage.Format.Format_ARGB32)
                self.map_loaded.emit(img)
                return

        # 캐시 미스 → Rate Limit
        wait_sec = _rate_limiter.acquire()
        if wait_sec > 0:
            QTimer.singleShot(int(wait_sec * 1000), self._start_render)
        else:
            self._start_render()


    def cancel(self) -> None:
        debug_print("OFMMapLoader.cancel()")
        self._cancelled = True
        self._running   = False
        _rate_limiter.release()          # ← 추가: 타임스탬프 롤백
        if self._timeout_timer is not None:
            self._timeout_timer.stop()
            self._timeout_timer = None
        self._cleanup_view()


    def isRunning(self) -> bool:
        return self._running


    # ── 내부 구현 ──────────────────────────────────────────────────────────────

    def _cache_key(self) -> str:
        return f"ofm_{self._lat:.4f}_{self._lon:.4f}_{self._zoom}_{self._width}x{self._height}"


    def _start_render(self) -> None:
        """실제 WebView 렌더링 시작 (Rate Limit 대기 후 호출)"""
        if self._cancelled:
            return

        if self._running:
            warning_print("OFMMapLoader: 이미 실행 중 — 무시")
            return
        if not WEBENGINE_AVAILABLE:
            self.load_failed.emit("PySide6-WebEngine 미설치 (pip install PySide6-Addons)")
            return

        self._running   = True
        # self._cancelled = False   ← 삭제: cancel() 후 QTimer 콜백 도착 시 취소 무시되는 버그

        key = self._cache_key()
        if not _render_cache.is_stale(key):
            pix = _render_cache.get(key)
            if pix is not None and not pix.isNull():
                debug_print(f"OFM 캐시 HIT: {key}")
                img = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
                _rate_limiter.release()
                self._running = False
                self.progress.emit(1, 1)
                self.map_loaded.emit(img)
                return

        self.progress.emit(0, 1)
        self._start_webview()


    def _start_webview(self) -> None:
        """QWebEngineView 생성 → HTML 로드 → 이벤트 리스너 등록"""
        self._view = QWebEngineView()
        self._view.setPage(_SilentPage(self._view))
        self._view.setFixedSize(self._width, self._height)

        # LocalContentCanAccessRemoteUrls: file:// 페이지 → https:// 타일 서버 접근 허용
        settings = self._view.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )

        # 화면 밖 배치 (show() 없이는 WebGL 렌더링 비활성)
        self._view.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnBottomHint
        )
        self._view.move(-self._width * 3, -self._height * 3)
        self._view.show()

        # ── base URL 결정 ─────────────────────────────────────────────────
        # Chromium 보안 정책:
        #   https:// base → file:// script 로드 차단 (mixed content)
        #   file://  base → 로컬 JS 로드 OK
        #              + LocalContentCanAccessRemoteUrls=True 로 타일 서버 접근 가능
        #
        # assets/index.html 을 가상 기준 파일로 지정 (실제 파일 불필요)
        # → <script src="maplibre-gl.min.js"> 가 assets/ 디렉토리 기준으로 해석됨
        assets_ok = (_ASSET_DIR / "maplibre-gl.min.js").exists()
        if assets_ok:
            base_url = QUrl.fromLocalFile(str(_ASSET_DIR / "index.html"))
        else:
            warning_print(
                "OFM: assets/maplibre-gl.min.js 없음 — CDN 사용\n"
                "권장: from core.map_loader import download_maplibre_assets; "
                "download_maplibre_assets()"
            )
            base_url = QUrl("https://tiles.openfreemap.org/")

        self._view.titleChanged.connect(self._on_title_changed)
        self._view.setHtml(self._build_html(assets_ok), base_url)

        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)
        self._timeout_timer.start(_RENDER_TIMEOUT_MS)

        debug_print(
            f"OFM WebView 시작: lat={self._lat:.4f}, lon={self._lon:.4f}, "
            f"z={self._zoom}, {self._width}x{self._height}"
        )


    def _build_html(self, use_local_assets: bool = True) -> str:
        """
        MapLibre GL JS 렌더링 HTML 생성.

        [중요] JS 문자열을 반드시 큰따옴표(")로 작성할 것.
        Python 3.12+ f-string(PEP 701) 파서는 삼중따옴표 f-string 내부의
        백슬래시-작은따옴표(\'..\') 시퀀스를 잘못 해석해 SyntaxError를 유발함.
        """
        if use_local_assets:
            # base_url = file:///assets/index.html 기준 상대 경로
            js_src  = "maplibre-gl.min.js"
            css_src = "maplibre-gl.css"
        else:
            js_src  = "https://unpkg.com/maplibre-gl/dist/maplibre-gl.js"
            css_src = "https://unpkg.com/maplibre-gl/dist/maplibre-gl.css"

        # JS 문자열은 모두 큰따옴표 사용 (Python f-string 파서 충돌 방지)
        return (
            "<!DOCTYPE html>"
            "<html><head>"
            '<meta charset="utf-8">'
            f'<script src="{js_src}"></script>'
            f'<link href="{css_src}" rel="stylesheet">'
            "<style>"
            "  * { margin: 0; padding: 0; box-sizing: border-box; }"
            f"  #map {{ width: {self._width}px; height: {self._height}px; overflow: hidden; }}"
            "</style>"
            "</head>"
            "<body>"
            '<div id="map"></div>'
            "<script>"
            "var map = new maplibregl.Map({"
            f'  style:                 "{_STYLE_URL}",'
            f"  center:                [{self._lon}, {self._lat}],"
            f"  zoom:                  {self._zoom},"
            '  container:             "map",'
            "  interactive:           false,"
            "  attributionControl:    false,"
            "  preserveDrawingBuffer: true"
            "});"
            'map.once("idle", function() {'
            '  document.title = "MAP_READY";'
            "});"
            'map.on("error", function(e) {'
            '  var msg = (e && e.error && e.error.message) ? e.error.message : "unknown";'
            '  document.title = "MAP_ERR:" + msg;'
            "});"
            "</script>"
            "</body></html>"
        )


    def _on_title_changed(self, title: str) -> None:
        if self._cancelled:
            return
        if title == "MAP_READY":
            if self._timeout_timer:
                self._timeout_timer.stop()
                self._timeout_timer = None
            QTimer.singleShot(_GRAB_DELAY_MS, self._capture)
        elif title.startswith("MAP_ERR:"):
            if self._timeout_timer:
                self._timeout_timer.stop()
                self._timeout_timer = None
            msg = title[8:]
            error_print(f"OFM MapLibre 오류: {msg}")
            self._cleanup_view()
            self._running = False
            self.load_failed.emit(f"지도 오류: {msg[:60]}")


    def _capture(self) -> None:
        if self._cancelled or self._view is None:
            return
        try:
            pixmap = self._view.grab()
        except Exception as e:
            error_print("OFM grab() 예외: " + str(e))
            self._cleanup_view()
            self._running = False
            self.load_failed.emit("캡처 실패: " + str(e)[:50])
            return

        if pixmap is None or pixmap.isNull():
            warning_print("OFM: grab() 결과가 null")
            self._cleanup_view()
            self._running = False
            self.load_failed.emit("지도 캡처 실패 — 빈 이미지")
            return

        # ── High-DPI 정규화 ───────────────────────────────────────────────
        # grab() 은 물리 픽셀(DPR × 논리 크기)로 캡처함.
        # 예) 150% 배율(DPR=1.5) → 400x300 요청 시 600x450 물리px 반환
        #
        # 문제: scaled(400, 300) 후에도 DPR=1.5 유지
        #       → QLabel 표시 시 400/1.5 × 300/1.5 = 267×200 논리px (여백 발생)
        # 캐시: PNG 저장→재로드 시 DPR=1.0 으로 초기화 → 정상 표시
        #       (이것이 "재오픈 시 정상" 현상의 원인)
        #
        # 수정: grab() 직후 논리 해상도(self._width × self._height)로 다운스케일
        #       + devicePixelRatio(1.0) 으로 강제 정규화
        dpr = pixmap.devicePixelRatio()
        debug_print("OFM grab(): {}x{} (DPR={:.2f})".format(
            pixmap.width(), pixmap.height(), dpr
        ))
        if abs(dpr - 1.0) > 0.01:
            pixmap = pixmap.scaled(
                self._width,
                self._height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap.setDevicePixelRatio(1.0)
            debug_print("OFM DPR 정규화: {}x{} DPR={:.2f} → {}x{} DPR=1.0".format(
                int(self._width * dpr), int(self._height * dpr), dpr,
                self._width, self._height
            ))

        img = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_marker(painter, self._width // 2, self._height // 2)
        self._draw_attribution(painter, self._width, self._height)
        painter.end()

        pix_final = QPixmap.fromImage(img)
        raw_png   = HybridCache.pixmap_to_bytes(pix_final, "PNG")

        MIN_VALID_BYTES = 10 * 1024   # 10KB 미만 = 흰 화면으로 판단
        if raw_png and len(raw_png) < MIN_VALID_BYTES:
            warning_print(
                "OFM 흰 화면 감지 ({}KB) → 캐시 미저장, 재시도".format(
                    len(raw_png) // 1024
                )
            )

            self._retry_count = 0
            self._retry_count += 1
            if self._retry_count <= 2:
                debug_print(f"OFM 재시도 {self._retry_count}/2 — 2초 후")
                QTimer.singleShot(2000, self._do_retry_capture)
            else:
                warning_print("OFM 재시도 횟수 초과 → 실패 처리")
                self._cleanup_view()
                self._running = False
                self.load_failed.emit("지도 렌더링 실패 (흰 화면)")
            return

        if raw_png:
            _render_cache.put(self._cache_key(), pix_final, raw_png)
            debug_print("OFM 캐시 저장: {} KB ({})".format(
                len(raw_png) // 1024, self._cache_key()))
        else:
            warning_print("OFM: PNG 변환 실패 — 캐시 저장 건너뜀")

        self._cleanup_view()
        self._running = False
        self.progress.emit(1, 1)
        self.map_loaded.emit(img)


    def _do_retry_capture(self) -> None:
        """흰 화면 재시도 — view는 살아있는 상태에서 다시 grab()"""
        if self._cancelled or self._view is None:
            return
        debug_print("OFM 재시도 grab()")
        self._capture()


    def _on_timeout(self) -> None:
        warning_print(f"OFM 렌더링 타임아웃 ({_RENDER_TIMEOUT_MS // 1000}s 초과)")
        self._timeout_timer = None
        self._cleanup_view()
        self._running = False
        self.load_failed.emit(f"지도 렌더링 타임아웃 ({_RENDER_TIMEOUT_MS // 1000}s)")


    def _cleanup_view(self) -> None:
        if self._view is not None:
            try:
                self._view.titleChanged.disconnect(self._on_title_changed)
            except (RuntimeError, TypeError):
                pass
            try:
                self._view.hide()
                self._view.deleteLater()
            except RuntimeError:
                pass
            self._view = None


    # ── 렌더링 헬퍼 ─────────────────────────────

    def _draw_marker(self, painter: QPainter, x: int, y: int) -> None:
        shadow = QRadialGradient(x, y + 18, 10)
        shadow.setColorAt(0, QColor(0, 0, 0, 80))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(shadow))
        painter.drawEllipse(QPoint(x, y + 18), 9, 3)

        path = QPainterPath()
        path.addEllipse(QPoint(x, y - 7), 9, 9)
        path.moveTo(x - 6, y + 2)
        path.lineTo(x, y + 14)
        path.lineTo(x + 6, y + 2)
        path.closeSubpath()

        grad = QRadialGradient(x - 3, y - 10, 12)
        grad.setColorAt(0, QColor(255, 80, 80))
        grad.setColorAt(1, QColor(220, 20, 20))
        painter.setPen(QPen(QColor(180, 0, 0), 1.5))
        painter.setBrush(QBrush(grad))
        painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QPoint(x, y - 7), 4, 4)


    def _draw_attribution(self, painter: QPainter, w: int, h: int) -> None:
        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)

        text = "OpenFreeMap © OpenMapTiles Data from OpenStreetMap"
        fm = painter.fontMetrics()

        pad = 6
        text_rect = fm.boundingRect(text)

        box_w = text_rect.width() + pad * 2
        box_h = text_rect.height() + pad * 2

        # 위치 결정
        if w <= 320:
            x = pad
        else:
            x = w - box_w - pad

        y = h - box_h - pad

        box_rect = QRect(x, y, box_w, box_h)

        # 배경
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawRect(box_rect)

        # 텍스트
        painter.setPen(QColor(0, 0, 0))
        painter.drawText(box_rect.adjusted(pad, pad, -pad, -pad),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        

# ============================================
# OFMPrefetcher(QObject)
# ============================================

class OFMPrefetcher(QObject):
    """
    인접 이미지 GPS 맵 백그라운드 프리패치.
    현재 맵 로드 완료 후 큐에 쌓인 좌표를 순차 렌더링해 캐시에 저장.
    """

    _START_DELAY_MS      = 2500   # 현재 이미지 로드 후 첫 태스크 시작 딜레이
    _INTER_TASK_DELAY_MS =  800   # 태스크 간 간격

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue: list[tuple] = []
        self._loader: Optional["OFMMapLoader"] = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._process_next)


    # ── 공개 API ─────────────────────────────────────────────────────────────

    def schedule(self, tasks: list[tuple]) -> None:
        """
        tasks: [(lat, lon, zoom, width, height), ...]
        이미 캐시된 항목은 자동 제외 후 큐 등록.
        """
        self.cancel()

        filtered = []
        for item in tasks:
            lat, lon, zoom, w, h = item
            key = f"ofm_{lat:.4f}_{lon:.4f}_{zoom}_{w}x{h}"
            if _render_cache.is_stale(key):
                filtered.append(item)

        if not filtered:
            debug_print("[Prefetch] 모두 캐시됨 — 스킵")
            return

        self._queue = filtered
        debug_print(f"[Prefetch] {len(self._queue)}개 예약 → {self._START_DELAY_MS}ms 후 시작")
        self._timer.start(self._START_DELAY_MS)


    def cancel(self) -> None:
        """진행 중인 프리패치 전체 취소."""
        self._timer.stop()
        self._queue.clear()
        if self._loader is not None:
            loader, self._loader = self._loader, None
            try:
                loader.map_loaded.disconnect()
                loader.load_failed.disconnect()
            except RuntimeError:
                pass
            loader.cancel()


    # ── 내부 ─────────────────────────────────────────────────────────────────

    def _process_next(self) -> None:
        if not self._queue:
            debug_print("[Prefetch] 큐 소진")
            return

        lat, lon, zoom, w, h = self._queue.pop(0)
        key = f"ofm_{lat:.4f}_{lon:.4f}_{zoom}_{w}x{h}"

        # 직전에 다른 경로로 캐시됐을 수 있으므로 재확인
        if not _render_cache.is_stale(key):
            debug_print(f"[Prefetch] 이미 캐시됨 스킵: {key}")
            self._timer.start(50)
            return

        debug_print(f"[Prefetch] 렌더 시작: {key}")
        self._loader = OFMMapLoader(lat, lon, zoom=zoom, width=w, height=h)
        self._loader.map_loaded.connect(
            self._on_done, Qt.ConnectionType.QueuedConnection
        )
        self._loader.load_failed.connect(
            self._on_failed, Qt.ConnectionType.QueuedConnection
        )
        self._loader.start()


    def _on_done(self, _img: QImage) -> None:
        debug_print("[Prefetch] 태스크 완료")
        self._loader = None
        if self._queue:
            self._timer.start(self._INTER_TASK_DELAY_MS)


    def _on_failed(self, error: str) -> None:
        debug_print(f"[Prefetch] 태스크 실패: {error}")
        self._loader = None
        if self._queue:
            self._timer.start(self._INTER_TASK_DELAY_MS)


# 모듈 싱글턴
_prefetcher = OFMPrefetcher()


