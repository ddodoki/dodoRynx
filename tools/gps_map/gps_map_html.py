from __future__ import annotations

import sys
from pathlib import Path
from string import Template
from typing import Optional
from tools.gps_map.gps_map_i18n import build_gps_map_i18n

_ML_VERSION = "4.7.1"
_ML_ASSETS: dict[str, str] = {
    "maplibre-gl.js":  f"https://unpkg.com/maplibre-gl@{_ML_VERSION}/dist/maplibre-gl.js",
    "maplibre-gl.css": f"https://unpkg.com/maplibre-gl@{_ML_VERSION}/dist/maplibre-gl.css",
}


def _base_dir() -> Path:
    """PyInstaller 번들 / 개발 환경 모두에서 올바른 템플릿 디렉터리 반환."""
    meipass: Optional[str] = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass is not None:
        return Path(meipass) / "tools" / "gps_map"
    return Path(__file__).parent


# ── 템플릿 경로 (lazy — 함수 호출 시 결정) ──────────────────────
def _shell_path()   -> Path: return _base_dir() / "gps_map_shell.html"
def _map_tpl()      -> Path: return _base_dir() / "gps_map_script.js.tpl"
def _elev_tpl()     -> Path: return _base_dir() / "gps_map_elevation.js.tpl"
def _toolbar_tpl()  -> Path: return _base_dir() / "gps_map_browser_toolbar-1.tpl"


_template_cache: dict[Path, Template] = {}


def _load_template(path: Path) -> Template:
    if path not in _template_cache:
        _template_cache[path] = Template(path.read_text(encoding="utf-8"))
    return _template_cache[path]


def invalidate_template_cache() -> None:
    _template_cache.clear()


def ml_asset_url(asset_dir: Path, port: int, name: str) -> str:
    if (asset_dir / name).exists():
        return f"http://127.0.0.1:{port}/assets/{name}"
    return _ML_ASSETS[name]


def build_html(
    port: int,
    center_lat: float,
    center_lon: float,
    zoom: int,
    points_json: str,
    route_json: str,
    min_zoom: int,
    max_zoom: int,
    tile_size: int,
    tms: bool,
    route_visible: bool,
    thumbbar_enabled: bool,
    pin_thumbs_enabled: bool,
    pin_thumb_zoom_threshold: int,
    rep_overrides_json: str,
    gpx_json: str = "null",
    gpx_visible: bool = True,
    gpx_has_elevation: bool = False,
    gpx_has_sensors: bool = False,
    elevation_visible: bool = False,
    asset_dir: Optional[Path] = None,
    geo_utc_offset_hours: "float | str" = "null",
    toolbar_mode: str = "",
    pin_singles_on: bool = True,
    pin_clusters_on: bool = True,
    i18n_json: Optional[str] = None,
) -> str:

    if i18n_json is None:
        i18n_json = build_gps_map_i18n()

    ad = asset_dir or Path(".")
    _ = thumbbar_enabled

    context: dict = {
        "js_url":               ml_asset_url(ad, port, "maplibre-gl.js"),
        "css_url":              ml_asset_url(ad, port, "maplibre-gl.css"),
        "port":                 port,
        "center_lat":           center_lat,
        "center_lon":           center_lon,
        "zoom":                 zoom,
        "points_json":          points_json,
        "route_json":           route_json,
        "route_visible":        "true" if route_visible else "false",
        "min_zoom":             min_zoom,
        "max_zoom":             max_zoom,
        "tile_size":            tile_size,
        "scheme":               "tms" if tms else "xyz",
        "pin_thumbs_on":        "true" if pin_thumbs_enabled else "false",
        "pin_zoom_thresh":      pin_thumb_zoom_threshold,
        "rep_overrides_json":   rep_overrides_json,
        "gpx_json":             gpx_json,
        "gpx_visible":          "true" if gpx_visible else "false",
        "gpx_has_elevation":    "true" if gpx_has_elevation else "false",
        "gpx_has_sensors":      "true" if gpx_has_sensors else "false",
        "elevation_visible":    "true" if elevation_visible else "false",
        "geo_utc_offset_hours": geo_utc_offset_hours,
        "toolbar_mode":         toolbar_mode,
        "pin_singles_on":       "true" if pin_singles_on else "false",
        "pin_clusters_on":      "true" if pin_clusters_on else "false",
        "i18n_json":            i18n_json,
    }

    # 매 호출마다 lazy path 함수로 실제 경로 결정
    script_map  = _load_template(_map_tpl()    ).safe_substitute(context)
    script_elev = _load_template(_elev_tpl()   ).safe_substitute(context)
    toolbar     = _load_template(_toolbar_tpl()).safe_substitute(context)

    shell_context = dict(context)
    shell_context["script_map"]       = script_map
    shell_context["script_elevation"] = script_elev
    shell_context["toolbar_script"]   = toolbar

    return _load_template(_shell_path()).safe_substitute(shell_context)