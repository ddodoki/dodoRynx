# -*- coding: utf-8 -*-
# core/folder_navigator.py

"""
폴더 탐색 및 이미지 인덱싱 (비동기 로딩 + 정렬 기능)
"""

import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional, Set, Dict

from natsort import natsorted
from PySide6.QtCore import QObject, QMutex, QThread, QTimer, Signal

from core.metadata_reader import MetadataReader
from utils.debug import debug_print, error_print, info_print, warning_print


class SortOrder(Enum):

    """정렬 순서"""
    HIGHLIGHT = "highlight"  
    NAME = "name"           
    CREATED = "created"    
    MODIFIED = "modified"  
    SIZE = "size"           
    EXIF_DATE = "exif_date"      
    CAMERA_MODEL = "camera_model"


class SortWorkerThread(QThread):
    """
    파일 정렬 백그라운드 스레드.
    메인 스레드 블로킹 없이 정렬 수행.
    completed(list) 시그널로 결과 전달.
    """
    completed = Signal(list) 

    def __init__(
        self,
        files: List[Path],
        sort_order: "SortOrder",
        reverse: bool,
        highlighted: set,
        metadata_reader,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._files = files.copy()  
        self._sort_order = sort_order
        self._reverse = reverse
        self._highlighted = highlighted.copy()
        self._cancelled = False
        self._metadata_reader = metadata_reader


    def cancel(self) -> None:
        self._cancelled = True


    def run(self) -> None:
        try:
            result = self._do_sort()
            if not self._cancelled:
                self.completed.emit(result)
        except Exception as e:
            error_print(f"SortWorkerThread 실패: {e}")
            import traceback; traceback.print_exc()
            self.completed.emit(self._files) 


    def _do_sort(self) -> List[Path]:

        files = self._files.copy()
        order = self._sort_order

        if order == SortOrder.HIGHLIGHT:
            files.sort(
                key=lambda f: (f not in self._highlighted, f.name),
                reverse=self._reverse,
            )
            return files

        if order == SortOrder.NAME:
            return natsorted(files, reverse=self._reverse)

        if order in (SortOrder.CREATED, SortOrder.MODIFIED, SortOrder.SIZE):
            stat_cache: dict = {}
            for f in files:
                if self._cancelled:
                    return self._files
                try:
                    stat_cache[f] = f.stat()
                except (FileNotFoundError, PermissionError):
                    from types import SimpleNamespace
                    stat_cache[f] = SimpleNamespace(st_ctime=0, st_mtime=0, st_size=0)

            if order == SortOrder.CREATED:
                files.sort(key=lambda f: stat_cache[f].st_ctime, reverse=self._reverse)
            elif order == SortOrder.MODIFIED:
                files.sort(key=lambda f: stat_cache[f].st_mtime, reverse=self._reverse)
            else:
                files.sort(key=lambda f: stat_cache[f].st_size, reverse=self._reverse)
            return files

        if order == SortOrder.EXIF_DATE:
            exif_cache: dict = {}
            for filepath in files:
                if self._cancelled:
                    return self._files
                dt = self._get_exif_date(filepath)
                exif_cache[filepath] = (
                    dt is None,
                    dt or datetime.max,
                    filepath.name
                )
            files.sort(key=lambda f: exif_cache[f], reverse=self._reverse)
            return files

        if order == SortOrder.CAMERA_MODEL:
            cam_cache: dict = {}
            for filepath in files:
                if self._cancelled:
                    return self._files
                model = self._get_camera_model(filepath)
                cam_cache[filepath] = (
                    model is None,
                    (model or "").lower(),
                    filepath.name
                )
            files.sort(key=lambda f: cam_cache[f], reverse=self._reverse)
            return files

        return self._files 


    def _get_exif_date(self, filepath: Path) -> Optional[datetime]:
        """3단계 폴백: MetadataReader 캐시 → piexif → MetadataReader 전체 읽기"""

        # 1단계: 캐시 히트
        if self._metadata_reader:
            cached = self._metadata_reader.get_from_cache(filepath)
            if cached and 'camera' in cached:
                dt = self._parse_date_str(cached['camera'].get('date_taken', ''))
                if dt:
                    return dt

        # 2단계: piexif 직접 (JPEG/TIFF)
        ext = filepath.suffix.lower()
        if ext in ('.jpg', '.jpeg', '.tiff', '.tif'):
            try:
                import piexif
                exif_dict  = piexif.load(str(filepath))
                exif_ifd   = exif_dict.get("Exif", {})
                date_bytes = exif_ifd.get(piexif.ExifIFD.DateTimeOriginal, b"")
                if not date_bytes:
                    ifd0 = exif_dict.get("0th", {})
                    date_bytes = ifd0.get(piexif.ImageIFD.DateTime, b"")
                if date_bytes:
                    date_str = date_bytes.decode("ascii", errors="ignore").strip("\x00").strip()
                    dt = self._parse_date_str(date_str)
                    if dt:
                        return dt
            except Exception:
                pass

        # 3단계: MetadataReader 전체 읽기 (RAW/HEIC)
        if self._metadata_reader and ext in (
            '.nef', '.cr2', '.cr3', '.arw', '.dng',
            '.raf', '.orf', '.rw2', '.heic', '.heif', '.avif'
        ):
            try:
                meta = self._metadata_reader.read(filepath)
                if meta and 'camera' in meta:
                    return self._parse_date_str(meta['camera'].get('date_taken', ''))
            except Exception:
                pass

        return None


    def _get_camera_model(self, filepath: Path) -> Optional[str]: 
        """3단계 폴백: MetadataReader 캐시 → piexif → MetadataReader 전체 읽기"""

        # 1단계: 캐시 히트
        if self._metadata_reader:
            cached = self._metadata_reader.get_from_cache(filepath)
            if cached and 'camera' in cached:
                model = cached['camera'].get('model', '').strip()
                make  = cached['camera'].get('make', '').strip()
                if model:
                    return f"{make} {model}".strip() if make else model

        # 2단계: piexif 직접 (JPEG/TIFF)
        ext = filepath.suffix.lower()
        if ext in ('.jpg', '.jpeg', '.tiff', '.tif'):
            try:
                import piexif
                exif_dict = piexif.load(str(filepath))
                ifd       = exif_dict.get("0th", {})

                make  = ifd.get(piexif.ImageIFD.Make, b"")
                model = ifd.get(piexif.ImageIFD.Model, b"")

                if isinstance(make, bytes):
                    make  = make.decode("utf-8", errors="ignore").strip("\x00").strip()
                if isinstance(model, bytes):
                    model = model.decode("utf-8", errors="ignore").strip("\x00").strip()

                if model:
                    return f"{make} {model}".strip() if make else model
            except Exception:
                pass

        # 3단계: MetadataReader 전체 읽기 (RAW/HEIC)
        if self._metadata_reader and ext in (
            '.nef', '.cr2', '.cr3', '.arw', '.dng',
            '.raf', '.orf', '.rw2', '.heic', '.heif', '.avif'
        ):
            try:
                meta = self._metadata_reader.read(filepath)
                if meta and 'camera' in meta:
                    model = meta['camera'].get('model', '').strip()
                    make  = meta['camera'].get('make', '').strip()
                    if model:
                        return f"{make} {model}".strip() if make else model
            except Exception:
                pass

        return None


    def _parse_date_str(self, date_str: str) -> Optional[datetime]:
        """piexif 포맷(콜론)과 MetadataReader 포맷(하이픈) 모두 처리"""
        if not date_str:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None


class FolderScanThread(QThread):
    """폴더 스캔 백그라운드 스레드"""
    
    progress = Signal(int, int)  
    completed = Signal(list)    

    # ============================================
    # 초기화
    # ============================================    

    def __init__(self, folder: Path, supported_extensions: Set[str]):
        super().__init__()
        self.folder = folder
        self.supported_extensions = supported_extensions
        self._is_cancelled = False
    
    # ============================================
    # 스레드 실행
    # ============================================

    def run(self):
        try:
            files = []
            
            # ===== 취소 체크를 더 자주 =====
            with os.scandir(self.folder) as entries:
                for i, entry in enumerate(entries):
                    if self._is_cancelled:
                        info_print(f"🛑 스캔 취소됨")
                        return
                    
                    if entry.is_file() and entry.name.lower().endswith(tuple(self.supported_extensions)):
                        files.append(Path(entry.path))
                    
                    if i % 50 == 0: 
                        if self._is_cancelled:
                            info_print(f"🛑 스캔 취소됨")
                            return
                        self.progress.emit(i + 1, -1) 
            
            # ===== 취소 체크 =====
            if self._is_cancelled:
                info_print(f"🛑 정렬 전 취소됨")
                return
            
            files = natsorted(files)
            
            # ===== 최종 취소 체크 =====
            if self._is_cancelled:
                info_print(f"🛑 완료 직전 취소됨")
                return
            
            self.progress.emit(len(files), len(files))
            self.completed.emit(files)
            info_print(f"✅ 스캔 성공: {len(files)}개")
        
        except Exception as e:
            error_print(f"❌ 폴더 스캔 실패: {e}")
            self.completed.emit([])
    

    def cancel(self):
        """스캔 취소"""
        self._is_cancelled = True
        info_print(f"🚫 스캔 취소 플래그 설정")
    

class FolderNavigator(QObject):
    """폴더 내 이미지 파일 탐색"""
    
    # 지원 포맷 (RAW 추가)
    SUPPORTED_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp',
        '.heif', '.heic', '.avif', '.tiff', '.tif', '.jxl',
        # RAW 포맷
        '.nef', '.cr2', '.cr3', '.arw', '.dng', '.raf',
        '.orf', '.rw2', '.pef', '.srw',
    }
    
    folder_changed = Signal(Path)    
    index_changed = Signal(int)       
    folder_scan_started = Signal()     
    folder_scan_progress = Signal(int, int) 
    folder_scan_completed = Signal(int)  
    highlight_changed       = Signal(Path, bool)  
    highlights_cleared      = Signal()         
    highlights_set          = Signal(set)        
    sort_order_changed = Signal(object, bool) 

    # ============================================
    # 초기화
    # ============================================

    def __init__(self):
        super().__init__()
        self.current_folder: Optional[Path] = None
        self.image_files: List[Path] = []
        self.current_index: int = -1
        self._highlighted: Set[Path] = set()
        self._highlights_by_folder: Dict[Path, Set[Path]] = {}
        self._temp_scan_prev_index: int = 0 

        # ===== 임시 하이라이트 (1회용 선택) =====
        self._temporary_highlights: Set[Path] = set()        

        # 비동기 스캔
        self.scan_thread: Optional[FolderScanThread] = None
        self._sort_thread: Optional[SortWorkerThread] = None

        # 정렬 순서
        self.current_sort_order = SortOrder.NAME
        self._sort_reverse: bool = False
        self._sort_by_folder: Dict[Path, tuple[SortOrder, bool]] = {}

        # ===== 메타데이터 리더 =====
        self.metadata_reader = MetadataReader(use_cache=True, max_cache_size=500)

        # 스캔 중복 방지 플래그
        self._scan_in_progress = False
        self._scan_lock = QMutex()

        self._pending_scan_request: Optional[tuple[Path, Optional[Path], bool]] = None
        self._bulk_deleting: bool = False

    # ============================================
    # 폴더 열기 및 인덱싱
    # ============================================

    def open_file(self, file_path: Path) -> bool:
        """
        파일 열기 및 같은 폴더 내 이미지 인덱싱 (비동기)
        """
        if not file_path.exists() or not file_path.is_file():
            return False
        
        # 지원하지 않는 포맷
        if file_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            return False
        
        folder = file_path.parent
        
        # 폴더가 바뀌었으면 재인덱싱 (비동기)
        if folder != self.current_folder:
            self._index_folder_async(folder, file_path, preserve_sort=False)
            return True
        
        # 같은 폴더 내에서 파일 변경
        try:
            self.current_index = self.image_files.index(file_path)
            self.index_changed.emit(self.current_index)
            return True
        except ValueError:
            return False
    

    def set_folder(self, folder: Path, index: int = 0) -> List[Path]:
        """
        폴더 설정 및 인덱싱 (비동기)
        """
        if not folder.exists() or not folder.is_dir():
            error_print(f"유효하지 않은 폴더: {folder}")
            return []
        
        self._index_folder_async(folder, initial_file=None)

        return []


    def scan_folder(self, folder: Path) -> List[Path]:
        """폴더 스캔 (set_folder의 별칭)"""
        return self.set_folder(folder, 0)


    def set_file_list(self, files: List[Path], current_index: int = 0):
        new_folder = files[0].parent if files else None
        folder_changed = bool(new_folder and new_folder != self.current_folder)

        if folder_changed and new_folder is not None:
            self._save_highlights()
            self._save_sort_order()      
            self._restore_highlights(new_folder)
            self._restore_sort_order(new_folder)  

        self.image_files = files
        self.current_index = current_index

        if files:
            self.current_folder = files[0].parent
            self.folder_changed.emit(self.current_folder)
            self.index_changed.emit(current_index)

        if folder_changed and new_folder is not None:
            self.highlights_set.emit(self._highlighted.copy())

        self.folder_scan_completed.emit(len(self.image_files))     


    def calculate_next_index_after_deletion(
        self,
        files_to_delete: List[Path],
        deletion_mode: str = "auto"
    ) -> int:
        """
        파일 삭제 후 이동할 인덱스 계산
        """
        if not files_to_delete or not self.image_files:
            return 0
        
        total_files = len(self.image_files)
        remaining_count = total_files - len(files_to_delete)
        
        if remaining_count <= 0:
            return 0
        
        # 삭제 모드 자동 감지
        if deletion_mode == "auto":
            deletion_mode = "single" if len(files_to_delete) == 1 else "multi"
        
        if deletion_mode == "single":
            # ===== 단일 삭제: 마지막이면 이전, 중간이면 다음 =====
            try:
                current_index = self.image_files.index(files_to_delete[0])
            except ValueError:
                return 0
            
            is_last = (current_index == total_files - 1)
            
            if is_last:
                next_index = max(0, current_index - 1)
                info_print(f"단일 삭제 (마지막) → 이전: {next_index}")
            else:
                next_index = current_index
                info_print(f"단일 삭제 (중간) → 같은 인덱스: {next_index}")
            
            return next_index
        
        else:
            # ===== 다중 삭제: 삭제 범위의 바로 다음 파일 (Windows 방식) =====
            try:
                # 삭제할 파일들의 인덱스 찾기
                indices_to_delete = []
                index_map = {path: i for i, path in enumerate(self.image_files)}
                indices_to_delete = [
                    index_map[p] for p in files_to_delete if p in index_map
                ]

                if not indices_to_delete:
                    return 0
                
                # 정렬하여 범위 파악
                indices_to_delete.sort()
                first_deleted_idx = indices_to_delete[0]
                last_deleted_idx = indices_to_delete[-1]
                
                next_index = first_deleted_idx
                
                # 범위 초과 시 마지막 파일
                if next_index >= remaining_count:
                    next_index = remaining_count - 1
                
                info_print(
                    f"다중 삭제 [{first_deleted_idx}~{last_deleted_idx}] → "
                    f"다음 파일 선택: {next_index} (남은 {remaining_count}개)"
                )
                
                return next_index
            
            except Exception as e:
                error_print(f"인덱스 계산 실패: {e}")
                return 0

    # ============================================
    # 비동기 스캔 (내부)
    # ============================================

    def _index_folder_async(
        self,
        folder: Path,
        initial_file: Optional[Path] = None,
        preserve_sort: bool = False
    ):
        if not self._scan_lock.tryLock():
            self._pending_scan_request = (folder, initial_file, preserve_sort)
            warning_print("⚠️ 이미 스캔 중 - 요청 큐에 저장")
            return

        try:
            if self._scan_in_progress:
                self._pending_scan_request = (folder, initial_file, preserve_sort)
                warning_print("⚠️ 스캔 진행 중 - 요청 큐에 저장")
                return

            self._scan_in_progress = True
            self._pending_scan_request = None

            # ── 기존 스레드 정리 ──────────────────────────────
            if self.scan_thread:
                old_thread = self.scan_thread
                self.scan_thread = None

                try:
                    old_thread.progress.disconnect()
                    old_thread.completed.disconnect()
                except Exception:
                    pass

                old_thread.cancel()

                if old_thread.isRunning():
                    if not old_thread.wait(300):
                        warning_print("⚠️ 이전 스캔 스레드 아직 실행 중 — 계속 진행")

                old_thread.deleteLater()

            # ── 상태 설정 ─────────────────────────────────────
            self._temp_scan_prev_index = self.current_index

            if self.current_folder != folder:
                self._save_highlights()
                self._save_sort_order()
                self._restore_highlights(folder)
                self._restore_sort_order(folder)
                self._temporary_highlights.clear()
                self.current_folder = folder
                self.folder_changed.emit(folder)

            self.folder_scan_started.emit()

            # ── 새 스레드 시작 ────────────────────────────────
            self.scan_thread = FolderScanThread(folder, self.SUPPORTED_EXTENSIONS)
            self.scan_thread.progress.connect(self._on_scan_progress)
            self.scan_thread.completed.connect(
                lambda files: self._on_scan_completed(files, initial_file)
            )
            self.scan_thread.start()
            info_print(f"▶️ 스캔 스레드 시작: {folder}")

        except Exception as e:
            error_print(f"_index_folder_async 실패: {e}")
            self._scan_in_progress = False
            self.scan_thread = None

        finally:
            self._scan_lock.unlock()


    def _index_folder(self, folder: Path):
        """폴더 내 모든 이미지 파일 인덱싱 (동기)"""
        try:
            files = [
                file_path for file_path in folder.iterdir()
                if file_path.is_file() and 
                file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS
            ]
            self.image_files = natsorted(files)
        except Exception as e:
            error_print(f"폴더 인덱싱 실패 {folder}: {e}")
            self.image_files = []


    def _on_scan_progress(self, current: int, total: int):
        """스캔 진행"""
        self.folder_scan_progress.emit(current, total)


    def _on_scan_completed(self, files: List[Path], initial_file: Optional[Path] = None):
        try:

            if self._sort_thread and self._sort_thread.isRunning():
                self._sort_thread.cancel()
                try:
                    self._sort_thread.completed.disconnect()
                except (RuntimeError, TypeError):
                    pass
                debug_print("on_scan_completed: 진행 중 정렬 취소됨")

            if self.current_sort_order != SortOrder.NAME or self._sort_reverse:
                self.image_files = files
                self._reapply_sort_after_reload(files, initial_file)
                return

            self.image_files = files

            if self._highlighted:
                valid_files = set(self.image_files)
                self._highlighted &= valid_files
            if self._temporary_highlights:
                valid_files = set(self.image_files)
                self._temporary_highlights &= valid_files

            prev_index = getattr(self, '_temp_scan_prev_index', 0)
            if initial_file and initial_file in self.image_files:
                self.current_index = self.image_files.index(initial_file)
            elif 0 <= prev_index < len(self.image_files):
                self.current_index = prev_index
            elif self.image_files:
                self.current_index = min(prev_index, len(self.image_files) - 1)
            else:
                self.current_index = -1

            if self.image_files and self.current_index < 0:
                self.current_index = 0

            self._temp_scan_prev_index = 0
            prev_index = self._temp_scan_prev_index

            self.folder_scan_completed.emit(len(self.image_files))
            self.index_changed.emit(self.current_index)
            self.highlights_set.emit(self._highlighted.copy())

            if not self.image_files:
                info_print("폴더가 비어있음 - index_changed(-1) emit")
            else:
                info_print(f"스캔 완료: {len(self.image_files)}개, index={self.current_index}")

        except Exception as e:
            error_print(f"_on_scan_completed 오류: {e}")

        finally:
            self.scan_thread = None
            self._scan_in_progress = False
            req = getattr(self, "_pending_scan_request", None)
            if req:
                self._pending_scan_request = None
                folder, pending_initial, pending_preserve = req
                QTimer.singleShot(
                    0,
                    lambda f=folder, i=pending_initial, p=pending_preserve:
                        self._index_folder_async(f, i, p)
                )

                
    def _reapply_sort_after_reload(
        self,
        new_files: List[Path],
        initial_file: Optional[Path] = None,
    ) -> None:
        current_file = initial_file
        snapshot_folder = self.current_folder 

        if self._highlighted:
            valid = set(new_files)
            self._highlighted &= valid
        if self._temporary_highlights:
            valid = set(new_files)
            self._temporary_highlights &= valid

        self._sort_thread = SortWorkerThread(
            files=new_files,
            sort_order=self.current_sort_order,
            reverse=self._sort_reverse,
            highlighted=self._highlighted,
            metadata_reader=self.metadata_reader,
            parent=self,
        )

        def _on_resort_done(sorted_files: list) -> None:
            if self.current_folder != snapshot_folder:
                warning_print(
                    f"_on_resort_done: 폴더 변경됨 "
                    f"({snapshot_folder.name if snapshot_folder else 'None'} → " 
                    f"{self.current_folder.name if self.current_folder else 'None'}), 결과 무시"
                )
                return

            self.image_files = sorted_files
            self._restore_current_index(current_file)
            self.folder_scan_completed.emit(len(self.image_files))
            self.highlights_set.emit(self._highlighted.copy())
            info_print(
                f"파일 변경 후 재정렬 완료: "
                f"{self.current_sort_order.value}, reverse={self._sort_reverse}, "
                f"총 {len(self.image_files)}개"
            )

        self._sort_thread.completed.connect(_on_resort_done)
        self._sort_thread.start()


    def sort_files_async(
        self,
        sort_order: SortOrder,
        reverse: bool = False,
        on_completed=None,
    ) -> None:
        if not self.image_files:
            warning_print("정렬할 파일 없음")
            return

        current_file = self.current()
        snapshot_folder = self.current_folder

        if self._sort_thread and self._sort_thread.isRunning():
            self._sort_thread.cancel()
            try:
                self._sort_thread.completed.disconnect()
            except (RuntimeError, TypeError):
                pass

        self._sort_thread = SortWorkerThread(
            files=self.image_files,
            sort_order=sort_order,
            reverse=reverse,
            highlighted=self._highlighted,
            metadata_reader=self.metadata_reader,
            parent=self,
        )

        def _on_thread_completed(sorted_files: list) -> None:
            if self.current_folder != snapshot_folder:
                warning_print(
                    f"sort_files_async: 폴더 변경됨 "
                    f"({snapshot_folder.name if snapshot_folder else 'None'} → "
                    f"{self.current_folder.name if self.current_folder else 'None'}), 결과 무시"
                )
                return
            self.image_files = sorted_files
            self.current_sort_order = sort_order
            self._sort_reverse = reverse
            self._save_sort_order()
            self.sort_order_changed.emit(sort_order, reverse)
            self._restore_current_index(current_file)
            info_print(f"정렬 완료: {sort_order.value}, reverse={reverse}")
            if on_completed:
                on_completed()

        self._sort_thread.completed.connect(_on_thread_completed)
        self._sort_thread.start()

    # ============================================
    # 폴더 새로고침
    # ============================================

    def reload(self) -> None:
        """파일 변경에 의한 새로고침 — 정렬 유지"""
        if not self.current_folder:
            return
        current_file = self.current()
        info_print(f"폴더 새로고침: {self.current_folder}")
        self._index_folder_async(
            self.current_folder,
            initial_file=current_file,
            preserve_sort=True, 
        )
        

    def reload_async(self) -> None:
        """폴더 새로고침 (reload 별칭)"""
        self.reload()


    def reload_after_deletion(self) -> None:
        """삭제 후 전용 reload — _temp_scan_prev_index(next_index) 우선 사용"""
        if not self.current_folder:
            return

        if self._bulk_deleting:
            info_print("⏸️ reload_after_deletion: 다중 삭제 중 — 스킵")
            return

        saved_index = self._temp_scan_prev_index
        self._index_folder_async(
            self.current_folder,
            initial_file=None,
            preserve_sort=True,
        )
        self._temp_scan_prev_index = saved_index


    def bulk_delete_start(self) -> None:
        """다중 삭제 시작 알림 — reload 억제 모드 진입"""
        self._bulk_deleting = True
        info_print("🗑️ bulk_delete_start: reload 억제 시작")


    def bulk_delete_end(self, next_index: int = 0) -> None:
        """다중 삭제 완료 — 플래그 해제 및 인덱스 저장만.
        실제 reload는 resume_events() → on_batch_deleted에서 1회 처리."""
        self._bulk_deleting = False
        self._temp_scan_prev_index = next_index
        info_print(f"✅ bulk_delete_end: bulk 모드 해제 (next_index={next_index})")
        
    # ============================================
    # 네비게이션 (이동)
    # ============================================

    def go_to(self, index: int) -> Optional[Path]:
        """
        특정 인덱스로 이동하는 단일 공개 API.
        모든 외부 코드(MainWindow, ThumbnailBar 등)는 반드시 이 메서드를 통해
        current_index를 변경해야 함 → navigator 내부 상태 일관성 보장.
        """
        if not self.image_files:
            return None
        if not (0 <= index < len(self.image_files)):
            warning_print(f"go_to: 범위 초과 index={index}, total={len(self.image_files)}")
            return None
        self.current_index = index
        self.index_changed.emit(self.current_index)
        return self.image_files[self.current_index]


    def has_prev(self) -> bool:
        """이전 이미지가 있는지 확인"""
        return self.current_index > 0
    

    def has_next(self) -> bool:
        """다음 이미지가 있는지 확인"""
        return len(self.image_files) > 0 and self.current_index < len(self.image_files) - 1
    

    def next(self) -> Optional[Path]:
        """
        다음 이미지로 이동.
        마지막 이미지에서 호출 시 이동하지 않고 None 반환 (경계 정지).
        """
        if not self.image_files:
            return None
        if self.current_index >= len(self.image_files) - 1:
            debug_print("next(): 마지막 이미지 — 이동 없음")
            return None
        return self.go_to(self.current_index + 1)


    def previous(self) -> Optional[Path]:
        """
        이전 이미지로 이동.
        첫 번째 이미지에서 호출 시 이동하지 않고 None 반환 (경계 정지).
        """
        if not self.image_files:
            return None
        if self.current_index <= 0:
            debug_print("previous(): 첫 번째 이미지 — 이동 없음")
            return None
        return self.go_to(self.current_index - 1)


    def first(self) -> Optional[Path]:
        """첫 번째 이미지"""
        if not self.image_files:
            return None
        return self.go_to(0)


    def last(self) -> Optional[Path]:
        """마지막 이미지"""
        if not self.image_files:
            return None
        return self.go_to(len(self.image_files) - 1) 


    def current(self) -> Optional[Path]:
        """현재 이미지"""
        if 0 <= self.current_index < len(self.image_files):
            return self.image_files[self.current_index]
        return None

    # ============================================
    # 정렬
    # ============================================

    def _restore_current_index(self, current_file: Optional[Path]) -> None:
        """현재 인덱스 복원 및 시그널 발생"""

        if current_file and current_file in self.image_files:
            self.current_index = self.image_files.index(current_file)
        elif self.image_files:
            fallback = min(self._temp_scan_prev_index, len(self.image_files) - 1)
            self.current_index = max(0, fallback)
        else:
            self.current_index = -1

        self._temp_scan_prev_index = self.current_index
        self.index_changed.emit(self.current_index)


    def get_sort_order(self) -> SortOrder:
        """현재 정렬 순서"""
        return self.current_sort_order


    def get_sort_state(self) -> tuple[SortOrder, bool]:
        """현재 정렬 상태 반환 (외부 접근 전용 API)"""
        return (self.current_sort_order, self._sort_reverse)
    
    # ============================================
    # 정렬 관리 - 내부
    # ============================================

    def _save_sort_order(self) -> None:
        if self.current_folder is None:
            return

        order = self.current_sort_order
        reverse = False if order == SortOrder.HIGHLIGHT else self._sort_reverse
        if order != SortOrder.NAME or reverse:
            self._sort_by_folder[self.current_folder] = (order, reverse)
            if len(self._sort_by_folder) > 100: 
                oldest = next(iter(self._sort_by_folder))
                del self._sort_by_folder[oldest]
        else:
            self._sort_by_folder.pop(self.current_folder, None)
            

    def _restore_sort_order(self, folder: Path) -> None:
        """폴더의 저장된 정렬 기준 복원. 미방문 폴더는 NAME 기본값."""
        order, reverse = self._sort_by_folder.get(
            folder, (SortOrder.NAME, False)
        )
        self.current_sort_order = order
        self._sort_reverse = reverse
        self.sort_order_changed.emit(order, reverse) 

    # ============================================
    # 하이라이트 관리 - 토글
    # ============================================

    def toggle_highlight(self, file_path: Optional[Path] = None) -> bool:
        """하이라이트 토글"""
        if file_path is None:
            file_path = self.current()
        if not file_path:
            return False

        if file_path in self._highlighted:
            self._highlighted.remove(file_path)
            self.highlight_changed.emit(file_path, False)
            return False
        else:
            self._highlighted.add(file_path)
            self.highlight_changed.emit(file_path, True)
            return True
        

    def toggle_highlight_by_index(self, index: int) -> bool:
        """인덱스로 하이라이트 토글"""
        if 0 <= index < len(self.image_files):
            return self.toggle_highlight(self.image_files[index])
        return False


    def toggle_current_highlight(self) -> bool:
        """현재 파일의 하이라이트 토글"""
        return self.toggle_highlight(self.current())

    # ============================================
    # 하이라이트 관리 - 내부
    # ============================================

    def _save_highlights(self) -> None:
        if self.current_folder is None:
            return
        if self._highlighted:
            self._highlights_by_folder[self.current_folder] = self._highlighted
            if len(self._highlights_by_folder) > 200: 
                empty = [k for k, v in self._highlights_by_folder.items() if not v]
                for k in empty[:50]:
                    del self._highlights_by_folder[k]
        else:
            self._highlights_by_folder.pop(self.current_folder, None)


    def _restore_highlights(self, folder: Path) -> None:
        if folder in self._highlights_by_folder:
            self._highlighted = self._highlights_by_folder[folder]
        else:
            self._highlighted = set()

    # ============================================
    # 하이라이트 관리 - 범위
    # ============================================

    def highlight_range(self, start_index: int, end_index: int) -> int:
        """범위 내 파일들을 하이라이트"""
        count = 0
        start = max(0, min(start_index, end_index))
        end = min(len(self.image_files) - 1, max(start_index, end_index))
        
        for i in range(start, end + 1):
            if i < len(self.image_files):
                file_path = self.image_files[i]
                if file_path not in self._highlighted:
                    self._highlighted.add(file_path)
                    self.highlight_changed.emit(file_path, True) 
                    count += 1

        return count


    def unhighlight_range(self, start_index: int, end_index: int) -> int:
        """범위 내 파일들의 하이라이트 해제"""
        count = 0
        start = max(0, min(start_index, end_index))
        end = min(len(self.image_files) - 1, max(start_index, end_index))
        
        for i in range(start, end + 1):
            if i < len(self.image_files):
                file_path = self.image_files[i]
                if file_path in self._highlighted:
                    self._highlighted.remove(file_path)
                    self.highlight_changed.emit(file_path, False)
                    count += 1
        
        return count


    def clear_highlights(self) -> None:
        """현재 폴더의 하이라이트만 해제"""
        if self._highlighted:
            self._highlighted.clear()
            self.highlights_cleared.emit()


    def clear_all_highlights_all_folders(self) -> int:
        total = self.get_total_highlight_count()
        self._highlights_by_folder.clear()
        self._highlighted.clear()
        self.highlights_cleared.emit() 
        return total


    def set_highlights(self, file_paths: set) -> None:
        self._highlighted = set(file_paths)
        if self.current_folder is not None:
            self._highlights_by_folder[self.current_folder] = self._highlighted
        self.highlights_set.emit(self._highlighted.copy())

    # ============================================
    # 임시 하이라이트 관리
    # ============================================

    def set_temporary_highlights(self, files: List[Path]) -> None:
        """
        임시 하이라이트 설정 (붙여넣기 등)
        기존 임시 하이라이트는 자동으로 제거됨
        """
        self._temporary_highlights = set(files)
        info_print(f"임시 하이라이트 설정: {len(files)}개")
    
    
    def clear_temporary_highlights(self) -> None:
        """임시 하이라이트 모두 해제"""
        count = len(self._temporary_highlights)
        if count > 0:
            self._temporary_highlights.clear()
            info_print(f"임시 하이라이트 해제: {count}개")
    
    
    def get_temporary_highlights(self) -> List[Path]:
        """임시 하이라이트 목록 반환"""
        return [f for f in self.image_files if f in self._temporary_highlights]
    
    
    def is_temporarily_highlighted(self, file_path: Optional[Path] = None) -> bool:
        """
        임시 하이라이트 여부 확인
        """
        if file_path is None:
            file_path = self.current()
        
        if not file_path:
            return False
        
        return file_path in self._temporary_highlights

    # ============================================
    # 하이라이트 조회
    # ============================================

    def is_highlighted(self, file_path: Optional[Path] = None) -> bool:
        """하이라이트 여부 확인"""
        if file_path is None:
            file_path = self.current()
        
        if not file_path:
            return False
        
        return file_path in self._highlighted
    

    def is_highlighted_by_index(self, index: int) -> bool:
        """인덱스로 하이라이트 여부 확인"""
        if 0 <= index < len(self.image_files):
            return self.image_files[index] in self._highlighted
        return False


    def is_current_highlighted(self) -> bool:
        """현재 파일이 하이라이트되었는지 확인"""
        return self.is_highlighted(self.current())


    def get_highlighted_files(self) -> List[Path]:
        """하이라이트된 파일 목록 반환"""
        return [f for f in self.image_files if f in self._highlighted]


    def get_highlighted_indices(self) -> List[int]:
        """하이라이트된 파일들의 인덱스 반환"""
        indices = []
        for i, file_path in enumerate(self.image_files):
            if file_path in self._highlighted:
                indices.append(i)
        return indices
    

    def get_highlight_count(self) -> int:
        """하이라이트된 파일 수"""
        return len(self._highlighted)    
    
    # ============================================
    # 파일 목록 조회
    # ============================================

    def get_image_list(self) -> List[Path]:
        """전체 이미지 목록"""
        return self.image_files.copy()


    def get_current_folder(self) -> Optional[Path]:
        """
        현재 열린 폴더 경로 반환
        """
        return self.current_folder


    def get_progress(self) -> tuple[int, int]:
        """진행 상황 (현재 인덱스, 전체 개수)"""
        return (self.current_index + 1, len(self.image_files))
    
    # ============================================
    # 파일 경로 업데이트
    # ============================================

    def update_file_path(self, old_path: Path, new_path: Path) -> bool:
        try:
            if old_path not in self.image_files:
                return False

            index = self.image_files.index(old_path)

            if self.current_sort_order == SortOrder.NAME:
                self.image_files.pop(index)     
                self.image_files = natsorted(
                    self.image_files + [new_path], reverse=self._sort_reverse
                )
            else:
                self.image_files[index] = new_path  

            for hl_set in self._highlights_by_folder.values():
                if old_path in hl_set:
                    hl_set.discard(old_path)
                    hl_set.add(new_path)

            if new_path in self.image_files:
                self.current_index = self.image_files.index(new_path)

            return True
        except Exception as e:
            error_print(f"update_file_path 오류: {e}")
            return False
    

    def get_all_highlighted_files(self, check_exists: bool = False) -> List[Path]:
        result = []
        for hl_set in self._highlights_by_folder.values():
            if check_exists:
                result.extend(f for f in hl_set if f.exists())
            else:
                result.extend(hl_set)
        return result


    def get_total_highlight_count(self) -> int:
        """전체 폴더 합산 하이라이트 수 (상태바 표시용)"""
        return sum(len(s) for s in self._highlights_by_folder.values())

    # ============================================
    # 상태 정보
    # ============================================
    
    def get_status(self) -> dict:
        """현재 상태 정보 반환"""
        return {
            'folder': str(self.current_folder) if self.current_folder else None,
            'total_files': len(self.image_files),
            'current_index': self.current_index,
            'current_file': str(self.current()) if self.current() else None,
            'highlighted_count': len(self._highlighted),
            'progress': self.get_progress(),
            'sort_order': self.current_sort_order.value,
        }

