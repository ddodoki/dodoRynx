# -*- coding: utf-8 -*-
# ui\viewer\viewer_setup_mixin.py
"""
ViewerSetupMixin — OpenGL 초기화, 스크롤바 스타일, 뷰 유틸리티 묶음.

사용하는 클래스에서 반드시 다음이 정의되어 있어야 합니다:
  - 속성:  config_manager, pixmap_item, current_pixmap, current_movie
  - 메서드: viewport(), mapFromScene(), setViewport(), setStyleSheet()
"""

from pathlib import Path
from typing import Optional, Tuple

from PIL import Image as PILImage

from PySide6.QtCore import QRect
from PySide6.QtGui import QPixmap, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget


from utils.debug import error_print, info_print, warning_print


class ViewerSetupMixin:

    # ============================================
    # OpenGL 설정
    # ============================================

    def _setup_opengl(self) -> None:
        """OpenGL 렌더링 설정. __init__ 에서 호출."""
        use_opengl = self.config_manager.get_rendering_setting('use_opengl', True)   # type: ignore[attr-defined]
        if not use_opengl:
            info_print("[INFO] 소프트웨어 렌더링 사용 (OpenGL 비활성화)")
            return
        try:
            fmt = QSurfaceFormat()
            fmt.setVersion(3, 3)
            fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)

            vsync = self.config_manager.get_rendering_setting('vsync', True)          # type: ignore[attr-defined]
            fmt.setSwapInterval(1 if vsync else 0)

            msaa_samples = self.config_manager.get_rendering_setting('msaa_samples', 4)  # type: ignore[attr-defined]
            if msaa_samples > 0:
                fmt.setSamples(msaa_samples)

            QSurfaceFormat.setDefaultFormat(fmt)
            self.setViewport(QOpenGLWidget())                                          # type: ignore[attr-defined]
            info_print(f"[INFO] OpenGL 렌더링 활성화 (MSAA: {msaa_samples}x, V-Sync: {vsync})")
        except ImportError:
            warning_print("[WARN] OpenGL 모듈 없음 - 소프트웨어 렌더링 사용")
        except Exception as e:
            error_print(f"[ERROR] OpenGL 초기화 실패: {e} - 소프트웨어 렌더링 사용")

    def toggle_opengl(self, enabled: bool) -> None:
        """OpenGL 렌더링 토글 (재시작 후 적용)."""
        self.config_manager.set_rendering_setting('use_opengl', enabled)              # type: ignore[attr-defined]
        self.config_manager.save_immediate()                                           # type: ignore[attr-defined]
        info_print(f"[INFO] OpenGL 설정 변경: {enabled} (재시작 후 적용)")

    # ============================================
    # 스크롤바 스타일
    # ============================================

    _SCROLLBAR_QSS = """
        QScrollBar:vertical {
            width: 6px; background: transparent; margin: 0px;
        }
        QScrollBar::handle:vertical {
            background: rgba(255,255,255,0.18); border-radius: 3px; min-height: 30px;
        }
        QScrollBar::handle:vertical:hover  { background: rgba(255,255,255,0.30); }
        QScrollBar::handle:vertical:pressed { background: rgba(74,158,255,0.60); }

        QScrollBar:horizontal {
            height: 6px; background: transparent; margin: 0px;
        }
        QScrollBar::handle:horizontal {
            background: rgba(255,255,255,0.18); border-radius: 3px; min-width: 30px;
        }
        QScrollBar::handle:horizontal:hover  { background: rgba(255,255,255,0.30); }
        QScrollBar::handle:horizontal:pressed { background: rgba(74,158,255,0.60); }

        QScrollBar::add-line, QScrollBar::sub-line {
            width: 0px; height: 0px; border: none; background: none;
        }
        QScrollBar::add-page, QScrollBar::sub-page { background: none; }
    """

    def _setup_scrollbar_style(self) -> None:
        """슬림 다크 스크롤바 스타일 적용. __init__ 에서 호출."""
        self.setStyleSheet(self._SCROLLBAR_QSS)                                       # type: ignore[attr-defined]

    # ============================================
    # 뷰 유틸리티
    # ============================================

    def get_current_pixmap(self) -> Optional[QPixmap]:
        """현재 표시 중인 픽스맵 반환 (정적/애니메이션 통합)."""
        if self.current_movie:                                                         # type: ignore[attr-defined]
            return self.current_movie.currentPixmap()                                  # type: ignore[attr-defined]
        return self.current_pixmap                                                     # type: ignore[attr-defined]

    def get_viewport_size(self) -> Tuple[int, int]:
        """뷰포트 (width, height) 반환."""
        vp = self.viewport()                                                           # type: ignore[attr-defined]
        return (vp.width(), vp.height())

    def get_image_rect(self) -> QRect:
        """현재 이미지가 뷰포트에서 차지하는 실제 픽셀 영역 반환."""
        if not self.pixmap_item or self.pixmap_item.pixmap().isNull():                 # type: ignore[attr-defined]
            return QRect()
        scene_rect = self.pixmap_item.sceneBoundingRect()                              # type: ignore[attr-defined]
        top_left     = self.mapFromScene(scene_rect.topLeft())                         # type: ignore[attr-defined]
        bottom_right = self.mapFromScene(scene_rect.bottomRight())                     # type: ignore[attr-defined]
        return QRect(top_left, bottom_right)

    # ============================================
    # 파일 타입 판별 유틸리티
    # ============================================

    @staticmethod
    def is_apng(file_path: Path) -> bool:
        """APNG 여부 판별 (.apng 확장자 또는 .png 다중 프레임)."""
        suffix = file_path.suffix.lower()
        if suffix == '.apng':
            return True
        if suffix == '.png':
            try:
                with PILImage.open(str(file_path)) as img:
                    return getattr(img, 'n_frames', 1) > 1
            except Exception:
                return False
        return False
