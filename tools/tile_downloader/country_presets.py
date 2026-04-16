# tools\tile_downloader\country_presets.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .tile_calculator import Bbox


@dataclass
class CountryPreset:
    code:         str
    name:         str
    bbox:         Optional[Bbox]
    z_max:        int
    antimeridian: bool = False


PRESETS: dict[str, CountryPreset] = {
    "world":   CountryPreset("world",   "전 세계",        Bbox(-180.0,-85.05, 180.0, 85.05), z_max=8),
    "KR":      CountryPreset("KR",      "대한민국",        Bbox(124.6,  33.1,  131.9, 38.6 ), z_max=15),
    "KR_N":    CountryPreset("KR_N",    "한반도 전체",     Bbox(124.0,  33.0,  132.0, 43.0 ), z_max=14),
    "JP":      CountryPreset("JP",      "일본",           Bbox(122.9,  24.0,  153.9, 45.5 ), z_max=14),
    "CN":      CountryPreset("CN",      "중국",           Bbox( 73.5,  18.2,  135.1, 53.6 ), z_max=12),
    "TW":      CountryPreset("TW",      "대만",           Bbox(119.9,  21.8,  122.1, 25.4 ), z_max=15),
    "US":      CountryPreset("US",      "미국 (본토)",     Bbox(-124.8, 24.4,  -66.9, 49.4 ), z_max=10),
    "US_ALL":  CountryPreset("US_ALL",  "미국 (전체)",    Bbox(-179.1, 18.9, -66.9, 71.4), z_max=9, antimeridian=False),
    "RU": CountryPreset("RU", "러시아",
        Bbox(19.6, 41.2, -170.0, 81.9),  
        z_max=8, antimeridian=True),
    "EU":      CountryPreset("EU",      "유럽",           Bbox(-10.0,  34.5,   34.6, 71.0 ), z_max=10),
    "DE":      CountryPreset("DE",      "독일",           Bbox(  5.9,  47.3,   15.0, 55.1 ), z_max=14),
    "GB":      CountryPreset("GB",      "영국",           Bbox( -8.2,  49.9,    2.0, 60.9 ), z_max=14),
    "FR":      CountryPreset("FR",      "프랑스",          Bbox( -5.1,  41.3,    9.6, 51.1 ), z_max=14),
    "IN":      CountryPreset("IN",      "인도",           Bbox( 68.1,   6.7,   97.4, 35.7 ), z_max=12),
    "AU":      CountryPreset("AU",      "호주",           Bbox(113.3, -43.6,  153.6,-10.7 ), z_max=10),
    "BR":      CountryPreset("BR",      "브라질",          Bbox(-73.9, -33.7,  -34.8,  5.3 ), z_max=10),
    "SA":      CountryPreset("SA",      "동남아시아",       Bbox( 95.0, -11.0,  141.0, 28.0 ), z_max=11),
    "custom":  CountryPreset("custom",  "직접 입력",       None, z_max=18),
}

_ORDER = [
    "world","KR","KR_N","JP","CN","TW",
    "US","US_ALL","RU","EU","DE","GB","FR","IN","AU","BR","SA",
    "custom",
]


def get_ordered_presets() -> list[tuple[str, CountryPreset]]:
    return [(k, PRESETS[k]) for k in _ORDER if k in PRESETS]
