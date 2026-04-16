# -*- coding: utf-8 -*-
# ui\mw_file_op_mixin.py
"""
MainWindow 파일 작업 전용 Mixin.

포함 범위:
  - 파일 CRUD 래퍼 (_delete_file, _cut_file, _copy_file, _paste_file,
                    _rename_file, _copy_file_path, _open_file_location,
                    _show_file_properties)
  - 작업 상태 헬퍼 (_set_op_status, _clear_op_status, _run_file_worker)
  - FolderWatcher FS 이벤트 핸들러 (_on_fs_file_added/deleted/modified/moved,
                                     _on_fs_batch_added/deleted)
  - FolderExplorer 폴더 선택 핸들러 (_on_folder_selected_from_explorer)
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Slot

from utils.debug import info_print, warning_print
from utils.lang_manager import t


if TYPE_CHECKING:
    from core.file_manager import FileManager
    from core.folder_navigator import FolderNavigator
    from ui.panels.folder_explorer import FolderExplorer
    from ui.status_bar import StatusBarController


class MwFileOpMixin:
    """파일 작업 + FS 이벤트 전담 Mixin."""

    if TYPE_CHECKING:
        file_manager:   FileManager
        navigator:      FolderNavigator
        folder_explorer: FolderExplorer
        status_ctrl:    StatusBarController
        _current_file:  Optional[Path]
        _is_closing:    bool

        def open_folder(self, folder_path: Path) -> None: ...
        def _show_status_message(self, message: str, duration: int = 2000) -> None: ...
        def _focus_is_in_folder_explorer(self) -> bool: ...

    # ============================================
    # 파일 작업 — FileManager 위임 래퍼
    # ============================================

    def _delete_file(self) -> None:
        self.file_manager.delete_file()

    def _cut_file(self) -> None:
        self.file_manager.cut_file()

    def _paste_file(self) -> None:
        """
        Ctrl+V / 메뉴 Paste 공용 진입점.
        - FolderExplorer에 포커스가 있으면: 폴더 탐색기 기준 붙여넣기
        - 아니면: 기존대로 파일 뷰어 기준 붙여넣기
        """
        try:
            if hasattr(self, "folder_explorer") and self.folder_explorer.isVisible():
                if self._focus_is_in_folder_explorer():
                    self.folder_explorer._paste_folder(None)  # pylint: disable=protected-access
                    return
        except Exception:
            pass

        if hasattr(self, "file_manager"):
            if hasattr(self.file_manager, "paste_file"):
                self.file_manager.paste_file()

    def _copy_file(self) -> None:
        self.file_manager.copy_file()

    def _copy_file_path(self) -> None:
        self.file_manager.copy_file_path()

    def _rename_file(self) -> None:
        self.file_manager.rename_file()

    def _open_file_location(self) -> None:
        self.file_manager.open_file_location()

    def _show_file_properties(self) -> None:
        self.file_manager.show_file_properties()

    # ============================================
    # 파일 작업 상태 헬퍼
    # ============================================

    def _set_op_status(self, text: str, current: int = 0, total: int = 0) -> None:
        if not text:
            self.status_ctrl.on_file_op_finished()
            return

        display = text if len(text) <= 42 else f"...{text[-39:]}"

        if current == 0 and total == 0:
            self.status_ctrl.on_file_op_started(display)
        else:
            self.status_ctrl.on_file_op_progress(display, current, total)

    def _clear_op_status(self, done_msg: str = "", duration: int = 2500) -> None:
        """진행 표시 숨기고 완료 토스트 표시."""
        self._set_op_status("")
        if done_msg:
            self._show_status_message(done_msg, duration)

    def _run_file_worker(self, operation, files, target_folder=None) -> None:
        self.file_manager.run_file_worker(operation, files, target_folder)

    # ============================================
    # FolderWatcher FS 이벤트 핸들러
    # ============================================

    @Slot(Path)
    def _on_fs_file_added(self, file_path: Path) -> None:
        if file_path.parent != self.navigator.current_folder:
            return

        self.file_manager.on_file_added(file_path)

        if hasattr(self, "folder_explorer") and self.folder_explorer:
            self.folder_explorer.on_files_changed(file_path)

    @Slot(Path)
    def _on_fs_file_deleted(self, file_path: Path) -> None:
        self.file_manager.on_file_deleted(file_path)
        if hasattr(self, "folder_explorer") and self.folder_explorer:
            self.folder_explorer.on_files_changed(file_path)

    @Slot(Path)
    def _on_fs_file_modified(self, file_path: Path) -> None:
        self.file_manager.on_file_modified(file_path)
        if hasattr(self, "folder_explorer") and self.folder_explorer:
            self.folder_explorer.on_files_changed(file_path)

    @Slot(Path, Path)
    def _on_fs_file_moved(self, src_path: Path, dest_path: Path) -> None:
        self.file_manager.on_file_moved(src_path, dest_path)
        if hasattr(self, "folder_explorer") and self.folder_explorer:
            self.folder_explorer.on_files_changed(src_path)
            self.folder_explorer.on_files_changed(dest_path)

    @Slot(list)
    def _on_fs_batch_deleted(self, deleted_files: list) -> None:
        self.file_manager.on_batch_deleted(deleted_files)

        if hasattr(self, "folder_explorer") and self.folder_explorer:
            parents: set[Path] = set()
            for fp in deleted_files:
                try:
                    parents.add(fp.parent)
                except Exception:
                    pass
            for p in parents:
                try:
                    self.folder_explorer.refresh_empty_state(p)
                except Exception:
                    pass

    @Slot(list)
    def _on_fs_batch_added(self, added_files: list) -> None:
        """파일 배치 추가 이벤트 (FolderWatcher.batch_added)"""
        if not added_files:
            return
        info_print(f"파일 배치 추가 감지: {len(added_files)}개")
        self.navigator.reload_async()
        for fp in added_files:
            self.folder_explorer.on_files_changed(fp)

    # ============================================
    # FolderExplorer 폴더 선택 핸들러
    # ============================================

    @Slot(Path)
    def _on_folder_selected_from_explorer(self, folder_path: Path) -> None:
        """FolderExplorer에서 폴더를 선택했을 때 호출."""
        if not folder_path or not folder_path.is_dir():
            warning_print(f"folder_selected: 유효하지 않은 경로 — {folder_path}")
            return

        if self.navigator.current_folder == folder_path and self.navigator.image_files:
            info_print(f"folder_selected: 동일 폴더 재선택 — 재스캔 건너뜀: {folder_path.name}")
            self.folder_explorer.navigate_to_folder(folder_path)
            return

        self.open_folder(folder_path)
