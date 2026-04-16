# -*- coding: utf-8 -*-
# utils/paths.py

"""
경로 관리 유틸리티
개발/배포 환경 자동 감지
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from utils.debug import debug_print, error_print, info_print, warning_print


# ============================================
# 환경 감지
# ============================================

def is_frozen() -> bool:
    """
    PyInstaller로 패키징된 실행 파일인지 확인
    
    Returns:
        True: 배포 환경 (exe)
        False: 개발 환경 (Python 스크립트)
    """
    return getattr(sys, "frozen", False)


# ============================================
# 기본 경로
# ============================================

def exe_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    else:
        # os.getcwd() 대신 파일 기준 고정 경로 사용
        return Path(__file__).resolve().parent.parent   # utils/ → project_root


def lib_dir() -> Path:
    """
    PyInstaller --onedir --contents-directory lib => /lib
    
    Returns:
        lib 디렉토리
    """
    return exe_dir() / "lib"


def lib_app_dir() -> Path:
    """
    앱 리소스 폴더 (내부 데이터)
    
    배포 환경: /lib/app/
    
    Returns:
        lib/app 디렉토리
    """
    return lib_dir() / "app"


def lib_licenses_dir() -> Path:
    """
    라이선스 폴더 (패키징된 라이선스 파일)
    
    배포 환경: /lib/Licenses/
    
    Returns:
        lib/Licenses 디렉토리
    """
    return lib_dir() / "Licenses"


# ============================================
# 리소스 경로
# ============================================

def app_resources_dir() -> Path:
    """
    앱 리소스 폴더 (아이콘, 로고 등)
    
    개발 환경: /resources/
    배포 환경: /lib/app/
    
    Returns:
        리소스 디렉토리
    """
    if is_frozen():
        # 배포 환경
        path = lib_app_dir()
    else:
        # 개발 환경
        path = Path(__file__).resolve().parent.parent / "resources"
    
    #debug_print(f"app_resources_dir: {path}")
    return path


def get_icon_path(icon_name: str) -> Path:
    """
    아이콘 파일 경로 반환
    
    Args:
        icon_name: "icon.png", "icon.ico", "logo.png" 등
    
    Returns:
        아이콘 파일 절대 경로
    
    Example:
        >>> get_icon_path("icon.png")
        PosixPath('/path/to/resources/icon.png')
    """
    path = app_resources_dir() / icon_name
    
    if not path.exists():
        warning_print(f"아이콘 파일이 존재하지 않음: {path}")
    
    return path


def get_licenses_dir() -> Path:
    """
    라이선스 폴더 경로 반환
    
    개발 환경: /Licenses/ (프로젝트 루트)
    배포 환경: /Licenses/ (exe 경로)
    
    Returns:
        라이선스 디렉토리
    """
    if is_frozen():
        # 배포 환경: exe 경로의 Licenses
        path = exe_dir() / "Licenses"
    else:
        # 개발 환경: 프로젝트 루트의 Licenses
        path = Path(__file__).resolve().parent.parent / "Licenses"
    
    debug_print(f"licenses_dir: {path}")
    return path


# ============================================
# 유틸리티
# ============================================

def ensure_dir(p: Path) -> Path:
    """
    폴더 생성 (없으면 생성)
    
    Args:
        p: 생성할 폴더 경로
    
    Returns:
        생성된 폴더 경로
    """
    try:
        p.mkdir(parents=True, exist_ok=True)
        debug_print(f"폴더 확인/생성: {p}")
    except Exception as e:
        error_print(f"폴더 생성 실패: {p}, 에러: {e}")
    
    return p


def ensure_licenses_in_exe_dir() -> None:
    """
    프로그램 최초 실행 시 Licenses 폴더 복사
    
    배포 환경에서만 동작:
    - exe 경로에 Licenses 폴더가 없으면 /lib/Licenses에서 복사
    - 사용자가 쉽게 라이선스 파일을 확인할 수 있도록 함
    """
    # 배포 환경이 아니면 무시
    if not is_frozen():
        debug_print(f"개발 환경 - Licenses 복사 스킵")
        return
    
    dst = exe_dir() / "Licenses"
    
    # 이미 존재하면 무시
    if dst.exists():
        debug_print(f"Licenses 폴더 이미 존재: {dst}")
        return
    
    src = lib_licenses_dir()
    
    # 소스가 없으면 무시
    if not src.exists():
        warning_print(f"소스 Licenses 폴더 없음: {src}")
        return
    
    try:
        # 폴더 복사
        shutil.copytree(src, dst, dirs_exist_ok=True)
        info_print(f"Licenses 폴더 복사: {src} → {dst}")
    except Exception as e:
        error_print(f"Licenses 폴더 복사 실패: {e}")

def norm_path(p: "Path | str") -> str:
    return str(p).replace("\\", "/")

# ============================================
# 경로 검증
# ============================================

def validate_path(path: Path, must_exist: bool = False) -> bool:
    """
    경로 유효성 검증
    
    Args:
        path: 검증할 경로
        must_exist: True이면 존재 여부도 확인
    
    Returns:
        유효 여부
    """
    if not isinstance(path, Path):
        error_print(f"Path 객체가 아님: {type(path)}")
        return False
    
    if must_exist and not path.exists():
        warning_print(f"경로가 존재하지 않음: {path}")
        return False
    
    return True


def get_relative_path(path: Path, base: Optional[Path] = None) -> Path:
    """
    절대 경로를 상대 경로로 변환
    
    Args:
        path: 변환할 경로
        base: 기준 경로 (None이면 exe_dir 사용)
    
    Returns:
        상대 경로 (변환 실패 시 원본 경로)
    """
    if base is None:
        base = exe_dir()
    
    try:
        return path.relative_to(base)
    except ValueError:
        # 상대 경로 변환 불가 (다른 드라이브 등)
        return path


# ============================================
# 디버그/정보 출력
# ============================================

def print_path_info() -> None:
    """
    경로 정보 출력 (디버깅용)
    """
    info_print(f"[PATH INFO] ========== 경로 정보 ==========")
    info_print(f"[PATH INFO] 환경: {'배포' if is_frozen() else '개발'}")
    info_print(f"[PATH INFO] exe_dir: {exe_dir()}")
    info_print(f"[PATH INFO] lib_dir: {lib_dir()}")
    info_print(f"[PATH INFO] lib_app_dir: {lib_app_dir()}")
    info_print(f"[PATH INFO] app_resources_dir: {app_resources_dir()}")
    info_print(f"[PATH INFO] licenses_dir: {get_licenses_dir()}")
    info_print(f"[PATH INFO] ========================================")


# ============================================
# 모듈 초기화 시 실행
# ============================================

# 배포 환경에서 자동으로 Licenses 폴더 복사
if is_frozen():
    try:
        ensure_licenses_in_exe_dir()
    except Exception as e:
        error_print(f"모듈 초기화 중 Licenses 복사 실패: {e}")


# ============================================
# 사용자 데이터 경로 (설정·캐시) — 단일 정의 지점
# ============================================

def get_user_data_dir() -> Path:
    """
    사용자 데이터 루트 디렉토리.

    개발 환경: <project_root>/.dodoRynx/
    배포 환경: <exe_dir>/lib/app/
    """
    if is_frozen():
        return lib_app_dir()          # exe_dir() / "lib" / "app"
    # paths.py 위치: utils/paths.py → parent.parent = 프로젝트 루트
    project_root = Path(__file__).resolve().parent.parent
    return project_root / ".dodoRynx"

def get_config_dir() -> Path:
    """설정 파일 디렉토리."""
    return get_user_data_dir()

def get_cache_dir() -> Path:
    """캐시 루트 디렉토리."""
    return get_user_data_dir() / "cache"

def get_thumb_cache_dir() -> Path:
    """썸네일 캐시 디렉토리."""
    return get_cache_dir() / "thumbnails"

def get_tile_cache_dir() -> Path:
    """지도 타일 캐시 디렉토리."""
    return get_cache_dir() / "tiles"

# ============================================
# 언어 팩
# ============================================

def get_langs_dir() -> Path:
    """언어팩 디렉토리 (배포/개발 환경 자동 분기)"""
    if is_frozen():
        # PyInstaller 배포 환경: exe 옆의 langs/
        return Path(sys.executable).parent / 'lib' / 'langs'
    else:
        # 개발 환경: 프로젝트 루트의 langs/
        return Path(__file__).parent.parent / 'langs'
    