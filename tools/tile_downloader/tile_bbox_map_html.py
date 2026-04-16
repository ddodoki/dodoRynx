# -*- coding: utf-8 -*-
# tools\tile_downloader\tile_bbox_map_html.py

from __future__ import annotations

import sys
from pathlib import Path
from string import Template
from typing import Optional

_TPL_CACHE: str | None = None


def _tpl_path() -> Path:
    meipass: Optional[str] = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass is not None:
        return Path(meipass) / "tools" / "tile_downloader" / "tile_bbox_map.html"
    return Path(__file__).parent / "tile_bbox_map.html"


def _load_template() -> str:
    global _TPL_CACHE
    if _TPL_CACHE is None:
        path = _tpl_path()
        if not path.exists():
            raise FileNotFoundError(
                f'[tile_bbox_map_html] No template file found: {path}'
            )
        _TPL_CACHE = path.read_text(encoding='utf-8')
    return _TPL_CACHE


def reload_template() -> None:
    global _TPL_CACHE
    _TPL_CACHE = None


def generate_html(
    minzoom:    int,
    maxzoom:    int,
    tilesize:   int,
    tms:        bool,
    style_id:   str   = 'light',
    center_lon: float = 128.0,
    center_lat: float = 36.0,
    init_zoom:  float = 5.0,
) -> str:
    scheme = 'tms' if tms else 'xyz'
    tpl = Template(_load_template())
    return tpl.safe_substitute(
        MINZOOM    = minzoom,
        MAXZOOM    = maxzoom,
        TILESIZE   = tilesize,
        SCHEME     = scheme,
        STYLE_ID   = style_id,
        CENTER_LON = center_lon,
        CENTER_LAT = center_lat,
        INIT_ZOOM  = init_zoom,
    )