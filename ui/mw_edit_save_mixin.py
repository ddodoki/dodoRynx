# -*- coding: utf-8 -*-
# ui\mw_edit_save_mixin.py

"""
MainWindow 편집·저장·회전 전담 Mixin.

포함 범위:
  - 편집 모드 진입/종료 가드
      enter_edit_mode, _edit_lock_guard,
      _on_edit_mode_changed, _on_edit_save_requested,
      _restore_viewer_after_discard
  - EXIF 헬퍼
      _build_save_exif, _get_exif_without_rotation
  - 저장 로직
      _get_save_format, _get_save_quality,
      _save_edit_same_folder, _save_edit_choose_path,
      _do_save_image, _do_save_as_jpg  (하위 호환 alias)
  - 회전 기능
      _on_rotate_left, _on_rotate_right,
      _on_rotate_apply, _on_rotate_reset,
      _apply_rotation_to_view_only,
      _invalidate_after_rotation, _reload_current_image
  - 빈 폴더 처리
      _handle_empty_folder
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import numpy as np
import piexif
from PIL import Image
from PIL.Image import Exif

from PySide6.QtCore import Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox, DarkSaveDialog as _DarkSaveDialog

from utils.app_meta import APP_VERSION
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t
from PySide6.QtWidgets import QWidget, QLabel


class MwEditSaveMixin:
    """편집·저장·회전·빈폴더 처리 전담 Mixin."""

    if TYPE_CHECKING:
        from core.cache_manager import CacheManager
        from core.folder_navigator import FolderNavigator
        from core.image_loader import ImageLoader
        from core.rotation_manager import RotationManager
        from ui.viewer.image_viewer import ImageViewer
        from ui.panels.metadata_panel import MetadataPanel
        from ui.panels.thumbnail_bar import ThumbnailBar
        from ui.overlay_widget import OverlayWidget


        cache_manager:    CacheManager
        navigator:        FolderNavigator
        imageloader:      ImageLoader
        rotation_manager: RotationManager
        image_viewer:     ImageViewer
        metadata_panel:   MetadataPanel
        thumbnail_bar:    ThumbnailBar
        overlay_widget: OverlayWidget
        overlay_widget_b: OverlayWidget

        _current_file:    Optional[Path]
        _edit_locked:     bool
        _thumb_lock_overlay: QWidget
        _thumb_lock_label:   QLabel

        def setWindowTitle(self, title: str) -> None: ...
        def _show_status_message(self, message: str, duration: int = 2000) -> None: ...
        def _load_current_image(self) -> None: ...

    def _q(self) -> 'QWidget':
        """Pylance 타입 힌트용 QWidget 캐스트 — 런타임 비용 없음."""
        from typing import cast
        from PySide6.QtWidgets import QWidget as _QW
        return cast(_QW, self)
    
    # ============================================
    # 편집 모드 진입 / 종료
    # ============================================

    def enter_edit_mode(self) -> None:
        if not self._current_file:
            return

        if getattr(self.image_viewer, '_edit_mode', False):
            self.image_viewer._edit_cancel()
            self._show_status_message(t('msg.edit_exited'), 1500)
        else:
            cp = self.image_viewer.current_pixmap
            if not cp or cp.isNull():
                pi = getattr(self.image_viewer, 'pixmap_item', None)
                if pi is not None and not pi.pixmap().isNull():
                    self.image_viewer.current_pixmap = pi.pixmap()

            if not self.image_viewer.current_pixmap or \
               self.image_viewer.current_pixmap.isNull():
                self._show_status_message(t('msg.edit_auto_exit'), 2000)
                return
            self.image_viewer.enter_edit_mode()

    def _edit_lock_guard(self, action: str = "이동") -> bool:
        """편집 모드 중 동작 차단. 차단됐으면 True 반환."""
        if getattr(self, '_edit_locked', False):
            self._show_status_message(
                t('msg.edit_blocked', action=action), 2500
            )
            return True
        return False

    def _on_edit_mode_changed(self, active: bool) -> None:
        """편집 모드 진입/종료 시 UI 잠금 처리"""
        self._edit_locked = active

        overlay = self._thumb_lock_overlay
        overlay.setGeometry(
            0, 0,
            self.thumbnail_bar.width(),
            self.thumbnail_bar.height()
        )
        if hasattr(self, '_thumb_lock_label'):
            self._thumb_lock_label.setGeometry(
                0, 0,
                self.thumbnail_bar.width(),
                self.thumbnail_bar.height()
            )
        overlay.setVisible(active)
        overlay.raise_()

        if active:
            debug_print("편집 모드: UI 잠금")
            self._show_status_message(t('msg.edit_nav_blocked'), 3000)
        else:
            debug_print("편집 모드 해제: UI 잠금 풀림")

    def _on_edit_save_requested(self, pixmap: QPixmap) -> None:
        """편집 완료 후 저장 방식 선택"""
        dlg = _DarkSaveDialog(self._q())
        result = dlg.exec()

        if result == _DarkSaveDialog.SAME_FOLDER:
            self._save_edit_same_folder(pixmap)
        elif result == _DarkSaveDialog.SAVE_AS:
            self._save_edit_choose_path(pixmap)
        else:
            self._restore_viewer_after_discard()

        if hasattr(self.image_viewer, '_edit_original_pixmap'):
            del self.image_viewer._edit_original_pixmap

    def _restore_viewer_after_discard(self) -> None:
        """편집 저장 취소 시 뷰어 원본 복원"""
        viewer   = self.image_viewer
        original = getattr(viewer, '_edit_original_pixmap', None)

        if original is not None and not original.isNull():
            viewer._replace_pixmap_inplace(original)
        else:
            if self._current_file:
                self._load_current_image()

    # ============================================
    # EXIF 헬퍼
    # ============================================

    def _build_save_exif(self, filepath: Optional[Path]) -> Optional[bytes]:
        """저장용 EXIF 빌드 (Orientation 제거 + Software 태그 추가)."""
        software_tag = f"dodoRynx v{APP_VERSION}"

        # ── piexif 경로 ──────────────────────────────────────
        try:
            if filepath and filepath.exists():
                exif_dict = piexif.load(str(filepath))
            else:
                exif_dict = {'0th': {}, 'Exif': {}, 'GPS': {}, '1st': {}}

            ifd_0th = exif_dict.setdefault('0th', {})
            ifd_0th.pop(piexif.ImageIFD.Orientation, None)
            ifd_0th[piexif.ImageIFD.Software] = software_tag.encode('utf-8')

            SKIP_TAGS = {
                piexif.ExifIFD.ComponentsConfiguration,
                piexif.ExifIFD.ExifVersion,
                piexif.ExifIFD.SceneType,
            }
            exif_ifd = exif_dict.get('Exif', {})
            for tag in SKIP_TAGS:
                if isinstance(exif_ifd.get(tag), (tuple, list)):
                    exif_ifd.pop(tag)

            return piexif.dump(exif_dict)

        except ImportError:
            pass
        except Exception as e:
            debug_print(f"piexif 처리 실패: {e} → Pillow fallback")

        # ── Pillow fallback ──────────────────────────────────
        try:
            if filepath and filepath.exists():
                with Image.open(str(filepath)) as img:
                    exif = img.getexif()
            else:
                exif = Exif()

            exif.pop(274, None)         # Orientation
            exif[305] = software_tag    # Software
            return exif.tobytes()

        except Exception as e:
            debug_print(f"Pillow EXIF 처리 실패: {e}")
            return None

    def _get_exif_without_rotation(self, filepath: Optional[Path]) -> Optional[bytes]:
        """Orientation 태그를 제거한 EXIF bytes 반환 (회전 저장 전용)."""
        if not filepath or not filepath.exists():
            return None

        # ── piexif ───────────────────────────────────────────
        try:
            exif_dict = piexif.load(str(filepath))

            ifd_0th = exif_dict.get('0th', {})
            ifd_0th.pop(piexif.ImageIFD.Orientation, None)

            PROBLEMATIC_EXIF_TAGS = {
                piexif.ExifIFD.ComponentsConfiguration,
                piexif.ExifIFD.ExifVersion,
                piexif.ExifIFD.SceneType,
            }
            exif_ifd = exif_dict.get('Exif', {})
            for tag in PROBLEMATIC_EXIF_TAGS:
                val = exif_ifd.get(tag)
                if val is not None and isinstance(val, (tuple, list)):
                    debug_print(f"piexif: 문제 태그 {tag} 제거 (tuple → skip)")
                    exif_ifd.pop(tag)

            return piexif.dump(exif_dict)

        except ImportError:
            debug_print("piexif 미설치 → Pillow fallback")
        except Exception as e:
            debug_print(f"piexif EXIF 처리 실패: {e} → Pillow fallback")

        # ── Pillow fallback ──────────────────────────────────
        try:
            with Image.open(str(filepath)) as img:
                exif = img.getexif()
            if not exif:
                return None
            exif.pop(274, None)     # Orientation
            return exif.tobytes()
        except Exception as e:
            debug_print(f"Pillow EXIF 로드 실패: {e}")
            return None

    # ============================================
    # 저장 포맷 / 품질 조회 헬퍼
    # ============================================

    def _get_save_format(self) -> str:
        """툴바에서 현재 선택된 저장 포맷 ('jpg' | 'webp')"""
        tb = getattr(self.image_viewer, '_edit_toolbar', None)
        if tb is not None and hasattr(tb, 'current_format'):
            return tb.current_format()
        return 'jpg'

    def _get_save_quality(self) -> int:
        """툴바에서 현재 저장 품질 (1-100)"""
        tb = getattr(self.image_viewer, '_edit_toolbar', None)
        if tb is not None and hasattr(tb, 'current_quality'):
            return tb.current_quality()
        return 85

    # ============================================
    # 저장 — 같은 폴더 / 사용자 경로 선택
    # ============================================

    def _save_edit_same_folder(self, pixmap: QPixmap) -> None:
        """원본과 같은 폴더에 자동 저장 — 선택 포맷 적용"""
        if not self._current_file:
            return
        try:
            fmt   = self._get_save_format()
            qual  = self._get_save_quality()
            ext   = '.webp' if fmt == 'webp' else '.jpg'

            stem   = self._current_file.stem
            parent = self._current_file.parent
            save_path = parent / f"{stem}_edited{ext}"
            counter = 1
            while save_path.exists():
                save_path = parent / f"{stem}_edited_{counter}{ext}"
                counter += 1

            self._do_save_image(pixmap, save_path, fmt, qual)
        except Exception as e:
            error_print(f"편집 저장 오류: {e}")
            _DarkMessageBox(self._q(), kind='danger', title=t('edit_dialog.error_title'), body=str(e)).exec()

    def _save_edit_choose_path(self, pixmap: QPixmap) -> None:
        """사용자가 직접 경로를 선택하여 저장 — 선택 포맷 적용"""
        if not self._current_file:
            return

        fmt  = self._get_save_format()
        qual = self._get_save_quality()
        ext  = '.webp' if fmt == 'webp' else '.jpg'

        default = str(
            self._current_file.parent / f"{self._current_file.stem}_edited{ext}"
        )
        save_path_str, _ = QFileDialog.getSaveFileName(
            self._q(),
            t('edit_dialog.save_as_title'),
            default,
            t('edit_dialog.save_as_filter'),
        )
        if not save_path_str:
            return

        try:
            save_path = Path(save_path_str)
            if not save_path.suffix:
                save_path = save_path.with_suffix(ext)

            suffix = save_path.suffix.lower()
            if suffix == '.webp':
                actual_fmt = 'webp'
            else:
                actual_fmt = 'jpg'
                if suffix not in ('.jpg', '.jpeg'):
                    save_path = save_path.with_suffix('.jpg')

            self._do_save_image(pixmap, save_path, actual_fmt, qual)
        except Exception as e:
            error_print(f"사본 저장 오류: {e}")
            _DarkMessageBox(self._q(), kind='danger', title=t('edit_dialog.error_title'), body=str(e)).exec()

    # ============================================
    # 저장 — 핵심 I/O
    # ============================================

    def _do_save_image(
        self,
        pixmap:    QPixmap,
        save_path: Path,
        fmt:       str,   # 'jpg' | 'webp'
        quality:   int,   # 1-100
    ) -> None:
        """
        JPG / WEBP 통합 저장.
        - JPG  : RGBA → 흰 배경 RGB 합성 후 JPEG 저장
        - WEBP : RGBA 그대로 저장 (투명도 보존)
        - EXIF : 두 포맷 모두 piexif/Pillow fallback 적용
        """
        qimg = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = qimg.width(), qimg.height()
        arr  = np.frombuffer(qimg.bits(), dtype=np.uint8).reshape((h, w, 4)).copy()
        pil_rgba = Image.fromarray(arr, 'RGBA')

        exif_bytes = self._build_save_exif(self._current_file)

        if fmt == 'webp':
            save_kwargs: dict = {'quality': quality, 'method': 4}
            if exif_bytes:
                try:
                    pil_rgba.save(str(save_path), 'WEBP',
                                  exif=exif_bytes,
                                  **{k: v for k, v in save_kwargs.items()})
                except Exception:
                    debug_print("WEBP EXIF 저장 실패 → EXIF 없이 재시도")
                    pil_rgba.save(str(save_path), 'WEBP', **save_kwargs)
            else:
                pil_rgba.save(str(save_path), 'WEBP', **save_kwargs)

        else:
            background = Image.new('RGB', (w, h), (255, 255, 255))
            background.paste(pil_rgba, mask=pil_rgba.split()[3])
            save_kwargs = {'quality': quality, 'optimize': True}
            if exif_bytes:
                save_kwargs['exif'] = exif_bytes
            background.save(str(save_path), 'JPEG', **save_kwargs)

        info_print(f"편집 저장: {save_path} fmt={fmt.upper()} quality={quality}")

        # 저장 완료 후 썸네일/목록 갱신
        self.navigator.reload_async()
        self._show_status_message(
            t('edit_dialog.saved_msg', name=save_path.name), 3000
        )

    def _do_save_as_jpg(self, pixmap: QPixmap, save_path: Path) -> None:
        """하위 호환 alias — 기존 호출부 대비."""
        self._do_save_image(pixmap, save_path, 'jpg', 85)

    # ============================================
    # 회전 기능
    # ============================================

    @Slot()
    def _on_rotate_left(self) -> None:
        current = self._current_file
        if not current:
            return

        state = self.rotation_manager.get_state()
        if not state or state.file_path != current:
            is_anim = self.imageloader.is_animated(current)
            self.rotation_manager.set_current_file(current, is_anim)
            state = self.rotation_manager.get_state()

        if not state:
            return
        if state.has_animation:
            _DarkMessageBox(self._q(), kind='warning', title=t('rotate.blocked_title'), body=t('rotate.blocked_msg')).exec()
            return

        self.rotation_manager.rotate_left()
        self._apply_rotation_to_view_only()

    @Slot()
    def _on_rotate_right(self) -> None:
        current = self._current_file
        if not current:
            return

        state = self.rotation_manager.get_state()
        if not state or state.file_path != current:
            is_anim = self.imageloader.is_animated(current)
            self.rotation_manager.set_current_file(current, is_anim)
            state = self.rotation_manager.get_state()

        if not state:
            return
        if state.has_animation:
            _DarkMessageBox(self._q(), kind='warning', title=t('rotate.blocked_title'), body=t('rotate.blocked_msg')).exec()
            return

        self.rotation_manager.rotate_right()
        self._apply_rotation_to_view_only()

    @Slot()
    def _on_rotate_apply(self) -> None:
        if not self._current_file:
            return

        state = self.rotation_manager.get_state()
        if not state or state.file_path != self._current_file:
            _DarkMessageBox(self._q(), kind='info', title=t('rotate.no_change_title'), body=t('rotate.no_change_msg')).exec()
            return

        if state.has_animation:
            _DarkMessageBox(self._q(), kind='warning', title=t('rotate.blocked_title'), body=t('rotate.blocked_msg')).exec()
            return

        _confirm = _DarkMessageBox(
            self._q(), kind='question',
            title=t('rotate.apply_title'),
            body=t('rotate.apply_msg'),
        )
        if _confirm.exec() != QDialog.DialogCode.Accepted:
            return

        success = self.rotation_manager.apply()
        if success:
            self._show_status_message(t('msg.rotate_saved'), 2000)
            self._invalidate_after_rotation(self._current_file)
            self.rotation_manager.set_current_file(self._current_file, state.has_animation)
        else:
            self._show_status_message(t('msg.rotate_no_change'), 2000)

    @Slot()
    def _on_rotate_reset(self) -> None:
        current = self._current_file
        if not current:
            return
        self.rotation_manager.reset()
        self._load_current_image()

    def _apply_rotation_to_view_only(self) -> None:
        """회전 미리보기만 뷰에 적용. 디스크 저장 없음."""
        state = self.rotation_manager.get_state()
        if not state:
            return
        if not self.image_viewer:
            return
        pix = self.image_viewer.get_current_pixmap()
        if not pix:
            return
        rotated = self.rotation_manager.get_preview_pixmap(pix)
        if rotated is None or rotated.isNull():
            return
        self.image_viewer.set_rotation_preview(rotated)

    def _invalidate_after_rotation(self, path: Path) -> None:
        """회전 저장 후 캐시 무효화 + 비동기 목록 갱신."""
        self.cache_manager.clear()
        self.navigator.reload_async()

    def _reload_current_image(self) -> None:
        """현재 파일을 디스크에서 다시 읽어 뷰/썸네일 모두 갱신."""
        if not self._current_file:
            return
        idx = self.navigator.current_index
        if idx >= 0:
            self.cache_manager.invalidate(idx)
        self.navigator.reload_async()

    # ============================================
    # 빈 폴더 처리
    # ============================================

    def _handle_empty_folder(self) -> None:
        """모든 파일이 제거됐을 때 전체 UI 초기화 (단일 진입점)"""
        info_print("폴더가 비어있음 - UI 전체 초기화")

        if hasattr(self, 'image_viewer') and self.image_viewer:
            self.image_viewer.clear()

        if hasattr(self, 'overlay_widget') and self.overlay_widget:
            self.overlay_widget.clear()

        if hasattr(self, 'thumbnail_bar'):
            self.thumbnail_bar.set_image_list([], -1)

        if hasattr(self, 'metadata_panel') and self.metadata_panel:
            try:
                self.metadata_panel.load_metadata(None)
            except Exception as e:
                warning_print(f"metadata_panel 클리어 실패: {e}")

        if hasattr(self, 'image_viewer') and hasattr(self.image_viewer, 'minimap'):
            self.image_viewer.minimap.hide()

        if hasattr(self, 'cache_manager'):
            self.cache_manager.set_image_list([])

        self.setWindowTitle("dodoRynx")
