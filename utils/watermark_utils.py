# utils/watermark_utils.py

from __future__ import annotations
import re
from typing import Dict, List

_INVALID_VALUES = frozenset({"None", "none", "알 수 없음"})


def flatten_watermark_metadata(metadata: dict) -> Dict[str, str]:
    """워터마크용 메타데이터 플래튼 — Panel/Mixin 공용."""
    out: Dict[str, str] = {}

    def put(key: str, value: object) -> None:
        s = str(value).strip()
        if not s or s in _INVALID_VALUES:
            return
        out[key] = s

    for group_name in ("file", "camera", "exif"):
        group = metadata.get(group_name)
        if isinstance(group, dict):
            for k, v in group.items():
                put(k, v)
                put(f"{group_name}.{k}", v)
                put(f"{group_name}_{k}", v)

    gps = metadata.get("gps")
    if isinstance(gps, dict):
        for k, v in gps.items():
            put(f"gps.{k}", v)
            put(f"gps_{k}", v)
            if k == "display":
                put("gps_display", v)
            elif k == "altitude":
                put("gps_altitude", v)

    return out


def resolve_template(
    template: str,
    flat: Dict[str, str],
    max_lines: int = 0,
) -> List[str]:
    """템플릿 문자열을 flat dict로 치환해 줄 목록 반환."""
    def repl(m: re.Match[str]) -> str:
        raw = m.group(1).strip()
        return flat.get(raw, flat.get(raw.replace(".", "_"), ""))

    resolved = re.sub(r"\{([^{}]+)\}", repl, template or "")
    lines = [ln.rstrip() for ln in resolved.splitlines() if ln.strip()]
    return lines[:max_lines] if max_lines else lines

