# -*- coding: utf-8 -*-
# tools\gpx_merger\gpx_map_html.py

"""GPX 프리뷰용 MapLibre GL JS HTML 생성기"""

from __future__ import annotations

import sys
from pathlib import Path
from string  import Template
from typing  import List, Optional

from .gpx_logic    import GpxFile        
from .gpx_analyzer import downsample_for_display

from utils.lang_manager import t


# ── 상수 ────────────────────────────────────────────────────

DISPLAY_MAX = 20_000
SPLIT_MAX   = 100_000

_TPL_CACHE: Optional[str] = None


def _tpl_path() -> Path:
    """PyInstaller 번들 / 개발 환경 모두에서 올바른 템플릿 경로 반환."""
    meipass: Optional[str] = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass is not None:
        return Path(meipass) / "tools" / "gpx_merger" / "gpx_map.html"
    return Path(__file__).parent / "gpx_map.html"


def _load_template() -> str:
    global _TPL_CACHE
    if _TPL_CACHE is None:
        _TPL_CACHE = _tpl_path().read_text(encoding='utf-8')
    return _TPL_CACHE


def reload_template() -> None:
    global _TPL_CACHE
    _TPL_CACHE = None

# ── 인덱스 샘플러 ────────────────────────────────────────────

def _sample_indices(total: int, max_count: int) -> List[int]:
    """
    total 개 포인트 중 max_count 개를 균등 간격으로 선택한 인덱스 목록 반환.
    total <= max_count 이면 전체 인덱스 반환.
    downsample_for_display 와 동일한 균등 샘플링 → orig_idx 일치 보장.
    """
    if total <= max_count:
        return list(range(total))
    step = total / max_count
    return [int(i * step) for i in range(max_count)]

# ── 페이로드 빌더 ────────────────────────────────────────────

def _build_track_payload(files: List[GpxFile]) -> dict:
    """
    MapLibre 에 넘길 JSON 페이로드 생성.
      tracks    : 각 트랙의 display 좌표
      allPoints : split 감지용 포인트 (flat 전역 orig_idx 기준)
      bounds    : [minLon, minLat, maxLon, maxLat]
    """
    tracks_out  = []
    all_pts_out = []
    min_lon, min_lat =  180.0,  90.0
    max_lon, max_lat = -180.0, -90.0

    flat_offset  = 0
    n_files = max(1, len(files))
    per_file_max = max(500, SPLIT_MAX // n_files)

    for f in files:
        all_pts = f.all_points

        # bounds 갱신
        for p in all_pts:
            min_lon = min(min_lon, p.lon)
            min_lat = min(min_lat, p.lat)
            max_lon = max(max_lon, p.lon)
            max_lat = max(max_lat, p.lat)

        for local_idx in _sample_indices(len(all_pts), per_file_max):
            p = all_pts[local_idx]
            all_pts_out.append({
                'lat':      p.lat,
                'lon':      p.lon,
                'orig_idx': flat_offset + local_idx, 
            })

        flat_offset += len(all_pts)

        # 트랙 display 좌표
        for trk in f.tracks:
            tpts = trk.all_points
            if not tpts:
                continue
            disp = (tpts if len(tpts) <= DISPLAY_MAX
                    else downsample_for_display(tpts, DISPLAY_MAX))
            tracks_out.append({
                'id':    trk.track_id,
                'name':  trk.name,
                'color': trk.color,
                'coords': [[p.lon, p.lat] for p in disp],
                'file':   f.path.name,
            })

    bounds = ([min_lon, min_lat, max_lon, max_lat] if tracks_out else None)
    return {'tracks': tracks_out, 'allPoints': all_pts_out, 'bounds': bounds}

# ── HTML 생성기 ──────────────────────────────────────────────

def generate_html(
    port:     int,
    minzoom:  int,
    maxzoom:  int,
    tilesize: int,
    tms:      bool,
    mlver:    str = '4.7.1',
) -> str:
    """
    gpx_map.html 템플릿에 런타임 값을 채워 완성된 HTML 문자열을 반환한다.

    템플릿 치환 변수 (gpx_map.html 참고)
    -------------------------------------
    ${PORT}     로컬 타일 서버 포트
    ${MINZOOM}  타일 최소 줌 레벨
    ${MAXZOOM}  타일 최대 줌 레벨
    ${TILESIZE} 타일 크기 (px)
    ${SCHEME}   타일 스킴  'xyz' | 'tms'
    """
    scheme = 'tms' if tms else 'xyz'
    tpl    = Template(_load_template())
    return tpl.safe_substitute(
        PORT     = port,
        MINZOOM  = minzoom,
        MAXZOOM  = maxzoom,
        TILESIZE = tilesize,
        SCHEME   = scheme,
        MLVER    = mlver,       
        SPLIT_HINT         = t('gpx_merger.map_html.split_hint'),
        SPLIT_MARKER_TITLE = t('gpx_merger.map_html.split_marker_title'),
    )
