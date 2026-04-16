# -*- coding: utf-8 -*-
# core/map_loader.py
"""
래스터 타일 기반 지도 이미지 로더.

resources/tiles/{z}/{x}/{y}.webp 구조의 로컬 WebP 타일을
Leaflet + QWebEngineView로 렌더링·캡처한다.
"""
from __future__ import annotations

import hashlib
import http.server
import math
import socket
import threading
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import (
    QBuffer, QByteArray, QIODevice,
    QObject, Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPixmap,
)

from utils.debug import debug_print, error_print, info_print, warning_print
from utils.paths import app_resources_dir

if TYPE_CHECKING:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile

WEBENGINE_AVAILABLE: bool
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView          # type: ignore[no-redef]
    from PySide6.QtWebEngineCore import QWebEnginePage             # type: ignore[no-redef]
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False
    warning_print("PySide6-WebEngine 없음 — pip install PySide6-Addons")

_ASSET_DIR         = app_resources_dir() / "assets"
_TILE_DIR_DEFAULT  = app_resources_dir() / "tiles"
_RENDER_TIMEOUT_MS = 15_000   # 15 s
_GRAB_DELAY_MS     = 120     

# ── 로컬 HTTP 서버 ────────────────────────────────────────────────────────────
_http_server:       Optional[http.server.HTTPServer] = None
_http_server_port:  int = 0
_http_server_lock:  threading.Lock = threading.Lock()

# ── 모듈 수준 래스터 타일 설정 ────────────────────────────────────────────────
_MAX_ZOOM: int = 16
_MIN_ZOOM: int = 1
_raster_tiles_dir: Optional[Path] = None
_raster_min_zoom:  int  = _MIN_ZOOM
_raster_max_zoom:  int  = _MAX_ZOOM
_raster_tile_size: int  = 512  
_raster_tms:       bool = False

# ── fallback 타일 LRU 캐시 (HTTP 서버 스레드 간 공유) ─────────────────────────
_fallback_tile_cache: OrderedDict[str, bytes] = OrderedDict()
_FALLBACK_CACHE_MAX:  int            = 512
_fallback_cache_lock: threading.Lock = threading.Lock()


# =============================================================================
# 타일 fallback 유틸리티
# =============================================================================

def _encode_webp(image: QImage, quality: int = 85) -> Optional[bytes]:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = image.save(buf, "WEBP", quality)  # type: ignore[arg-type]
    buf.close()
    if not ok or ba.isEmpty():
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")             # type: ignore[arg-type]
        buf.close()
    return ba.data() if not ba.isEmpty() else None


def _find_parent_tile(
    z: int, x: int, y: int
) -> Optional[tuple[int, int, int, Path]]:
    """
    (z, x, y) 위치에서 파일이 존재하는 가장 가까운 줌(z 포함 상위) 탐색.
    TMS/XYZ 공통 — 부모-자식 bit-shift 관계는 방향 무관하게 성립.
    반환: (parent_z, parent_x, parent_y, Path) 또는 None.
    """
    if _raster_tiles_dir is None:
        return None
    for pz in range(z, _raster_min_zoom - 1, -1):
        shift = z - pz
        px    = x >> shift
        py    = y >> shift
        path  = _raster_tiles_dir / str(pz) / str(px) / f"{py}.webp"
        if path.exists():
            return pz, px, py, path
    return None


def _build_fallback_tile(z: int, x: int, y: int) -> Optional[bytes]:
    """
    (z, x, y) 위치의 WebP 타일 바이트를 반환한다.

    처리 순서:
      1. LRU 캐시 HIT → 즉시 반환
      2. exact tile 파일 존재 → 파일 그대로 반환
      3. 상위 줌 타일 발견 → 해당 영역 crop + tile_size로 확대 후 반환
      4. 부모 없음 → None (Leaflet이 404 처리)

    ⚠ HTTP 서버 스레드에서 호출됨. QImage 조작은 thread-safe.
    """
    cache_key = f"{z}/{x}/{y}"

    with _fallback_cache_lock:
        if cache_key in _fallback_tile_cache:
            _fallback_tile_cache.move_to_end(cache_key)
            return _fallback_tile_cache[cache_key]

    found = _find_parent_tile(z, x, y)
    if found is None:
        return None

    pz, px, py, path = found

    # ── exact tile: 파일 그대로 반환 ──────────────────────────────────────
    if pz == z:
        try:
            data = path.read_bytes()
        except OSError:
            return None
        _store_fallback_cache(cache_key, data)
        return data

    # ── parent tile: crop → 확대 ───────────────────────────────────────────
    img = QImage(str(path))
    if img.isNull():
        return None

    scale   = 1 << (z - pz)       
    src_w   = img.width()  / scale    
    src_h   = img.height() / scale  
    child_x = x - (px << (z - pz))  
    child_y = y - (py << (z - pz)) 

    sx = int(round(child_x * src_w))
    sy = int(round(child_y * src_h))
    sw = max(1, int(round(src_w)))
    sh = max(1, int(round(src_h)))

    cropped = img.copy(sx, sy, sw, sh)
    if cropped.isNull():
        return None

    scaled = cropped.scaled(
        _raster_tile_size, _raster_tile_size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation, 
    )

    data = _encode_webp(scaled)
    if data:
        _store_fallback_cache(cache_key, data)
    return data


def _store_fallback_cache(key: str, data: bytes) -> None:
    """fallback LRU 캐시 저장. 초과 시 가장 오래된 항목 제거."""
    with _fallback_cache_lock:
        if key in _fallback_tile_cache:
            _fallback_tile_cache.move_to_end(key)
        else:
            if len(_fallback_tile_cache) >= _FALLBACK_CACHE_MAX:
                _fallback_tile_cache.popitem(last=False)
            _fallback_tile_cache[key] = data

# =============================================================================
# 래스터 타일 유틸리티
# =============================================================================

def detect_raster_zoom_range(base_dir: Path) -> tuple[int, int]:
    """항상 고정 줌 범위 반환. 폴더 존재 여부와 무관하게 1~16."""
    return (_MIN_ZOOM, _MAX_ZOOM)


def configure_raster_tiles(
    base_dir:  Optional[Path] = None,
    tile_size: int  = 512,        
    tms:       bool = False,
) -> None:
    global _raster_tiles_dir, _raster_min_zoom, _raster_max_zoom
    global _raster_tile_size, _raster_tms

    _raster_tile_size = tile_size
    _raster_tms       = tms
    target            = Path(base_dir) if base_dir is not None else _TILE_DIR_DEFAULT
    _raster_tiles_dir = target
    _raster_min_zoom  = _MIN_ZOOM   
    _raster_max_zoom  = _MAX_ZOOM 

    with _fallback_cache_lock:
        _fallback_tile_cache.clear()

    info_print(
        f"[RasterTiles] 설정 완료: {target}, "
        f"tile_size={tile_size}, tms={tms}, zoom={_MIN_ZOOM}~{_MAX_ZOOM}"
    )

    _js  = _ASSET_DIR / "leaflet.js"
    _css = _ASSET_DIR / "leaflet.css"
    if not _js.exists() or not _css.exists():
        threading.Thread(
            target=download_assets, daemon=True, name="leaflet-asset-dl"
        ).start()
        info_print("[RasterTiles] Leaflet 에셋 백그라운드 다운로드 시작")
    else:
        debug_print("[RasterTiles] Leaflet 에셋 존재 확인 — 다운로드 불필요")


def get_raster_zoom_range() -> tuple[int, int]:
    """현재 설정된 (min_zoom, max_zoom) 반환."""
    return (_raster_min_zoom, _raster_max_zoom)


def configure_render_cache(memory_mb: int = 50) -> None:
    """앱 시작 시 렌더 메모리 캐시 크기 설정."""
    global _render_cache
    _render_cache = _MemRenderCache(max_mb=memory_mb)
    info_print(f"[RasterTiles] 렌더 캐시 구성: 메모리={memory_mb}MB")

# =============================================================================
# _MemRenderCache — 메모리 전용 LRU
# =============================================================================

def _pixmap_bytes(pixmap: QPixmap) -> int:
    return pixmap.width() * pixmap.height() * 4


class _MemRenderCache:

    def __init__(self, max_mb: int = 50) -> None:
        self._cache:     OrderedDict[str, QPixmap] = OrderedDict()
        self._bytes:     int  = 0
        self._max_bytes: int  = max_mb * 1024 * 1024
        self._lock:      Lock = Lock()


    def get(self, key: str) -> Optional[QPixmap]:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None


    def put(self, key: str, pixmap: QPixmap) -> None:
        size = _pixmap_bytes(pixmap)
        with self._lock:
            if key in self._cache:
                self._bytes -= _pixmap_bytes(self._cache.pop(key))
            while self._bytes + size > self._max_bytes and self._cache:
                _, evicted = self._cache.popitem(last=False)
                self._bytes -= _pixmap_bytes(evicted)
            self._cache[key] = pixmap
            self._bytes += size


    def is_stale(self, key: str) -> bool:
        with self._lock:
            return key not in self._cache


    def invalidate(self, key: str) -> None:
        with self._lock:
            if key in self._cache:
                self._bytes -= _pixmap_bytes(self._cache.pop(key))


    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._bytes = 0
        info_print("렌더 캐시 전체 삭제")


    def memory_count(self) -> int:
        with self._lock:
            return len(self._cache)


    def memory_bytes_used(self) -> int:
        with self._lock:
            return self._bytes


    def stats(self) -> dict:
        return {
            "memory_count":  self.memory_count(),
            "memory_mb":     f"{self.memory_bytes_used() / 1024 / 1024:.1f}",
            "max_memory_mb": self._max_bytes // 1024 // 1024,
        }


_render_cache = _MemRenderCache(max_mb=50)


# =============================================================================
# 로컬 HTTP 핸들러
# =============================================================================

class _RasterHTTPHandler(http.server.BaseHTTPRequestHandler):
    """
    /map                    → Leaflet HTML
    /tiles/{z}/{x}/{y}.webp → 타일 서빙 (없으면 부모 타일 crop·확대 fallback)
    /assets/leaflet.js|css  → Leaflet 에셋
    """

    _ASSET_TYPES = {
        ".js":   "application/javascript",
        ".css":  "text/css",
        ".webp": "image/webp",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
    }

    def do_GET(self) -> None:
        from urllib.parse import urlparse
        base = urlparse(self.path).path
        if base == "/map":
            self._serve_map_html()
        elif base.startswith("/tiles/"):
            self._serve_tile(base)
        elif base.startswith("/assets/"):
            self._serve_asset(base[len("/assets/"):])
        else:
            self.send_response(404)
            self.end_headers()

    # ── /map ─────────────────────────────────────────────────────────────────
    def _serve_map_html(self) -> None:
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(self.path).query)
        lat  = float(params.get("lat",  ["0"])[0])
        lon  = float(params.get("lon",  ["0"])[0])
        zoom = int(params.get("zoom", [str(_raster_max_zoom)])[0])
        w    = int(params.get("w",   ["400"])[0])
        h    = int(params.get("h",   ["300"])[0])

        html = _generate_leaflet_html(
            lat, lon, zoom, w, h,
            _http_server_port,
            _raster_min_zoom,
            _raster_max_zoom,
            _raster_tile_size,
            tms=_raster_tms,
        )
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control",  "no-store, no-cache, must-revalidate")
        self.send_header("Pragma",         "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ── /tiles/{z}/{x}/{y}.webp ───────────────────────────────────────────────
    def _serve_tile(self, url_path: str) -> None:
        if _raster_tiles_dir is None:
            self.send_response(503)
            self.end_headers()
            return

        parts = url_path.strip("/").split("/")
        if len(parts) != 4:
            self.send_response(400)
            self.end_headers()
            return

        _, z_str, x_str, y_part = parts
        try:
            z = int(z_str)
            x = int(x_str)
            y = int(Path(y_part).stem)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return

        try:
            data = _build_fallback_tile(z, x, y)
        except Exception as e:
            error_print(f"[RasterTiles] fallback 빌드 오류 z={z}/{x}/{y}: {e}")
            data = None

        if data is None:
            self.send_response(404)
            self.end_headers()
            return

        try:
            self.send_response(200)
            self.send_header("Content-Type",   "image/webp")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control",  "public, max-age=86400")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── /assets/... ───────────────────────────────────────────────────────────
    def _serve_asset(self, filename: str) -> None:
        path = _ASSET_DIR / filename
        if not path.exists():
            _download_asset_sync(filename, path)
        if not path.exists():
            warning_print(f"[RasterTiles] 에셋 없음 (다운로드 실패): {filename}")
            self.send_response(404)
            self.end_headers()
            return
        try:
            data  = path.read_bytes()
            ctype = self._ASSET_TYPES.get(path.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type",   ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control",  "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, BrokenPipeError): 
            return                         
        except Exception as e:
            error_print(f"[RasterTiles] 에셋 서빙 오류 ({filename}): {e}")
            try:                              
                self.send_response(500)
                self.end_headers()
            except (ConnectionAbortedError, BrokenPipeError, OSError): 
                pass


    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()


    def log_message(self, format, *args) -> None:
        pass 


def _ensure_local_server() -> int:
    global _http_server, _http_server_port
    with _http_server_lock:
        if _http_server is not None:
            return _http_server_port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), _RasterHTTPHandler
        )
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        _http_server      = server
        _http_server_port = port
        info_print(f"[RasterTiles] 로컬 서버 시작: http://127.0.0.1:{port}/")
        return port


def _generate_leaflet_html(
    lat:       float,
    lon:       float,
    zoom:      int,
    w:         int,
    h:         int,
    port:      int,
    min_zoom:  int,
    max_zoom:  int,
    tile_size: int,
    tms:       bool = False,
) -> str:
    zoom        = max(min_zoom, min(zoom, max_zoom))
    tms_str     = "true" if tms else "false"
    fallback_ms = max(_RENDER_TIMEOUT_MS - 3000, 8_000)

    return f"""<!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <!-- grab() 정상 작동을 위해 L_DISABLE_3D=true 반드시 유지 -->
    <script>window.L_DISABLE_3D = true;</script>
    <style>
    html, body, #map {{
        margin: 0; padding: 0;
        width: {w}px; height: {h}px;
        overflow: hidden;
        background: #1a1a1a;
    }}
    .leaflet-container {{ background: #1a1a1a; }}

    /* 타일 경계선 제거 - L_DISABLE_3D=true(소프트웨어 렌더) 호환 CSS */
    .leaflet-tile {{
        border: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
        display: block !important;
        image-rendering: auto;
        /* 2D 렌더링에서 sub-pixel 안티앨리어싱 방지 */
        outline: 1px solid transparent !important;
    }}
    .leaflet-tile-container {{
        /* 컨테이너 레벨 렌더링 힌트 */
        transform-style: flat !important;
    }}
    .leaflet-zoom-animated {{
        backface-visibility: hidden;
    }}
    </style>
    <link rel="stylesheet" href="http://127.0.0.1:{port}/assets/leaflet.css">
    <script src="http://127.0.0.1:{port}/assets/leaflet.js"></script>
    </head>
    <body>
    <div id="map"></div>
    <script>
    /* ── 핵심 수정: 타일 컨테이너 위치를 정수 픽셀로 강제 ──────────────
    원인: Leaflet의 setTransform()이 소수점 위치(e.g. translate(-127.4px, 83.7px))로
    타일 컨테이너를 배치 → 브라우저 렌더링 시 반올림 방향 불일치 → 1px 경계선
    수정: leaflet.js 로드 후 즉시 setTransform을 오버라이드하여 정수 강제 */
    (function() {{
        var _origSetTransform = L.DomUtil.setTransform;
        L.DomUtil.setTransform = function(el, offset, scale) {{
            if (offset && el.classList && el.classList.contains('leaflet-tile-container')) {{
                offset = L.point(Math.round(offset.x), Math.round(offset.y));
            }}
            return _origSetTransform.call(this, el, offset, scale);
        }};
    }})();

    /* ── 타일 위치 정수화 + 1px 겹침 패치 ───────────────────────── */
    function patchGridLayerGap() {{
        if (!L || !L.GridLayer || L.GridLayer.prototype._gapPatched) return;

        var origInitTile = L.GridLayer.prototype._initTile;
        var origSetPosition = L.DomUtil.setPosition;

        L.GridLayer.include({{
            _initTile: function(tile) {{
                origInitTile.call(this, tile);
                var sz = this.getTileSize();
                tile.style.width = sz.x + 'px';
                tile.style.height = sz.y + 'px';
                tile.style.marginRight = '-1px';
                tile.style.marginBottom = '-1px';
            }}
        }});

        L.DomUtil.setPosition = function(el, point) {{
            if (el && el.classList && el.classList.contains('leaflet-tile')) {{
                point = L.point(Math.round(point.x), Math.round(point.y));
            }}
            return origSetPosition.call(this, el, point);
        }};

        L.GridLayer.prototype._gapPatched = true;
    }}

    var map = L.map('map', {{
        center: [{lat}, {lon}],
        zoom: {zoom},
        minZoom: {min_zoom},
        maxZoom: {max_zoom},
        zoomControl: false,
        attributionControl: false,
        preferCanvas: true,
        zoomAnimation: false,
        fadeAnimation: false,
        markerZoomAnimation: false,
        inertia: false,
        bounceAtZoomLimits: false,
        keyboard: false,
    }});

    patchGridLayerGap();

    var layer = L.tileLayer('http://127.0.0.1:{port}/tiles/{{z}}/{{x}}/{{y}}.webp', {{
        minZoom: {min_zoom},
        maxZoom: {max_zoom},
        tileSize: {tile_size},
        tms: {tms_str},
        detectRetina: false,
        keepBuffer: 4,
        updateWhenIdle: false,
        updateWhenZooming: false,
        updateInterval: 100,
    }}).addTo(map);

    L.circleMarker([{lat}, {lon}], {{
        radius: 6, color: '#ff4444', fillColor: '#ff4444',
        fillOpacity: 0.9, weight: 2
    }}).addTo(map);

    var ready = false;
    var settle = null;
    function markReady() {{
        if (ready) return;
        ready = true;
        document.title = 'MAPREADY';
    }}
    function scheduleReady(delay) {{
        if (settle) clearTimeout(settle);
        settle = setTimeout(markReady, delay);
    }}
    layer.on('load',      function() {{ scheduleReady(200); }});
    layer.on('tileerror', function() {{ scheduleReady(400); }});
    layer.on('tileabort', function() {{ scheduleReady(400); }});
    setTimeout(function() {{ scheduleReady(0); }}, {fallback_ms});
    </script>
    </body>
    </html>"""

# =============================================================================
# WebEngine 공유 프로파일
# =============================================================================

_web_profile:      Optional["QWebEngineProfile"] = None
_web_profile_lock: threading.Lock = threading.Lock()


def _get_web_profile() -> "QWebEngineProfile":
    global _web_profile
    if _web_profile is not None:
        return _web_profile
    with _web_profile_lock:
        if _web_profile is not None:
            return _web_profile
        from PySide6.QtWebEngineCore import QWebEngineProfile
        profile = QWebEngineProfile("raster_renderer")
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        profile.setHttpCacheMaximumSize(20 * 1024 * 1024) 
        _web_profile = profile
        info_print("[RasterTiles] WebEngine 공유 프로파일 생성 (메모리 캐시)")
        return profile


# =============================================================================
# _SilentPage
# =============================================================================

class _SilentPage(QWebEnginePage):
    _SUPPRESS = ("Expected value", "could not be loaded", "unknown property", "favicon")

    def __init__(self, profile=None, parent=None):
        super().__init__(profile, parent) if profile is not None else super().__init__(parent)

    def javaScriptConsoleMessage(self, level, message, line, source):
        if any(s in message for s in self._SUPPRESS):
            return
        debug_print(f"[JS] {message} (line {line})")


# =============================================================================
# RasterTileMapLoader
# =============================================================================

class RasterTileMapLoader(QObject):
    """
    래스터 타일 기반 지도 이미지 로더.

    외부 인터페이스
    ───────────────
    map_loaded  Signal(QImage) : 완성된 지도 이미지 (attribution 포함)
    load_failed Signal(str)    : 실패 메시지
    progress    Signal(int,int): (0,1) 로딩 시작, (1,1) 완료

    """

    map_loaded  = Signal(QImage)
    load_failed = Signal(str)
    progress    = Signal(int, int)

    # ── 캐시 classmethods ─────────────────────────────────────────────────────

    @classmethod
    def get_cache_size(cls) -> int:
        return _render_cache.memory_count()


    @classmethod
    def clear_cache(cls) -> None:
        _render_cache.clear()


    @classmethod
    def is_cached(cls, lat: float, lon: float, zoom: int, width: int, height: int) -> bool:
        return not _render_cache.is_stale(
            cls._make_cache_key(lat, lon, zoom, width, height)
        )


    @classmethod
    def get_cached_pixmap(
        cls, lat: float, lon: float, zoom: int, width: int, height: int
    ) -> Optional[QPixmap]:
        key = cls._make_cache_key(lat, lon, zoom, width, height)
        return None if _render_cache.is_stale(key) else _render_cache.get(key)


    @staticmethod
    def _make_cache_key(lat, lon, zoom, width, height) -> str:
        dir_hash = (
            hashlib.md5(str(_raster_tiles_dir).encode()).hexdigest()[:8]
            if _raster_tiles_dir is not None else "no_tiles"
        )
        tms_flag = "t" if _raster_tms else "x"
        return f"rast_{dir_hash}_{tms_flag}_{lat:.4f}_{lon:.4f}_{zoom}_{width}x{height}"

    # ── 초기화 ────────────────────────────────────────────────────────────────

    def __init__(
        self,
        latitude:  float,
        longitude: float,
        zoom:      int = 15,
        width:     int = 400,
        height:    int = 300,
        parent=None,
    ):
        super().__init__(parent)
        self._lat    = latitude
        self._lon    = longitude
        self._zoom   = max(_raster_min_zoom, min(zoom, _raster_max_zoom))
        self._width  = width
        self._height = height

        self._view:          Optional[QWebEngineView] = None
        self._timeout_timer: Optional[QTimer]         = None
        self._running   = False
        self._cancelled = False
        self._retry_cnt = 0

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if _raster_tiles_dir is None:
            self.load_failed.emit(
                "래스터 타일 디렉터리가 설정되지 않았습니다.\n"
                "앱 시작 시 configure_raster_tiles()를 호출하세요."
            )
            return

        key = self._cache_key()
        if not _render_cache.is_stale(key):
            cached = _render_cache.get(key)
            if cached is not None:
                img = cached.toImage().convertToFormat(QImage.Format.Format_ARGB32)
                self.progress.emit(1, 1)
                self.map_loaded.emit(img)
                return

        self._start_render()


    def cancel(self) -> None:
        debug_print("RasterTileMapLoader.cancel()")
        self._cancelled = True
        self._running   = False
        if self._timeout_timer:
            self._timeout_timer.stop()
            self._timeout_timer = None
        self._cleanup_view()


    def isRunning(self) -> bool:
        return self._running

    # ── 내부 구현 ─────────────────────────────────────────────────────────────

    def _cache_key(self) -> str:
        return self._make_cache_key(
            self._lat, self._lon, self._zoom, self._width, self._height
        )


    def _start_render(self) -> None:
        if self._cancelled:
            return
        if self._running:
            warning_print("RasterTileMapLoader: 이미 실행 중 — 무시")
            return
        if not WEBENGINE_AVAILABLE:
            self.load_failed.emit("PySide6-WebEngine 미설치 (pip install PySide6-Addons)")
            return
        if _raster_tiles_dir is None or not _raster_tiles_dir.is_dir():
            self.load_failed.emit(f"타일 디렉터리가 없습니다: {_raster_tiles_dir}")
            return

        self._running = True

        key = self._cache_key()
        if not _render_cache.is_stale(key):
            pix = _render_cache.get(key)
            if pix is not None and not pix.isNull():
                img = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
                self._running = False
                self.progress.emit(1, 1)
                self.map_loaded.emit(img)
                return

        self.progress.emit(0, 1)
        self._start_webview()


    def _start_webview(self) -> None:
        port = _ensure_local_server()

        self._view = QWebEngineView()
        self._view.setPage(_SilentPage(_get_web_profile(), self._view))
        self._view.setFixedSize(self._width, self._height)

        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            _s   = self._view.settings()
            _atr = getattr(
                QWebEngineSettings.WebAttribute, "ScrollAnimatorEnabled", None
            )
            if _atr is not None:
                _s.setAttribute(_atr, False)
        except Exception:
            pass

        self._view.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnBottomHint
        )
        self._view.move(-self._width * 3, -self._height * 3)
        self._view.show()

        url = QUrl(
            f"http://127.0.0.1:{port}/map"
            f"?lat={self._lat}&lon={self._lon}&zoom={self._zoom}"
            f"&w={self._width}&h={self._height}"
        )
        self._view.titleChanged.connect(self._on_title_changed)
        self._view.load(url)

        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)
        self._timeout_timer.start(_RENDER_TIMEOUT_MS)

        debug_print(
            f"[RasterTiles] WebView 시작: "
            f"({self._lat:.4f},{self._lon:.4f}) z={self._zoom} "
            f"{self._width}×{self._height}"
        )


    def _on_title_changed(self, title: str) -> None:
        if self._cancelled:
            return
        if title == "MAPREADY":
            if self._timeout_timer:
                self._timeout_timer.stop()
                self._timeout_timer = None
            QTimer.singleShot(_GRAB_DELAY_MS, self._capture)
        elif title.startswith("MAPERR:"):
            if self._timeout_timer:
                self._timeout_timer.stop()
                self._timeout_timer = None
            msg = title[7:]
            error_print(f"[RasterTiles] 렌더링 오류: {msg}")
            self._cleanup_view()
            self._running = False
            self.load_failed.emit(f"지도 렌더링 오류: {msg[:80]}")


    def _capture(self) -> None:
        if self._cancelled or self._view is None:
            return

        try:
            pixmap = self._view.grab()
        except Exception as e:
            error_print(f"[RasterTiles] grab 실패: {e}")
            self._cleanup_view()
            self._running = False
            self.load_failed.emit(str(e)[:60])
            return

        if pixmap is None or pixmap.isNull():
            self._cleanup_view()
            self._running = False
            self.load_failed.emit("화면 캡처 실패 (null pixmap)")
            return

        dpr = pixmap.devicePixelRatio()
        if abs(dpr - 1.0) > 0.01:
            pixmap = pixmap.scaled(
                self._width, self._height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation, 
            )
            pixmap.setDevicePixelRatio(1.0)

        img     = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_attribution(painter, self._width, self._height)
        painter.end()

        pix_final = QPixmap.fromImage(img)

        if pix_final.width() < 4 or pix_final.height() < 4:
            self._retry_cnt += 1
            if self._retry_cnt <= 2:
                debug_print(f"[RasterTiles] 캡처 재시도 {self._retry_cnt}/2")
                QTimer.singleShot(500, self._capture)
                return
            self._retry_cnt = 0
            self._cleanup_view()
            self._running = False
            self.load_failed.emit("지도 캡처 실패 (빈 이미지)")
            return

        self._retry_cnt = 0
        _render_cache.put(self._cache_key(), pix_final)
        debug_print(f"[RasterTiles] 캐시 저장: {self._cache_key()}")

        self._cleanup_view()
        self._running = False
        self.progress.emit(1, 1)
        self.map_loaded.emit(img)


    def _on_timeout(self) -> None:
        warning_print(f"[RasterTiles] 렌더링 타임아웃 ({_RENDER_TIMEOUT_MS / 1000:.0f}s)")
        self._timeout_timer = None
        self._cleanup_view()
        self._running = False
        self.load_failed.emit(f"렌더링 타임아웃 ({_RENDER_TIMEOUT_MS / 1000:.0f}s)")


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

    # ── Attribution ────────────────────────────────────────────────────────────

    def _draw_attribution(self, painter: QPainter, w: int, h: int) -> None:
        from PySide6.QtCore import QRect
        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)
        text = "© OpenStreetMap contributors"
        fm   = painter.fontMetrics()
        pad  = 4
        bw = fm.horizontalAdvance(text) + pad * 2 + 4
        bh = fm.height() + pad * 2
        box = QRect(w - bw - pad, h - bh - pad, bw, bh)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawRoundedRect(box, 2, 2)
        painter.setPen(QColor(30, 30, 30))
        painter.drawText(
            box.adjusted(pad, pad, -pad, -pad),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )
        

# =============================================================================
# RasterTilePrefetcher
# =============================================================================

class RasterTilePrefetcher(QObject):
    """GPS 좌표 목록을 백그라운드에서 미리 렌더링."""

    START_DELAY_MS      = 500
    INTER_TASK_DELAY_MS = 100

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.queue:  list[tuple]                   = []
        self.loader: Optional[RasterTileMapLoader] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._process_next)


    def schedule(self, tasks: list[tuple]) -> None:
        """tasks: [(lat, lon, zoom, width, height), ...]"""
        self.cancel()
        filtered = [
            item for item in tasks
            if _render_cache.is_stale(
                RasterTileMapLoader._make_cache_key(*item[:5])
            )
        ]
        if not filtered:
            debug_print("[Prefetch] 모두 캐시됨 — 스킵")
            return
        self.queue = filtered
        debug_print(f"[Prefetch] {len(self.queue)}건 예약")
        self._timer.start(self.START_DELAY_MS)


    def cancel(self) -> None:
        self._timer.stop()
        self.queue.clear()
        if self.loader is not None:
            loader, self.loader = self.loader, None
            try:
                loader.map_loaded.disconnect()
                loader.load_failed.disconnect()
            except RuntimeError:
                pass
            loader.cancel()


    def _process_next(self) -> None:
        if not self.queue:
            debug_print("[Prefetch] 완료")
            return
        lat, lon, zoom, w, h = self.queue.pop(0)
        key = RasterTileMapLoader._make_cache_key(lat, lon, zoom, w, h)
        if not _render_cache.is_stale(key):
            self._timer.start(30)
            return
        debug_print(f"[Prefetch] 렌더링: {key}")
        self.loader = RasterTileMapLoader(lat, lon, zoom=zoom, width=w, height=h)
        self.loader.map_loaded.connect(self._on_done,    Qt.ConnectionType.QueuedConnection)
        self.loader.load_failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        self.loader.start()


    def _on_done(self, _img: QImage) -> None:
        self.loader = None
        if self.queue:
            self._timer.start(self.INTER_TASK_DELAY_MS)


    def _on_failed(self, error: str) -> None:
        debug_print(f"[Prefetch] 실패: {error}")
        self.loader = None
        if self.queue:
            self._timer.start(self.INTER_TASK_DELAY_MS)


prefetcher = RasterTilePrefetcher()


# =============================================================================
# Leaflet 에셋 다운로드 유틸리티
# =============================================================================

def download_assets() -> bool:
    """Leaflet JS / CSS를 resources/assets/에 다운로드. 이미 존재하면 건너뜀."""
    import urllib.request
    _ASSET_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "leaflet.js":  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
        "leaflet.css": "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
    }
    ok = True
    for fname, url in files.items():
        dest = _ASSET_DIR / fname
        if dest.exists():
            debug_print(f"[RasterTiles] assets/{fname} 존재 — 건너뜀")
            continue
        try:
            info_print(f"[RasterTiles] 다운로드 중: {fname} ...")
            urllib.request.urlretrieve(url, dest)
            info_print(f"[RasterTiles] 저장 완료: {dest} ({dest.stat().st_size // 1024} KB)")
        except Exception as e:
            error_print(f"[RasterTiles] 다운로드 실패 ({fname}): {e}")
            ok = False
    return ok


# =============================================================================
# 하위 호환 alias — 기존 코드 점진적 마이그레이션
# =============================================================================
PMTilesMapLoader  = RasterTileMapLoader 
PMTilesPrefetcher = RasterTilePrefetcher 
configure_pmtiles = configure_raster_tiles  # type: ignore[assignment]


def _release_shared_profile() -> None:
    """앱 종료 시 공유 WebEngine 프로파일 참조 해제."""
    global _web_profile
    if _web_profile is not None:
        _web_profile = None
        info_print("[RasterTiles] 공유 WebEngine 프로파일 해제")


# ── 에셋 즉시 다운로드 (HTTP 핸들러 스레드용) ─────────────────────────────────
_LEAFLET_ASSET_URLS: dict[str, str] = {
    "leaflet.js":  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
    "leaflet.css": "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
}


def _download_asset_sync(filename: str, dest: Path) -> None:
    """HTTP 핸들러 스레드에서 동기적으로 에셋 다운로드."""
    url = _LEAFLET_ASSET_URLS.get(filename)
    if url is None:
        return
    try:
        import urllib.request
        _ASSET_DIR.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        info_print(f"[RasterTiles] 에셋 즉시 다운로드: {filename} ...")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(dest)
        info_print(
            f"[RasterTiles] 에셋 저장 완료: {filename} "
            f"({dest.stat().st_size // 1024} KB)"
        )
    except Exception as e:
        error_print(f"[RasterTiles] 에셋 즉시 다운로드 실패 ({filename}): {e}")


def get_raster_tile_config() -> dict:
    """타일 설정 전체를 dict로 반환 (gps_map_window 등 외부 모듈 전용).
    _raster_tile_size, _raster_tms 직접 import는 값 복사 문제가 있으므로
    이 함수를 통해 항상 최신 값을 가져와야 함.
    """
    return {
        "tile_size": _raster_tile_size,
        "tms":       _raster_tms,
        "min_zoom":  _raster_min_zoom,
        "max_zoom":  _raster_max_zoom,
    }
