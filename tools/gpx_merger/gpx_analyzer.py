# -*- coding: utf-8 -*-
# tools\gpx_merger\gpx_analyzer.py

"""GPX 통계, 갭 감지, 이상치 탐지, 다운샘플링, 평탄화"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from .gpx_logic import GpxFile, GpxPoint, haversine
from utils.lang_manager import t


# ── 데이터 클래스 ─────────────────────────────────────────────

@dataclass
class SegmentStats:
    point_count:     int
    distance_m:      float
    duration_sec:    Optional[float]    = None
    moving_time_sec: Optional[float]    = None
    stop_time_sec:   Optional[float]    = None
    max_speed_kmh:   Optional[float]    = None
    avg_speed_kmh:   Optional[float]    = None
    ele_gain_m:      Optional[float]    = None
    ele_loss_m:      Optional[float]    = None
    max_ele_m:       Optional[float]    = None
    min_ele_m:       Optional[float]    = None
    start_time:      Optional[datetime] = None
    end_time:        Optional[datetime] = None


@dataclass
class GapInfo:
    split_index:  int
    gap_seconds:  float
    lat:          float
    lon:          float
    before_time:  Optional[datetime] = None
    after_time:   Optional[datetime] = None

    @property
    def gap_minutes(self) -> float:
        return self.gap_seconds / 60.0


@dataclass
class AnomalyInfo:
    index:       int
    kind:        str    
    description: str
    lat:         float
    lon:         float


@dataclass
class FileStats:
    total_points:     int
    total_distance_m: float
    segments:         List[SegmentStats] = field(default_factory=list)
    anomaly_count:    int = 0
    gap_count:        int = 0


# ── 통계 계산 ────────────────────────────────────────────────

STOP_SPEED_KMH = 1.0
MOVING_MIN_SEC = 2.0


def compute_stats(points: List[GpxPoint]) -> SegmentStats:
    n = len(points)
    if n == 0:
        return SegmentStats(point_count=0, distance_m=0.0)

    total_dist   = 0.0
    ele_gain     = 0.0
    ele_loss     = 0.0
    eles         = [p.ele for p in points if p.ele is not None]
    speeds_kmh   = []
    moving_sec   = 0.0
    stop_sec     = 0.0
    duration_sec: Optional[float] = None
    start_time   = points[0].time
    end_time     = points[-1].time

    if start_time and end_time:
        duration_sec = (end_time - start_time).total_seconds()

    for i in range(1, n):
        p0, p1 = points[i - 1], points[i]
        dist = haversine(p0.lat, p0.lon, p1.lat, p1.lon)
        total_dist += dist

        if p0.ele is not None and p1.ele is not None:
            diff = p1.ele - p0.ele
            if diff > 0:
                ele_gain += diff
            else:
                ele_loss -= diff

        if p0.time and p1.time:
            dt = (p1.time - p0.time).total_seconds()
            if dt > 0:
                spd = (dist / dt) * 3.6
                speeds_kmh.append(spd)
                if spd >= STOP_SPEED_KMH:
                    moving_sec += dt
                else:
                    stop_sec += dt

    return SegmentStats(
        point_count=n,
        distance_m=total_dist,
        duration_sec=duration_sec,
        moving_time_sec=moving_sec if moving_sec > 0 else None,
        stop_time_sec=stop_sec if stop_sec > 0 else None,
        max_speed_kmh=max(speeds_kmh) if speeds_kmh else None,
        avg_speed_kmh=(
            (total_dist / 1000.0) / (moving_sec / 3600.0)
            if moving_sec > 0 else None),
        ele_gain_m=ele_gain if eles else None,
        ele_loss_m=ele_loss if eles else None,
        max_ele_m=max(eles) if eles else None,
        min_ele_m=min(eles) if eles else None,
        start_time=start_time,
        end_time=end_time,
    )


def compute_file_stats(f: GpxFile) -> FileStats:
    pts  = f.all_points
    segs = []
    for trk in f.tracks:
        for s in trk.segments:
            segs.append(compute_stats(s.points))
    total_dist = sum(s.distance_m for s in segs)
    return FileStats(
        total_points=len(pts),
        total_distance_m=total_dist,
        segments=segs,
    )


# ── 갭 감지 ──────────────────────────────────────────────────

def detect_gaps(
    points:          List[GpxPoint],
    min_gap_seconds: float = 600.0,
) -> List[GapInfo]:
    gaps: List[GapInfo] = []
    for i in range(1, len(points)):
        t0, t1 = points[i - 1].time, points[i].time
        if t0 is None or t1 is None:
            continue
        diff = (t1 - t0).total_seconds()
        if diff >= min_gap_seconds:
            gaps.append(GapInfo(
                split_index=i,
                gap_seconds=diff,
                lat=points[i].lat,
                lon=points[i].lon,
                before_time=t0,
                after_time=t1,
            ))
    return gaps


# ── 이상치 탐지 ──────────────────────────────────────────────

MAX_SPEED_DEFAULT = 300.0


def detect_anomalies(
    points:        List[GpxPoint],
    max_speed_kmh: float = MAX_SPEED_DEFAULT,
) -> List[AnomalyInfo]:
    anomalies: List[AnomalyInfo] = []
    seen_times: set[datetime] = set()

    if points and points[0].time:
        seen_times.add(points[0].time)

    for i in range(1, len(points)):
        p0, p1 = points[i - 1], points[i]

        # 속도 이상
        if p0.time and p1.time:
            dt = (p1.time - p0.time).total_seconds()
            if dt > 0:
                dist = haversine(p0.lat, p0.lon, p1.lat, p1.lon)
                spd  = (dist / dt) * 3.6
                if spd > max_speed_kmh:
                    anomalies.append(AnomalyInfo(
                        index=i, kind='speed',
                        description=t('gpx_merger.analyzer.anomaly_speed',
                                    spd=spd, max_spd=max_speed_kmh),
                        lat=p1.lat, lon=p1.lon,
                    ))

        # 중복 타임스탬프
        if p1.time:
            if p1.time in seen_times:
                anomalies.append(AnomalyInfo(
                    index=i, kind='dup_time',
                    description=t('gpx_merger.analyzer.anomaly_dup_time', time=p1.time),
                    lat=p1.lat, lon=p1.lon,
                ))
            seen_times.add(p1.time)

    return anomalies

# ── 이상 포인트 제거 ─────────────────────────────────────────

def remove_anomalies(
    points:        List[GpxPoint],
    max_speed_kmh: float = MAX_SPEED_DEFAULT,
) -> Tuple[List[GpxPoint], int]:
    """속도 이상 포인트를 제거한 새 리스트와 제거 수 반환"""
    if max_speed_kmh <= 0:
        raise ValueError(t('gpx_merger.analyzer.err_max_speed'))
    if not points:
        return [], 0
    result  = [points[0]]
    removed = 0
    for i in range(1, len(points)):
        p0, p1 = result[-1], points[i]
        if p0.time and p1.time:
            dt = (p1.time - p0.time).total_seconds()
            if dt > 0:
                dist = haversine(p0.lat, p0.lon, p1.lat, p1.lon)
                spd  = (dist / dt) * 3.6
                if spd > max_speed_kmh:
                    removed += 1
                    continue
        result.append(p1)
    return result, removed

# ── RDP 다운샘플링 ────────────────────────────────────────────

def _perp_distance(
    p:     GpxPoint,
    start: GpxPoint,
    end:   GpxPoint,
) -> float:
    """점 p와 선분 start-end 사이의 수직 거리 (도 단위 근사, cos 위도 보정)"""
    dx = end.lon - start.lon
    dy = end.lat - start.lat
    if dx == 0 and dy == 0:
        mid_lat  = math.radians((p.lat + start.lat) / 2.0)
        return math.hypot(
            (p.lon - start.lon) * math.cos(mid_lat),
            (p.lat - start.lat),
        )
    ratio    = ((p.lon - start.lon) * dx + (p.lat - start.lat) * dy) / (dx * dx + dy * dy)
    ratio    = max(0.0, min(1.0, ratio))
    proj_lon = start.lon + ratio * dx
    proj_lat = start.lat + ratio * dy
    mid_lat  = math.radians((p.lat + proj_lat) / 2.0)
    return math.hypot(
        (p.lon - proj_lon) * math.cos(mid_lat),
        (p.lat - proj_lat),
    )


def rdp_downsample(
    points:  List[GpxPoint],
    epsilon: float = 0.0001,
) -> List[GpxPoint]:
    """
    Ramer-Douglas-Peucker 다운샘플링. 원본 GpxPoint 참조 유지.
    """
    n = len(points)
    if n < 3:
        return list(points)

    keep = [False] * n
    keep[0] = keep[n - 1] = True

    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end - start <= 1:
            continue
        max_dist = 0.0
        max_idx  = start
        for i in range(start + 1, end):
            d = _perp_distance(points[i], points[start], points[end])
            if d > max_dist:
                max_dist = d
                max_idx  = i
        if max_dist > epsilon:
            keep[max_idx] = True
            stack.append((start, max_idx))
            stack.append((max_idx, end))

    return [p for p, k in zip(points, keep) if k]


def downsample_for_display(
    points:    List[GpxPoint],
    max_count: int = 20_000,
) -> List[GpxPoint]:
    """지도 표시용 다운샘플링. 포인트 수를 max_count 이하로 줄임."""
    if len(points) <= max_count:
        return list(points)

    epsilon = 0.0001
    result  = points
    for _ in range(20):
        result = rdp_downsample(points, epsilon)
        if len(result) <= max_count:
            break
        epsilon *= 2.0

    if len(result) > max_count:
        step   = len(result) / max_count
        result = [result[int(i * step)] for i in range(max_count)]

    return result

# ── 고도 평탄화 ──────────────────────────────────────────────

def smooth_elevation(
    points: List[GpxPoint],
    window: int = 5,
) -> List[GpxPoint]:
    """이동 평균으로 고도 평탄화. 원본 리스트 복사 후 ele 만 수정."""
    if window < 1:
        raise ValueError(t('gpx_merger.analyzer.err_window'))
    result = copy.deepcopy(points)
    half   = window // 2
    eles   = [p.ele for p in points]
    for i, p in enumerate(result):
        if p.ele is None:
            continue
        vals = [e for e in eles[max(0, i - half): i + half + 1] if e is not None]
        if vals:
            result[i].ele = sum(vals) / len(vals)
    return result

# ── 고도/속도 프로파일 데이터 ────────────────────────────────

@dataclass
class ProfilePoint:
    dist_m:    float
    ele_m:     Optional[float]   
    speed_kmh: Optional[float]
    time:      Optional[datetime]
    orig_idx:  int


def build_profile(points: List[GpxPoint]) -> List[ProfilePoint]:
    """고도/속도 차트용 프로파일 데이터 생성"""
    profile: List[ProfilePoint] = []
    dist = 0.0
    for i, p in enumerate(points):
        spd = None
        if i > 0:
            p0   = points[i - 1]
            d     = haversine(p0.lat, p0.lon, p.lat, p.lon)
            dist += d
            if p0.time and p.time:
                dt = (p.time - p0.time).total_seconds()
                if dt > 0:
                    spd = (d / dt) * 3.6
        profile.append(ProfilePoint(
            dist_m=dist,
            ele_m=p.ele,    
            speed_kmh=spd,
            time=p.time,
            orig_idx=p.orig_idx,
        ))
    return profile
