# -*- coding: utf-8 -*-
# tools\gpx_merger\gpx_logic.py

"""GPX 데이터 모델, 파싱, 합치기, 쪼개기, 저장 핵심 로직"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from utils.lang_manager import t

try:
    import gpxpy
    import gpxpy.gpx
    GPXPY_OK = True
except ImportError:
    gpxpy    = None  # type: ignore[assignment]
    GPXPY_OK = False

TRACK_COLORS = [
    '#4E9AF1', '#F15C4E', '#4EF16B', '#F1C44E',
    '#C44EF1', '#F14E9A', '#4EF1D8', '#F1844E',
    '#A4F14E', '#4E74F1', '#F1A44E', '#84F14E',
]


# ── 데이터 모델 ───────────────────────────────────────────────

@dataclass
class GpxPoint:
    lat:  float
    lon:  float
    ele:  Optional[float] = None
    time: Optional[datetime] = None
    orig_idx: int = 0

    def is_valid_coord(self) -> bool:
        return -90.0 <= self.lat <= 90.0 and -180.0 <= self.lon <= 180.0


@dataclass
class GpxSegment:
    points: List[GpxPoint] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return len(self.points) == 0

    @property
    def has_timestamps(self) -> bool:
        return any(p.time is not None for p in self.points)


@dataclass
class GpxTrack:
    name:     str
    color:    str = '#4E9AF1'
    track_id: str = ''
    segments: List[GpxSegment] = field(default_factory=list)

    @property
    def all_points(self) -> List[GpxPoint]:
        pts: List[GpxPoint] = []
        for s in self.segments:
            pts.extend(s.points)
        return pts


@dataclass
class GpxFile:
    path:           Path
    tracks:         List[GpxTrack] = field(default_factory=list)
    waypoints:      List[GpxPoint] = field(default_factory=list)
    has_timestamps: bool           = False
    encoding:       str            = 'utf-8'
    warnings:       List[str]      = field(default_factory=list)

    @property
    def all_points(self) -> List[GpxPoint]:
        pts: List[GpxPoint] = []
        for trk in self.tracks:   
            pts.extend(trk.all_points)
        return pts

    @property
    def point_count(self) -> int:
        return sum(len(s.points)
                for trk in self.tracks
                for s in trk.segments)

    @property
    def bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """(minLon, minLat, maxLon, maxLat) 또는 None"""
        pts = self.all_points
        if not pts:
            return None
        lats = [p.lat for p in pts]
        lons = [p.lon for p in pts]
        return min(lons), min(lats), max(lons), max(lats)


@dataclass
class MergeOptions:
    sort_by_time:          bool = True
    merge_as:              str  = 'single_track'
    merge_waypoints:       bool = True
    deduplicate_waypoints: bool = True


@dataclass
class SplitResult:
    fragments:     List[GpxFile]
    split_indices: List[int]


# ── 유틸리티 ─────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2.0 * R * math.asin(min(1.0, math.sqrt(a)))


def _to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _assign_colors(files: List[GpxFile]) -> None:
    idx = 0
    for f in files:
        for trk in f.tracks:   
            trk.color    = TRACK_COLORS[idx % len(TRACK_COLORS)]
            trk.track_id = f'track_{idx}'
            idx += 1


# ── GPX 파싱 ─────────────────────────────────────────────────

def parse_gpx_file(path: Path) -> GpxFile:
    if not GPXPY_OK:
        raise ImportError(t('gpx_merger.logic.gpxpy_missing'))
    assert gpxpy is not None
    # NOTE: gpxpy is not None here — guaranteed by GPXPY_OK flag (type narrowing)
    # pyright: ignore[reportOptionalMemberAccess]

    result = GpxFile(path=path)
    raw    = path.read_bytes()

    if raw.startswith(b'\xef\xbb\xbf'):
        raw = raw[3:]

    gpx_obj  = None
    used_enc = 'utf-8'
    for enc in ('utf-8', 'cp1252', 'latin-1'):
        try:
            gpx_obj  = gpxpy.parse(raw.decode(enc))
            used_enc = enc
            break
        except Exception:
            continue

    if gpx_obj is None:
        raise ValueError(t('gpx_merger.logic.parse_fail', name=path.name))

    result.encoding = used_enc
    if used_enc != 'utf-8':
        result.warnings.append(t('gpx_merger.logic.warn_encoding', enc=used_enc))

    flat_idx = 0
    any_time = False

    for ti, raw_track in enumerate(gpx_obj.tracks):
        tdata = GpxTrack(
            name=raw_track.name or f'Track {ti + 1}',
            track_id=f't{ti}',
        )

        prev_time:  Optional[datetime] = None
        seen_times: set[datetime] = set()

        for si, raw_seg in enumerate(raw_track.segments):
            sdata = GpxSegment()

            for pi, pt in enumerate(raw_seg.points):
                try:
                    lat = float(pt.latitude)
                    lon = float(pt.longitude)
                except (TypeError, ValueError):
                    result.warnings.append(t('gpx_merger.logic.warn_coord_fail', ti=ti, si=si, pi=pi))
                    continue

                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    result.warnings.append(t('gpx_merger.logic.warn_coord_range', lat=lat, lon=lon))
                    continue

                ele = (float(pt.elevation)
                       if pt.elevation is not None else None)

                pt_time = pt.time
                if pt_time is not None:
                    pt_time = _to_utc_naive(pt_time)
                    if prev_time is not None and pt_time < prev_time:
                        result.warnings.append(t('gpx_merger.logic.warn_time_reverse', ti=ti, si=si, pi=pi))
                        pt_time = None
                    elif pt_time in seen_times:           
                        result.warnings.append(t('gpx_merger.logic.warn_time_dup', ti=ti, si=si, pi=pi))
                    else:
                        seen_times.add(pt_time)      
                        prev_time = pt_time
                        any_time  = True

                sdata.points.append(
                    GpxPoint(lat=lat, lon=lon, ele=ele, time=pt_time, orig_idx=flat_idx))
                flat_idx += 1

            if not sdata.is_empty:
                tdata.segments.append(sdata)
            else:
                result.warnings.append(t('gpx_merger.logic.warn_empty_seg', ti=ti, si=si))

        if tdata.segments:
            result.tracks.append(tdata)
        else:
            result.warnings.append(t('gpx_merger.logic.warn_empty_track', ti=ti))

    for wpt in gpx_obj.waypoints:
        try:
            lat = float(wpt.latitude)
            lon = float(wpt.longitude)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                wpt_time = _to_utc_naive(wpt.time) if wpt.time else None
                result.waypoints.append(
                    GpxPoint(lat=lat, lon=lon,
                            ele=float(wpt.elevation) if wpt.elevation else None,
                            time=wpt_time))
        except (TypeError, ValueError):
            pass

    if not result.tracks:
        result.warnings.append(t('gpx_merger.logic.warn_no_track')) 

    return result


# ── 합치기 ───────────────────────────────────────────────────

def _reassign_orig_idx(f: GpxFile) -> None:
    idx = 0
    for trk in f.tracks:   
        for s in trk.segments:
            for p in s.points:
                p.orig_idx = idx
                idx += 1


def merge_gpx_files(
    files:       List[GpxFile],
    options:     MergeOptions,
    output_name: str = 'merged',
) -> GpxFile:
    if not files:
        raise ValueError(t('gpx_merger.logic.merge_empty'))

    result = GpxFile(path=files[0].path.parent / f'{output_name}.gpx')

    if options.sort_by_time and all(f.has_timestamps for f in files):
        def first_time(f: GpxFile) -> datetime:
            for p in f.all_points:
                if p.time:
                    return p.time
            return datetime.min
        files = sorted(files, key=first_time)

    if options.merge_as == 'single_track':
        merged = GpxTrack(name=output_name, track_id='merged')
        seg    = GpxSegment()
        for f in files:
            for trk in f.tracks:        
                for s in trk.segments:
                    seg.points.extend(s.points)
        merged.segments.append(seg)
        result.tracks.append(merged)

    elif options.merge_as == 'multi_track':
        for i, f in enumerate(files):
            for trk in f.tracks:
                nt = GpxTrack(
                    name=trk.name or f.path.stem,
                    track_id=f'track_{i}',
                    segments=[GpxSegment(points=list(s.points)) for s in trk.segments],
                )
                result.tracks.append(nt)

    elif options.merge_as == 'segments':
        merged = GpxTrack(name=output_name, track_id='merged')
        for f in files:
            for trk in f.tracks:       
                merged.segments.extend(
                    GpxSegment(points=list(s.points)) for s in trk.segments)
        result.tracks.append(merged)

    _reassign_orig_idx(result)

    if options.merge_waypoints:
        all_w = [w for f in files for w in f.waypoints]
        if options.deduplicate_waypoints:
            seen: set = set()
            deduped = []
            for w in all_w:
                k = (round(w.lat, 5), round(w.lon, 5))
                if k not in seen:
                    seen.add(k)
                    deduped.append(w)
            result.waypoints = deduped
        else:
            result.waypoints = all_w

    result.has_timestamps = any(f.has_timestamps for f in files)
    _assign_colors([result])
    return result

# ── 쪼개기 공통 ──────────────────────────────────────────────

def _flat_points(f: GpxFile) -> List[GpxPoint]:
    pts: List[GpxPoint] = []
    for trk in f.tracks:
        for s in trk.segments:
            pts.extend(s.points)
    return pts


def _build_fragment(
    points:      List[GpxPoint],
    idx:         int,
    source_path: Path,
    name:        str = '',
) -> GpxFile:
    frag  = GpxFile(path=source_path)
    seg   = GpxSegment(points=points)
    track = GpxTrack(
        name=name or f'Segment {idx + 1}',
        color=TRACK_COLORS[idx % len(TRACK_COLORS)],
        track_id=f'frag_{idx}',
        segments=[seg],
    )
    frag.tracks.append(track)
    frag.has_timestamps = any(p.time for p in points)
    return frag


def _split_by_indices(f: GpxFile, indices: List[int]) -> SplitResult:
    pts        = _flat_points(f)
    boundaries = sorted({i for i in indices if 0 < i < len(pts)})
    starts     = [0] + boundaries
    ends       = boundaries + [len(pts)]
    fragments  = []
    for i, (s, e) in enumerate(zip(starts, ends)):
        chunk = pts[s:e]
        if chunk:
            fragments.append(_build_fragment(chunk, i, f.path))
    return SplitResult(fragments=fragments, split_indices=boundaries)

# ── 쪼개기: 시간 갭 ──────────────────────────────────────────

def split_by_time_gap(f: GpxFile, gap_minutes: float = 30.0) -> SplitResult:
    if not f.has_timestamps:
        raise ValueError(t('gpx_merger.logic.split_no_ts_gap'))
    if gap_minutes <= 0:
        raise ValueError(t('gpx_merger.logic.split_gap_zero'))
    
    pts     = _flat_points(f)
    gap_sec = gap_minutes * 60.0
    indices = []
    for i in range(1, len(pts)):
        t0, t1 = pts[i - 1].time, pts[i].time
        if t0 and t1 and (t1 - t0).total_seconds() >= gap_sec:
            indices.append(i)
    return _split_by_indices(f, indices)

# ── 쪼개기: 날짜 ─────────────────────────────────────────────

def split_by_date(f: GpxFile) -> SplitResult:
    if not f.has_timestamps:
        raise ValueError(t('gpx_merger.logic.split_no_ts_date'))
    pts     = _flat_points(f)
    indices = []
    for i in range(1, len(pts)):
        t0, t1 = pts[i - 1].time, pts[i].time
        if t0 and t1 and t0.date() != t1.date():
            indices.append(i)
    return _split_by_indices(f, indices)

# ── 쪼개기: 거리 ─────────────────────────────────────────────

def split_by_distance(f: GpxFile, distance_km: float = 10.0) -> SplitResult:
    if distance_km <= 0:
        raise ValueError(t('gpx_merger.logic.split_dist_zero'))
        
    pts       = _flat_points(f)
    threshold = distance_km * 1000.0
    accum     = 0.0
    indices   = []
    for i in range(1, len(pts)):
        accum += haversine(pts[i-1].lat, pts[i-1].lon, pts[i].lat, pts[i].lon)
        if accum >= threshold:
            indices.append(i)
            accum = 0.0
    return _split_by_indices(f, indices)

# ── 쪼개기: 포인트 수 ────────────────────────────────────────

def split_by_point_count(f: GpxFile, count: int = 1000) -> SplitResult:
    if count <= 0:
        raise ValueError(t('gpx_merger.logic.split_pts_zero'))
        
    pts     = _flat_points(f)
    indices = list(range(count, len(pts), count))
    return _split_by_indices(f, indices)

# ── 쪼개기: 수동 ─────────────────────────────────────────────

def split_manual(f: GpxFile, split_indices: List[int]) -> SplitResult:
    return _split_by_indices(f, split_indices)

# ── 트림 ─────────────────────────────────────────────────────

def trim_gpx(
    f:                  GpxFile,
    trim_start_minutes: float = 0.0,
    trim_end_minutes:   float = 0.0,
    start_index:        Optional[int] = None,
    end_index:          Optional[int] = None,
) -> GpxFile:
    pts   = _flat_points(f)
    s_idx = 0
    e_idx = len(pts)

    if start_index is not None:
        s_idx = max(0, start_index)
    elif trim_start_minutes > 0 and f.has_timestamps:
        first = next((p.time for p in pts if p.time), None)
        if first:
            cutoff = first + timedelta(minutes=trim_start_minutes)
            for i, p in enumerate(pts):
                if p.time and p.time >= cutoff:
                    s_idx = i
                    break

    if end_index is not None:
        e_idx = min(len(pts), end_index)
    elif trim_end_minutes > 0 and f.has_timestamps:
        last = next((p.time for p in reversed(pts) if p.time), None)
        if last:
            cutoff = last - timedelta(minutes=trim_end_minutes)
            for i, p in reversed(list(enumerate(pts))):
                if p.time and p.time <= cutoff:
                    e_idx = i + 1
                    break

    chunk = pts[s_idx:e_idx]
    if not chunk:
        raise ValueError(t('gpx_merger.logic.trim_empty_result'))
    return _build_fragment(chunk, 0, f.path)

# ── 저장 ─────────────────────────────────────────────────────

def save_gpx_file(
    f:           GpxFile,
    output_path: Path,
    overwrite:   bool = False,
) -> None:
    if not GPXPY_OK:
        raise ImportError(t('gpx_merger.logic.save_no_gpxpy'))
    assert gpxpy is not None

    try:
        if f.path.resolve() == output_path.resolve():
            raise ValueError(t('gpx_merger.logic.save_src_conflict', name=output_path.name))
    except (AttributeError, OSError):
        pass

    if not overwrite and output_path.exists():
        raise FileExistsError(t('gpx_merger.logic.save_exists', name=output_path.name))

    folder = output_path.parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        raise PermissionError(t('gpx_merger.logic.save_no_permission', folder=folder))

    gpx_out = gpxpy.gpx.GPX()
    for trk in f.tracks:   
        track = gpxpy.gpx.GPXTrack(name=trk.name)
        for s in trk.segments:
            seg = gpxpy.gpx.GPXTrackSegment()
            for p in s.points:
                seg.points.append(
                    gpxpy.gpx.GPXTrackPoint(
                        latitude=p.lat, longitude=p.lon,
                        elevation=p.ele, time=p.time))
            track.segments.append(seg)
        gpx_out.tracks.append(track)

    for w in f.waypoints:
        gpx_out.waypoints.append(
            gpxpy.gpx.GPXWaypoint(
                latitude=w.lat, longitude=w.lon,
                elevation=w.ele, time=w.time))

    output_path.write_text(gpx_out.to_xml(), encoding='utf-8')

# ── 출력 파일명 생성 ─────────────────────────────────────────

def make_output_filename(
    source_stem: str,
    index:       int,
    fragment:    GpxFile,
    template:    str = '{stem}_{index:02d}',
) -> str:
    pts      = fragment.all_points
    date_str = ''
    if fragment.has_timestamps:
        first = next((p.time for p in pts if p.time), None)
        if first:
            date_str = first.strftime('%Y%m%d')
    try:
        return template.format(
            stem=source_stem, index=index,
            date=date_str or 'nodate') + '.gpx'
    except (KeyError, ValueError):
        return f'{source_stem}_{index:02d}.gpx'

