# utils/gpx_parser.py
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── 내부 유틸 ─────────────────────────────────────────────────

def _strip(tag: str) -> str:
    """XML 네임스페이스 제거: {ns}local → local"""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text.strip())
    except Exception:
        return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ── GpxPoint 데이터클래스 (외부 참조용) ──────────────────────
# Bug #6 수정: 센서 필드 추가

@dataclass(slots=True)
class GpxPoint:
    idx:             int
    lat:             float
    lon:             float
    ele:             float | None
    time:            str
    dist_m:          float
    speed_mps:       float | None = None
    heart_rate_bpm:  float | None = None
    cadence_spm:     float | None = None
    temperature_c:   float | None = None


# ── 센서 파싱 설정 ────────────────────────────────────────────
# Bug #4 수정: alias를 네임스페이스 제거 후 소문자 로컬명으로 통일
#             → 중복 제거, 탐색 횟수 최소화
# Bug #7 수정: 실제 Garmin/Polar GPX에서 쓰이는 로컬태그명 기준으로 정리

_SENSOR_ALIASES: dict[str, list[str]] = {
    # GPX 표준: <speed>, Garmin: <ns3:speed>, <gpxtpx:speed>
    'speed_mps':      ['speed', 'velocity'],
    # Garmin: <hr>, <gpxtpx:hr>, Polar: <heartrate>
    'heart_rate_bpm': ['hr', 'heartrate', 'heart_rate'],
    # Garmin: <cad>, <gpxtpx:cad>, RunKeeper: <runcadence>
    'cadence_spm':    ['cad', 'cadence', 'runcadence'],
    # Garmin: <atemp>, Suunto: <temp>
    'temperature_c':  ['atemp', 'temp', 'temperature', 'airtemp'],
}

_SENSOR_VALID: dict[str, tuple[float, float]] = {
    'speed_mps':       (0.0,   100.0),   # 0 ~ 360 km/h (스키·사이클링 포함)
    'heart_rate_bpm':  (20.0,  250.0),
    'cadence_spm':     (0.0,   300.0),
    'temperature_c':  (-60.0,   80.0),
}

_SENSOR_FIELDS = ('speed_mps', 'heart_rate_bpm', 'cadence_spm', 'temperature_c')


# ── Bug #3 수정: trkpt 를 단 한 번만 순회해 모든 태그 수집 ────
def _build_ext_map(pt_elem: ET.Element) -> dict[str, str]:
    """
    trkpt 하위 전체를 단일 순회 → {소문자_로컬태그명: 첫 번째 유효 text}.
    setdefault 사용으로 중복 태그는 트리 상위(첫 번째) 값 우선.
    """
    result: dict[str, str] = {}
    for elem in pt_elem.iter():
        tag  = _strip(elem.tag).lower()
        text = (elem.text or "").strip()
        if text:
            result.setdefault(tag, text)
    return result


def _extract_sensor(ext_map: dict[str, str], field_key: str) -> float | None:
    """
    사전 빌드된 ext_map 에서 field_key 에 해당하는 첫 유효값 반환.
    alias 순서대로 탐색 → None 반환 시 다음 alias 시도.
    """
    lo, hi = _SENSOR_VALID[field_key]
    for alias in _SENSOR_ALIASES[field_key]:
        text = ext_map.get(alias)
        if text is None:
            continue
        try:
            v = float(text)
            if lo <= v <= hi:
                return v
        except ValueError:
            pass
    return None


# ── 메인 파서 ─────────────────────────────────────────────────
# Bug #1 수정: 중복 parse_gpx_file 제거 — 단일 완전 구현
# Bug #2 수정: 불필요한 _parse_trkpt() 제거 — 루프에 인라인
# Bug #5 수정: bounds 를 pts_raw 대신 최종 points 에서 계산

def parse_gpx_file(path: str | Path) -> dict[str, Any]:
    """
    GPX 파일을 파싱해 JS 측이 소비할 dict 반환.

    points[*] 구조:
        idx, lat, lon, ele, time, dist_m,
        speed_mps, heart_rate_bpm, cadence_spm, temperature_c
        (센서 없는 포인트는 해당 필드 = None)

    반환 dict 최상위 추가 키:
        sensors.available: { speed, heart_rate, cadence, temperature }
    """
    path = Path(path)
    root = ET.fromstring(path.read_text(encoding="utf-8", errors="ignore"))

    # ── 1차 수집: trkpt XML 요소 파싱 ─────────────────────────
    pts_raw: list[dict[str, Any]] = []

    for elem in root.iter():
        if _strip(elem.tag) != "trkpt":
            continue

        lat = _parse_float(elem.attrib.get("lat"))
        lon = _parse_float(elem.attrib.get("lon"))
        if lat is None or lon is None:
            continue

        # ele / time: 직계 자식에서만 추출 (정확성 우선)
        ele:       float | None = None
        time_text: str          = ""
        for child in elem:
            name = _strip(child.tag)
            if name == "ele":
                ele       = _parse_float(child.text)
            elif name == "time":
                time_text = (child.text or "").strip()

        # 센서: 전체 하위 트리 단일 순회로 ext_map 구축 후 추출
        ext_map = _build_ext_map(elem)
        pts_raw.append({
            "lat":            lat,
            "lon":            lon,
            "ele":            ele,
            "time":           time_text,
            "speed_mps":      _extract_sensor(ext_map, "speed_mps"),
            "heart_rate_bpm": _extract_sensor(ext_map, "heart_rate_bpm"),
            "cadence_spm":    _extract_sensor(ext_map, "cadence_spm"),
            "temperature_c":  _extract_sensor(ext_map, "temperature_c"),
        })

    if len(pts_raw) < 2:
        raise ValueError("GPX 트랙 포인트가 2개 이상 필요합니다.")

    # ── 2차 처리: 누적 거리 계산 + 최종 points 빌드 ────────────
    points:      list[dict[str, Any]] = []
    dist         = 0.0
    prev:        dict[str, Any] | None = None
    elev_values: list[float]           = []

    for idx, pt in enumerate(pts_raw):
        if prev is not None:
            dist += _haversine_m(prev["lat"], prev["lon"], pt["lat"], pt["lon"])
        prev = pt

        if pt["ele"] is not None:
            elev_values.append(pt["ele"])

        points.append({
            "idx":            idx,
            "lat":            pt["lat"],
            "lon":            pt["lon"],
            "ele":            pt["ele"],
            "time":           pt["time"],
            "dist_m":         round(dist, 2),
            "speed_mps":      pt["speed_mps"],
            "heart_rate_bpm": pt["heart_rate_bpm"],
            "cadence_spm":    pt["cadence_spm"],
            "temperature_c":  pt["temperature_c"],
        })

    # ── Bug #5 수정: bounds 를 최종 points 에서 계산 ───────────
    lats    = [p["lat"] for p in points]
    lons    = [p["lon"] for p in points]
    min_lat = min(lats);  max_lat = max(lats)
    min_lon = min(lons);  max_lon = max(lons)

    has_elevation = len(elev_values) >= 2

    # ── 센서 가용성 플래그 ─────────────────────────────────────
    avail: dict[str, bool] = {
        "speed":       any(p["speed_mps"]       is not None for p in points),
        "heart_rate":  any(p["heart_rate_bpm"]  is not None for p in points),
        "cadence":     any(p["cadence_spm"]      is not None for p in points),
        "temperature": any(p["temperature_c"]   is not None for p in points),
    }

    return {
        "file_path":     str(path),
        "file_name":     path.name,
        "points":        points,
        "route":         [[p["lon"], p["lat"]] for p in points],
        "bounds": {
            "west":  min_lon,
            "south": min_lat,
            "east":  max_lon,
            "north": max_lat,
        },
        "start":         points[0],
        "end":           points[-1],
        "has_elevation": has_elevation,
        "elevation_min": min(elev_values) if elev_values else None,
        "elevation_max": max(elev_values) if elev_values else None,
        "distance_m":    round(points[-1]["dist_m"], 2),
        "point_count":   len(points),
        "sensors": {
            "available": avail,
        },
    }
