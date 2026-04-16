# -*- coding: utf-8 -*-
# tools\tile_downloader\config_manager.py

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from utils.debug import error_print
from utils.lang_manager import t
from utils.paths import get_user_data_dir

if TYPE_CHECKING:
    from utils.config_manager import ConfigManager as MainConfigManager


APP_NAME  = "MapTileDownloader" 
APP_VENDOR = "tools"

DATA_DIR     = get_user_data_dir() / "tile_downloader"
HISTORY_FILE = get_user_data_dir() / "tile_session_history.json"
PRESETS_DIR  = get_user_data_dir() / "tile_presets"
MAX_HISTORY  = 100


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)


def _t(key: str, **kw) -> str:
    return t(f"tile_downloader.{key}", **kw)


# ── ConfigManager ──────────────────────────────────────────────────────────────

class ConfigManager:
    """
    타일 다운로더 설정 관리.
    main_cfg 가 주어지면 메인 config.json 의 tile_downloader 섹션에 위임.
    """

    def __init__(self, main_cfg: "MainConfigManager | None" = None) -> None:
        self._cfg = main_cfg

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _td(self) -> dict:
        assert self._cfg is not None, "_td() called without main_cfg"
        cfg = self._cfg.config  
        if "tile_downloader" not in cfg or not isinstance(cfg["tile_downloader"], dict):
            cfg["tile_downloader"] = {"last": {}, "window_geometry": None}
        return cfg["tile_downloader"]

    # ── 마지막 사용값 ──────────────────────────────────────────────────────────

    def save_last(self, cfg: dict) -> None:
        """_collect_config_dict() 전체를 그대로 저장 (bbox 중첩 포함)."""
        if self._cfg is None:
            return
        self._td()["last"] = cfg
        self._cfg.schedule_save()


    def load_last(self) -> dict:
        """저장된 마지막 설정 반환. 없으면 빈 dict."""
        if self._cfg is None:
            return {}
        return dict(self._td().get("last", {}))

    # ── 창 위치/크기 ───────────────────────────────────────────────────────────

    def save_geometry(self, geom: bytes) -> None:
        if self._cfg is None:
            return
        self._td()["window_geometry"] = base64.b64encode(geom).decode("ascii")
        self._cfg.schedule_save()


    def load_geometry(self) -> bytes | None:
        if self._cfg is None:
            return None
        val = self._td().get("window_geometry")
        if not val:
            return None
        try:
            return base64.b64decode(val)
        except Exception:
            return None

    def flush(self) -> None:
        """앱 종료 등 즉각 디스크 반영이 필요할 때 호출."""
        if self._cfg is not None:
            self._cfg.save_immediate()


# ── 세션 히스토리 (별도 JSON — 항목당 크기가 커서 config.json 분리 유지) ─────────

def load_history() -> list[dict]:
    _ensure_dirs()
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def append_history(entry: dict) -> None:
    _ensure_dirs()
    history = load_history()
    entry.setdefault("id", str(uuid.uuid4()))
    entry.setdefault("recorded_at", datetime.now().isoformat())
    history.insert(0, entry)
    try:
        HISTORY_FILE.write_text(
            json.dumps(history[:MAX_HISTORY], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        error_print(f"[ConfigManager] 히스토리 저장 실패: {e}")


def delete_history_entry(entry_id: str) -> None:
    _ensure_dirs()
    history = [e for e in load_history() if e.get("id") != entry_id]
    try:
        HISTORY_FILE.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        error_print(f"[ConfigManager] 히스토리 삭제 실패: {e}")


# ── 프리셋 JSON (여러 파일 — config.json 분리 유지) ──────────────────────────────

def save_preset_json(name: str, config_dict: dict) -> Path:
    _ensure_dirs()
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
    path = PRESETS_DIR / f"{safe}.json"
    if path.exists():
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        uid = uuid.uuid4().hex[:4]
        path = PRESETS_DIR / f"{safe}_{ts}_{uid}.json"
    path.write_text(
        json.dumps({"name": name, "version": 1, "config": config_dict},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_preset_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(_t("preset.err_read", e=e)) from e

    if data.get("version") != 1:
        raise ValueError(_t("preset.err_version"))

    cfg = data.get("config", {})
    required = ["base_url", "style_id", "tile_format", "tile_size_mode",
                "z_min", "z_max", "concurrency", "out_root", "bbox"]
    for k in required:
        if k not in cfg:
            raise ValueError(_t("preset.err_field", k=k))

    try:
        z_min = int(cfg["z_min"])
        z_max = int(cfg["z_max"])
    except (ValueError, TypeError):
        raise ValueError(_t("preset.err_zoom_type",
                            z_min=cfg["z_min"], z_max=cfg["z_max"]))
    if not (0 <= z_min <= z_max <= 22):
        raise ValueError(_t("preset.err_zoom", z_min=z_min, z_max=z_max))

    if cfg["tile_format"] not in ("webp", "png", "jpg"):
        raise ValueError(_t("preset.err_format", fmt=cfg["tile_format"]))

    if cfg["tile_size_mode"] not in ("256", "@2x", "512"):
        raise ValueError(_t("preset.err_size_mode", mode=cfg["tile_size_mode"]))

    try:
        concurrency = int(cfg["concurrency"])
    except (ValueError, TypeError):
        raise ValueError(_t("preset.err_concurrency_type", val=cfg["concurrency"]))
    if not (1 <= concurrency <= 500):
        raise ValueError(_t("preset.err_concurrency", val=concurrency))

    return cfg


def list_preset_files() -> list[Path]:
    _ensure_dirs()
    return sorted(PRESETS_DIR.glob("*.json"))