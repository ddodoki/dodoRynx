# -*- coding: utf-8 -*-
# tools\tile_downloader\tile_bbox_map.py

"""
MapLibre GL 기반 BBOX 선택 위젯 — gpx_map_preview.py 패턴 그대로 적용.
"""

from __future__ import annotations

import http.server
import socket
import threading
import urllib.request
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import (
        QWebEnginePage, QWebEngineSettings,
    )
    from PySide6.QtWebChannel import QWebChannel
    WE_OK = True
except ImportError:
    WE_OK = False

if TYPE_CHECKING:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import (
        QWebEnginePage, QWebEngineSettings,
    )
    from PySide6.QtWebChannel import QWebChannel

from core.map_loader import (
    _ensure_local_server, _get_web_profile,
    get_raster_tile_config, get_raster_zoom_range,
)

from .tile_bbox_map_html import generate_html
from .tile_calculator import Bbox

from utils.debug import debug_print, error_print
from utils.lang_manager import t
from utils.paths import app_resources_dir



def _t(key: str, **kw) -> str:
    return t(f"tile_downloader.{key}", **kw)


MLVERSION = '4.7.1'


# ── JS 억제 페이지 ──────────────────────────────────────────────────────────
if WE_OK:

    class _ExternalLinkPage(QWebEnginePage):
        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            QDesktopServices.openUrl(url)
            return False


    class _SilentPage(QWebEnginePage):
        _SUPPRESS = (
            'Expected value',
            'favicon',
            'Unknown property',
            'net::ERR_',
            'Failed to fetch',  
            'AJAXError',    
        )

        def __init__(self, profile=None, parent=None):
            if profile is not None:
                super().__init__(profile, parent)
            else:
                super().__init__(parent)

            self._ext_pages: list = []

        def javaScriptConsoleMessage(self, level, msg, line, src):
            if any(s in msg for s in self._SUPPRESS):
                return
            
            debug_print(f'[TileBboxMap JS] {msg} L{line}')


        def createWindow(self, window_type):
            pg = _ExternalLinkPage(self.profile(), self.parent())
            self._ext_pages.append(pg)
            pg.loadFinished.connect(
                lambda: self._ext_pages.remove(pg)
                if pg in self._ext_pages else None
            )
            return pg
else:
    _SilentPage = None  # type: ignore


# ── QWebChannel 브리지 ──────────────────────────────────────────────────────

class TileBboxBridge(QObject):
    """JS ↔ Python 통신 브리지"""
    map_ready        = Signal()
    bbox_changed     = Signal(float, float, float, float) 
    bbox_cleared     = Signal()
    draw_mode_exited = Signal()


    @Slot()
    def onMapReady(self) -> None:
        self.map_ready.emit()


    @Slot(float, float, float, float)
    def onBboxChanged(self,
                      lon_min: float, lat_min: float,
                      lon_max: float, lat_max: float) -> None:
        self.bbox_changed.emit(lon_min, lat_min, lon_max, lat_max)


    @Slot()
    def onBboxCleared(self) -> None:
        self.bbox_cleared.emit()


    @Slot()
    def onDrawModeExited(self) -> None:
        self.draw_mode_exited.emit()


_EMPTY_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


class _TileProxyServer:
    """
    BBOX 선택 지도 전용 미니 HTTP 서버.
    - /assets/...        → 로컬 에셋 파일 서빙
    - /tiles/{z}/{x}/{y} → 실제 타일 서버로 프록시 (CORS 우회)
    map_loader.py 수정 없이 독립 동작.
    """

    def __init__(self, base_url, style_id, asset_dir, map_loader_port: int) -> None:
        self._base_url  = base_url.rstrip('/')
        self._style_id  = style_id
        self._asset_dir = asset_dir
        self._map_loader_port = map_loader_port 
        self._html_content = b''
        self._server    = None
        self._port      = 0


    def set_html(self, html: str) -> None:
        """HTML 내용 갱신 — load() 호출 전 반드시 먼저 호출."""
        self._html_content = html.encode('utf-8')   


    def start(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            self._port = s.getsockname()[1]

        handler = self._build_handler()

        class _Server(http.server.ThreadingHTTPServer):
            request_queue_size = 64

        self._server = _Server(('127.0.0.1', self._port), handler)
        threading.Thread(
            target=self._server.serve_forever,
            daemon=True, name='tile-bbox-proxy',
        ).start()
        return self._port


    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None


    @property
    def port(self) -> int:
        return self._port


    def _build_handler(self):
        import http.client as _hc
        from pathlib import Path as _P
        from urllib.parse import urlparse
        import urllib.request as _req

        base_url        = self._base_url
        style_id        = self._style_id
        asset_dir       = self._asset_dir
        map_loader_port = self._map_loader_port 
        server_ref      = self

        _tl = threading.local()

        def _get_local_conn():
            conn = getattr(_tl, 'local_conn', None)
            if conn is None:
                conn = _hc.HTTPConnection('127.0.0.1', map_loader_port, timeout=5)
                _tl.local_conn = conn
            return conn

        _CTYPES = {
            '.js':   'application/javascript',
            '.css':  'text/css',
            '.webp': 'image/webp',
            '.png':  'image/png',
            '.jpg':  'image/jpeg',
        }

        class _Handler(http.server.BaseHTTPRequestHandler):

            def do_GET(self):
                path = urlparse(self.path).path
                if path == '/map':
                    self._serve_map()
                elif path.startswith('/tiles/'):
                    self._proxy(path)
                elif path.startswith('/assets/'):
                    self._asset(path[len('/assets/'):])
                else:
                    self.send_response(404)
                    self.end_headers()

            def _serve_map(self):
                data = server_ref._html_content
                self.send_response(200)
                self.send_header('Content-Type',   'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control',  'no-store')
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (ConnectionAbortedError, BrokenPipeError, OSError):
                    pass

            def _proxy(self, path):
                parts = path.strip('/').split('/')
                if len(parts) != 4:
                    self.send_response(400); self.end_headers(); return
                _, zs, xs, yp = parts
                try:
                    z = int(zs); x = int(xs)
                    y   = int(_P(yp).stem)
                    ext = _P(yp).suffix.lstrip('.') or 'webp'
                except ValueError:
                    self.send_response(400); self.end_headers(); return

                # ① 로컬 map_loader (persistent connection)
                try:
                    conn = _get_local_conn()
                    try:
                        conn.request('GET', f'/tiles/{z}/{x}/{y}.{ext}',
                                    headers={'Connection': 'keep-alive'})
                        resp = conn.getresponse()
                    except Exception:
                        conn.close()
                        conn = _hc.HTTPConnection('127.0.0.1', map_loader_port, timeout=5)
                        _tl.local_conn = conn
                        conn.request('GET', f'/tiles/{z}/{x}/{y}.{ext}',
                                    headers={'Connection': 'keep-alive'})
                        resp = conn.getresponse()

                    if resp.status == 200:
                        data  = resp.read()                                   
                        ctype = resp.getheader('Content-Type', f'image/{ext}')
                        self.send_response(200)
                        self.send_header('Content-Type',   ctype)          
                        self.send_header('Content-Length', str(len(data)))  
                        self.send_header('Cache-Control',  'public, max-age=300')
                        self.end_headers()
                        try:
                            self.wfile.write(data)                          
                        except (ConnectionAbortedError, BrokenPipeError, OSError):
                            pass
                        return
                    else:
                        resp.read() 
                except Exception:
                    pass

                # ② 사용자 타일 서버 (fallback)
                if base_url:
                    url = f"{base_url}/styles/{style_id}/{z}/{x}/{y}.{ext}"
                    try:
                        rq = _req.Request(url, headers={
                            'User-Agent': 'TileBboxProxy/1.0',
                            'Accept':     'image/*,*/*',
                        })
                        with _req.urlopen(rq, timeout=3) as r:
                            data  = r.read()
                            ctype = r.headers.get('Content-Type', f'image/{ext}')
                        self.send_response(200)
                        self.send_header('Content-Type',   ctype)
                        self.send_header('Content-Length', str(len(data)))
                        self.send_header('Cache-Control',  'public, max-age=300')
                        self.end_headers()
                        try:
                            self.wfile.write(data)
                        except (ConnectionAbortedError, BrokenPipeError, OSError):
                            pass
                        return
                    except Exception:
                        pass

                # ③ 모두 실패 → 투명 PNG (204 대신 — MapLibre fetch 오류 방지)
                self.send_response(200)
                self.send_header('Content-Type',   'image/png')
                self.send_header('Content-Length', str(len(_EMPTY_PNG)))
                self.send_header('Cache-Control',  'public, max-age=60')
                self.end_headers()
                try:
                    self.wfile.write(_EMPTY_PNG)
                except (ConnectionAbortedError, BrokenPipeError, OSError):
                    pass

            def _asset(self, filename):
                p = _P(asset_dir) / filename
                if not p.exists():
                    self.send_response(404); self.end_headers(); return
                try:
                    data  = p.read_bytes()
                    ctype = _CTYPES.get(p.suffix.lower(), 'application/octet-stream')
                    self.send_response(200)
                    self.send_header('Content-Type',   ctype)
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Cache-Control',  'public, max-age=86400')
                    self.end_headers()
                    try:
                        self.wfile.write(data)
                    except (ConnectionAbortedError, BrokenPipeError, OSError):
                        pass
                except Exception:
                    self.send_response(500); self.end_headers()

            def log_message(self, format: str, *args: object) -> None:  
                pass

        return _Handler
    

# ══════════════════════════════════════════════════════════════════════════════
# 지도 위젯
# ══════════════════════════════════════════════════════════════════════════════

class TileBboxMapWidget(QWidget):
    """
    MapLibre GL 기반 BBOX 선택 위젯.

    Parameters
    ──────────
    style_id   : 타일 다운로더에서 설정한 Style ID (지도 배경 표시용)
    init_bbox  : 초기에 표시할 BBOX (None 이면 선택 없음)
    parent     : Qt 부모 위젯
    """
    bbox_changed     = Signal(float, float, float, float)
    bbox_cleared     = Signal()
    draw_mode_exited = Signal()

    def __init__(
        self,
        style_id:  str = 'light',
        base_url:  str = '',          
        init_bbox: Bbox | None = None,
        parent:    QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._style_id   = style_id
        self._base_url   = base_url    
        self._init_bbox  = init_bbox
        self._ready      = False
        self._pending_bbox: Bbox | None = init_bbox
        self._current_bbox: Bbox | None = None
        self._draw_mode  = False
        self._proxy_server: _TileProxyServer | None = None

        self._view:    QWebEngineView | None = None
        self._bridge:  TileBboxBridge | None = None
        self._channel: QWebChannel | None    = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        if not WE_OK:
            lbl = QLabel(_t("bbox_map.no_webengine"), self)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet('color:#888; font-size:13px;')
            lay.addWidget(lbl)
            return

        lay.addWidget(self._make_toolbar())
        self._init_webengine()

        if self._view is None:
            raise RuntimeError('TileBboxMapWidget: WebEngine 초기화 실패')
        lay.addWidget(self._view, 1)

    # ── 툴바 ──────────────────────────────────────────────────────────────────

    def _make_toolbar(self) -> QWidget:
        w = QWidget(self)
        w.setStyleSheet('background:#2b2b2b;')
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(5)

        self._btn_draw   = QPushButton(_t("bbox_map.btn_draw"))
        self._btn_clear  = QPushButton(_t("bbox_map.btn_clear"))
        self._btn_fit    = QPushButton(_t("bbox_map.btn_fit"))
        self._btn_world  = QPushButton(_t("bbox_map.btn_world"))

        for btn in (self._btn_draw, self._btn_clear,
                    self._btn_fit, self._btn_world):
            btn.setStyleSheet(
                'QPushButton{background:#3c3c3c;color:#ddd;border:none;'
                'padding:4px 10px;border-radius:4px;font-size:11px;}'
                'QPushButton:hover{background:#4a4a4a;}'
                'QPushButton:checked{background:#1a5fa8;}'
            )
            lay.addWidget(btn)

        self._btn_draw.setCheckable(True)
        lay.addStretch()

        self._lbl_hint = QLabel(_t("bbox_map.hint_initial"))
        self._lbl_hint.setStyleSheet(
            'color:#888; font-size:11px; font-family:monospace;')
        lay.addWidget(self._lbl_hint)

        self._btn_draw.toggled.connect(self.set_draw_mode)
        self._btn_clear.clicked.connect(self.clear_bbox)
        self._btn_fit.clicked.connect(self.fit_to_bbox)
        self._btn_world.clicked.connect(self.fit_all)
        w.setFixedHeight(36)
        return w

    # ── WebEngine 초기화 ───────────────────────────────────────────────────────

    def _init_webengine(self) -> None:


        map_loader_port = _ensure_local_server()  

        self._proxy_server = _TileProxyServer(
            base_url        = self._base_url,
            style_id        = self._style_id,
            asset_dir       = app_resources_dir() / 'assets',
            map_loader_port = map_loader_port,    
        )
        proxy_port = self._proxy_server.start()

        cfg    = get_raster_tile_config()
        mz, xz = get_raster_zoom_range()

        self._ensure_assets(blocking=False)

        profile = _get_web_profile()
        self._view = QWebEngineView(self)
        self._view.setPage(_SilentPage(profile, self._view))
        self._view.page().setBackgroundColor(QColor(26, 26, 26))

        s = self._view.settings()
        for name, val in (
            ('ScrollAnimatorEnabled',          False),
            ('Accelerated2dCanvasEnabled',      True),
            ('WebGLEnabled',                    True),
            ('LocalContentCanAccessRemoteUrls', True),
            ('ShowScrollBars',                  False),
        ):
            attr = getattr(QWebEngineSettings.WebAttribute, name, None)
            if attr is not None:
                s.setAttribute(attr, val)

        self._bridge  = TileBboxBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject('tileBridge', self._bridge)
        self._view.page().setWebChannel(self._channel)

        self._bridge.map_ready.connect(self._on_map_ready)
        self._bridge.bbox_changed.connect(self._on_bbox_changed)
        self._bridge.bbox_cleared.connect(self._on_bbox_cleared)
        self._bridge.draw_mode_exited.connect(self._on_draw_mode_exited)

        if self._init_bbox:
            cx = (self._init_bbox.lon_min + self._init_bbox.lon_max) / 2
            cy = (self._init_bbox.lat_min + self._init_bbox.lat_max) / 2
            z  = 5.0
        else:
            cx, cy, z = 128.0, 36.0, 5.0

        html = generate_html(
            minzoom=mz, maxzoom=xz,
            tilesize=cfg['tile_size'], tms=cfg['tms'],
            style_id=self._style_id,
            center_lon=cx, center_lat=cy, init_zoom=z,
        )
        self._proxy_server.set_html(html)                               
        self._view.load(QUrl(f'http://127.0.0.1:{proxy_port}/map'))      

        self._asset_poll = QTimer(self)
        self._asset_poll.setInterval(500)
        self._asset_poll.timeout.connect(self._check_assets_ready)
        self._asset_poll.start()

    # ── 에셋 다운로드 ──────────────────────────────────────────────────────────

    def _check_assets_ready(self) -> None:
        asset_dir = app_resources_dir() / 'assets'
        if not all((asset_dir / n).exists()
                for n in ('maplibre-gl.js', 'maplibre-gl.css')):
            return
        self._asset_poll.stop()
        if self._ready or self._view is None or self._proxy_server is None:
            return                      
        proxy  = self._proxy_server
        cfg    = get_raster_tile_config()
        mz, xz = get_raster_zoom_range()
        cx = (self._init_bbox.lon_min + self._init_bbox.lon_max) / 2 \
            if self._init_bbox else 128.0
        cy = (self._init_bbox.lat_min + self._init_bbox.lat_max) / 2 \
            if self._init_bbox else 36.0
        html = generate_html(
            minzoom=mz, maxzoom=xz,
            tilesize=cfg['tile_size'], tms=cfg['tms'],
            style_id=self._style_id,
            center_lon=cx, center_lat=cy, init_zoom=5.0,
        )
        proxy.set_html(html)
        self._view.load(QUrl(f'http://127.0.0.1:{proxy.port}/map'))


    def _ensure_assets(self, blocking: bool = False) -> None:
        asset_dir = app_resources_dir() / 'assets'
        assets = {
            'maplibre-gl.js':
                f'https://unpkg.com/maplibre-gl@{MLVERSION}/dist/maplibre-gl.js',
            'maplibre-gl.css':
                f'https://unpkg.com/maplibre-gl@{MLVERSION}/dist/maplibre-gl.css',
        }
        missing = [
            (name, url) for name, url in assets.items()
            if not (asset_dir / name).exists()
        ]
        if not missing:
            return

        def dl() -> None:
            asset_dir.mkdir(parents=True, exist_ok=True)
            for name, url in missing:
                dest = asset_dir / name
                tmp  = dest.with_suffix(dest.suffix + '.tmp')
                try:
                    with urllib.request.urlopen(url, timeout=15) as r:
                        tmp.write_bytes(r.read())
                    tmp.replace(dest)
                except Exception as e:
                    error_print(f'[TileBboxMap] 에셋 다운로드 실패 {name}: {e}')
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass

        if blocking:
            dl()
        else:
            threading.Thread(target=dl, daemon=True,
                             name='mlasset-bbox-dl').start()

    # ── 맵 준비 완료 ──────────────────────────────────────────────────────────

    def _on_map_ready(self) -> None:
        self._ready = True
        if self._pending_bbox is not None:
            b = self._pending_bbox
            self._pending_bbox = None
            self._do_set_bbox(b)
            QTimer.singleShot(300, self.fit_to_bbox)  

    # ── 브리지 수신 ───────────────────────────────────────────────────────────

    def _on_bbox_changed(self,
                         lon_min: float, lat_min: float,
                         lon_max: float, lat_max: float) -> None:
        self._current_bbox = Bbox(lon_min, lat_min, lon_max, lat_max)
        self._lbl_hint.setText(
            f'W {lon_min:.4f}  S {lat_min:.4f}  '
            f'E {lon_max:.4f}  N {lat_max:.4f}'
        )
        self.bbox_changed.emit(lon_min, lat_min, lon_max, lat_max)


    def _on_bbox_cleared(self) -> None:
        self._current_bbox = None
        self._lbl_hint.setText(_t("bbox_map.hint_initial"))
        self.bbox_cleared.emit()


    def _on_draw_mode_exited(self) -> None:
      
        self._btn_draw.blockSignals(True)
        try:
            self._btn_draw.setChecked(False)  
        finally:
            self._btn_draw.blockSignals(False)

        self._draw_mode = False
        self.draw_mode_exited.emit()

    # ── 공개 API ───────────────────────────────────────────────────────────────

    def set_bbox(self, bbox: Bbox | None) -> None:
        """Python에서 BBOX를 지도에 설정 (프리셋/스핀박스 → 지도 반영)."""
        if bbox is None:
            self.clear_bbox()
            return
        if self._ready:
            self._do_set_bbox(bbox)
        else:
            self._pending_bbox = bbox


    def _do_set_bbox(self, bbox: Bbox) -> None:
        self._current_bbox = bbox 
        self._lbl_hint.setText(
            f'W {bbox.lon_min:.4f} S {bbox.lat_min:.4f} '
            f'E {bbox.lon_max:.4f} N {bbox.lat_max:.4f}'
        )
        if not self._view: return
        self._view.page().runJavaScript(
            f'setBbox({bbox.lon_min},{bbox.lat_min},{bbox.lon_max},{bbox.lat_max});'
        )


    def get_bbox(self) -> Bbox | None:
        return self._current_bbox


    def set_draw_mode(self, enabled: bool) -> None:
        self._draw_mode = enabled
        self._btn_draw.blockSignals(True)
        try:
            self._btn_draw.setChecked(enabled)
        finally:
            self._btn_draw.blockSignals(False)

        if self._view:
            flag = 'true' if enabled else 'false'
            self._view.page().runJavaScript(f'setDrawMode({flag});')


    def clear_bbox(self) -> None:
        if self._view:
            self._view.page().runJavaScript('clearBbox();')


    def fit_to_bbox(self) -> None:
        if self._view:
            self._view.page().runJavaScript('fitToBbox();')


    def fit_all(self) -> None:
        if self._view:
            self._view.page().runJavaScript('fitAll();')

    # ── 리소스 해제 ───────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._cleanup_webengine()
        super().closeEvent(event)


    def _cleanup_webengine(self) -> None:
        if not WE_OK or not self._view:
            return

        if hasattr(self, '_asset_poll'):
            self._asset_poll.stop()

        self._view.stop()    
        self._view.load(QUrl('about:blank'))  

        if hasattr(self, '_proxy_server') and self._proxy_server:
            self._proxy_server.stop()
            self._proxy_server = None

        if self._channel and self._bridge:
            self._channel.deregisterObject(self._bridge)
        self._channel = None

        if self._bridge:
            try:
                self._bridge.map_ready.disconnect()
                self._bridge.bbox_changed.disconnect()
                self._bridge.bbox_cleared.disconnect()
                self._bridge.draw_mode_exited.disconnect()
            except RuntimeError:
                pass
            self._bridge = None

        page = self._view.page()
        self._view.setPage(QWebEnginePage())
        if page:
            page.deleteLater()

        self._view.deleteLater()
        self._view  = None
        self._ready = False


# ══════════════════════════════════════════════════════════════════════════════
# 다이얼로그 래퍼 (tile_downloader_window.py 에서 사용)
# ══════════════════════════════════════════════════════════════════════════════

class _BboxDialog(QDialog):
    """TileBboxDialog.open()이 반환하는 실제 타입 — get_bbox() 정적 선언 포함."""

    def __init__(
        self,
        parent,
        style_id: str,
        base_url: str,
        init_bbox: Bbox | None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("bbox_map.dlg_title"))
        self.resize(900, 650)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self._map_w = TileBboxMapWidget(
            style_id=style_id, base_url=base_url,   
            init_bbox=init_bbox, parent=self)
        self._map_w.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self._map_w)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel,
            parent=self)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        lay.addWidget(btn_box)

        ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setEnabled(init_bbox is not None)
            self._map_w.bbox_changed.connect(lambda *_: ok_btn.setEnabled(True))
            self._map_w.bbox_cleared.connect(lambda: ok_btn.setEnabled(False))

        self.finished.connect(lambda _: self._map_w._cleanup_webengine())


    def get_bbox(self) -> Bbox | None:
        return self._map_w.get_bbox()


class TileBboxDialog:
    @staticmethod
    def open(
        parent,
        style_id: str = 'light',
        base_url: str = '',
        init_bbox: Bbox | None = None,
    ) -> '_BboxDialog':                    
        return _BboxDialog(parent, style_id, base_url, init_bbox)
    