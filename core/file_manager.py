# -*- coding: utf-8 -*-
# core/file_manager.py
"""
파일·폴더·하이라이트 관리 모듈

담당 범위:
  FileWorkerThread   : 파일 복사/이동/삭제 백그라운드 스레드
  FileOperations     : 단일 파일 작업 (열기, 삭제, 이름변경, 복사, 잘라내기, 붙여넣기)
  HighlightOperations: 하이라이트 토글·클리어·배치 작업
  FolderWatchHandler : FolderWatcher 이벤트 → UI 갱신
  FileManager        : MainWindow 브릿지 (위 클래스들의 컨트롤러)
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import send2trash
from PySide6.QtCore import QMimeData, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t

if TYPE_CHECKING:
    from ui.main_window import MainWindow


# ══════════════════════════════════════════════════════════════
# FileWorkerThread
# ══════════════════════════════════════════════════════════════

class FileWorkerThread(QThread):
    """
    파일 복사·이동·삭제를 백그라운드에서 처리.
    operation: 'copy' | 'move' | 'delete'
    """

    progress = Signal(int, int, str, str)
    finished = Signal(int, int, str)

    def __init__(
        self,
        operation: str,
        files: list,
        target_folder=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.operation     = operation
        self.files         = files
        self.target_folder = target_folder
        self._cancelled    = False


    def cancel(self) -> None:
        self._cancelled = True


    def run(self) -> None:

        total = len(self.files)
        success = 0
        fail = 0
        op_label = {"copy": "복사", "move": "이동", "delete": "삭제"}.get(
            self.operation, self.operation
        )

        for i, src in enumerate(self.files):
            if self._cancelled:
                break

            filename = src.name if hasattr(src, "name") else str(src)
            self.progress.emit(i + 1, total, filename, op_label)

            try:
                if self.operation == "delete":
                    send2trash.send2trash(str(src))

                elif self.operation in ("copy", "move"):
                    # target_folder None 가드 추가
                    if self.target_folder is None:
                        raise ValueError("target_folder가 지정되지 않았습니다.")

                    dst = self.target_folder / src.name
                    if dst.exists():
                        stem, suffix = src.stem, src.suffix
                        counter = 1
                        while dst.exists():
                            dst = self.target_folder / f"{stem}({counter}){suffix}"
                            counter += 1

                    if self.operation == "copy":
                        shutil.copy2(str(src), str(dst))
                    else:
                        shutil.move(str(src), str(dst))

                success += 1

            except Exception as e:
                fail += 1
                error_print(f"FileWorkerThread: {filename} 처리 실패: {e}")

        self.finished.emit(success, fail, self.operation)


# ══════════════════════════════════════════════════════════════
# FileOperations
# ══════════════════════════════════════════════════════════════

class FileOperations:
    """단일 파일 작업 (열기/삭제/이름변경/복사/잘라내기/붙여넣기/클립보드)."""


    def __init__(self, main_window: "MainWindow") -> None:
        self._mw: "MainWindow" = main_window

    # ── 열기 ──────────────────────────────────────────────────

    def open_file_dialog(self) -> None:
        mw = self._mw
        ext_filter = (
            "이미지 파일 (*.jpg *.jpeg *.png *.gif *.webp *.bmp "
            "*.heif *.heic *.avif *.tiff *.tif "
            "*.nef *.cr2 *.arw *.dng *.raf *.orf)"
        )
        filepath, _ = QFileDialog.getOpenFileName(
            mw,
            t('file_manager.open_image_title'),
            str(Path.home()),
            t('file_manager.open_image_filter'),
        )
        if filepath:
            mw.open_image(Path(filepath))


    def open_folder_dialog(self) -> None:
        mw = self._mw
        folder = QFileDialog.getExistingDirectory(
            mw,
            t('file_manager.open_folder_title'),
            str(Path.home()),
        )
        if folder:
            mw.open_folder(Path(folder))


    # ── 이름 변경 ─────────────────────────────────────────────

    def rename_file(self) -> None:
        mw = self._mw
        if not mw._current_file:
            return

        current_name = mw._current_file.stem
        current_ext  = mw._current_file.suffix

        dialog = QInputDialog(mw)
        dialog.setWindowTitle(t('file_manager.rename_dialog_title'))
        dialog.setLabelText(t('file_manager.rename_dialog_body'))
        dialog.setTextValue(current_name)
        dialog.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.MSWindowsFixedSizeDialogHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        dialog.setMinimumSize(300, 150)
        dialog.setMaximumSize(300, 150)
        dialog.resize(300, 150)

        target_file = mw._current_file
        ok = dialog.exec()
        new_name = dialog.textValue().strip()

        if not ok or not new_name or new_name == current_name:
            return

        try:
            new_path = target_file.parent / f"{new_name}{current_ext}"

            is_case_rename = (
                new_path.exists()
                and target_file.exists()
                and target_file.samefile(new_path)
            )

            if is_case_rename:
                # 2단계 rename: 임시 이름 경유
                import uuid
                tmp_path = target_file.parent / f"_tmp_{uuid.uuid4().hex}{current_ext}"
                target_file.rename(tmp_path)
                tmp_path.rename(new_path)

            elif new_path.exists():
                # 실제 다른 파일이 같은 이름으로 존재
                QMessageBox.warning(
                    mw,
                    t('file_manager.rename_error_title'),
                    t('file_manager.rename_exists', name=new_path.name),
                )
                return

            else:
                target_file.rename(new_path)
                mw._current_file = new_path     
                mw.navigator.update_file_path(target_file, new_path) 

            mw._current_file = new_path
            mw.navigator.update_file_path(target_file, new_path)

            info_print(f"이름 변경: {target_file.name} → {new_path.name}")
            mw.navigator.reload()
            mw._show_status_message(t('file_manager.renamed', name=new_path.name), 3000)

        except PermissionError:
            QMessageBox.critical(
                mw,
                t('file_manager.rename_error_title'),
                t('file_manager.rename_permission_error', name=target_file.name),
            )
            error_print(f"rename_file PermissionError: {target_file}")

        except Exception as e:
            QMessageBox.critical(
                mw,
                t('file_manager.rename_error_title'),
                t('file_manager.rename_error_msg', error=e),
            )
            error_print(f"rename_file: {e}")
            

    # ── 붙여넣기 ──────────────────────────────────────────────

    def paste_file(self, target_folder: Optional[Path] = None) -> None:
        mw = self._mw

        if target_folder and target_folder.is_dir():
            dest_dir = target_folder

        elif hasattr(mw, 'folder_explorer'):
            try:
                dest_dir = mw.folder_explorer.get_current_folder()
            except Exception:
                dest_dir = None
        else:
            dest_dir = None

        if not dest_dir or not dest_dir.is_dir():
            dest_dir = getattr(mw.navigator, 'current_folder', None)

        if not dest_dir or not dest_dir.is_dir():
            QMessageBox.warning(mw, "Paste", "Please select a target folder.")
            return

        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if not mime or not mime.hasUrls():
            QMessageBox.warning(mw, "Paste", "No files in the clipboard.")
            return

        files = [Path(url.toLocalFile()) for url in mime.urls()
                if Path(url.toLocalFile()).exists()]
        if not files:
            return

        op = self._detect_clipboard_op()
        operation = "move" if op == "cut" else "copy"
        mw._run_file_worker(operation, files, dest_dir) 
        

    def _detect_clipboard_op(self) -> str:
        """Windows: 클립보드 DropEffect 읽어 'cut'/'copy' 반환."""
        if sys.platform != "win32":
            return "copy"
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                fmt = win32clipboard.RegisterClipboardFormat("Preferred DropEffect")
                if win32clipboard.IsClipboardFormatAvailable(fmt):
                    data   = win32clipboard.GetClipboardData(fmt)
                    effect = int.from_bytes(data[:4], "little")
                    if effect == 2:
                        info_print("클립보드: Cut (DROPEFFECT_MOVE)")
                        return "cut"
                    else:
                        info_print(f"클립보드: Copy (effect={effect})")
                        return "copy"
                else:
                    debug_print("Preferred DropEffect 없음 → Copy")
                    return "copy"
            finally:
                win32clipboard.CloseClipboard()
        except ImportError:
            error_print("win32clipboard 없음. pip install pywin32")
            return "copy"
        except Exception as e:
            error_print(f"detect_clipboard_op: {e}")
            return "copy"

    # ── 경로 복사 ─────────────────────────────────────────────

    def copy_file_path(self) -> None:
        mw = self._mw
        if not mw._current_file:
            return
        QApplication.clipboard().setText(str(mw._current_file))
        mw._show_status_message(t('file_manager.path_copied', path=mw._current_file), 2000)
        info_print(f"경로 복사: {mw._current_file}")


    def copy_image_to_clipboard(self) -> None:
        mw = self._mw
        if not mw._current_file:
            return
        from PySide6.QtCore import QMimeData, QUrl
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(mw._current_file))])
        if sys.platform == "win32":
            mime.setData("Preferred DropEffect", b"\x01\x00\x00\x00") 
        QApplication.clipboard().setMimeData(mime)
        mw._show_status_message(
            t('file_manager.image_clipboard', name=mw._current_file.name), 2000
        )
        info_print(f"이미지 클립보드 복사: {mw._current_file}")

    # ── 파일 위치 / 속성 ──────────────────────────────────────

    def open_file_location(self) -> None:
        mw = self._mw
        if not mw._current_file:
            return

        if platform.system() == "Windows":
            subprocess.run(["explorer", "/select,", str(mw._current_file)])
        elif platform.system() == "Darwin":
            subprocess.run(["open", "-R", str(mw._current_file)])
        else:
            subprocess.run(["xdg-open", str(mw._current_file.parent)])


    def show_file_properties(self) -> None:
        mw = self._mw
        current_path = mw.navigator.current()
        if not current_path:
            error_print("show_file_properties: 현재 파일 없음")
            return
        if not Path(current_path).exists():
            error_print(f"show_file_properties: 파일 없음 {current_path}")
            return
        if sys.platform != "win32":
            return
        try:
            import ctypes, ctypes.wintypes as wintypes

            class SHELLEXECUTEINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize",       wintypes.DWORD),
                    ("fMask",        ctypes.c_ulong),
                    ("hwnd",         wintypes.HWND),
                    ("lpVerb",       ctypes.c_wchar_p),
                    ("lpFile",       ctypes.c_wchar_p),
                    ("lpParameters", ctypes.c_wchar_p),
                    ("lpDirectory",  ctypes.c_wchar_p),
                    ("nShow",        ctypes.c_int),
                    ("hInstApp",     wintypes.HINSTANCE),
                    ("lpIDList",     ctypes.c_void_p),
                    ("lpClass",      ctypes.c_wchar_p),
                    ("hkeyClass",    wintypes.HKEY),
                    ("dwHotKey",     wintypes.DWORD),
                    ("hIcon",        wintypes.HANDLE),
                    ("hProcess",     wintypes.HANDLE),
                ]

            SEEINVOKECOMMAND = 0x0000000C
            sei = SHELLEXECUTEINFO()
            sei.cbSize      = ctypes.sizeof(sei)
            sei.fMask       = SEEINVOKECOMMAND
            sei.hwnd        = None
            sei.lpVerb      = "properties"
            sei.lpFile      = str(Path(current_path).resolve())
            sei.lpParameters = None
            sei.lpDirectory  = None
            sei.nShow        = 1
            sei.hInstApp     = None
            ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
        except Exception as e:
            error_print(f"show_file_properties: {e}")


# ══════════════════════════════════════════════════════════════
# HighlightOperations
# ══════════════════════════════════════════════════════════════

class HighlightOperations:
    """하이라이트 토글·전체해제·배치 파일 작업."""


    def __init__(self, main_window: "MainWindow") -> None:
        self._mw: "MainWindow" = main_window


    def toggle_highlight(self) -> None:
        mw           = self._mw
        current_file = mw.navigator.current()
        if not current_file:
            return
        is_highlighted = mw.navigator.toggle_highlight(current_file)
        self._sync_thumbnail_highlight(current_file, is_highlighted)
        highlight_count = mw.navigator.get_highlight_count()
        status = "하이라이트 설정" if is_highlighted else "하이라이트 해제"
        mw._show_status_message(t('file_manager.highlight_status', status=status, count=highlight_count), 1500)
        info_print(f"{current_file.name}: {status}")


    def clear_all_highlights(self) -> None:
        mw = self._mw
        highlighted_count = mw.navigator.get_highlight_count()
        if highlighted_count == 0:
            return
        mw.navigator.clear_highlights()
        mw._sync_highlight_state(force_full_sync=True)
        mw._show_status_message(t('file_manager.highlight_cleared', count=highlighted_count), 2000)
        info_print(f"하이라이트 전체 해제: {highlighted_count}개")


    def delete_highlighted_files(self) -> None:
        mw          = self._mw
        highlighted = mw.navigator.get_highlighted_files()
        if not highlighted:
            QMessageBox.information(mw, t('file_manager.no_highlight_title'),
                        t('file_manager.no_highlight_msg'))
            return
        preview = "\n".join(f.name for f in list(highlighted)[:5])
        if len(highlighted) > 5:
            preview += f"\n... 외 {len(highlighted) - 5}개"
        reply = QMessageBox.question(
            mw,  t('file_manager.delete_hl_title'),
            t('file_manager.delete_hl_msg', count=len(highlighted), preview=preview),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        next_index = mw.navigator.calculate_next_index_after_deletion(
            files_to_delete=highlighted, deletion_mode="multi"
        )
        mw.navigator._temp_scan_prev_index = next_index
        mw._run_file_worker("delete", list(highlighted))


    def copy_highlighted_files(self) -> None:
        mw          = self._mw
        highlighted = mw.navigator.get_highlighted_files()
        if not highlighted:
            QMessageBox.information(mw, t('print_dialog.notice_title'), t('print_dialog.no_highlight_msg'))
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(f)) for f in highlighted])
        if sys.platform == "win32":
            mime.setData("Preferred DropEffect", b"\x01\x00\x00\x00")
        QApplication.clipboard().setMimeData(mime)
        mw._show_status_message( t('file_manager.highlight_copied', count=len(highlighted)), 2000)
        info_print(f"하이라이트 복사: {len(highlighted)}개")


    def cut_highlighted_files(self) -> None:
        mw          = self._mw
        highlighted = mw.navigator.get_highlighted_files()
        if not highlighted:
            QMessageBox.information(mw, t('print_dialog.notice_title'), t('print_dialog.no_highlight_msg'))
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(f)) for f in highlighted])
        if sys.platform == "win32":
            mime.setData("Preferred DropEffect", b"\x02\x00\x00\x00")
        QApplication.clipboard().setMimeData(mime)
        mw._show_status_message( t('file_manager.highlight_cut', count=len(highlighted)), 2000)
        info_print(f"하이라이트 잘라내기: {len(highlighted)}개")


    def _sync_thumbnail_highlight(self, filepath: Path, is_highlighted: bool) -> None:
        mw = self._mw
        # ThumbnailBar highlighted_files 동기화
        if is_highlighted:
            mw.thumbnail_bar.highlighted_files.add(filepath)
        else:
            mw.thumbnail_bar.highlighted_files.discard(filepath)
        # 썸네일 아이템 UI 갱신
        try:
            index = mw.thumbnail_bar.image_list.index(filepath)
            if 0 <= index < len(mw.thumbnail_bar.thumbnail_items):
                mw.thumbnail_bar.thumbnail_items[index].set_highlighted(is_highlighted)
        except (ValueError, AttributeError):
            warning_print(f"썸네일 하이라이트 갱신 실패: {filepath.name}")


# ══════════════════════════════════════════════════════════════
# FolderWatchHandler
# ══════════════════════════════════════════════════════════════

def _short_name(name: str, max_len: int = 28) -> str:
    """파일명을 max_len 이하로 줄여 반환. 확장자는 보존."""
    if len(name) <= max_len:
        return name

    suffix = Path(name).suffix    
    stem   = Path(name).stem
    keep   = max_len - len(suffix) - 3  
    if keep < 4:
        return name[:max_len - 3] + "..."
    return stem[:keep] + "..." + suffix


class FolderWatchHandler:
    """FolderWatcher 시그널 이벤트 처리 → UI 갱신."""

    def __init__(self, main_window: "MainWindow") -> None:
        self._mw: "MainWindow" = main_window

        # 디바운스용: 연속 추가 파일을 한 번에 묶어 처리
        self._pending_added_files: list = []
        self._add_debounce_timer = QTimer()
        self._add_debounce_timer.setSingleShot(True)
        self._add_debounce_timer.setInterval(250)  # 250ms 안에 들어오는 파일 전부 묶음
        self._add_debounce_timer.timeout.connect(self._flush_added_files)


    def _flush_added_files(self) -> None:
        """디바운스 타이머 만료 → 누적된 파일 전체를 한 번에 처리."""
        mw = self._mw
        if not self._pending_added_files:
            return

        files = self._pending_added_files.copy()
        self._pending_added_files.clear()

        n = len(files)
        label = f"'{files[0].name}'" if n == 1 else f"{n}개 파일"

        def on_reload_complete(count):
            # 스캔 완료 후 image_files 확인 → 이제 valid가 채워짐
            valid = [f for f in files if f in mw.navigator.image_files]
            if valid:
                mw.navigator.set_temporary_highlights(valid)
                mw.thumbnail_bar.set_temp_highlights(valid)
            mw._clear_op_status(f"{label} 추가됨", 2000)
            try:
                mw.navigator.folder_scan_completed.disconnect(on_reload_complete)
            except (TypeError, RuntimeError):
                pass

        mw.navigator.folder_scan_completed.connect(on_reload_complete)
        mw.navigator.reload_async()


    def on_file_added(self, filepath: Path) -> None:
        mw = self._mw
        sn = _short_name(filepath.name)                         
        mw._set_op_status(f"추가됨: {sn}", 0, 0)

        # 중복 방지 후 누적
        if filepath not in self._pending_added_files:
            self._pending_added_files.append(filepath)
        # 타이머 재시작 (디바운스: 마지막 파일 추가 후 250ms 뒤에 실행)
        self._add_debounce_timer.start()


    def on_file_deleted(self, filepath: Path) -> None:
        mw = self._mw
        sn = _short_name(filepath.name)                        
        info_print(f"파일 삭제 감지: {filepath.name}")      

        def on_done(count):
            mw._clear_op_status(f"'{sn}' 삭제 감지됨", 2000) 
            try:
                mw.navigator.folder_scan_completed.disconnect(on_done)
            except Exception:
                pass

        mw.navigator.folder_scan_completed.connect(on_done)
        mw.navigator.reload_async()


    def on_file_modified(self, filepath: Path) -> None:
        mw = self._mw
        sn = _short_name(filepath.name)
        debug_print(f"파일 변경 감지: {filepath.name}")

        current = mw.navigator.current()
        if current == filepath:
            mw._show_status_message(t('file_manager.file_changed', name=sn), 2000)
            idx = mw.navigator.current_index
            if hasattr(mw, 'cache_manager'):
                mw.cache_manager.invalidate(idx) 
            mw._load_current_image() 


    def on_file_moved(self, src_path: Path, dest_path: Path) -> None:
        mw = self._mw
        info_print(f"파일 이동 감지: {src_path.name} → {dest_path.name}")
        mw.navigator.update_file_path(src_path, dest_path)
        mw.thumbnail_bar.update_file_name(src_path, dest_path)
        if mw._current_file and mw._current_file == src_path:
            mw._current_file = dest_path
            if hasattr(mw, "metadata_panel"):
                mw.metadata_panel.load_metadata(dest_path)
            if hasattr(mw, "image_viewer"):
                mw.image_viewer.update_overlay()
        mw._show_status_message(t('file_manager.file_moved', name=dest_path.name), 2000)


    def on_batch_deleted(self, deleted_files: list) -> None:
        mw   = self._mw
        count = len(deleted_files)
        info_print(f"배치 삭제 감지: {count}개")
        mw._set_op_status(f"{count}개 삭제 감지 중...", 0, 0)

        # 하이라이트에서도 제거
        for fp in deleted_files:
            if mw.navigator.is_highlighted(fp):
                mw.navigator.toggle_highlight(fp)
            mw.thumbnail_bar.highlighted_files.discard(fp)

        next_index = mw.navigator.calculate_next_index_after_deletion(
            files_to_delete=deleted_files, deletion_mode="auto"
        )
        mw.navigator._temp_scan_prev_index = next_index

        def on_done(count_after):
            mw._clear_op_status(f"{count}개 삭제 완료", 2000)
            try:
                mw.navigator.folder_scan_completed.disconnect(on_done)
            except Exception:
                pass

        mw.navigator.folder_scan_completed.connect(on_done)
        mw.navigator.reload_async()


# ══════════════════════════════════════════════════════════════
# FileManager  (브릿지 컨트롤러)
# ══════════════════════════════════════════════════════════════

class FileManager:
    """
    MainWindow ↔ FileOperations / HighlightOperations /
                  FolderWatchHandler / FileWorkerThread 브릿지.

    main_window._init_core() 마지막에 초기화:
        self.file_manager = FileManager(self)
    """

    def __init__(self, main_window: "MainWindow") -> None:
        self._mw: "MainWindow"  = main_window
        self._file_ops           = FileOperations(main_window)
        self._hl_ops             = HighlightOperations(main_window)
        self._watch_handler      = FolderWatchHandler(main_window)
        self._file_worker: Optional[FileWorkerThread] = None

    # ── FileOperations 위임 ───────────────────────────────────

    def open_file_dialog(self)           -> None: self._file_ops.open_file_dialog()
    def open_folder_dialog(self)         -> None: self._file_ops.open_folder_dialog()
    def rename_file(self)                -> None: self._file_ops.rename_file()
    def paste_file(self)                 -> None: self._file_ops.paste_file()
    def copy_file_path(self)             -> None: self._file_ops.copy_file_path()
    def copy_image_to_clipboard(self)    -> None: self._file_ops.copy_image_to_clipboard()
    def open_file_location(self)         -> None: self._file_ops.open_file_location()
    def show_file_properties(self)       -> None: self._file_ops.show_file_properties()

    # ── HighlightOperations 위임 ──────────────────────────────

    def toggle_highlight(self)           -> None: self._hl_ops.toggle_highlight()
    def clear_all_highlights(self)       -> None: self._hl_ops.clear_all_highlights()
    def delete_highlighted_files(self)   -> None: self._hl_ops.delete_highlighted_files()
    def copy_highlighted_files(self)     -> None: self._hl_ops.copy_highlighted_files()
    def cut_highlighted_files(self)      -> None: self._hl_ops.cut_highlighted_files()

    # ── FolderWatchHandler 위임 ───────────────────────────────

    def on_file_added(self, filepath: Path)                        -> None: self._watch_handler.on_file_added(filepath)
    def on_file_deleted(self, filepath: Path)                      -> None: self._watch_handler.on_file_deleted(filepath)
    def on_file_modified(self, filepath: Path)                     -> None: self._watch_handler.on_file_modified(filepath)
    def on_file_moved(self, src: Path, dest: Path)                 -> None: self._watch_handler.on_file_moved(src, dest)
    def on_batch_deleted(self, deleted_files: list)                -> None: self._watch_handler.on_batch_deleted(deleted_files)

    # ── FileWorkerThread 관리 ─────────────────────────────────

    def run_file_worker(
        self,
        operation: str,
        files: list,
        target_folder=None,
    ) -> None:
        """백그라운드 파일 작업 실행. 이전 작업이 실행 중이면 취소 후 재시작."""
        mw = self._mw
        if self._file_worker and self._file_worker.isRunning():
            self._file_worker.cancel()
            try:
                self._file_worker.progress.disconnect() 
                self._file_worker.finished.disconnect()  
            except (RuntimeError, TypeError):
                pass
            self._file_worker.wait(3000)

        self._file_worker = FileWorkerThread(
            operation=operation,
            files=files,
            target_folder=target_folder,
            parent=mw,
        )
        self._file_worker.progress.connect(self._on_file_op_progress)
        self._file_worker.finished.connect(self._on_file_op_finished)
        self._file_worker.start()


    def _on_file_op_progress(
        self, current: int, total: int, filename: str, op_label: str
    ) -> None:
        self._mw._set_op_status(f"{op_label}: {filename}", current, total)


    def _on_file_op_finished(self, success: int, fail: int, operation: str) -> None:
        mw = self._mw
        op_label = {"copy": "복사", "move": "이동", "delete": "삭제"}.get(operation, operation)

        msg = f"{op_label} 완료: {success}개"
        if fail:
            msg += f" (실패: {fail}개)"
        mw._clear_op_status(msg, 3000)


    def cancel_worker(self) -> None:
        """앱 종료 시 호출."""
        if self._file_worker and self._file_worker.isRunning():
            self._file_worker.cancel()
            self._file_worker.wait(3000)


    def _get_target_files(self) -> list[Path]:
        """작업 대상 파일 결정: 하이라이트 우선, 없으면 현재 파일."""
        mw = self._mw
        highlighted = mw.navigator.get_highlighted_files()
        if highlighted:
            return highlighted
        if mw._current_file:
            return [mw._current_file]
        return []


    def copy_file(self) -> None:
        files = self._get_target_files()
        if not files:
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(f)) for f in files])
        if sys.platform == "win32":
            mime.setData("Preferred DropEffect", b"\x01\x00\x00\x00")
        QApplication.clipboard().setMimeData(mime)
        label = f"'{files[0].name}'" if len(files) == 1 else f"{len(files)}개 파일"
        self._mw._show_status_message(t('file_manager.label_copied', label=label), 2000)


    def cut_file(self) -> None:
        files = self._get_target_files()
        if not files:
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(f)) for f in files])
        if sys.platform == "win32":
            mime.setData("Preferred DropEffect", b"\x02\x00\x00\x00")
        QApplication.clipboard().setMimeData(mime)
        label = f"'{files[0].name}'" if len(files) == 1 else f"{len(files)}개 파일"
        self._mw._show_status_message(t('file_manager.label_cut', label=label), 2000)


    def delete_file(self) -> None:
        mw = self._mw
        files = self._get_target_files()
        if not files:
            return
        # 다중/단일 메시지 분기
        is_multi = len(files) > 1
        if is_multi:
            preview = "\n".join(f.name for f in files[:5])
            if len(files) > 5:
                preview += t('file_manager.delete_more', count=len(files) - 5)
            msg = t('file_manager.delete_multi_msg', count=len(files), preview=preview)
            mode = "multi"
        else:
            msg = t('file_manager.delete_single_msg', name=files[0].name)
            mode = "single"

        reply = QMessageBox.question(mw, t('file_manager.delete_file_title'), msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        next_index = mw.navigator.calculate_next_index_after_deletion(
            files_to_delete=files, deletion_mode=mode)
        mw.navigator._temp_scan_prev_index = next_index
        mw._run_file_worker("delete", files)

