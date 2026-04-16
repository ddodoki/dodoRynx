# -*- coding: utf-8 -*-
# tools\gps_map\gps_map_cluster_mixin.py

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List

from utils.debug import info_print

# ── TYPE_CHECKING 전용 ──────────────────────────────────────────
if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget as _QWidgetBase
    from tools.gps_map.gps_map_thumbs import PinThumbRegistry, GpsThumbProvider
    _MixinBase = _QWidgetBase
else:
    _MixinBase = object


class GpsMapClusterMixin(_MixinBase):
    """클러스터 핀의 대표 이미지 선택·저장·JS 반영을 담당."""

    # ── Pylance 전용: 외부에서 제공되는 속성·메서드 선언 ──────────
    if TYPE_CHECKING:
        _thumb_registry:            PinThumbRegistry
        _thumb_provider:            GpsThumbProvider
        _gps_data:                  List[Dict[str, Any]]
        _map_ready:                 bool
        _view:                      Any   # Optional[QWebEngineView]
        _pin_thumbs_enabled:        bool
        _pin_thumb_zoom_threshold:  int
        _active_cluster_key:        str
        _active_cluster_fps:        list[str]
        _current_fp:                str
        _gps_thumbbar:              Any   # Optional[GpsMapThumbBar]


    # ──────────────────────────────────────────────────────────
    # 퍼블릭 진입점
    # ──────────────────────────────────────────────────────────

    def _open_cluster_dialog(self, clusterkey: str, filepaths: list) -> None:
        """
        ClusterRepresentativeDialog 를 열어 대표 이미지를 선택한다.
        JsBridgeMixin 의 CLUSTERSHOW 핸들러에서 호출된다.
        """
        from tools.gps_map.gps_map_cluster_dialog import ClusterRepresentativeDialog

        current = self._thumb_registry.representative_for(clusterkey, filepaths) or ""
        dlg = ClusterRepresentativeDialog(
            self._thumb_provider, filepaths, current, self
        )
        if dlg.exec() != ClusterRepresentativeDialog.DialogCode.Accepted:
            return
        chosen = dlg.selected_filepath()
        if not chosen:
            return
        self._apply_representative(clusterkey, chosen)


    def _pick_cluster_representative_for_current_view(self) -> None:
        """
        현재 뷰포트에서 가장 가까운 클러스터를 찾아 CLUSTERSEL 이벤트를 발생시킨다.
        JS 쪽 CLUSTERSEL title → JsBridgeMixin → _on_thumbbar_set_representative 흐름.
        """
        if not self._map_ready or not self._view or not self._gps_data:
            return

        js = (
            "(function(){"
            "  var b=map.getBounds();"
            "  var vis=points.filter(function(p){"
            "    return b.contains({lng:p.lon,lat:p.lat});"
            "  });"
            "  if(vis.length===0) return;"
            "  var k=clusterKey(vis[0],map.getZoom());"
            "  var g=points.filter(function(p){"
            "    return clusterKey(p,map.getZoom())===k;"
            "  });"
            "  document.title='CLUSTERSEL:'"
            "    +encodeURIComponent(k)+':'"
            "    +encodeURIComponent(g.map(function(m){return m.filepath}).join('|'));"
            "})()"
        )
        self._view.page().runJavaScript(js)


    def _set_current_as_representative(self) -> None:
        """
        현재 열린 사진(self._current_fp)을 해당 클러스터의 대표로 지정한다.
        활성 클러스터 선택 상태면 그 클러스터 키를 직접 사용하고,
        아니면 _on_thumbbar_set_representative 에 위임하여 JS 줌 기반으로 처리한다.
        """
        if not self._current_fp:
            return
        if (
            self._active_cluster_key
            and self._current_fp in self._active_cluster_fps
        ):
            self._apply_representative(self._active_cluster_key, self._current_fp)
        else:
            self._on_thumbbar_set_representative(self._current_fp)

    # ──────────────────────────────────────────────────────────
    # 썸네일바 시그널 핸들러
    # ──────────────────────────────────────────────────────────

    def _on_thumbbar_set_representative(
        self, filepath: str, cluster_key: str = ""
    ) -> None:
        """
        GpsMapThumbBar.set_representative_requested 시그널 핸들러.

        활성 클러스터 컨텍스트가 있으면 즉시 _apply_representative 를 호출하고,
        없으면 JS 에서 현재 줌 값을 받아 동적으로 클러스터 키를 계산한 뒤 적용한다.
        """
        if self._active_cluster_key and filepath in self._active_cluster_fps:
            self._apply_representative(self._active_cluster_key, filepath)
            return

        if self._map_ready and self._view:
            def apply(zoom_val) -> None:
                from tools.gps_map.gps_map_thumbs import make_cluster_key

                target = next(
                    (d for d in self._gps_data if d["filepath"] == filepath), None
                )
                if not target:
                    return
                cz = int(round(min(float(zoom_val) if zoom_val else 12, 17)))
                key = make_cluster_key(target["lat"], target["lon"], float(cz))
                self._apply_representative(key, filepath)

            self._view.page().runJavaScript(
                "Math.round(Math.min(map.getZoom(), 17))", apply
            )


    def _on_thumbbar_clear_representative(self, filepath: str) -> None:

        from tools.gps_map.gps_map_thumbs import make_cluster_key

        target = next(
            (d for d in self._gps_data if d["filepath"] == filepath), None
        )
        if not target:
            return

        clusterkey = make_cluster_key(
            target["lat"], target["lon"], float(self._pin_thumb_zoom_threshold)
        )
        self._thumb_registry.clear_representative(clusterkey)
        self._thumb_registry.save()

        if self._map_ready and self._view:
            overrides = json.dumps(
                self._thumb_registry.representative_overrides, ensure_ascii=False
            )
            self._view.page().runJavaScript(
                f"window.setRepOverrides({overrides})"
            )
        info_print(f"[GpsMap] 대표 해제: {clusterkey}")

    # ──────────────────────────────────────────────────────────
    # 내부 공통 로직
    # ──────────────────────────────────────────────────────────

    def _apply_representative(self, clusterkey: str, filepath: str) -> None:
        """
        레지스트리에 대표 이미지를 저장하고 JS 의 window.setRepOverrides 를 갱신한다.
        _open_cluster_dialog / _on_thumbbar_set_representative 양쪽에서 호출된다.
        """
        self._thumb_registry.set_representative(clusterkey, filepath)
        if self._map_ready and self._view:
            overrides = json.dumps(
                self._thumb_registry.representative_overrides, ensure_ascii=False
            )
            self._view.page().runJavaScript(
                f"window.setRepOverrides({overrides})"
            )
        info_print(f"[GpsMap] 대표 설정: {clusterkey} → {filepath}")

