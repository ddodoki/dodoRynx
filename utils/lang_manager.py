# -*- coding: utf-8 -*-
# utils/lang_manager.py

"""
언어팩 싱글톤 매니저
사용법: from utils.lang_manager import t
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from utils.debug import debug_print, error_print, warning_print
from utils.paths import get_langs_dir


class LangManager:
    """싱글톤 언어팩 매니저"""

    _instance: Optional['LangManager'] = None

    # ── 싱글톤 ──────────────────────────────────────────────────
    @classmethod
    def instance(cls) -> 'LangManager':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._langs_dir: Path      = get_langs_dir()
        self._translations: dict   = {}   # 현재 언어 데이터
        self._fallback: dict       = {}   # 영어 데이터 (항상 로드)
        self._current_code: str    = 'en'

    # ── 사용 가능한 언어 목록 ─────────────────────────────────────
    def get_available_languages(self) -> Dict[str, str]:
        """
        설치된 언어팩 목록 반환.
        Returns: {코드: 언어 표시명}  예) {'en': 'English', 'ko': '한국어'}
        """
        result: Dict[str, str] = {}
        if not self._langs_dir.exists():
            warning_print(f"langs 디렉토리 없음: {self._langs_dir}")
            return result

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

        return result

    # ── OS 언어 자동 감지 ─────────────────────────────────────────
    def detect_os_language(self) -> str:
        """
        OS 기본 언어 코드 반환.
        언어팩이 없으면 'en' 반환.
        """
        code = self._get_os_lang_code()
        available = self.get_available_languages()

        # 정확히 일치하는 코드가 있으면 사용
        if code in available:
            return code

        # 앞 2글자만 비교 (zh_CN → zh 계열 검색)
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
                primary = lang_id & 0x00FF  # 주 언어 ID
                # https://docs.microsoft.com/en-us/windows/win32/intl/language-identifier-constants-and-strings
                WIN_LANG_MAP = {
                    0x09: 'en',    # English
                    0x12: 'ko',    # Korean
                    0x11: 'ja',    # Japanese
                    0x04: 'zh_CN', # Chinese (Simplified)
                    0x1C: 'zh_TW', # Chinese (Traditional)
                    0x07: 'de',    # German
                    0x0C: 'fr',    # French
                    0x0A: 'es',    # Spanish
                    0x10: 'it',    # Italian
                    0x19: 'ru',    # Russian
                    0x1D: 'sv',    # Swedish
                    0x13: 'nl',    # Dutch
                    0x16: 'pt',    # Portuguese
                    0x1F: 'tr',    # Turkish
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
        언어팩 로드.
        항상 English fallback을 먼저 로드하고, 그 위에 지정 언어를 오버레이.
        Returns: 성공 여부
        """
        # 1. English fallback 로드 (항상)
        self._fallback = self._load_file('en') or {}

        # 2. 지정 언어 로드
        if lang_code == 'en':
            self._translations = self._fallback
            self._current_code = 'en'
            debug_print(f"언어팩 로드: English (기본)")
            return True

        data = self._load_file(lang_code)
        if data:
            self._translations = data
            self._current_code = lang_code
            debug_print(f"언어팩 로드: {lang_code}")
            return True
        else:
            # 해당 언어팩 없음 → English로 fallback
            self._translations = self._fallback
            self._current_code = 'en'
            warning_print(f"언어팩 없음: {lang_code} → English 사용")
            return False

    def _load_file(self, code: str) -> Optional[dict]:
        path = self._langs_dir / f'{code}.json'
        if not path.exists():
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            error_print(f"언어팩 파싱 실패 ({code}.json): {e}")
            return None

    # ── 번역 조회 ─────────────────────────────────────────────────
    def t(self, key: str, **kwargs) -> str:
        """
        번역 문자열 반환.

        우선순위: 현재 언어 → English fallback → key 그대로 반환

        사용법:
            t('settings.title')                  → "설정"
            t('status.zoom', zoom=150)           → "확대: 150%"
            t('error.file_not_found', path=p)    → "파일 없음: /path/to/file"
        """
        # 현재 언어에서 조회
        value = self._nested_get(self._translations, key)

        # fallback (영어)에서 조회
        if value is None:
            value = self._nested_get(self._fallback, key)

        # 키 자체 반환 (개발 중 누락 키 식별용)
        if value is None:
            debug_print(f"[i18n] 누락 키: '{key}'")
            return key

        # 포맷 적용
        if kwargs:
            try:
                return value.format(**kwargs)
            except (KeyError, IndexError):
                return value

        return value

    @staticmethod
    def _nested_get(data: dict, key: str) -> Optional[str]:
        """
        점 표기법으로 중첩 dict 조회.
        'settings.cache.title' → data['settings']['cache']['title']
        """
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


# ── 전역 편의 함수 ────────────────────────────────────────────────
def t(key: str, **kwargs) -> str:
    """어디서든 한 줄로 번역 문자열 접근."""
    return LangManager.instance().t(key, **kwargs)
