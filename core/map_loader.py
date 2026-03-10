# -*- coding: utf-8 -*-
# core/map_loader.py

"""
PMTiles 로컬 파일 기반 지도 이미지 로더.
"""

from __future__ import annotations
import warnings
import http.server
import json
import socket
import threading
import hashlib
import json
import struct
import enum
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, 
)

from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t 
from utils.paths import app_resources_dir

if TYPE_CHECKING:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage, QWebEngineProfile

WEBENGINE_AVAILABLE: bool
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView       # type: ignore[no-redef]
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage  # type: ignore[no-redef]
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False
    warning_print("PySide6-WebEngine 없음 — pip install PySide6-Addons")


_ASSET_DIR    = app_resources_dir() / "assets"
_RENDER_TIMEOUT_MS = 30_000
_GRAB_DELAY_MS     = 300
_PMTILES_MAGIC       = b"PMTiles" 
_PMTILES_HEADER_SIZE = 127  

class _GlyphSignals(QObject):
    download_done = Signal()   # 다운로드 완료 → overlay 재로드 트리거

glyph_signals = _GlyphSignals()

# 로컬 HTTP 서버
_http_server:      Optional[http.server.HTTPServer] = None
_http_server_port: int  = 0
_http_server_lock: threading.Lock = threading.Lock()

# ── 글리프 다운로드 상태 관리 ────────────────────────────────────────────

class _GlyphState(enum.Enum):
    IDLE        = "idle"        # 초기 상태
    SCHEDULED   = "scheduled"   # 타이머 대기 중
    DOWNLOADING = "downloading" # 백그라운드 다운로드 중
    DONE        = "done"        # 완료 (서빙 가능)
    FAILED      = "failed"      # 실패 (로컬 폴백 유지)

_glyph_state: _GlyphState = _GlyphState.IDLE
_glyph_state_lock: threading.Lock = threading.Lock()
_glyph_timer: Optional["QTimer"] = None   # GUI 스레드 소유

# ─────────────────────────────────────────────────────────────────────────────
# 모듈 수준 PMTiles 설정 상태
# ─────────────────────────────────────────────────────────────────────────────

_pmtiles_path:     Optional[Path] = None
_pmtiles_max_zoom: int             = 14    # configure_pmtiles()와 이름 통일
_SERVE_CHUNK_SIZE = 1 * 1024 * 1024

# ─────────────────────────────────────────────────────────────────────────────
# PMTiles 유틸리티
# ─────────────────────────────────────────────────────────────────────────────

def read_pmtiles_header(path: Path) -> Optional[dict]:
    """
    PMTiles v3 헤더(127 bytes)를 파싱한다.
    성공 시 dict, 실패 시 None.

    PMTiles v3 헤더 레이아웃:
      [0:7]     magic              = b"PMTiles"
      [7]       spec_version       = uint8 (should be 3)
      [8:96]    11 x uint64 LE     root_dir_offset ~ num_tile_contents
      [96:102]  6 x uint8          clustered ~ max_zoom
      [102:118] 4 x int32 LE       min_lon_e7 ~ max_lat_e7
      [118]     center_zoom        = uint8
      [119:127] 2 x int32 LE       center_lon_e7, center_lat_e7
    """
    try:
        with open(path, "rb") as f:
            data = f.read(_PMTILES_HEADER_SIZE)

        if len(data) < _PMTILES_HEADER_SIZE:
            warning_print(
                f"[PMTiles] 파일이 너무 작음: {len(data)} < {_PMTILES_HEADER_SIZE}"
            )
            return None

        # ── Magic 확인 ──────────────────────────────────────────────
        if data[0:7] != _PMTILES_MAGIC:
            warning_print(
                f"[PMTiles] 매직 불일치: {data[0:7]!r} (expected {_PMTILES_MAGIC!r})"
            )
            return None

        # ── Spec version ────────────────────────────────────────────
        spec_version = data[7]
        if spec_version != 3:
            warning_print(f"[PMTiles] 지원하지 않는 버전: {spec_version} (지원: 3)")
            return None

        # ── uint64 필드 11개 (offset 8~96, 88 bytes) ─────────────────
        # root_dir_offset, root_dir_length,
        # metadata_offset, metadata_length,
        # leaf_dirs_offset, leaf_dirs_length,
        # tile_data_offset, tile_data_length,
        # num_addressed_tiles, num_tile_entries, num_tile_contents
        (
            root_dir_offset, root_dir_length,
            metadata_offset, metadata_length,
            leaf_dirs_offset, leaf_dirs_length,
            tile_data_offset, tile_data_length,
            num_addressed_tiles, num_tile_entries, num_tile_contents,
        ) = struct.unpack_from("<11Q", data, 8)   # ← 9Q 아닌 11Q

        # ── uint8 필드 6개 (offset 96~102) ───────────────────────────
        (
            clustered,
            internal_compression,
            tile_compression,
            tile_type,
            min_zoom,
            max_zoom,
        ) = struct.unpack_from("<6B", data, 96)

        # ── int32 필드 4개 (offset 102~118) ──────────────────────────
        min_lon_e7, min_lat_e7, max_lon_e7, max_lat_e7 = \
            struct.unpack_from("<4i", data, 102)

        # ── center (offset 118~127) ───────────────────────────────────
        center_zoom = data[118]
        center_lon_e7, center_lat_e7 = struct.unpack_from("<2i", data, 119)

        return {
            "spec_version":         spec_version,
            "root_dir_offset":      root_dir_offset,
            "root_dir_length":      root_dir_length,
            "metadata_offset":      metadata_offset,
            "metadata_length":      metadata_length,
            "tile_data_offset":     tile_data_offset,
            "tile_data_length":     tile_data_length,
            "num_addressed_tiles":  num_addressed_tiles,
            "num_tile_entries":     num_tile_entries,
            "num_tile_contents":    num_tile_contents,
            "clustered":            bool(clustered),
            "internal_compression": internal_compression,
            "tile_compression":     tile_compression,
            "tile_type":            tile_type,
            "min_zoom":             min_zoom,
            "max_zoom":             max_zoom,
            "min_lon":              min_lon_e7 / 1e7,
            "min_lat":              min_lat_e7 / 1e7,
            "max_lon":              max_lon_e7 / 1e7,
            "max_lat":              max_lat_e7 / 1e7,
            "center_zoom":          center_zoom,
            "center_lon":           center_lon_e7 / 1e7,
            "center_lat":           center_lat_e7 / 1e7,
        }

    except struct.error as e:
        error_print(f"[PMTiles] struct 파싱 오류: {e}")
        return None
    except OSError as e:
        error_print(f"[PMTiles] 파일 읽기 오류: {e}")
        return None
    except Exception as e:
        error_print(f"[PMTiles] 헤더 파싱 예외: {e}")
        return None


def validate_pmtiles(path: Path) -> tuple[bool, str]:
    """PMTiles 파일 유효성 검사. Returns (True, "") or (False, 이유)."""
    if not path.exists():
        return False, t('map_loader.validate.not_exists')
    if not path.is_file():
        return False, t('map_loader.validate.not_file')
    if path.suffix.lower() != ".pmtiles":
        return False, t('map_loader.validate.wrong_ext', ext=path.suffix)
    size = path.stat().st_size
    if size < _PMTILES_HEADER_SIZE:
        return False, t('map_loader.validate.too_small',
                        size=size, min=_PMTILES_HEADER_SIZE)
    header = read_pmtiles_header(path)
    if header is None:
        return False, t('map_loader.validate.invalid_header')
    return True, ""


def configure_pmtiles(path: Optional[Path], max_zoom: int = 15) -> None:
    global _pmtiles_path, _pmtiles_max_zoom
    if path is None:
        _pmtiles_path = None
        _pmtiles_max_zoom = 15
        return

    _pmtiles_path = Path(path)
    header = read_pmtiles_header(_pmtiles_path)
    if header is not None:
        _pmtiles_max_zoom = header["max_zoom"]
    else:
        _pmtiles_max_zoom = max_zoom

    _render_cache.clear()

    _ensure_glyphs_local()

    info_print(f"[PMTiles] 설정: {_pmtiles_path.name}, max_zoom={_pmtiles_max_zoom}")


def configure_render_cache(memory_mb: int = 50) -> None:
    """앱 시작 시 렌더 메모리 캐시 크기 설정."""
    global _render_cache
    _render_cache = _MemRenderCache(max_mb=memory_mb)
    info_print(f"[PMTiles] 렌더 캐시 구성: 메모리={memory_mb}MB")


def _is_blank_image(img: QImage) -> bool:
    """
    MapLibre 렌더링이 미완료된 빈 이미지를 판별한다.

    판별 기준:
      1. 모든 샘플 픽셀이 동일 색 AND
      2. 그 색이 흰색/밝은회색(로딩 배경) 또는 완전 투명

    지도 스타일의 단색 배경(#f0ece4 육지, #a8d4f0 바다)은
    흰색 임계값(R,G,B > 240)에 해당하지 않으므로 정상으로 통과.
    """
    w, h = img.width(), img.height()
    if w < 4 or h < 4:
        return True

    # 9점 샘플링 (3x3 그리드)
    xs = [w // 4, w // 2, 3 * w // 4]
    ys = [h // 4, h // 2, 3 * h // 4]
    samples = [img.pixel(x, y) for x in xs for y in ys]

    unique = set(samples)

    # Case 1: 다양한 색 → 정상 렌더링
    if len(unique) > 2:
        return False

    # Case 2: 단색 또는 2색 → 로딩 배경색인지 확인
    for argb in unique:
        a = (argb >> 24) & 0xFF
        r = (argb >> 16) & 0xFF
        g = (argb >> 8)  & 0xFF
        b =  argb        & 0xFF

        # 완전 투명 → 빈 이미지
        if a < 10:
            return True

        # 흰색 계열 (R>240, G>240, B>240) → WebEngine 로딩 배경
        if r > 240 and g > 240 and b > 240:
            return True

        # 밝은 회색 계열 → WebEngine 초기 배경
        if r > 200 and g > 200 and b > 200 and abs(r - g) < 10 and abs(g - b) < 10:
            return True

    # 지도 스타일 배경색(#f0ece4, #a8d4f0 등) → 정상으로 간주
    return False


def schedule_font_download(delay_ms: int = 8000) -> None:
    """
    앱 시작 후 delay_ms 뒤 백그라운드에서 글리프 폰트를 다운로드한다.

    특징
    ────
    - 비차단(non-blocking): 호출 즉시 반환
    - 이미 다운로드됐으면 아무것도 하지 않음
    - 다운로드 중·완료·실패 상태에선 중복 실행 없음
    - 폰트 없이도 렌더링은 동작 (MapLibre 로컬 폴백 사용)

    호출 위치 예시
    ──────────────
      # 메인 윈도우 show() 직후
      schedule_font_download(delay_ms=8000)   # 8초 후 시작
    """
    global _glyph_state, _glyph_timer

    with _glyph_state_lock:
        # 이미 예약·실행·완료 상태면 아무것도 하지 않음
        if _glyph_state != _GlyphState.IDLE:
            return

        # 로컬에 이미 있으면 즉시 DONE
        glyph_dir = _ASSET_DIR / "glyph_cache" / "fonts"
        marker    = _ASSET_DIR / "glyph_cache" / ".fonts_downloaded"
        if marker.exists() and _verify_minimum_fonts(glyph_dir):
            _glyph_state = _GlyphState.DONE
            debug_print("[Glyph] 폰트 이미 존재 — 다운로드 생략")
            return

        _glyph_state = _GlyphState.SCHEDULED

    # ── GUI 스레드에서 QTimer 실행 ──────────────────────────────────
    _glyph_timer = QTimer()
    _glyph_timer.setSingleShot(True)
    _glyph_timer.timeout.connect(_on_glyph_timer_fired)
    _glyph_timer.start(delay_ms)

    info_print(f"[Glyph] 폰트 다운로드 {delay_ms / 1000:.0f}초 후 예약")


def _verify_minimum_fonts(glyph_dir: Path) -> bool:
    """최소 필수 PBF (Regular + Medium, 0-255) 존재 여부 확인."""
    required = [
        glyph_dir / "Noto Sans Regular" / "0-255.pbf",
        glyph_dir / "Noto Sans Medium"  / "0-255.pbf",
    ]
    return all(p.exists() for p in required)


def _on_glyph_timer_fired() -> None:
    """QTimer 만료 → 백그라운드 스레드 시작. GUI 스레드에서 호출됨."""
    global _glyph_state, _glyph_timer

    _glyph_timer = None 

    with _glyph_state_lock:
        if _glyph_state != _GlyphState.SCHEDULED:
            return  
        _glyph_state = _GlyphState.DOWNLOADING

    t = threading.Thread(
        target=_run_font_download,
        name="GlyphDownloader",
        daemon=True,   
    )
    t.start()
    info_print("[Glyph] 폰트 다운로드 시작 (백그라운드)")


def _run_font_download() -> None:
    """백그라운드 스레드에서 실행 — Qt 객체 접근 금지."""
    global _glyph_state

    from utils.download_fonts import download_protomaps_fonts

    glyph_dir = _ASSET_DIR / "glyph_cache" / "fonts"

    try:
        ok, msg = download_protomaps_fonts(glyph_dir)
    except Exception as e:
        ok, msg = False, str(e)

    new_state = _GlyphState.DONE if ok else _GlyphState.FAILED

    with _glyph_state_lock:
        _glyph_state = new_state

    if ok:
        _render_cache.clear()
        info_print("[Glyph] 폰트 다운로드 완료 — 다음 렌더링부터 적용됩니다")
        glyph_signals.download_done.emit()
    else:
        warning_print(f"[Glyph] 폰트 다운로드 실패: {msg}")
        warning_print("[Glyph] 지도 텍스트는 시스템 폰트로 대체됩니다")


_shared_profile: Optional["QWebEngineProfile"] = None


def _cleanup_webview_pool() -> None:
    """앱 종료 전 WebEngineView를 프로파일보다 먼저 파괴."""
    global _webview_pool
    for entry in _webview_pool:
        try:
            entry.view.stop()
            entry.view.deleteLater()
        except RuntimeError:
            pass
    _webview_pool.clear()
    debug_print("[PMTiles] WebView 풀 정리 완료")


def _release_shared_profile() -> None:
    global _shared_profile  
    _cleanup_webview_pool()
    if _shared_profile is not None:
        _shared_profile = None
        debug_print("[PMTiles] WebEngine 프로파일 해제")

# ─────────────────────────────────────────────────────────────────────────────
# _MemRenderCache — 메모리 전용 LRU
# ─────────────────────────────────────────────────────────────────────────────

def _pixmap_bytes(pixmap: QPixmap) -> int:
    """
    QPixmap의 실제 메모리 사용량(bytes)을 반환한다.

    QPixmap.width()/height()는 '논리 픽셀' 기준이다.
    HiDPI(Retina) 디스플레이에서 DPR=2.0이면
    실제 버퍼는 (width*2) * (height*2) * 4 bytes를 차지한다.
    """
    dpr = pixmap.devicePixelRatio()
    if abs(dpr - 1.0) < 0.01:
        return pixmap.width() * pixmap.height() * 4
    actual_w = int(pixmap.width() * dpr + 0.5) 
    actual_h = int(pixmap.height() * dpr + 0.5)
    return actual_w * actual_h * 4


# ── 모듈 수준: WebView 풀 ────────────────────────────────────────────
_WEBVIEW_POOL_MAX = 2  # 동시 렌더링 최대 수. 3 이상은 메모리 낭비

class _PooledWebView:
    """재사용 가능한 QWebEngineView 래퍼."""

    def __init__(self, width: int, height: int) -> None:
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.view = QWebEngineView()
        self.view.setPage(_SilentPage(_get_web_profile(), self.view))
        self.view.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnBottomHint
        )
        self.in_use = False
        self.resize(width, height)


    def resize(self, width: int, height: int) -> None:
        self.view.setFixedSize(width, height)
        self.view.move(-width * 3, -height * 3)
        self.view.show()


_webview_pool: list[_PooledWebView] = []
_webview_pool_lock = threading.Lock()


def _acquire_webview(width: int, height: int) -> Optional["_PooledWebView"]:
    with _webview_pool_lock:
        for entry in _webview_pool:
            if not entry.in_use:
                entry.in_use = True
                entry.resize(width, height)
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        entry.view.titleChanged.disconnect()
                except (RuntimeError, TypeError):
                    pass
                return entry
        if len(_webview_pool) < _WEBVIEW_POOL_MAX:
            entry = _PooledWebView(width, height)
            entry.in_use = True
            _webview_pool.append(entry)
            return entry
    return None


def _release_webview(entry: "_PooledWebView") -> None:
    """풀로 반납. about:blank 로드 완전 제거."""
    with _webview_pool_lock:
        entry.in_use = False


# ─── 모듈 수준 404 캐시 — CDN에 없는 Range 반복 요청 방지 ────────────
_glyph_404_cache: set[str] = set()

class _PMTilesHTTPHandler(http.server.BaseHTTPRequestHandler):

    _glyph_dir: Optional[Path] = None
    _GLYPH_CDN = "https://protomaps.github.io/basemaps-assets"

    _ASSET_TYPES = {
        ".js":  "application/javascript",
        ".css": "text/css",
        ".pbf": "application/x-protobuf",
    }


    def do_GET(self) -> None:
        from urllib.parse import urlparse
        base = urlparse(self.path).path

        if base == "/map":
            self._serve_map_html()
        elif base.startswith("/fonts/"):
            self._serve_glyph(base)
                
        elif base.lstrip("/") in ("maplibre-gl.min.js",
                                   "maplibre-gl.css",
                                   "pmtiles.js"):
            self._serve_asset(base.lstrip("/"))
        else:
            self._serve_pmtiles()


    def _serve_map_html(self) -> None:
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(self.path).query)
        lat  = float(params.get("lat",  ["0"])[0])
        lon  = float(params.get("lon",  ["0"])[0])
        zoom = int(  params.get("zoom", ["14"])[0])
        w    = int(  params.get("w",    ["400"])[0])
        h    = int(  params.get("h",    ["300"])[0])
        sid  = params.get("sid", ["unknown"])[0]
        html = _generate_map_html(lat, lon, zoom, w, h, _http_server_port, sid)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


    def _serve_asset(self, filename: str) -> None:
        path = _ASSET_DIR / filename
        if not path.exists():
            self.send_response(404); self.end_headers(); return
        try:
            data  = path.read_bytes()
            ctype = self._ASSET_TYPES.get(path.suffix, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type",   ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control",  "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            error_print(f"[asset] {filename}: {e}")
            self.send_response(500); self.end_headers()


    def _serve_glyph(self, url_path: str) -> None:
        from urllib.parse import unquote
        decoded = unquote(url_path)
        rel = decoded.lstrip("/")   # "fonts/Noto Sans Bold/0-255.pbf"

        if self._glyph_dir is None:
            self.send_response(503); self.end_headers(); return

        local = self._glyph_dir / rel
        if not local.exists():
            if rel not in _glyph_404_cache:
                _glyph_404_cache.add(rel)
                debug_print(f"[glyph] 로컬 없음 (CDN 시도 안 함): {rel}")
            self.send_response(404); self.end_headers(); return

        try:
            data = local.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            error_print(f"[glyph] 서빙 오류: {e}")
            self.send_response(500); self.end_headers()


    def _serve_pmtiles(self) -> None:
        if _pmtiles_path is None or not _pmtiles_path.exists():
            self.send_response(404); self.end_headers(); return
        file_size = _pmtiles_path.stat().st_size
        range_hdr = self.headers.get("Range", "")
        try:
            if range_hdr.startswith("bytes="):
                parts = range_hdr[6:].split("-")
                start = int(parts[0])
                end = int(parts[1]) if parts[1] else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(_pmtiles_path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(_SERVE_CHUNK_SIZE, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(file_size))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(_pmtiles_path, "rb") as f:
                    while chunk := f.read(_SERVE_CHUNK_SIZE):
                        self.wfile.write(chunk)
        except (
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError, 
            TimeoutError,
        ):
            pass  
        except OSError as e:
            # Windows 소켓 오류는 OSError로 올라옴 (errno 10053/10054/10061)
            import errno as _errno
            _SILENT_WINERRORS = {10053, 10054, 10061}  # WSAECONNABORTED, WSAECONNRESET, WSAECONNREFUSED
            if hasattr(e, 'winerror') and e.winerror in _SILENT_WINERRORS:
                pass   
            else:
                error_print(f"[PMTiles HTTP] OSError({e.errno}): {e.strerror or e}")
        except Exception as e:
            msg = str(e)
            if msg: 
                error_print(f"[PMTiles HTTP] {msg}")


    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Headers", "Range")
        self.end_headers()


    def _serve_empty_pbf(self) -> None:
        """
        유효한 빈 PBF 응답.
        MapLibre는 빈 glyph range를 '해당 범위 글리프 없음'으로 처리하며
        렌더링을 blocking하지 않는다. 404와 달리 즉시 완료로 처리됨.
        """
        self.send_response(200)    
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()


    def log_message(self, format, *args) -> None:
        pass


# ─────────────────────────────────────────────────────────────────
# 렌더 전 글리프 파일 존재 보장
# ─────────────────────────────────────────────────────────────────

_GLYPH_FONTS = ("Noto Sans Regular", "Noto Sans Medium")
_GLYPH_RANGES = ("0-255",)  # name:en 은 기본 라틴 범위만 필요

def _ensure_glyphs_local() -> None:
    with _glyph_state_lock:
        state = _glyph_state

    if state == _GlyphState.DONE:
        return  


def _ensure_local_server() -> int:
    global _http_server, _http_server_port

    with _http_server_lock:
        if _http_server is not None:
            return _http_server_port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        _PMTilesHTTPHandler._glyph_dir = _ASSET_DIR / "glyph_cache"
        _PMTilesHTTPHandler._glyph_dir.mkdir(parents=True, exist_ok=True)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _PMTilesHTTPHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        _http_server      = server
        _http_server_port = port
        info_print(f"[PMTiles] 로컬 서버 시작: http://127.0.0.1:{port}/")
        return port


_web_profile: Optional["QWebEngineProfile"] = None
_web_profile_lock = threading.Lock()

def _get_web_profile() -> "QWebEngineProfile":
    """
    모든 PMTilesMapLoader 인스턴스가 공유하는 QWebEngineProfile.
    글리프(폰트) HTTP 캐시를 디스크에 영속화 → 재렌더링 시 즉시 사용.
    """
    global _web_profile
    if _web_profile is not None:
        return _web_profile
    with _web_profile_lock:
        if _web_profile is not None:
            return _web_profile
        from PySide6.QtWebEngineCore import QWebEngineProfile
        profile = QWebEngineProfile("pmtiles_renderer")
        webcache_dir = _ASSET_DIR / "webcache"
        webcache_dir.mkdir(parents=True, exist_ok=True)
        profile.setCachePath(str(webcache_dir))
        profile.setPersistentStoragePath(str(webcache_dir / "storage"))
        profile.setHttpCacheType(
            QWebEngineProfile.HttpCacheType.DiskHttpCache
        )
        profile.setHttpCacheMaximumSize(50 * 1024 * 1024)  # 50MB
        _web_profile = profile
        info_print("[PMTiles] WebEngine 공유 프로파일 생성 (글리프 캐시 활성)")
        return profile
        

class _MemRenderCache:
    """
    메모리 전용 LRU 렌더 캐시.

    PMTiles 로컬 파일은 재렌더링 비용이 낮아 디스크 캐시가 불필요하다.
    HybridCache의 SQLite/파일I/O/락 복잡도를 완전히 제거한다.
    """

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

        if size > self._max_bytes:
            warning_print(
                f"[RenderCache] 항목 크기({size//1024}KB)가 "
                f"캐시 한도({self._max_bytes//1024//1024}MB)를 초과 — 저장 생략"
            )
            return

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


# 모듈 수준 렌더 캐시 싱글턴 (기본 50MB)
_render_cache = _MemRenderCache(max_mb=50)

# ── Protomaps MVT 스키마용 인라인 스타일 레이어 ─────────────────────
# line-cap / line-join 은 반드시 layout 에 위치해야 함 (paint 불가)
_PMTILES_STYLE_LAYERS: list = [

    # ── 배경 = 바다색 ────────────────────────────────────────────────
    {"id": "bg",
     "type": "background",
     "paint": {"background-color": "#a8d4f0"}},

    # ── 육지 (earth 레이어로 육지 면 채움) ──────────────────────────
    {"id": "earth",
     "type": "fill", "source": "s", "source-layer": "earth",
     "paint": {"fill-color": "#f0ece4"}},

    # ── 수역 ────────────────────────────────────────────────────────
    {"id": "water",
     "type": "fill", "source": "s", "source-layer": "water",
     "paint": {"fill-color": "#a8d4f0"}},

    # ── 토지피복 (저줌 녹지) ─────────────────────────────────────────
    {"id": "landcover",
     "type": "fill", "source": "s", "source-layer": "landcover",
     "paint": {"fill-color": "#d4e8c4", "fill-opacity": 0.6}},

    # ── 토지이용 ────────────────────────────────────────────────────
    {"id": "landuse_green",
     "type": "fill", "source": "s", "source-layer": "landuse",
     "filter": ["in", "kind",
                "park", "forest", "grass", "meadow",
                "recreation_ground", "village_green", "playground"],
     "paint": {"fill-color": "#c8e8b0"}},
    {"id": "landuse_built",
     "type": "fill", "source": "s", "source-layer": "landuse",
     "filter": ["in", "kind",
                "residential", "commercial", "industrial",
                "school", "hospital", "military"],
     "paint": {"fill-color": "#e8e4d8", "fill-opacity": 0.6}},

    # ── 도로 케이싱 (외곽선) ─────────────────────────────────────────
    {"id": "road_casing_major",
     "type": "line", "source": "s", "source-layer": "roads",
     "filter": ["in", "kind", "motorway", "trunk", "primary"],
     "layout": {"line-cap": "round", "line-join": "round"},
     "paint": {
         "line-color": "#c89820",
         "line-width": ["interpolate", ["linear"], ["zoom"],
                        8, 2.5, 14, 9, 18, 20],
     }},
    {"id": "road_casing_secondary",
     "type": "line", "source": "s", "source-layer": "roads",
     "filter": ["in", "kind", "secondary", "tertiary"],
     "layout": {"line-cap": "round", "line-join": "round"},
     "paint": {
         "line-color": "#b8a878",
         "line-width": ["interpolate", ["linear"], ["zoom"],
                        10, 2, 14, 6, 18, 14],
     }},

    # ── 도로 채움 ────────────────────────────────────────────────────
    {"id": "road_minor",
     "type": "line", "source": "s", "source-layer": "roads",
     "filter": ["in", "kind",
                "minor_road", "service", "path",
                "track", "pedestrian", "other", "unknown"],
     "layout": {"line-cap": "round", "line-join": "round"},
     "paint": {
         "line-color": "#f0ece8",
         "line-width": ["interpolate", ["linear"], ["zoom"],
                        13, 0.8, 15, 2, 18, 7],
     }},
    {"id": "road_secondary",
     "type": "line", "source": "s", "source-layer": "roads",
     "filter": ["in", "kind", "secondary", "tertiary"],
     "layout": {"line-cap": "round", "line-join": "round"},
     "paint": {
         "line-color": "#ffffff",
         "line-width": ["interpolate", ["linear"], ["zoom"],
                        10, 1, 14, 4, 18, 12],
     }},
    {"id": "road_major",
     "type": "line", "source": "s", "source-layer": "roads",
     "filter": ["in", "kind", "motorway", "trunk", "primary"],
     "layout": {"line-cap": "round", "line-join": "round"},
     "paint": {
         "line-color": "#ffd060",
         "line-width": ["interpolate", ["linear"], ["zoom"],
                        8, 1.5, 14, 6, 18, 16],
     }},

    # ── 건물 ─────────────────────────────────────────────────────────
    {"id": "buildings",
     "type": "fill", "source": "s", "source-layer": "buildings",
     "minzoom": 14,
     "paint": {"fill-color": "#ddd8d0", "fill-outline-color": "#c0b8b0"}},

    # ── 국경 ─────────────────────────────────────────────────────────
    # Protomaps boundaries.kind = "country" | "region" | "county"
    # admin_level 필드 없음 — kind 기준으로 필터
    {"id": "boundary_country",
     "type": "line", "source": "s", "source-layer": "boundaries",
     "filter": ["==", "kind", "country"],
     "paint": {
         "line-color": "#8888aa",
         "line-width": 1.5,
         "line-dasharray": [4.0, 3.0],
     }},
    {"id": "boundary_region",
     "type": "line", "source": "s", "source-layer": "boundaries",
     "filter": ["==", "kind", "region"],
     "minzoom": 5,
     "paint": {
         "line-color": "#aaaacc",
         "line-width": 0.8,
         "line-dasharray": [3.0, 3.0],
     }},

    # ── 도로명 (줌 13~) ──────────────────────────────────────────────
    {"id": "road_label", "type": "symbol", "source": "s", "source-layer": "roads",
     "minzoom": 13,
     "filter": ["in", "kind", "motorway", "trunk", "primary", "secondary", "tertiary"],
     "layout": {
         "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
         "text-font": ["Noto Sans Regular"],
         "text-size": ["interpolate", ["linear"], ["zoom"], 13, 10, 17, 13],
         "symbol-placement": "line",
         "text-max-angle": 30,
         "text-padding": 2,
     },
     "paint": {
         "text-color": "#333333",
         "text-halo-color": "rgba(255,255,255,0.9)",
         "text-halo-width": 1.5,
     }},
    # ── POI (줌 15~) ────────────────────────────────────────────────
    {"id": "poi_label", "type": "symbol", "source": "s", "source-layer": "pois",
     "minzoom": 15,
     "layout": {
         "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
         "text-font": ["Noto Sans Regular"],
         "text-size": 10,
         "text-anchor": "top",
         "text-offset": [0, 0.5],
     },
     "paint": {
         "text-color": "#555555",
         "text-halo-color": "rgba(255,255,255,0.9)",
         "text-halo-width": 1.2,
     }},
    # ── 국가 이름 (줌 2~) ───────────────────────────────────────────
    # Protomaps v4: places.kind = "country"
    {"id": "place_country", "type": "symbol", "source": "s", "source-layer": "places",
     "filter": ["==", "kind", "country"],
     "layout": {
         "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
         "text-font": ["Noto Sans Medium"],
         "text-size": ["interpolate", ["linear"], ["zoom"], 2, 10, 5, 12, 8, 14],
         "text-transform": "uppercase",
         "text-letter-spacing": 0.1,
     },
     "paint": {
         "text-color": "#223355",
         "text-halo-color": "rgba(255,255,255,0.95)",
         "text-halo-width": 2.0,
     }},
    # ── 광역시도·주·도 (줌 5~) ──────────────────────────────────────
    # Protomaps v4: places.kind = "region"
    {"id": "place_region", "type": "symbol", "source": "s", "source-layer": "places",
     "minzoom": 5,
     "filter": ["==", "kind", "region"],
     "layout": {
         "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
         "text-font": ["Noto Sans Regular"],
         "text-size": ["interpolate", ["linear"], ["zoom"], 5, 10, 8, 12, 12, 14],
         "text-transform": "uppercase",
         "text-letter-spacing": 0.05,
     },
     "paint": {
         "text-color": "#334466",
         "text-halo-color": "rgba(255,255,255,0.9)",
         "text-halo-width": 1.5,
     }},
    # ── 군·구 (줌 8~) ────────────────────────────────────────────────
    # Protomaps v4: places.kind = "county" (신규 추가)
    {"id": "place_county", "type": "symbol", "source": "s", "source-layer": "places",
     "minzoom": 8,
     "filter": ["==", "kind", "county"],
     "layout": {
         "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
         "text-font": ["Noto Sans Regular"],
         "text-size": ["interpolate", ["linear"], ["zoom"], 8, 9, 12, 11],
     },
     "paint": {
         "text-color": "#445566",
         "text-halo-color": "rgba(255,255,255,0.9)",
         "text-halo-width": 1.5,
     }},
    # ── 도시·읍·마을 (줌 4~) ─────────────────────────────────────────
    # Protomaps v4 에서 도시·읍·마을은 places.kind = "locality" 로 저장됨
    # kind_detail: city | town | village | hamlet | isolated_dwelling
    # 기존 filter ["in","kind","city","town","county"] 는 0건 매칭이었음!
    {"id": "place_locality", "type": "symbol", "source": "s", "source-layer": "places",
     "minzoom": 4,
     "filter": ["==", "kind", "locality"],
     "layout": {
         "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
         "text-font": ["Noto Sans Medium"],
         "text-size": [
             "interpolate", ["linear"], ["zoom"],
             4,  ["match", ["get", "kind_detail"], "city", 11, 9],
             8,  ["match", ["get", "kind_detail"], "city", 14, "town", 12, 10],
             12, ["match", ["get", "kind_detail"], "city", 16, "town", 13, 11],
         ],
     },
     "paint": {
         "text-color": "#111111",
         "text-halo-color": "rgba(255,255,255,0.95)",
         "text-halo-width": 2.0,
     }},
    # ── 구·suburb·동네 (줌 11~) ──────────────────────────────────────
    # hamlet/village 는 kind_detail 값 → kind 에 없음
    # Protomaps v4 kind: suburb | neighbourhood | quarter | borough
    {"id": "place_small", "type": "symbol", "source": "s", "source-layer": "places",
     "minzoom": 11,
     "filter": ["in", "kind", "suburb", "neighbourhood", "quarter", "borough"],
     "layout": {
         "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
         "text-font": ["Noto Sans Regular"],
         "text-size": ["interpolate", ["linear"], ["zoom"], 11, 10, 14, 12],
     },
     "paint": {
         "text-color": "#555555",
         "text-halo-color": "rgba(255,255,255,0.9)",
         "text-halo-width": 1.5,
     }},
]

# 모듈 수준 캐시 — 레이어는 정적이므로 한 번만 직렬화
_STYLE_LAYERS_JSON_CACHE: Optional[str] = None

def _get_style_layers_json() -> str:
    global _STYLE_LAYERS_JSON_CACHE
    if _STYLE_LAYERS_JSON_CACHE is None:
        _STYLE_LAYERS_JSON_CACHE = json.dumps(
            _PMTILES_STYLE_LAYERS, ensure_ascii=False, separators=(",", ":")
        )
    return _STYLE_LAYERS_JSON_CACHE


def _generate_map_html(lat: float, lon: float, zoom: int,
                       w: int, h: int, port: int,
                       session_id: str) -> str: 
    layers_json = _get_style_layers_json()

    # URL 포함 부분만 포맷팅 (매번 바뀌는 최소한의 부분)
    glyphs_url = f"http://127.0.0.1:{port}/fonts/{{fontstack}}/{{range}}.pbf"
    tiles_url  = f"pmtiles://http://127.0.0.1:{port}/file.pmtiles"

    style_json = (
        '{"version":8,'
        f'"glyphs":"{glyphs_url}",'
        f'"sources":{{"s":{{"type":"vector","url":"{tiles_url}"}}}},'
        f'"layers":{layers_json}}}'
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ margin: 0; padding: 0; overflow: hidden; }}
  #map {{ width: {w}px; height: {h}px; }}
</style>
<link rel="stylesheet" href="http://127.0.0.1:{port}/maplibre-gl.css">
<script src="http://127.0.0.1:{port}/pmtiles.js"></script>
<script src="http://127.0.0.1:{port}/maplibre-gl.min.js"></script>
</head>
<body>
<div id="map"></div>
<script>
  let protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);

  let map = new maplibregl.Map({{
    container: 'map',
    style: {style_json},
    center: [{lon}, {lat}],
    zoom: {zoom},
    interactive: false,
    attributionControl: false,
    localIdeographFontFamily:
      "'Malgun Gothic', 'Nanum Gothic', 'Apple SD Gothic Neo', sans-serif"
  }});

    // 타이틀에 세션 ID 포함 → Python에서 출처 검증 가능
    map.once('idle', function() {{
        document.title = 'MAPREADY:{session_id}';
    }});
    map.on('error', function(e) {{
        let msg = e.error && e.error.message ? e.error.message : 'unknown';
        document.title = 'MAPERR:{session_id}:' + msg;
    }});
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# _SilentPage
# ─────────────────────────────────────────────────────────────────────────────

class _SilentPage(QWebEnginePage):
    _SUPPRESS = (
        "Expected value to be of type number",
        "Unable to load glyph range",  
        "Failed to fetch",      
        "unknown property",
    )

    def javaScriptConsoleMessage(self, level, message, line, source):
        if any(s in message for s in self._SUPPRESS):
            return
        debug_print(f"[JS] {message} (line {line})")


# ─────────────────────────────────────────────────────────────────────────────
# PMTilesMapLoader
# ─────────────────────────────────────────────────────────────────────────────

class PMTilesMapLoader(QObject):
    """
    로컬 PMTiles 파일 기반 지도 이미지 로더.
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
    def is_cached(cls, lat: float, lon: float, zoom: int,
                  width: int, height: int) -> bool:
        return not _render_cache.is_stale(
            cls._make_cache_key(lat, lon, zoom, width, height)
        )


    @classmethod
    def get_cached_pixmap(cls, lat, lon, zoom, width, height):
        key = cls._make_cache_key(lat, lon, zoom, width, height)
        return _render_cache.get(key) 


    @staticmethod
    def _make_cache_key(lat: float, lon: float, zoom: int,
                        width: int, height: int) -> str:
        """
        PMTiles 파일 경로 해시 포함 캐시 키.
        파일이 달라지면 같은 좌표라도 반드시 다른 키가 생성된다.
        """
        path_hash = (
            hashlib.md5(str(_pmtiles_path).encode()).hexdigest()[:8]
            if _pmtiles_path is not None else "no_pmtiles"
        )
        return f"pmt_{path_hash}_{lat:.4f}_{lon:.4f}_{zoom}_{width}x{height}"

    # ── 초기화 ────────────────────────────────────────────────────────────────

    def __init__(self, latitude, longitude,
                zoom: int = 15, 
                width=275, height=200, parent=None):
        super().__init__(parent)
        self._lat    = latitude
        self._lon    = longitude
        self._zoom = min(zoom, _pmtiles_max_zoom)
        self._width  = width
        self._height = height

        self._view:          Optional[QWebEngineView] = None
        self._timeout_timer: Optional[QTimer]         = None
        self._running   = False
        self._cancelled = False
        self._retry_count = 0

        self._pooled_view: Optional[_PooledWebView] = None  
        self._view: Optional[QWebEngineView] = None         
        self._session_id: str = "" 

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if _pmtiles_path is None:
            self.load_failed.emit(t('map_loader.error.no_pmtiles_set'))
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
        debug_print("PMTilesMapLoader.cancel()")
        self._cancelled = True
        self._running   = False
        if self._timeout_timer is not None:
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
            warning_print("PMTilesMapLoader: 이미 실행 중 — 무시")
            return
        if not WEBENGINE_AVAILABLE:
            self.load_failed.emit(t('map_loader.error.no_webengine'))
            return

        if _pmtiles_path is None:
            self.load_failed.emit(t('map_loader.error.no_pmtiles_set'))
            self._running = False
            return

        valid, reason = validate_pmtiles(_pmtiles_path)
        if not valid:
            self.load_failed.emit(t('map_loader.error.pmtiles_file_error', reason=reason))
            return

        self._running = True

        key = self._cache_key()
        if not _render_cache.is_stale(key):
            pix = _render_cache.get(key)
            if pix is not None and not pix.isNull():
                debug_print(f"[PMTiles] 캐시 HIT: {key}")
                img = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
                self._running = False
                self.progress.emit(1, 1)
                self.map_loaded.emit(img)
                return

        self.progress.emit(0, 1)
        self._start_webview()


    def _start_webview(self) -> None:
        import uuid
        self._session_id = uuid.uuid4().hex[:8] 
        port = _ensure_local_server()

        self._pooled_view = _acquire_webview(self._width, self._height)
        if self._pooled_view is None:
            # 모든 View가 사용 중 → 짧게 대기 후 재시도
            QTimer.singleShot(200, self._start_webview)
            return
        self._view = self._pooled_view.view

        url = QUrl(
            f"http://127.0.0.1:{port}/map"
            f"?lat={self._lat}&lon={self._lon}&zoom={self._zoom}"
            f"&w={self._width}&h={self._height}"
            f"&sid={self._session_id}" 
        )
        self._view.titleChanged.connect(self._on_title_changed)
        self._view.load(url)

        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)
        self._timeout_timer.start(_RENDER_TIMEOUT_MS)


    def _check_assets(self) -> bool:
        # "pmtiles.min.js" → "pmtiles.js" 로 통일 (_build_html 과 일치)
        required = ["maplibre-gl.min.js", "maplibre-gl.css", "pmtiles.js"]
        missing  = [f for f in required if not (_ASSET_DIR / f).exists()]
        if missing:
            error_print(f"[PMTiles] 누락된 assets: {missing}")
            return False
        return True


    @staticmethod
    def _build_style_layers() -> list:
        """
        OpenMapTiles 스키마 호환 다크 테마 레이어.
        완전 오프라인을 위해 글리프(폰트) 의존 symbol 레이어 제외.
        """
        return [
            # 배경
            {"id": "background", "type": "background",
             "paint": {"background-color": "#1a1a2e"}},
            # 수역
            {"id": "water", "type": "fill", "source": "pmtiles-src",
             "source-layer": "water",
             "paint": {"fill-color": "#0d2137"}},
            # 토지 피복 (녹지)
            {"id": "landcover", "type": "fill", "source": "pmtiles-src",
             "source-layer": "landcover",
             "paint": {"fill-color": "#1a3020", "fill-opacity": 0.7}},
            # 토지 이용
            {"id": "landuse", "type": "fill", "source": "pmtiles-src",
             "source-layer": "landuse",
             "paint": {"fill-color": "#0f2d45", "fill-opacity": 0.6}},
            # 공원
            {"id": "park", "type": "fill", "source": "pmtiles-src",
             "source-layer": "park",
             "paint": {"fill-color": "#1a3828", "fill-opacity": 0.8}},
            # 건물
            {"id": "building", "type": "fill", "source": "pmtiles-src",
             "source-layer": "building",
             "paint": {
                 "fill-color": "#252540",
                 "fill-outline-color": "#333360",
             }},
            # 도로 케이싱 (테두리)
            {"id": "road-casing", "type": "line", "source": "pmtiles-src",
             "source-layer": "transportation",
             "filter": ["in", ["get", "class"],
                        ["literal", ["motorway", "trunk", "primary", "secondary"]]],
             "paint": {"line-color": "#0a0a1e", "line-width": 4,
                       "line-cap": "round", "line-join": "round"}},
            # 도로
            {"id": "road", "type": "line", "source": "pmtiles-src",
             "source-layer": "transportation",
             "paint": {
                 "line-color": [
                     "match", ["get", "class"],
                     "motorway", "#c8780a",
                     "trunk",    "#c8780a",
                     "primary",  "#3a6aaf",
                     "secondary","#2d5490",
                     "#243a5a"
                 ],
                 "line-width": [
                     "match", ["get", "class"],
                     "motorway",  3.5,
                     "trunk",     3.0,
                     "primary",   2.0,
                     "secondary", 1.5,
                     0.8
                 ],
                 "line-cap":  "round",
                 "line-join": "round",
             }},
        ]


    def _on_title_changed(self, title: str) -> None:
        if self._cancelled:
            return

        if title == f"MAPREADY:{self._session_id}":
            if self._timeout_timer:
                self._timeout_timer.stop()
                self._timeout_timer = None
            QTimer.singleShot(_GRAB_DELAY_MS, self._capture)

        elif title.startswith(f"MAPERR:{self._session_id}:"):
            if self._timeout_timer:
                self._timeout_timer.stop()
                self._timeout_timer = None
            msg = title[len(f"MAPERR:{self._session_id}:"):]
            error_print(f"[PMTiles] MapLibre 오류: {msg}")
            self._cleanup_view()
            self._running = False
            self.load_failed.emit(t('map_loader.error.render_error', msg=msg[:80]))

        elif title in ("MAPREADY",) or title.startswith("MAPERR:"):
            debug_print(f"[PMTiles] stale 이벤트 무시 (이전 페이지): {title[:60]}")


    def _capture(self) -> None:
        if self._cancelled or self._view is None:
            return
        try:
            pixmap = self._view.grab()
        except Exception as e:
            error_print(f"[PMTiles] grab 실패: {e}")
            self._cleanup_view()
            self._running = False
            self.load_failed.emit(str(e)[:60])  
            return

        if pixmap is None or pixmap.isNull():
            warning_print("[PMTiles] grab null pixmap")
            self._cleanup_view()
            self._running = False
            self.load_failed.emit(t('map_loader.error.capture_null'))
            return

        # DPR 정규화
        dpr = pixmap.devicePixelRatio()
        debug_print(f"[PMTiles] grab {pixmap.width()}x{pixmap.height()} DPR={dpr:.2f}")
        if abs(dpr - 1.0) > 0.01:
            pixmap = pixmap.scaled(
                self._width, self._height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap.setDevicePixelRatio(1.0)

        # 마커 + attribution 오버레이
        img     = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_marker(painter, self._width // 2, self._height // 2)
        self._draw_attribution(painter, self._width, self._height)
        painter.end()

        if _is_blank_image(img):
            self._retry_count += 1
            if self._retry_count <= 3:
                delay = 600 + self._retry_count * 400 
                debug_print(f"[PMTiles] 빈 이미지 재시도 {self._retry_count}/3 ({delay}ms 후)")
                QTimer.singleShot(delay, self._capture)
            else:
                warning_print("[PMTiles] 재시도 초과 — 렌더링 실패")
                self._retry_count = 0
                self._cleanup_view()
                self._running = False
                self.load_failed.emit(t('map_loader.error.capture_empty'))
            return

        self._retry_count = 0
        pix_final = QPixmap.fromImage(img)
        _render_cache.put(self._cache_key(), pix_final)
        debug_print(f"[PMTiles] 캐시 저장: {self._cache_key()}")
        self._cleanup_view()
        self._running = False
        self.progress.emit(1, 1)
        self.map_loaded.emit(img)


    def _on_timeout(self) -> None:
        sec = int(_RENDER_TIMEOUT_MS / 1000)
        warning_print(f"[PMTiles] 렌더링 타임아웃 ({sec}s)")
        self._timeout_timer = None
        self._cleanup_view()
        self._running = False
        self.load_failed.emit(t('map_loader.error.render_timeout', sec=sec))


    def _cleanup_view(self) -> None:
        if self._view is not None:
            self._view = None

        if self._pooled_view is not None:
            _release_webview(self._pooled_view)
            self._pooled_view = None

    # ── 마커 / Attribution ────────────────────────────────────────────────────

    def _draw_marker(self, painter: QPainter, x: int, y: int) -> None:
        # 그림자
        shadow = QRadialGradient(x, y + 18, 10)
        shadow.setColorAt(0, QColor(0, 0, 0, 80))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(shadow))
        painter.drawEllipse(x - 9, y + 15, 18, 6)

        # 핀 몸체
        path = QPainterPath()
        path.addEllipse(x - 9, y - 16, 18, 18)
        path.moveTo(x - 6, y + 1)
        path.lineTo(x,     y + 14)
        path.lineTo(x + 6, y + 1)
        path.closeSubpath()
        grad = QRadialGradient(x - 3, y - 10, 12)
        grad.setColorAt(0, QColor(255, 80,  80))
        grad.setColorAt(1, QColor(200, 20,  20))
        painter.setPen(QPen(QColor(160, 0, 0), 1.5))
        painter.setBrush(QBrush(grad))
        painter.drawPath(path)

        # 반사광
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 180))
        painter.drawEllipse(x - 5, y - 14, 8, 8)


    @staticmethod
    def _draw_attribution(painter: QPainter, w: int, h: int) -> None:
        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)
        text = "© OpenStreetMap contributors"
        fm = painter.fontMetrics()
        pad = 5

        tw = fm.horizontalAdvance(text)
        th = fm.height()
        bw = tw + pad * 2 + 2 
        bh = th + pad * 2

        x = w - bw - pad
        y = h - bh - pad

        from PySide6.QtCore import QRect
        box = QRect(x, y, bw, bh)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 210))
        painter.drawRoundedRect(box, 2, 2)

        painter.setPen(QColor(30, 30, 30))
        painter.drawText(
            box.adjusted(pad, pad, -pad, -pad),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PMTilesPrefetcher
# ─────────────────────────────────────────────────────────────────────────────

class PMTilesPrefetcher(QObject):
    """
    GPS 좌표 목록을 백그라운드에서 미리 렌더링하는 프리페처.
    PMTiles는 로컬 파일이므로 OFM 대비 딜레이를 대폭 단축.
    """

    START_DELAY_MS      = 1500   # OFM 2500ms → 800ms
    INTER_TASK_DELAY_MS = 150   # OFM 800ms  → 150ms


    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.queue:  list[tuple]               = []
        self.loader: Optional[PMTilesMapLoader] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._process_next)


    def schedule(self, tasks: list[tuple]) -> None:
        """tasks: [(lat, lon, zoom, width, height), ...]"""
        self.cancel()
        filtered = [
            item for item in tasks
            if _render_cache.is_stale(PMTilesMapLoader._make_cache_key(*item[:5]))
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
        key = PMTilesMapLoader._make_cache_key(lat, lon, zoom, w, h)
        if not _render_cache.is_stale(key):
            self._timer.start(30)
            return
        debug_print(f"[Prefetch] 렌더링: {key}")
        self.loader = PMTilesMapLoader(lat, lon, zoom=zoom, width=w, height=h)
        self.loader.map_loaded.connect(self._on_done,   Qt.ConnectionType.QueuedConnection)
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


prefetcher = PMTilesPrefetcher()

def download_assets() -> bool:
    """MapLibre GL JS / CSS / pmtiles.js + 기본 글리프 다운로드."""
    import urllib.request
    from utils.paths import ensure_dir
    ensure_dir(_ASSET_DIR)

    files = {
        "maplibre-gl.min.js": "https://unpkg.com/maplibre-gl/dist/maplibre-gl.js",
        "maplibre-gl.css":    "https://unpkg.com/maplibre-gl/dist/maplibre-gl.css",
        "pmtiles.js":         "https://unpkg.com/pmtiles@3/dist/pmtiles.js",
    }

    ok = True
    for fname, url in files.items():
        dest = _ASSET_DIR / fname
        if dest.exists():
            debug_print(f"[PMTiles] assets/{fname} 존재 — 건너뜀")
            continue
        try:
            info_print(f"[PMTiles] 다운로드 중: {fname} ...")
            urllib.request.urlretrieve(url, dest)
            info_print(f"[PMTiles] 저장 완료: {dest} ({dest.stat().st_size // 1024} KB)")
        except Exception as e:
            error_print(f"[PMTiles] 다운로드 실패 ({fname}): {e}")
            ok = False

    glyph_dir = _ASSET_DIR / "glyph_cache" / "fonts"
    for font in ("Noto Sans Regular", "Noto Sans Medium"):
        pbf_path = glyph_dir / font / "0-255.pbf"
        if pbf_path.exists():
            continue
        pbf_path.parent.mkdir(parents=True, exist_ok=True)
        from urllib.parse import quote
        cdn_url = (
            f"https://protomaps.github.io/basemaps-assets/fonts/"
            f"{quote(font)}/0-255.pbf"
        )
        try:
            urllib.request.urlretrieve(cdn_url, pbf_path)
            info_print(f"[Glyph] 다운로드 완료: {font}/0-255.pbf")
        except Exception as e:
            warning_print(f"[Glyph] 다운로드 실패 ({font}/0-255.pbf): {e}")

    return ok


