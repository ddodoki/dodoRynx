# -*- coding: utf-8 -*-
# main.py

"""
dodoRynx
진입점 및 애플리케이션 초기화
"""

import os
import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QThreadPool, qVersion
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from utils.app_meta import APP_AUTHOR, APP_NAME, APP_VERSION
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import LangManager, t
from utils.paths import (
    get_cache_dir,
    get_user_data_dir,
    is_frozen,
    print_path_info,
)

if TYPE_CHECKING:
    from ui.main_window import MainWindow


# ============================================
# 다크 테마 설정
# ============================================

def setup_dark_theme(app: QApplication) -> None:
    dark_palette = QPalette()
    dark_color = QColor(26, 26, 26)

    dark_palette.setColor(QPalette.ColorRole.Window, dark_color)
    dark_palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.Base, QColor(20, 20, 20))
    dark_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(42, 42, 42))
    dark_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.Button, QColor(42, 42, 42))
    dark_palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    dark_palette.setColor(QPalette.ColorRole.Link, QColor(74, 158, 255))
    dark_palette.setColor(QPalette.ColorRole.Highlight, QColor(74, 158, 255))
    dark_palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    dark_palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Window, dark_color)
    dark_palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Window, dark_color)

    app.setPalette(dark_palette)
    app.setStyleSheet("""
        QWidget {
            background-color: #1a1a1a;
            color: #ffffff;
        }
        QMainWindow {
            background-color: #1a1a1a;
        }
        QToolTip {
            background-color: #2b2b2b;
            color: #ffffff;
            border: 1px solid #555;
            border-radius: 4px;
            padding: 5px 8px;
            font-size: 11px;
        }
    """)
    info_print("다크 테마 적용 완료")


# ============================================
# Windows 타이틀바 다크모드 강제 적용
# ============================================

def _apply_windows_dark_titlebar(hwnd: int) -> None:
    if sys.platform != 'win32':
        return
    try:
        import ctypes

        dwmapi = ctypes.windll.dwmapi

        # 1단계: 다크모드 활성화 (텍스트/버튼 색상을 밝게)
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        dark = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(dark), ctypes.sizeof(dark)
        )

        # 2단계: 타이틀바 배경색 고정 (Windows 11 22000+)
        # COLORREF 형식: 0x00BBGGRR
        DWMWA_CAPTION_COLOR = 35
        # #1a1a1a → R=0x1a, G=0x1a, B=0x1a → COLORREF = 0x001a1a1a
        caption_color = ctypes.c_uint(0x001a1a1a)
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_CAPTION_COLOR,
            ctypes.byref(caption_color), ctypes.sizeof(caption_color)
        )

        # 3단계: 타이틀 텍스트 색상 고정 (Windows 11 22000+)
        DWMWA_TEXT_COLOR = 36
        # #ffffff → 0x00ffffff
        text_color = ctypes.c_uint(0x00ffffff)
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_TEXT_COLOR,
            ctypes.byref(text_color), ctypes.sizeof(text_color)
        )

    except Exception as e:
        warning_print(f"타이틀바 색상 설정 실패: {e}")
        

# ============================================
# 스레드 풀 설정
# ============================================

def setup_thread_pool() -> None:
    thread_pool = QThreadPool.globalInstance()
    cpu_count = os.cpu_count() or 4
    max_threads = min(max(2, cpu_count // 2), 8)
    thread_pool.setMaxThreadCount(max_threads)
    info_print(f"스레드 풀: {max_threads}개 (CPU: {cpu_count}개)")


# ============================================
# 시스템 정보 출력
# ============================================

def print_system_info(app: QApplication) -> None:
    info_print("========================================")
    info_print(f"{APP_NAME} v{APP_VERSION}")
    info_print("========================================")
    info_print(f"Python: {sys.version.split()[0]}")
    info_print(f"Qt: {qVersion()}")
    info_print(f"플랫폼: {sys.platform}")
    info_print(f"환경: {'배포' if is_frozen() else '개발'}")
    info_print("========================================")
    if '--debug' in sys.argv:
        print_path_info()


# ============================================
# 초기 파일 조기 추출
# ============================================

def _extract_initial_file() -> Optional[Path]:
    """sys.argv에서 파일 경로 조기 추출 (QApplication 생성 전)"""
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if not arg.startswith('--'):
            p = Path(arg)
            if p.exists() and p.is_file():
                return p
    return None


# ============================================
# 커맨드라인 인자 처리 (폴더 열기 전용)
# 타입 힌트를 문자열로 → 런타임 NameError 방지
# ============================================

def handle_command_line_args(window: 'MainWindow') -> None:
    if len(sys.argv) > 1:
        arg_path = sys.argv[1]
        if arg_path.startswith('--'):
            return
        try:
            file_path = Path(arg_path)
            if not file_path.exists():
                warning_print(f"경로가 존재하지 않음: {file_path}")
                return

            if file_path.is_dir():
                info_print(f"폴더 열기: {file_path}")
                window.open_folder(file_path)
            elif not file_path.is_file():
                warning_print(f"지원하지 않는 경로 타입: {file_path}")
        except Exception as e:
            error_print(f"커맨드라인 인자 처리 실패: {e}")


# ============================================
# 언어팩 로드
# ============================================

def _init_language(config) -> None:
    """설정에 따라 언어팩 로드"""
    from utils.lang_manager import LangManager
    manager = LangManager.instance()

    lang_code = config.get('ui.language', 'auto')

    if lang_code == 'auto':
        lang_code = manager.detect_os_language()
        info_print(f"OS 언어 감지: {lang_code}")

    manager.load(lang_code)
    info_print(f"언어팩 로드 완료: {manager.current_name} ({manager.current_code})")


# ============================================
# 메인 함수 (단일 정의)
# ============================================

def main() -> int:
    try:
        initial_file = _extract_initial_file()

        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setOrganizationName(APP_AUTHOR)
        app.setApplicationVersion(APP_VERSION)

        print_system_info(app)
        setup_dark_theme(app)
        setup_thread_pool()

        from utils.config_manager import ConfigManager
        from ui.main_window import MainWindow

        try:
            config = ConfigManager(parent=app)
            _init_language(config)
            info_print(f"앱 디렉토리: {get_user_data_dir()}")
            info_print(f"캐시 디렉토리: {get_cache_dir()}")
        except Exception as e:
            error_print(f"설정 로드 실패: {e}")
            QMessageBox.critical(
                None,
                t('error.config_load_failed_title'),
                t('error.config_load_failed', error=e),
            )
            config = ConfigManager(parent=app)

        try:
            window = MainWindow(config)
            window.show()
            app.processEvents() 

            if sys.platform == 'win32':
                hwnd = int(window.winId())
                _apply_windows_dark_titlebar(hwnd)

            info_print("메인 윈도우 생성 완료")
        except Exception as e:
            error_print(f"메인 윈도우 생성 실패: {e}")
            QMessageBox.critical(
                None,
                t('error.init_failed_title'),
                t('error.init_failed', error=e),
            )
            return 1

        try:
            if initial_file:
                window.open_image(initial_file) 
            else:
                handle_command_line_args(window)  
        except Exception as e:
            error_print(f"초기 파일 열기 예외: {e}")

        info_print("애플리케이션 시작")
        exit_code = app.exec()
        info_print(f"애플리케이션 종료: 코드={exit_code}")
        return exit_code

    except Exception as e:
        error_print(f"[CRITICAL] 치명적 오류: {e}")
        try:
            QMessageBox.critical(
                None,
                t('error.fatal_title'),
                t('error.fatal', error=e),
            )
        except Exception:
            print(f"[CRITICAL] 치명적 오류: {e}")
        return 1


# ============================================
# 진입점
# ============================================

if __name__ == "__main__":
    sys.exit(main())
