# -*- coding: utf-8 -*-
# tools\tile_downloader\tdw_constants.py

"""TileDownloader 공용 상수 — 순환 import 방지를 위해 별도 파일로 분리."""

from utils.lang_manager import t


# ── 단축 함수 ─────────────────────────────────────────────────────────────────

def _t(key: str, **kw) -> str:
    return t(f"tile_downloader.{key}", **kw)


# ── 상태 상수 ─────────────────────────────────────────────────────────────────

class S:
    IDLE        = "IDLE"
    CALCULATING = "CALCULATING"
    READY       = "READY"
    RUNNING     = "RUNNING"
    PAUSED      = "PAUSED"
    CANCELLING  = "CANCELLING"


# ── 로그 레벨 색상 (언어 무관 — 그대로 유지) ──────────────────────────────────

LEVEL_COLOR = {"INFO": "#888888", "WARN": "#E6A817", "ERROR": "#E05252"}


# ── 언어팩 연동 함수 ──────────────────────────────────────────────────────────

def get_state_label(state: str) -> str:
    """상태 문자열 → 현재 언어의 표시 텍스트."""
    return _t(f"state.{state.lower()}")


def get_fmt_options() -> list[tuple[str, str]]:
    """포맷 콤보박스 옵션 — 호출 시점의 언어로 반환."""
    return [
        ("webp", _t("grp_tile.fmt_webp")),
        ("png",  _t("grp_tile.fmt_png")),
        ("jpg",  _t("grp_tile.fmt_jpg")),
    ]


def get_size_options() -> list[tuple[str, str]]:
    """타일 크기 콤보박스 옵션 — 호출 시점의 언어로 반환."""
    return [
        ("256", _t("grp_tile.size_256")),
        ("@2x", _t("grp_tile.size_2x")),
        ("512", _t("grp_tile.size_512")),
    ]

