# -*- coding: utf-8 -*-
# utils/config_manager.py

"""
설정 파일 관리자
JSON 기반 설정 저장/로드
"""

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, QTimer

from utils.debug import debug_print, error_print, info_print, warning_print
from utils.paths import get_cache_dir as paths_get_cache_dir, get_user_data_dir


class ConfigManager(QObject):
    """
    앱 설정 관리 (JSON 파일 기반, 1초 debounce 자동 저장)

    접근 패턴
    ─────────
    • config.get("flat.key", default)          → 최상위 flat 키
    • config.set("flat.key", value)            → 최상위 flat 키 저장
    • config.get_overlay_setting(key, default) → overlay{} 중첩 키
    • config.get_rendering_setting(key)        → rendering{} 중첩 키
    • config.get_overlay_scale()               → overlay.scale flat 키 (50~200)
    • config.get_ui_visibility(element)        → ui{"show_{element}"}
    """

    DEFAULT_CONFIG: Dict[str, Any] = {
        # ── 캐시 (flat) ─────────────────────────────────────────
        "cache.ahead_count":     25,
        "cache.behind_count":    5,
        "cache.max_memory_mb":   700,
        "cache.thumb_memory_mb": 100,
        "cache.thumb_disk_mb":   500,
        "cache.render_memory_mb": 50, 

        # ── 렌더링 (nested) ──────────────────────────────────────
        "rendering": {
            "use_opengl":   False,
            "vsync":        False,
            "msaa_samples": 0,
        },

        # ── 애니메이션 (nested) ───────────────────────────────────
        "animation": {
            "scale_quality": "high",
            "cache_mode":    True,
            "webp_mode":     "fast",
        },

        # ── 기타 flat 키 ──────────────────────────────────────────
        "map.service":    "google",
        "browser.path":   "system_default",

        # ── 지도 (PMTiles) ────────────────────────────────────────
        "map.tiles_dir":  "",      
        "map.tms":        False,  

        # ── 뷰어 (flat) ───────────────────────────────────────────
        "viewer.wheel_delay_ms": 100,

        # ── 미니맵 (flat) ─────────────────────────────────────────
        "minimap.opacity": 0.8,

        # ── 오버레이 스케일 (flat) ────────────────────────────────
        "overlay.scale": 100,

        # ── 창 상태 (flat) ────────────────────────────────────────
        "window.geometry":      None,
        "window.state":         None,
        "window.splitter_sizes": None,

        # ── GPS 지도 동작 (nested) ────────────────────────────────
        "gps_map": {
            "auto_load":    False,
            "default_zoom": 15,
        },

        # ── UI 가시성 (nested) ────────────────────────────────────
        "ui": {
            "show_metadata":      False,
            "show_thumbnail_bar": True,
            "show_status_bar":    True,
            "show_perf_overlay":  False,
        },

        # ── 오버레이 정보 표시 (nested) ───────────────────────────
        "overlay": {
            "enabled":          False,
            "show_file_info":   True,
            "show_camera_info": True,
            "show_exif_info":   True,
            "show_lens_info":   True,
            "show_gps_info":    True,
            "show_map":         False,
            "opacity":          0.8,
            "position":         "top_left",
        },

        # ── 폴더 탐색기 (nested) ──────────────────────────────────
        "folder_explorer": {
            "favorites":     [],
            "last_path":     None,
            "panel_width":   220,
            "visible":       False,
            "empty_folders": [],
        },

        # ── UI 언어 (flat) ────────────────────────────────────────
        "ui.language": "auto",   

        # ── 타일 다운로더 ──────────────────────────────────────────
        "tile_downloader": {
            "last":            {},   
            "window_geometry": None,  
        },
    }


    def __init__(
        self, config_file: Optional[Path] = None, parent=None
    ) -> None:
        super().__init__(parent)
        if config_file is None:
            config_dir = get_user_data_dir()
            config_dir.mkdir(parents=True, exist_ok=True)
            self.config_file = config_dir / "config.json"
        else:
            self.config_file = config_file

        self.config = copy.deepcopy(self.DEFAULT_CONFIG)
        self.load()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._do_save)
        self._pending_save = False


    @staticmethod
    def get_cache_dir() -> Path:
        """Deprecated: utils.paths.get_cache_dir() 를 직접 사용하세요."""
        from utils.paths import get_cache_dir
        return get_cache_dir()
    
    # ─── 로드 / 저장 ─────────────────────────────────────────

    def load(self) -> None:
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._deep_update(self.config, loaded)
                self._migrate_config()  
                info_print(f"설정 로드: {self.config_file}")
            except Exception as e:
                warning_print(f"설정 로드 실패: {e}, 기본값 사용")
        else:
            info_print(f"설정 파일 없음 → 기본값 사용: {self.config_file}")
            self.save()


    def _migrate_config(self) -> None:
        _LEGACY_KEYS = (
            "cache.ofm_memory_mb", "cache.ofm_disk_mb", "cache.ofm_expiry_days",
            "map.pmtiles_path", "map.max_zoom",
            "map.default_zoom", 
        )
        removed = [k for k in _LEGACY_KEYS if k in self.config]
        for k in removed:
            del self.config[k]

        _migrated = bool(removed)

        gps_map    = self.config.get("gps_map", {})
        saved_zoom = gps_map.get("default_zoom", 9)
        MAX_ZOOM = 16
        if not isinstance(saved_zoom, int) or not (1 <= saved_zoom <= 16):
            corrected = max(1, min(saved_zoom, 16)) if isinstance(saved_zoom, int) else 15
            gps_map["default_zoom"] = corrected
            self.config["gps_map"] = gps_map
            warning_print(f"설정 마이그레이션: default_zoom {saved_zoom} → {corrected}")
            _migrated = True

        if _migrated:
            self.save() 


    def save(self) -> None:
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            info_print(f"설정 저장: {self.config_file}")
        except Exception as e:
            error_print(f"설정 저장 실패: {e}")


    def save_immediate(self) -> None:
        """debounce 없이 즉시 저장 (앱 종료 등)"""
        if self._save_timer.isActive():
            self._save_timer.stop()
        self.save()
        self._pending_save = False


    def schedule_save(self) -> None:
        """1초 debounce 자동 저장"""
        self._pending_save = True
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._save_timer.start(1000)


    def _do_save(self) -> None:
        if self._pending_save:
            self.save()
            self._pending_save = False


    def reset(self) -> None:
        self.config = copy.deepcopy(self.DEFAULT_CONFIG)
        self.save()
        info_print("설정 초기화 완료")

    # ─── 범용 get / set ──────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)


    def set(self, key: str, value: Any) -> None:
        self.config[key] = value

    # ─── UI 가시성 ───────────────────────────────────────────

    def get_ui_visibility(self, element: str) -> bool:
        return self.config.get("ui", {}).get(f"show_{element}", True)


    def set_ui_visibility(self, element: str, visible: bool) -> None:
        if "ui" not in self.config:
            self.config["ui"] = {}
        self.config["ui"][f"show_{element}"] = visible
        self.schedule_save()
        debug_print(f"UI 가시성: {element} = {visible}")

    # ─── 오버레이 ────────────────────────────────────────────

    def get_overlay_setting(self, key: str, default: Any = None) -> Any:
        return self.config.get("overlay", {}).get(key, default)


    def set_overlay_setting(self, key: str, value: Any) -> None:
        if "overlay" not in self.config:
            self.config["overlay"] = {}
        self.config["overlay"][key] = value
        self.schedule_save()
        debug_print(f"overlay.{key} = {value}")


    def get_overlay_scale(self) -> int:
        """오버레이 크기 스케일 (50~200, 기본 100)"""
        return int(self.config.get("overlay.scale", 100))


    def set_overlay_scale(self, value: int) -> None:
        """오버레이 크기 스케일 저장 (범위 자동 보정)"""
        self.config["overlay.scale"] = max(50, min(200, int(value)))
        self.schedule_save()
        debug_print(f"overlay.scale = {value}")

    # ─── 렌더링 ──────────────────────────────────────────────

    def get_rendering_setting(self, key: str, default: Any = None) -> Any:
        return self.config.get("rendering", {}).get(key, default)


    def set_rendering_setting(self, key: str, value: Any) -> None:
        if "rendering" not in self.config:
            self.config["rendering"] = {}
        self.config["rendering"][key] = value
        self.schedule_save()
        debug_print(f"rendering.{key} = {value}")

    # ─── GPS 지도 ────────────────────────────────────────────

    def get_gps_map_setting(self, key: str, default: Any = None) -> Any:
        return self.config.get("gps_map", {}).get(key, default)


    def set_gps_map_setting(self, key: str, value: Any) -> None:
        if "gps_map" not in self.config:
            self.config["gps_map"] = {}
        self.config["gps_map"][key] = value
        self.schedule_save()
        debug_print(f"gps_map.{key} = {value}")


    # ─── 폴더 탐색기 ───────────────────────────────────────────

    def get_folder_explorer_setting(self, key: str, default: Any = None) -> Any:
        return self.config.get("folder_explorer", {}).get(key, default)


    def set_folder_explorer_setting(self, key: str, value: Any) -> None:
        if "folder_explorer" not in self.config:
            self.config["folder_explorer"] = {}
        self.config["folder_explorer"][key] = value
        self.schedule_save()
        debug_print(f"folder_explorer.{key} = {value}")


    def is_folder_explorer_visible(self) -> bool:
        return self.get_folder_explorer_setting("visible", False)


    def set_folder_explorer_visible(self, visible: bool) -> None:
        self.set_folder_explorer_setting("visible", bool(visible))


    # ─── 내부 유틸 ───────────────────────────────────────────

    def _deep_update(self, target: Dict, source: Dict) -> None:
        """source 값을 target 에 재귀 병합 (기존 중첩 dict 보존)"""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._deep_update(target[key], value)
            else:
                target[key] = value
                