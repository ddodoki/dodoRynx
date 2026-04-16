# -*- coding: utf-8 -*-
# tools\gpx_merger\gpx_map_preview.py

"""GPX 프리뷰 지도 위젯 — MapLibre GL + QWebChannel"""
from __future__ import annotations

import json
import threading
import urllib.request
import uuid
from typing import TYPE_CHECKING, List, Optional

from PySide6.QtCore    import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui     import QCloseEvent, QColor, QDesktopServices
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore    import (
        QWebEnginePage, QWebEngineSettings)
    from PySide6.QtWebChannel       import QWebChannel
    WE_OK = True
except ImportError:
    WE_OK = False

if TYPE_CHECKING:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore    import (
        QWebEnginePage, QWebEngineSettings)
    from PySide6.QtWebChannel       import QWebChannel

from core.map_loader  import (_ensure_local_server, _get_web_profile,
                               get_raster_zoom_range, get_raster_tile_config)
from .gpx_logic       import GpxFile
from .gpx_map_html    import generate_html, _build_track_payload
from utils.debug      import debug_print, error_print
from utils.lang_manager import t
from utils.paths      import app_resources_dir

MLVERSION = '4.7.1'


# ── JS 억제 페이지 ────────────────────────────────────────────
if WE_OK:

    class _ExternalLinkPage(QWebEnginePage):
        """target=_blank 링크를 시스템 브라우저로 열기 위한 임시 페이지."""

        def acceptNavigationRequest(
            self,
            url: QUrl | str,       
            nav_type: QWebEnginePage.NavigationType,
            is_main_frame: bool,
        ) -> bool:
            # 이 페이지는 target=_blank 전용 임시 페이지이므로
            # 모든 내비게이션을 차단하고 시스템 브라우저로 위임한다.
            QDesktopServices.openUrl(QUrl(url) if isinstance(url, str) else url)
            return False


    class _SilentPage(QWebEnginePage):
        _SUPPRESS = ('Expected value', 'favicon', 'Unknown property')

        def __init__(self, profile=None, parent=None):
            if profile:
                super().__init__(profile, parent)
            else:
                super().__init__(parent)
            self._ext_pages: set = set()   

        def javaScriptConsoleMessage(self, level, msg, line, src):
            if any(s in msg for s in self._SUPPRESS):
                return
            debug_print(f'[GpxMap JS] {msg} L{line}')

        def createWindow(self, window_type):
            pg = _ExternalLinkPage(self.profile(), self.parent())
            self._ext_pages.add(pg)     
            pg.loadFinished.connect(
                lambda _ok, p=pg: self._ext_pages.discard(p) 
            )
            return pg

else:
    _SilentPage = None  # type: ignore


# ── QWebChannel 브리지 ───────────────────────────────────────

class GpxMapBridge(QObject):
    """JS ↔ Python 통신 브리지"""
    map_ready           = Signal()
    split_point_added   = Signal(int)
    split_point_removed = Signal(int)

    @Slot()
    def onMapReady(self) -> None:
        self.map_ready.emit()

    @Slot(int)
    def onSplitPointAdded(self, orig_idx: int) -> None:
        self.split_point_added.emit(orig_idx)

    @Slot(int)
    def onSplitPointRemoved(self, orig_idx: int) -> None:
        self.split_point_removed.emit(orig_idx)


# ── 지도 위젯 ────────────────────────────────────────────────

class GpxMapPreview(QWidget):
    """
    MapLibre GL 기반 GPX 프리뷰 지도.
    - 여러 GPX 트랙 오버레이 (색상 구분)
    - 분할 포인트 마커 (클릭으로 추가/제거)
    - 수동 분할 모드
    """
    split_point_added   = Signal(int)
    split_point_removed = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ready:          bool                      = False
        self._pending_tracks: Optional[list]            = None
        self._pending_splits: Optional[list]            = None
        self._view:           Optional[QWebEngineView]  = None 
        self._bridge:         Optional[GpxMapBridge]    = None
        self._channel:        Optional[QWebChannel]     = None 

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        if not WE_OK:
            lbl = QLabel(t('gpx_merger.map_preview.webengine_missing'), self)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                'color: #d4d4d4; font-size: 13px; background: #1e1e1e;'
            )
            lay.addWidget(lbl)
            return

        self._init_webengine()

        if self._view is None:
            raise RuntimeError(t('gpx_merger.map_preview.webengine_init_fail'))
        lay.addWidget(self._view)

    # ── WebEngine 초기화 ─────────────────────────────────────

    def _init_webengine(self) -> None:
        profile = _get_web_profile()
        port    = _ensure_local_server()
        cfg     = get_raster_tile_config()
        mz, xz  = get_raster_zoom_range()

        self._ensure_assets(blocking=False)

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

        self._bridge  = GpxMapBridge(self)
        self._channel = QWebChannel(self._view.page())
        self._channel.registerObject('gpxBridge', self._bridge)
        self._view.page().setWebChannel(self._channel)

        self._bridge.map_ready.connect(self._on_map_ready)
        self._bridge.split_point_added.connect(self.split_point_added)
        self._bridge.split_point_removed.connect(self.split_point_removed)

        html = generate_html(
            port=port, minzoom=mz, maxzoom=xz,
            tilesize=cfg['tile_size'], tms=cfg['tms'],
        )
        self._view.setHtml(html, QUrl(f'http://127.0.0.1:{port}'))

        self._asset_poll = QTimer(self)
        self._asset_poll.setInterval(500)
        self._asset_poll.timeout.connect(self._check_assets_ready)
        self._asset_poll.start()


    def _check_assets_ready(self) -> None:
        asset_dir = app_resources_dir() / 'assets'
        if all((asset_dir / n).exists()
            for n in ('maplibre-gl.js', 'maplibre-gl.css')):
            self._asset_poll.stop()
            if not self._ready and self._view is not None:
                port   = _ensure_local_server()
                cfg    = get_raster_tile_config()
                mz, xz = get_raster_zoom_range()
                html   = generate_html(port=port, minzoom=mz, maxzoom=xz,
                                    tilesize=cfg['tile_size'], tms=cfg['tms'])
                self._view.setHtml(html, QUrl(f'http://127.0.0.1:{port}'))


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
                tmp  = dest.with_suffix(f'.{uuid.uuid4().hex[:8]}.tmp')
                try:
                    urllib.request.urlretrieve(url, tmp)
                    tmp.replace(dest)
                except Exception as e:
                    error_print(f'[GpxMap] MapLibre 에셋 다운로드 실패 {name}: {e}')
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass

        if blocking:
            dl()
        else:
            threading.Thread(target=dl, daemon=True, name='mlasset-dl').start()

    # ── 맵 준비 핸들러 ───────────────────────────────────────
    @Slot()
    def _on_map_ready(self) -> None:
        self._ready = True
        if self._pending_tracks is not None:
            try:
                self._do_load_tracks(self._pending_tracks)
            finally:
                self._pending_tracks = None

        if self._pending_splits is not None:
            try:
                self._do_set_splits(self._pending_splits)
            finally:
                self._pending_splits = None

    # ── 공개 API ────────────────────────────────────────────

    def load_tracks(self, files: List[GpxFile]) -> None:
        if self._ready:
            self._do_load_tracks(files)
        else:
            self._pending_tracks = files


    def set_split_points(self, orig_indices: List[int]) -> None:
        if self._ready:
            self._do_set_splits(orig_indices)
        else:
            self._pending_splits = orig_indices


    def set_split_mode(self, enabled: bool) -> None:
        if self._view:
            flag = 'true' if enabled else 'false'
            self._view.page().runJavaScript(f'setSplitMode({flag});')


    def highlight_segment(self, seg_idx: int) -> None:
        if self._view:
            self._view.page().runJavaScript(f'highlightSegment({seg_idx});')


    def clear_highlight(self) -> None:
        if self._view:
            self._view.page().runJavaScript('clearHighlight();')


    def fit_all(self) -> None:
        if self._view:
            self._view.page().runJavaScript('fitAll();')


    def show_hover_point(self, lat: float, lon: float) -> None:
        """고도 차트 호버 위치를 지도 핀으로 표시."""
        if self._view:
            self._view.page().runJavaScript(f'showHoverPin({lat},{lon});')


    def clear_hover_point(self) -> None:
        """호버 핀 제거."""
        if self._view:
            self._view.page().runJavaScript('clearHoverPin();')

    # ── 내부 ─────────────────────────────────────────────────

    def _do_load_tracks(self, files: List[GpxFile]) -> None:
        if not self._view:
            return
        payload = _build_track_payload(files)
        js_json = json.dumps(payload, ensure_ascii=False)
        self._view.page().runJavaScript(f'loadTracks({js_json});')


    def _do_set_splits(self, orig_indices: List[int]) -> None:
        if not self._view:
            return
        js_json = json.dumps(orig_indices)
        self._view.page().runJavaScript(f'setSplitPoints({js_json});')


    def closeEvent(self, event: QCloseEvent) -> None:
        self._cleanup_webengine()
        super().closeEvent(event)


    def _cleanup_webengine(self) -> None:
        """WebEngine 리소스 명시적 해제."""
        if not WE_OK or not self._view:
            return

        if hasattr(self, '_asset_poll'):
            self._asset_poll.stop()

        if self._channel and self._bridge:
            self._channel.deregisterObject(self._bridge)
        self._channel = None

        if self._bridge:
            try:
                self._bridge.map_ready.disconnect()
                self._bridge.split_point_added.disconnect()
                self._bridge.split_point_removed.disconnect()
            except RuntimeError:
                pass
            self._bridge = None

        page = self._view.page()
        self._view.setPage(QWebEnginePage()) 
        page.deleteLater()

        self._view.deleteLater()
        self._view = None
        self._ready = False
        