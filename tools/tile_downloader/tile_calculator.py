# -*- coding: utf-8 -*-
# tools\tile_downloader\tile_calculator.py

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Generator

# 포맷·크기별 평균 타일 용량 (KB, 경험치 기반)
AVG_TILE_KB: dict[tuple[str, int], int] = {
    ("webp", 256): 18,  ("webp", 512): 55,
    ("png",  256): 35,  ("png",  512): 110,
    ("jpg",  256): 22,  ("jpg",  512): 68,
    ("jpeg", 256): 22,  ("jpeg", 512): 68, 
}

TILE_SIZE_MAP: dict[str, int] = {"256": 256, "@2x": 512, "512": 512}
SIZE_FOLDER_MAP: dict[str, str] = {"256": "256", "@2x": "512_2x", "512": "512_native"}


# ── 데이터 클래스 ──────────────────────────────────────────────────────────────
@dataclass
class Bbox:
    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float

    def to_dict(self) -> dict:
        return {"lon_min": self.lon_min, "lat_min": self.lat_min,
                "lon_max": self.lon_max, "lat_max": self.lat_max}

    @staticmethod
    def from_dict(d: dict) -> Bbox:
        return Bbox(float(d["lon_min"]), float(d["lat_min"]),
                    float(d["lon_max"]), float(d["lat_max"]))


@dataclass
class ExistingScanResult:
    count:       int
    size_bytes:  int
    corrupt_tmp: list[Path]


@dataclass
class CalcResult:
    tile_count:   int            # 전체 타일 수 (기존 포함)
    new_count:    int            # 실제 다운로드 필요 수
    size_mb:      float          # 추가 예상 용량 (MB)
    eta_sec:      float          # 예상 소요 초
    existing:     ExistingScanResult
    z_breakdown:  dict[int, int] # 줌별 전체 타일 수
    bbox_list:    list[Bbox]     # split_antimeridian 결과
    antimeridian: bool


# ── 수학 함수 ─────────────────────────────────────────────────────────────────

def deg2tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    """위도는 ±85.051129° 클리핑 (Web Mercator 한계)."""
    if z == 0:
        return 0, 0
    lat = max(-85.051129, min(85.051129, lat))
    lat_r = math.radians(lat)
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    cap = n - 1
    return max(0, min(cap, x)), max(0, min(cap, y))


def bbox_to_tile_range(z: int, bbox: Bbox) -> tuple[int, int, int, int]:
    """BBox -> (x0, y0, x1, y1) 타일 범위. 항상 x0<=x1, y0<=y1 보장."""
    x_a, y_a = deg2tile(bbox.lat_min, bbox.lon_min, z)
    x_b, y_b = deg2tile(bbox.lat_max, bbox.lon_max, z)
    cap = max(0, 2 ** z - 1)
    return (max(0, min(x_a, x_b)), max(0, min(y_a, y_b)),
            min(cap, max(x_a, x_b)), min(cap, max(y_a, y_b)))


def split_antimeridian(bbox: Bbox) -> list[Bbox]:
    """날짜변경선 통과 BBox를 동쪽/서쪽 두 조각으로 분리."""
    if bbox.lon_min > bbox.lon_max:
        return [
            Bbox(bbox.lon_min, bbox.lat_min,  180.0,        bbox.lat_max),
            Bbox(-180.0,       bbox.lat_min,  bbox.lon_max, bbox.lat_max),
        ]
    return [bbox]


def estimate_z_breakdown(z_min: int, z_max: int,
                          bbox_list: list[Bbox]) -> dict[int, int]:
    """줌별 타일 수 수식 계산 (O(zoom range) — 즉시 완료)."""
    result: dict[int, int] = {}
    for z in range(z_min, z_max + 1):
        count = 0
        for bbox in bbox_list:
            x0, y0, x1, y1 = bbox_to_tile_range(z, bbox)
            count += (x1 - x0 + 1) * (y1 - y0 + 1)
        result[z] = count
    return result


def estimate_disk_size_mb(new_count: int, fmt: str, tile_size_mode: str) -> float:
    size = TILE_SIZE_MAP.get(tile_size_mode, 256)
    avg_kb = AVG_TILE_KB.get((fmt, size), 25)
    return new_count * avg_kb / 1024.0

# ── 제너레이터 & 경로/URL 빌더 ───────────────────────────────────────────────

def build_tile_generator(z_min: int, z_max: int,
                          bbox_list: list[Bbox]) -> Generator[tuple[int, int, int], None, None]:
    """메모리 안전 타일 제너레이터. 매번 새 호출로 재생성 필요."""
    for z in range(z_min, z_max + 1):
        for bbox in bbox_list:
            x0, y0, x1, y1 = bbox_to_tile_range(z, bbox)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    yield (z, x, y)


def build_out_path(out_root: Path, style_id: str,
                   tile_size_mode: str, fmt: str,
                   z: int, x: int, y: int) -> Path:
    folder = SIZE_FOLDER_MAP.get(tile_size_mode, "256")
    return out_root / style_id / folder / str(z) / str(x) / f"{y}.{fmt}"


def build_tile_url(base_url: str, style_id: str,
                   tile_size_mode: str, fmt: str,
                   z: int, x: int, y: int) -> str:
    base = base_url.rstrip("/")
    if tile_size_mode == "256":
        return f"{base}/styles/{style_id}/{z}/{x}/{y}.{fmt}"
    elif tile_size_mode == "@2x":
        return f"{base}/styles/{style_id}/{z}/{x}/{y}@2x.{fmt}"
    elif tile_size_mode == "512":
        return f"{base}/styles/{style_id}/512/{z}/{x}/{y}.{fmt}"
    else:
        return f"{base}/styles/{style_id}/{z}/{x}/{y}.{fmt}"

# ── 기존 파일 스캔 (QThread 내에서 실행) ─────────────────────────────────────

def scan_existing(out_root: Path, style_id: str,
                  tile_size_mode: str, fmt: str,
                  z_breakdown: dict[int, int],
                  cancel_check: Callable[[], bool]) -> ExistingScanResult:
    """
    OUT_ROOT 내 완성 파일 수/크기 집계 + .tmp 잔존 파일 목록 수집.
    cancel_check() 가 True 를 반환하면 즉시 중단.
    """
    count = 0
    size_bytes = 0
    corrupt_tmp: list[Path] = []
    base_dir = out_root / style_id / SIZE_FOLDER_MAP.get(tile_size_mode, "256")
    _check_interval = 5_000  
    _checked = 0

    for z in sorted(z_breakdown.keys()):
        if cancel_check():
            break
        z_dir = base_dir / str(z)
        if not z_dir.exists():
            continue
        for f in z_dir.rglob("*"):
            if not f.is_file():
                continue
            # ── 주기적 취소 확인 ────────────────────────
            _checked += 1
            if _checked % _check_interval == 0 and cancel_check():
                return ExistingScanResult(count, size_bytes, corrupt_tmp)
            # ────────────────────────────────────────────
            if f.suffix == ".tmp":
                corrupt_tmp.append(f)
                continue
            if f.suffix != f".{fmt}":
                continue
            try:
                st = f.stat()
                if st.st_size > 0:
                    count += 1
                    size_bytes += st.st_size
            except FileNotFoundError:
                pass

    return ExistingScanResult(count=count, size_bytes=size_bytes,
                              corrupt_tmp=corrupt_tmp)