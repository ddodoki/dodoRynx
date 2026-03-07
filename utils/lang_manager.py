# -*- coding: utf-8 -*-
# utils/lang_manager.py

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from utils.debug import debug_print, error_print, warning_print
from utils.paths import get_langs_dir


class LangManager:
    """싱글톤 언어팩 매니저 (도메인별 분리 + 자동 병합)"""

    _instance: Optional['LangManager'] = None

    @classmethod
    def instance(cls) -> 'LangManager':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._langs_dir: Path    = get_langs_dir()
        self._translations: dict = {}
        self._fallback: dict     = {}
        self._current_code: str  = 'en'

    # ── 사용 가능한 언어 목록 ──────────────────────────────────────
    def get_available_languages(self) -> Dict[str, str]:
        """
        설치된 언어팩 목록 반환.
        단일 파일(ko.json)과 디렉터리(ko/) 방식을 모두 지원.
        """
        result: Dict[str, str] = {}
        if not self._langs_dir.exists():
            warning_print(f"langs 디렉토리 없음: {self._langs_dir}")
            return result

        # 단일 파일 방식: ko.json
        for path in sorted(self._langs_dir.glob('*.json')):
            code = path.stem
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                name = data.get('meta', {}).get('language', code)
            except Exception as e:
                warning_print(f"언어팩 읽기 실패 ({path.name}): {e}")
                name = code
            result[code] = name

        # 디렉터리 방식: ko/common.json 등
        for lang_dir in sorted(self._langs_dir.iterdir()):
            if not lang_dir.is_dir():
                continue
            code = lang_dir.name
            if code in result:          # 단일 파일이 이미 등록된 경우 스킵
                continue
            # meta.json 또는 common.json에서 언어명 추출 시도
            name = code
            for meta_candidate in ['meta.json', 'common.json']:
                mp = lang_dir / meta_candidate
                if mp.exists():
                    try:
                        with open(mp, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        name = data.get('meta', {}).get('language', code)
                        break
                    except Exception:
                        pass
            result[code] = name

        return result

    # ── OS 언어 자동 감지 (변경 없음) ─────────────────────────────
    def detect_os_language(self) -> str:
        code = self._get_os_lang_code()
        available = self.get_available_languages()
        if code in available:
            return code
        prefix = code[:2].lower()
        for avail_code in available:
            if avail_code.startswith(prefix):
                return avail_code
        return 'en'

    def _get_os_lang_code(self) -> str:
        try:
            if sys.platform == 'win32':
                import ctypes
                lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
                primary = lang_id & 0x00FF
                WIN_LANG_MAP = {
                    0x09: 'en', 0x12: 'ko', 0x11: 'ja',
                    0x04: 'zh_CN', 0x1C: 'zh_TW', 0x07: 'de',
                    0x0C: 'fr', 0x0A: 'es', 0x10: 'it',
                    0x19: 'ru', 0x1D: 'sv', 0x13: 'nl',
                    0x16: 'pt', 0x1F: 'tr',
                }
                return WIN_LANG_MAP.get(primary, 'en')
            else:
                import locale
                loc = locale.getdefaultlocale()[0] or 'en_US'
                return loc[:2].lower()
        except Exception as e:
            warning_print(f"OS 언어 감지 실패: {e}")
            return 'en'

    # ── 언어팩 로드 ───────────────────────────────────────────────
    def load(self, lang_code: str) -> bool:
        """
        단일 파일(ko.json) 또는 디렉터리(ko/) 중 존재하는 쪽을 자동 선택.
        항상 English fallback을 먼저 로드하고 그 위에 오버레이.
        """
        self._fallback = self._load_lang('en') or {}

        if lang_code == 'en':
            self._translations = self._fallback
            self._current_code = 'en'
            debug_print("언어팩 로드: English (기본)")
            return True

        data = self._load_lang(lang_code)
        if data:
            self._translations = data
            self._current_code = lang_code
            debug_print(f"언어팩 로드: {lang_code}")
            return True
        else:
            self._translations = self._fallback
            self._current_code = 'en'
            warning_print(f"언어팩 없음: {lang_code} → English 사용")
            return False

    def _load_lang(self, code: str) -> Optional[dict]:
        """
        단일 파일 우선, 없으면 디렉터리 방식으로 로드.
        디렉터리 방식이면 모든 JSON을 깊은 병합(deep merge)으로 합침.
        """
        # 1. 단일 파일 시도
        single = self._langs_dir / f'{code}.json'
        if single.exists():
            return self._load_file(single)

        # 2. 디렉터리 방식 시도
        lang_dir = self._langs_dir / code
        if lang_dir.is_dir():
            return self._load_directory(lang_dir)

        return None

    def _load_file(self, path: Path) -> Optional[dict]:
        """단일 JSON 파일 로드"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            error_print(f"언어팩 파싱 실패 ({path.name}): {e}")
            return None

    def _load_directory(self, lang_dir: Path) -> dict:
        """
        디렉터리 내 모든 *.json을 로드해 깊은 병합.
        파일명 순서대로 병합 (common.json → dialogs.json → ... 알파벳 순)
        """
        merged: dict = {}
        files = sorted(lang_dir.glob('*.json'))

        if not files:
            warning_print(f"언어팩 디렉터리가 비어 있음: {lang_dir}")
            return merged

        for path in files:
            data = self._load_file(path)
            if data:
                self._deep_merge(merged, data)
                debug_print(f"  병합: {path.name}")

        debug_print(f"디렉터리 언어팩 병합 완료: {lang_dir.name}/ ({len(files)}개)")
        return merged

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> None:
        """
        override를 base에 재귀적으로 병합 (in-place).
        같은 키가 있으면 override 값이 이깁니다.
        """
        for key, value in override.items():
            if (
                key in base
                and isinstance(base[key], dict)
                and isinstance(value, dict)
            ):
                LangManager._deep_merge(base[key], value)
            else:
                base[key] = value

    # ── 번역 조회 (변경 없음) ─────────────────────────────────────
    def t(self, key: str, **kwargs) -> str:
        value = self._nested_get(self._translations, key)
        if value is None:
            value = self._nested_get(self._fallback, key)
        if value is None:
            debug_print(f"[i18n] 누락 키: '{key}'")
            return key
        if kwargs:
            try:
                return value.format(**kwargs)
            except (KeyError, IndexError):
                return value
        return value

    @staticmethod
    def _nested_get(data: dict, key: str) -> Optional[str]:
        parts = key.split('.')
        node: Any = data
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node if isinstance(node, str) else None

    @property
    def current_code(self) -> str:
        return self._current_code

    @property
    def current_name(self) -> str:
        langs = self.get_available_languages()
        return langs.get(self._current_code, self._current_code)


def t(key: str, **kwargs) -> str:
    return LangManager.instance().t(key, **kwargs)
