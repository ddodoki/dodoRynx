# -*- coding: utf-8 -*-
# main.py


"""
dodoRynx
진입점 및 애플리케이션 초기화
"""


import os
import sys
from pathlib import Path
from typing import Optional


from PySide6.QtCore import Qt, QThreadPool, qInstallMessageHandler, QtMsgType, qVersion
from PySide6.QtGui import QColor, QPalette, QSurfaceFormat  
from PySide6.QtWidgets import QApplication
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox


from utils.app_meta import APP_AUTHOR, APP_NAME, APP_VERSION
from utils.debug import debug_print, error_print, info_print, warning_print, set_debug_mode, set_log_file
from utils.lang_manager import LangManager, t
from utils.paths import (
    get_cache_dir,
    get_user_data_dir,
    is_frozen,
    print_path_info,
)


# ============================================
# Qt 메시지 핸들러 (알려진 무해 경고 차단)
# ============================================


def _qt_message_handler(msg_type: QtMsgType, context, message: str) -> None:
    if "QFont::setPointSize: Point size <= 0" in message:
        return
    prefix = {
        QtMsgType.QtDebugMsg:    "[Qt Debug]",
        QtMsgType.QtInfoMsg:     "[Qt Info]",
        QtMsgType.QtWarningMsg:  "[Qt Warning]",
        QtMsgType.QtCriticalMsg: "[Qt Critical]",
        QtMsgType.QtFatalMsg:    "[Qt Fatal]",
    }.get(msg_type, "[Qt]")
    print(f"{prefix} {message}")


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

        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        dark = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(dark), ctypes.sizeof(dark)
        )

        DWMWA_CAPTION_COLOR = 35
        caption_color = ctypes.c_uint(0x001a1a1a)
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_CAPTION_COLOR,
            ctypes.byref(caption_color), ctypes.sizeof(caption_color)
        )

        DWMWA_TEXT_COLOR = 36
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
# 지도 초기화
# ============================================


def _init_map(config) -> None:
    from core.map_loader import configure_raster_tiles, configure_render_cache, get_raster_zoom_range
    from utils.paths import app_resources_dir

    render_mb = config.get('cache.render_memory_mb', 50)
    configure_render_cache(render_mb)
    info_print(f"렌더 캐시 크기: {render_mb}MB")

    tiles_dir_str = config.get('map.tiles_dir', str(app_resources_dir() / "tiles")).strip()
    p = Path(tiles_dir_str) if tiles_dir_str else (app_resources_dir() / "tiles")
    tms       = config.get('map.tms',       False)
    tile_size = config.get('map.tile_size', 256)

    info_print(f"[MAP 진단] tiles_dir = {p}")
    info_print(f"[MAP 진단] tms       = {tms}")
    info_print(f"[MAP 진단] tile_size = {tile_size}")
    info_print(f"[MAP 진단] dir_exists= {p.exists()}")

    if p.exists() and p.is_dir():
        configure_raster_tiles(p, tile_size=tile_size, tms=tms)
        mn, mx = get_raster_zoom_range()
        info_print(f"래스터 타일 경로 적용: {p}  줌범위={mn}~{mx}  tms={tms}  tile_size={tile_size}")
    else:
        warning_print(f"타일 디렉터리 없음: {p}")
        configure_raster_tiles(None, tile_size=tile_size, tms=tms)


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
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if not arg.startswith('--'):
            p = Path(arg)
            if p.exists() and p.is_file():
                return p
    return None


# ============================================
# 언어팩 로드
# ============================================


def _init_language(config) -> None:
    from utils.lang_manager import LangManager
    manager = LangManager.instance()

    lang_code = config.get('ui.language', 'auto')

    if lang_code == 'auto':
        lang_code = manager.detect_os_language()
        info_print(f"OS 언어 감지: {lang_code}")

    manager.load(lang_code)
    info_print(f"언어팩 로드 완료: {manager.current_name} ({manager.current_code})")


# ============================================
# 메인 함수
# ============================================


def main() -> int:
    try:
        initial_file = _extract_initial_file()

        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

        fmt = QSurfaceFormat()
        fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.TripleBuffer)
        fmt.setSwapInterval(0)
        QSurfaceFormat.setDefaultFormat(fmt)

        os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", " ".join([
            "--disable-partial-raster",
            "--force-gpu-rasterization",
            "--disable-checker-imaging",
            "--num-raster-threads=4",
            "--enable-surface-synchronization",
            "--gpu-rasterization-msaa-sample-count=0",
            "--memory-model=moderate",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--max-decoded-image-size-mb=256",
            "--js-flags=--max-old-space-size=512",
        ]))

        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setOrganizationName(APP_AUTHOR)
        app.setApplicationVersion(APP_VERSION)
        qInstallMessageHandler(_qt_message_handler)

        print_system_info(app)
        setup_dark_theme(app)
        setup_thread_pool()

        from utils.config_manager import ConfigManager
        from ui.main_window import MainWindow

        try:
            config = ConfigManager(parent=app)
            _init_language(config)
            _init_map(config)
            info_print(f"앱 디렉토리: {get_user_data_dir()}")
            info_print(f"캐시 디렉토리: {get_cache_dir()}")
        except Exception as e:
            error_print(f"설정 로드 실패: {e}")
            _DarkMessageBox(
                None, kind='danger',
                title=t('error.config_load_failed_title'),
                body=t('error.config_load_failed', error=e),
            ).exec()
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
            _DarkMessageBox(
                None, kind='danger',
                title=t('error.init_failed_title'),
                body=t('error.init_failed', error=e),
            ).exec()
            return 1

        try:
            if initial_file:
                window.open_image(initial_file)
            elif len(sys.argv) > 1:
                arg = sys.argv[1]
                if not arg.startswith('--'):
                    p = Path(arg)
                    if p.exists() and p.is_dir():
                        info_print(f"폴더 열기: {p}")
                        window.open_folder(p)
                    elif p.exists() and not p.is_file():
                        warning_print(f"지원하지 않는 경로 타입: {p}")
        except Exception as e:
            error_print(f"초기 경로 열기 실패: {e}")

        info_print("애플리케이션 시작")
        exit_code = app.exec()
        info_print(f"애플리케이션 종료: 코드={exit_code}")
        return exit_code

    except Exception as e:
        error_print(f"[CRITICAL] 치명적 오류: {e}")
        try:
            _DarkMessageBox(
                None, kind='danger',
                title=t('error.fatal_title'),
                body=t('error.fatal', error=e),
            ).exec()
        except Exception:
            print(f"[CRITICAL] 치명적 오류: {e}")
        return 1


# ============================================
# 진입점
# ============================================


if __name__ == "__main__":
    sys.exit(main())
