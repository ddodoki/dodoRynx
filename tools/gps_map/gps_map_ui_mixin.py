# -*- coding: utf-8 -*-
# tools\gps_map\gps_map_ui_mixin.py

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from utils.lang_manager import t


# ── TYPE_CHECKING 전용 임포트 (런타임엔 실행 안 됨) ─────────────
if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _QWidgetBase
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from tools.gps_map.gps_map_thumbs import GpsThumbProvider, GpsThumbHttpServer
    _MixinBase = _QWidgetBase
else:
    _MixinBase = object   


class GpsMapUIMixin(_MixinBase):
    """
    GPSMapWindow의 UI 구성·이벤트 처리를 담당.

    주의: self.* 인스턴스 변수는 GPSMapWindow.__init__ 에서
    이미 선언되어 있다고 가정한다. 이 믹스인 자체는 상태를 선언하지 않는다.
    """

    # ──────────────────────────────────────────────────────────
    # Pylance 전용: 외부(GPSMapWindow / 다른 믹스인)에서 제공되는
    # 속성과 메서드를 선언한다. 런타임엔 평가되지 않는다.
    # ──────────────────────────────────────────────────────────
    if TYPE_CHECKING:
        # ── GPSMapWindow.__init__ 에서 초기화되는 상태 ──────────
        _view:                Optional[QWebEngineView]
        _gps_data:            List[Dict[str, Any]]
        _gpx_data:            Optional[Dict[str, Any]]
        _map_ready:           bool
        _proxy:               Any   # Optional[_BrowserProxy]
        _overlay:             Any   # Optional[_LoadingOverlay]
        _thumb_provider:      GpsThumbProvider
        _thumb_http:          GpsThumbHttpServer
        _pin_singles_enabled: bool
        _pin_clusters_enabled: bool

        # ── GPSMapWindow 또는 다른 믹스인이 제공하는 메서드 ──────
        def _on_thumbbar_photo_activated(self, filepath: str) -> None: ...
        def _on_thumbbar_photo_hovered(self, filepath: str) -> None: ...
        def _on_thumbbar_set_representative(self, filepath: str, cluster_key: str = "") -> None: ...
        def _on_thumbbar_clear_representative(self, filepath: str) -> None: ...
        def _stop_ready_timer(self) -> None: ...
        def _disconnect_signals(self) -> None: ...
        def _on_title(self, title: str) -> None: ...
        def _load_html(self) -> None: ...
        def _run_js(self, js: str) -> None: ...

        # ── QDialog / QWidget 메서드 (MRO 상 존재하나 믹스인엔 없음) ─
        def setWindowTitle(self, title: str) -> None: ...
        def setStyleSheet(self, styleSheet: str) -> None: ...

    # ──────────────────────────────────────────────────────────
    # UI 빌드
    # ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._thumbbar_enabled = True
        self._pin_thumbs_enabled = False
        self._gpx_visible = True
        self._gpx_elevation_visible = False
        self._pin_singles_btn:  Optional[QPushButton] = None
        self._pin_clusters_btn: Optional[QPushButton] = None

        self._speed_heatmap_btn: Optional[QPushButton] = None
        self._arrows_btn:        Optional[QPushButton] = None
        self._stop_markers_btn:  Optional[QPushButton] = None
        self._playback_btn:      Optional[QPushButton] = None

        self._map_container = QWidget()
        self._map_container.setStyleSheet("background:#1a1a1a")
        self._map_layout = QVBoxLayout(self._map_container)
        self._map_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._map_container, 1)

        self.setStyleSheet(
            "QDialog{background:#1a1a1a;} QLabel{color:#ccc;font-size:11px;}"
        )

        from tools.gps_map.gps_map_thumbbar import GpsMapThumbBar
        self._gps_thumbbar = GpsMapThumbBar(self._thumb_provider, self._map_container)
        self._gps_thumbbar.photo_activated.connect(self._on_thumbbar_photo_activated)
        self._gps_thumbbar.photo_hovered.connect(self._on_thumbbar_photo_hovered)
        self._gps_thumbbar.hide()
        self._map_layout.addWidget(self._gps_thumbbar)
        self._gps_thumbbar.set_representative_requested.connect(
            self._on_thumbbar_set_representative
        )
        self._gps_thumbbar.clear_representative_requested.connect(
            self._on_thumbbar_clear_representative
        )

    # ──────────────────────────────────────────────────────────
    # 윈도우 타이틀 / 빈 화면
    # ──────────────────────────────────────────────────────────

    def _update_window_title(self) -> None:
        """GPS 장 수 + GPX 정보를 포함한 윈도우 타이틀을 갱신한다."""
        gps_count = len(self._gps_data)
        title = f"dodoRynx - GPS Photo Map  |  GPS {gps_count}장"

        if self._gpx_data:
            fname   = self._gpx_data.get("filename", "")
            dist_m  = self._gpx_data.get("distance_m") or 0
            time_sec = self._gpx_data.get("total_time_sec")
            parts: list[str] = []
            if fname:
                parts.append(f"GPX {fname}")
            if dist_m:
                parts.append(f"{dist_m / 1000:.2f} km")
            if time_sec and time_sec > 0:
                h = int(time_sec // 3600)
                m = int(time_sec % 3600 // 60)
                parts.append(f"{h}h {m:02d}m" if h else f"{m}m {int(time_sec % 60):02d}s")
            if parts:
                title += "  |  " + "  ".join(parts)

        self.setWindowTitle(title)


    def _show_empty(self) -> None:
        """GPS 데이터가 없을 때 빈 화면을 표시한다."""
        if self._view:
            self._view.setHtml(
                "<html><body style='"
                "background:#1a1a1a;color:#666;font-family:sans-serif;"
                "display:flex;align-items:center;justify-content:center;"
                "height:100vh;margin:0;font-size:14px"
                "'>No GPS data</body></html>"
            )


    def _sync_pin_buttons(self) -> None:
        """단일/클러스터 핀 버튼 체크 상태를 Python 상태와 동기화."""
        if self._pin_singles_btn:
            self._pin_singles_btn.setChecked(self._pin_singles_enabled)
        if self._pin_clusters_btn:
            self._pin_clusters_btn.setChecked(self._pin_clusters_enabled)


    def set_pin_singles_enabled(self, enabled: bool) -> None:
        self._pin_singles_enabled = enabled
        self._sync_pin_buttons()
        if self._map_ready:
            v = 'true' if enabled else 'false'
            self._run_js(f'window.setPinSinglesEnabled({v});')


    def set_pin_clusters_enabled(self, enabled: bool) -> None:
        self._pin_clusters_enabled = enabled
        self._sync_pin_buttons()
        if self._map_ready:
            v = 'true' if enabled else 'false'
            self._run_js(f'window.setPinClustersEnabled({v});')


    def _toggle_photo_playback(self, checked: bool) -> None:
        if self._playback_btn:
            self._playback_btn.setText(t('gps_map.toolbar_qt.play_on') if checked
            else t('gps_map.toolbar_qt.play_off'))
        if not self._map_ready:
            return
        if checked:
            self._run_js('window.startPhotoPlayback(2500);')
        else:
            self._run_js('window.stopPhotoPlayback();')

    # ──────────────────────────────────────────────────────────
    # Qt 이벤트 핸들러
    # ──────────────────────────────────────────────────────────

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._map_ready and self._view and self._gps_data:
            if self._overlay:
                self._overlay.set_loading_map()
                self._overlay.show()
                self._overlay.raise_()
            self._load_html()


    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        if self._overlay:
            self._overlay.setGeometry(self._map_container.rect())


    def closeEvent(self, e: QCloseEvent) -> None:
        import tools.gps_map.gps_map_window as _mod

        if self._proxy:
            self._proxy.shutdown()
            self._proxy = None

        if self._thumb_http:
            self._thumb_http.shutdown()

        self._stop_ready_timer()

        if self._overlay:
            self._overlay.stop()
            self._overlay = None

        self._disconnect_signals()

        if self._view:
            try:
                self._view.titleChanged.disconnect(self._on_title)
            except RuntimeError:
                pass
            self._view.setUrl(QUrl("about:blank"))
            self._view.deleteLater()
            self._view = None

        self._gps_data = []
        self._map_ready = False
        _mod._instance = None  

        super().closeEvent(e)

