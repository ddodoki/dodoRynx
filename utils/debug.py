# -*- coding: utf-8 -*-
# utils/debug.py

"""
디버그 및 로깅 유틸리티
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TextIO


# ========================================
# 전역 설정
# ========================================

# 디버그 모드 (런타임에 변경 가능)
DEBUG = False

# 로그 파일 (None이면 파일 로깅 비활성화)
LOG_FILE: Optional[Path] = None
_log_file_handle: Optional[TextIO] = None

# 타임스탬프 표시 여부
SHOW_TIMESTAMP = True


# ========================================
# 초기화
# ========================================

def set_debug_mode(enabled: bool) -> None:
    """
    디버그 모드 설정
    
    Args:
        enabled: True이면 디버그 출력 활성화
    """
    global DEBUG
    DEBUG = enabled
    info_print(f"디버그 모드: {'활성화' if enabled else '비활성화'}")


def set_log_file(log_path: Optional[Path]) -> None:
    """
    로그 파일 설정
    
    Args:
        log_path: 로그 파일 경로 (None이면 파일 로깅 비활성화)
    """
    global LOG_FILE, _log_file_handle
    
    # 기존 파일 핸들 닫기
    if _log_file_handle:
        _log_file_handle.close()
        _log_file_handle = None
    
    LOG_FILE = log_path
    
    if LOG_FILE:
        try:
            # 로그 디렉토리 생성
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            
            # 파일 열기 (append 모드)
            _log_file_handle = open(LOG_FILE, 'a', encoding='utf-8')
            
            info_print(f"로그 파일 설정: {LOG_FILE}")
        except Exception as e:
            print(f"[ERROR] 로그 파일 열기 실패: {e}", file=sys.stderr)
            LOG_FILE = None


def close_log_file() -> None:
    """로그 파일 닫기"""
    global _log_file_handle
    
    if _log_file_handle:
        _log_file_handle.close()
        _log_file_handle = None


# ========================================
# 내부 헬퍼 함수
# ========================================

def _get_timestamp() -> str:
    """현재 시각을 문자열로 반환"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # 밀리초까지


def _format_message(level: str, *args: Any, **kwargs: Any) -> str:
    """
    로그 메시지 포맷팅
    
    Args:
        level: 로그 레벨 (DEBUG, INFO, WARN, ERROR 등)
        *args: 출력할 내용
        **kwargs: print의 키워드 인자
    
    Returns:
        포맷된 문자열
    """
    # args를 문자열로 변환
    message_parts = [str(arg) for arg in args]
    message = kwargs.get('sep', ' ').join(message_parts)
    
    # 타임스탬프 추가
    if SHOW_TIMESTAMP:
        timestamp = _get_timestamp()
        return f"[{level}] {message}"
        #return f"[{timestamp}] [{level}] {message}"
    else:
        return f"[{level}] {message}"


def _write_log(formatted_message: str) -> None:
    """
    로그를 콘솔 및 파일에 출력
    
    Args:
        formatted_message: 포맷된 로그 메시지
    """
    # 콘솔 출력
    print(formatted_message)
    
    # 파일 출력
    if _log_file_handle:
        try:
            _log_file_handle.write(formatted_message + '\n')
            _log_file_handle.flush()  # 즉시 디스크에 쓰기
        except Exception as e:
            print(f"[ERROR] 로그 파일 쓰기 실패: {e}", file=sys.stderr)


# ========================================
# 로깅 함수
# ========================================

def debug_print(*args: Any, **kwargs: Any) -> None:
    """
    디버그 모드일 때만 출력
    
    Args:
        *args: 출력할 내용
        **kwargs: print의 키워드 인자
    """
    if DEBUG:
        formatted = _format_message("DEBUG", *args, **kwargs)
        _write_log(formatted)


def info_print(*args: Any, **kwargs: Any) -> None:
    """
    정보 메시지 출력 (항상)
    
    Args:
        *args: 출력할 내용
        **kwargs: print의 키워드 인자
    """
    formatted = _format_message("INFO", *args, **kwargs)
    _write_log(formatted)


def warning_print(*args: Any, **kwargs: Any) -> None:
    """
    경고 메시지 출력 (항상)
    
    Args:
        *args: 출력할 내용
        **kwargs: print의 키워드 인자
    """
    formatted = _format_message("WARN", *args, **kwargs)
    _write_log(formatted)


def error_print(*args: Any, **kwargs: Any) -> None:
    """
    에러 메시지 출력 (항상, stderr)
    
    Args:
        *args: 출력할 내용
        **kwargs: print의 키워드 인자
    """
    formatted = _format_message("ERROR", *args, **kwargs)
    
    # 콘솔 출력 (stderr)
    print(formatted, file=sys.stderr)
    
    # 파일 출력
    if _log_file_handle:
        try:
            _log_file_handle.write(formatted + '\n')
            _log_file_handle.flush()
        except Exception as e:
            print(f"[ERROR] 로그 파일 쓰기 실패: {e}", file=sys.stderr)


def success_print(*args: Any, **kwargs: Any) -> None:
    """
    성공 메시지 출력
    
    Args:
        *args: 출력할 내용
        **kwargs: print의 키워드 인자
    """
    formatted = _format_message("SUCCESS", *args, **kwargs)
    _write_log(formatted)


def critical_print(*args: Any, **kwargs: Any) -> None:
    """
    치명적 에러 메시지 출력 (항상, stderr)
    
    Args:
        *args: 출력할 내용
        **kwargs: print의 키워드 인자
    """
    formatted = _format_message("CRITICAL", *args, **kwargs)
    
    # 콘솔 출력 (stderr)
    print(formatted, file=sys.stderr)
    
    # 파일 출력
    if _log_file_handle:
        try:
            _log_file_handle.write(formatted + '\n')
            _log_file_handle.flush()
        except Exception as e:
            print(f"[ERROR] 로그 파일 쓰기 실패: {e}", file=sys.stderr)


# ========================================
# 타이머
# ========================================

@contextmanager
def timer(name: str, log_level: str = "DEBUG"):
    """
    타이머 컨텍스트 매니저
    
    Args:
        name: 작업 이름
        log_level: 로그 레벨 (DEBUG, INFO 등)
    
    Example:
        >>> with timer("image_load"):
        ...     load_image()
    """
    start = time.perf_counter()
    
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        elapsed_ms = elapsed * 1000
        
        message = f"[{name}] {elapsed_ms:.1f}ms"
        
        # 로그 레벨에 따라 출력
        if log_level == "DEBUG":
            debug_print(message)
        elif log_level == "INFO":
            info_print(message)
        elif log_level == "WARN":
            warning_print(message)
        else:
            info_print(message)


# ========================================
# 초기화 (app_meta에서 DEBUG 읽기)
# ========================================

def _init_from_app_meta() -> None:
    """app_meta에서 DEBUG 설정 읽기 (순환 import 방지)"""
    global DEBUG
    
    try:
        # 함수 내부에서 import (순환 import 방지)
        from utils import app_meta
        DEBUG = app_meta.DEBUG
    except ImportError:
        # app_meta가 없으면 기본값 사용
        DEBUG = False

# 모듈 import 시 초기화
_init_from_app_meta()
