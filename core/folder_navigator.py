# -*- coding: utf-8 -*-
# core/folder_navigator.py

"""
폴더 탐색 및 이미지 인덱싱 (비동기 로딩 + 정렬 기능)
"""

import os
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional, Set

from natsort import natsorted
from PySide6.QtCore import QObject, QMutex, QThread, QTimer, Signal

from core.metadata_reader import MetadataReader
from utils.debug import debug_print, error_print, info_print, warning_print


class SortOrder(Enum):

    """정렬 순서"""
    HIGHLIGHT = "highlight"  # 하이라이트 순
    NAME = "name"            # 파일명 순
    CREATED = "created"      # 생성일 순
    MODIFIED = "modified"    # 수정일 순
    SIZE = "size"            # 파일 크기 순
    EXIF_DATE = "exif_date"        # EXIF 촬영 날짜
    CAMERA_MODEL = "camera_model"  # 카메라 기종


class SortWorkerThread(QThread):
    """
    파일 정렬 백그라운드 스레드.
    메인 스레드 블로킹 없이 정렬 수행.
    completed(list) 시그널로 결과 전달.
    """
    completed = Signal(list)  # 정렬된 파일 목록

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
            self.completed.emit(self._files)  # 폴백: 원본 순서

    def _do_sort(self) -> List[Path]:
        from natsort import natsorted

        files = self._files.copy()
        order = self._sort_order

        # ── 빠른 정렬 (I/O 없음) ─────────────────────────────
        if order == SortOrder.HIGHLIGHT:
            files.sort(
                key=lambda f: (f not in self._highlighted, f.name),
                reverse=self._reverse,
            )
            return files

        if order == SortOrder.NAME:
            return natsorted(files, reverse=self._reverse)

        # ── 파일 시스템 정렬 ──────────────────────────────────
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

        # ── EXIF 날짜 정렬 (느림 — 스레드에서 실행되므로 OK) ──

        if order == SortOrder.EXIF_DATE:
            import piexif

            exif_cache: dict = {}
            for filepath in files:
                if self._cancelled:
                    return self._files
                try:
                    exif_dict = piexif.load(str(filepath))
                    exif_ifd = exif_dict.get("Exif", {})

                    # DateTimeOriginal만 읽음 (GPS IFD 완전 무시)
                    date_bytes = exif_ifd.get(piexif.ExifIFD.DateTimeOriginal, b"")
                    if not date_bytes:
                        # 폴백: IFD0 DateTime
                        ifd0 = exif_dict.get("0th", {})
                        date_bytes = ifd0.get(piexif.ImageIFD.DateTime, b"")

                    if isinstance(date_bytes, bytes):
                        date_str = date_bytes.decode("ascii", errors="ignore").strip("\x00").strip()
                    else:
                        date_str = ""

                    if date_str:
                        # piexif 원시 포맷: "YYYY:MM:DD HH:MM:SS" (하이픈 아님)
                        dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                        exif_cache[filepath] = (False, dt, filepath.name)
                    else:
                        exif_cache[filepath] = (True, datetime.max, filepath.name)

                except Exception:
                    # piexif 미지원 포맷 (RAW, PNG 등) → 맨 뒤로
                    exif_cache[filepath] = (True, datetime.max, filepath.name)

            files.sort(key=lambda f: exif_cache[f], reverse=self._reverse)
            return files

        # ── 카메라 기종 정렬 ──────────────────────────────────
        if order == SortOrder.CAMERA_MODEL:
            import piexif

            cam_cache: dict = {}
            for filepath in files:
                if self._cancelled:
                    return self._files
                try:
                    # metadata_reader 대신 piexif로 Make/Model만 직접 읽기
                    exif_dict = piexif.load(str(filepath))
                    ifd = exif_dict.get("0th", {})

                    make = ifd.get(piexif.ImageIFD.Make, b"")
                    if isinstance(make, bytes):
                        make = make.decode("utf-8", errors="ignore").strip("\x00").strip()

                    model = ifd.get(piexif.ImageIFD.Model, b"")
                    if isinstance(model, bytes):
                        model = model.decode("utf-8", errors="ignore").strip("\x00").strip()

                    if model:
                        full = f"{make} {model}".strip() if make else model
                        cam_cache[filepath] = (False, full.lower(), filepath.name)
                    else:
                        cam_cache[filepath] = (True, "", filepath.name)

                except Exception:
                    # piexif 미지원 포맷 (PNG, BMP 등) → 맨 뒤로
                    cam_cache[filepath] = (True, "", filepath.name)

            files.sort(key=lambda f: cam_cache[f], reverse=self._reverse)
            return files
    
        return self._files


class FolderScanThread(QThread):
    """폴더 스캔 백그라운드 스레드"""
    
    progress = Signal(int, int)  # (현재, 전체)
    completed = Signal(list)     # 스캔 완료 (파일 리스트)


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
                    # 매 파일마다 취소 체크
                    if self._is_cancelled:
                        info_print(f"🛑 스캔 취소됨")
                        return
                    
                    if entry.is_file() and entry.name.lower().endswith(tuple(self.supported_extensions)):
                        files.append(Path(entry.path))
                    
                    # 진행 상황 업데이트 (빈도 감소)
                    if i % 50 == 0:  # 10 → 50으로 변경
                        if self._is_cancelled:
                            info_print(f"🛑 스캔 취소됨")
                            return
                        self.progress.emit(i + 1, -1)  # 전체 개수는 -1
            
            # ===== 취소 체크 =====
            if self._is_cancelled:
                info_print(f"🛑 정렬 전 취소됨")
                return
            
            # 정렬
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
    
    folder_changed = Signal(Path)         # 폴더 변경
    index_changed = Signal(int)           # 인덱스 변경
    folder_scan_started = Signal()        # 스캔 시작
    folder_scan_progress = Signal(int, int)  # 스캔 진행 (현재, 전체)
    folder_scan_completed = Signal(int)   # 스캔 완료 (파일 수)
    highlight_changed       = Signal(Path, bool)   # (file_path, is_highlighted)
    highlights_cleared      = Signal()             # 전체 해제 시
    highlights_set          = Signal(set)          # 일괄 설정 시 (set[Path])
    

# ============================================
# 초기화
# ============================================

    def __init__(self):
        super().__init__()
        self.current_folder: Optional[Path] = None
        self.image_files: List[Path] = []
        self.current_index: int = -1
        self._highlighted: Set[Path] = set()
        self._temp_scan_prev_index: int = 0 

        # ===== 추가: 임시 하이라이트 (1회용 선택) =====
        self._temporary_highlights: Set[Path] = set()        

        # 비동기 스캔
        self.scan_thread: Optional[FolderScanThread] = None
        self._sort_thread: Optional[SortWorkerThread] = None

        # 정렬 순서
        self.current_sort_order = SortOrder.NAME

        # ===== 메타데이터 리더 추가 =====
        self.metadata_reader = MetadataReader(use_cache=True, max_cache_size=500)

        # 스캔 중복 방지 플래그
        self._scan_in_progress = False
        self._scan_lock = QMutex()  # 추가

        self._pending_scan_request: Optional[tuple[Path, Optional[Path]]] = None


# ============================================
# 폴더 열기 및 인덱싱
# ============================================

    def open_file(self, file_path: Path) -> bool:
        """
        파일 열기 및 같은 폴더 내 이미지 인덱싱 (비동기)
        
        Returns:
            성공 여부
        """
        if not file_path.exists() or not file_path.is_file():
            return False
        
        # 지원하지 않는 포맷
        if file_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            return False
        
        folder = file_path.parent
        
        # 폴더가 바뀌었으면 재인덱싱 (비동기)
        if folder != self.current_folder:
            self._index_folder_async(folder, file_path)
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
        
        Args:
            folder: 대상 폴더
            index: 초기 인덱스 (사용 안 함 - 비동기이므로 initial_file로 처리)
        
        Returns:
            빈 리스트 (비동기이므로 완료 전까지 파일 목록 없음)
        """
        if not folder.exists() or not folder.is_dir():
            error_print(f"유효하지 않은 폴더: {folder}")
            return []
        
        # 비동기 스캔 시작
        self._index_folder_async(folder, initial_file=None)
        
        # 비동기 완료 전까지는 빈 리스트
        return []


    def scan_folder(self, folder: Path) -> List[Path]:
        """폴더 스캔 (set_folder의 별칭)"""
        return self.set_folder(folder, 0)


    def set_file_list(self, files: List[Path], current_index: int = 0):
        """
        파일 목록 직접 설정 (폴더 DnD용)
        
        Args:
            files: 이미지 파일 목록
            current_index: 초기 인덱스
        """
        self.image_files = files
        self.current_index = current_index
        self._highlighted.clear()
        
        if files:
            self.current_folder = files[0].parent
            self.folder_changed.emit(self.current_folder)
            self.index_changed.emit(current_index)
    

    def calculate_next_index_after_deletion(
        self,
        files_to_delete: List[Path],
        deletion_mode: str = "auto"
    ) -> int:
        """
        파일 삭제 후 이동할 인덱스 계산
        
        Args:
            files_to_delete: 삭제될 파일 목록
            deletion_mode: "single" | "multi" | "auto"
        
        Returns:
            삭제 후 선택할 인덱스
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
                for file_path in files_to_delete:
                    if file_path in self.image_files:
                        idx = self.image_files.index(file_path)
                        indices_to_delete.append(idx)
                
                if not indices_to_delete:
                    return 0
                
                # 정렬하여 범위 파악
                indices_to_delete.sort()
                first_deleted_idx = indices_to_delete[0]
                last_deleted_idx = indices_to_delete[-1]
                
                # 삭제 범위의 다음 파일 선택
                # 예: [10, 11, 12, ..., 19] 삭제 → 새 인덱스 10 (원래 20번)
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

    def _index_folder_async(self, folder: Path, initial_file: Optional[Path] = None):
        """폴더 비동기 인덱싱 - 요청 누락 방지(큐잉)"""

        # 1) 락을 못 잡으면: 현재 스캔이 끝난 뒤 실행되도록 '대기 요청'만 저장
        if not self._scan_lock.tryLock():
            self._pending_scan_request = (folder, initial_file)
            warning_print("⚠️ 이미 스캔 중 - 요청 큐에 저장")
            return

        try:
            # 2) 스캔 진행 중이면: '대기 요청' 저장 후 종료 (무시 금지)
            if self._scan_in_progress:
                self._pending_scan_request = (folder, initial_file)
                warning_print("⚠️ 스캔 진행 중 - 요청 큐에 저장")
                return

            # 지금부터는 이 호출이 스캔을 책임짐
            self._scan_in_progress = True
            self._pending_scan_request = None

            # ===== 2. 기존 스레드 정리 (기존 코드 유지) =====
            if self.scan_thread:
                info_print("🛑 기존 스캔 스레드 정리 시작")
                old_thread = self.scan_thread
                try:
                    old_thread.progress.disconnect()
                    old_thread.completed.disconnect()
                except Exception:
                    pass

                old_thread.cancel()

                if old_thread.isRunning():
                    info_print("   ⏳ 스레드 종료 대기 중...")
                    if not old_thread.wait(300):
                        warning_print("⚠️ 이전 스캔 스레드 아직 실행 중 — 계속 진행 (취소 플래그 설정됨)")

                old_thread.deleteLater()
                self.scan_thread = None

                from PySide6.QtCore import QCoreApplication
                for _ in range(3):
                    QCoreApplication.processEvents()

                info_print("✅ 기존 스레드 정리 완료")

            # ===== 3. 상태/시그널/스레드 시작 (기존 코드 유지) =====
            self._temp_scan_prev_index = self.current_index

            if self.current_folder != folder:
                self._highlighted.clear()
                self._temporary_highlights.clear()
                self.current_folder = folder
                self.folder_changed.emit(folder)

            from PySide6.QtCore import QCoreApplication
            QCoreApplication.processEvents()

            self.folder_scan_started.emit()

            self.scan_thread = FolderScanThread(folder, self.SUPPORTED_EXTENSIONS)
            self.scan_thread.progress.connect(self._on_scan_progress)
            self.scan_thread.completed.connect(lambda files: self._on_scan_completed(files, initial_file))
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
            # 리스트 컴프리헨션 대신 제너레이터
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
        """폴더 스캔 완료 - 안정성 강화"""
        try:
            # ── 진행 중인 정렬 취소 (스캔 결과와 충돌 방지) ──────────
            if self._sort_thread and self._sort_thread.isRunning():
                self._sort_thread.cancel()
                try:
                    self._sort_thread.completed.disconnect()
                except (RuntimeError, TypeError):
                    pass
                debug_print("on_scan_completed: 진행 중 정렬 취소됨")
                
            self.image_files = files
            
            # 하이라이트 정리 (set 연산으로 최적화)
            if self._highlighted:
                valid_files = set(self.image_files)
                removed = len(self._highlighted) - len(self._highlighted & valid_files)
                self._highlighted &= valid_files
                if removed > 0:
                    info_print(f"하이라이트 정리: {removed}개 제거")
            
            if self._temporary_highlights:
                valid_files = set(self.image_files)
                removed = len(self._temporary_highlights) - len(self._temporary_highlights & valid_files)
                self._temporary_highlights &= valid_files
                if removed > 0:
                    info_print(f"임시 하이라이트 정리: {removed}개 제거")
            
            # 인덱스 복원
            prev_index = getattr(self, '_temp_scan_prev_index', 0)
            
            if initial_file and initial_file in self.image_files:
                self.current_index = self.image_files.index(initial_file)
            elif 0 <= prev_index < len(self.image_files):
                self.current_index = prev_index
            elif self.image_files:
                self.current_index = min(prev_index, len(self.image_files) - 1)
            else:
                self.current_index = -1

            # 방어: 파일이 있는데 -1이면 0으로
            if self.image_files and self.current_index < 0:
                self.current_index = 0            

            # 임시 변수 정리
            if hasattr(self, '_temp_scan_prev_index'):
                delattr(self, '_temp_scan_prev_index')
            
            # 시그널 발생
            self.folder_scan_completed.emit(len(self.image_files))
        
            self.index_changed.emit(self.current_index) 
            if not self.image_files:
                info_print("폴더가 비어있음 - index_changed(-1) emit")
            else:
                info_print(f"스캔 완료: {len(self.image_files)}개, index={self.current_index}")

        except Exception as e:
            error_print(f"_on_scan_completed 오류: {e}")
        
        finally:
            # 스레드 정리
            self.scan_thread = None
            self._scan_in_progress = False

            # 스캔 중 들어온 다음 요청이 있으면 즉시 이어서 처리
            req = getattr(self, "_pending_scan_request", None)
            if req:
                self._pending_scan_request = None
                folder, pending_initial = req   # ✅ 의미 구분
                QTimer.singleShot(0, lambda f=folder, i=pending_initial:
                                self._index_folder_async(f, i))
                

    def sort_files_async(
        self,
        sort_order: SortOrder,
        reverse: bool = False,
        on_completed=None,
    ) -> None:
        """
        비동기 정렬. 백그라운드 스레드에서 실행 후 on_completed() 호출.
        기존 sort_files() 동기 버전을 대체.
        """
        if not self.image_files:
            warning_print("정렬할 파일 없음")
            return

        current_file = self.current()

        # 기존 정렬 스레드 취소 (중복 요청 방지)
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
            self.image_files = sorted_files
            self.current_sort_order = sort_order 
            self._restore_current_index(current_file)
            info_print(f"정렬 완료: {sort_order.value}, reverse={reverse}")
            if on_completed:
                on_completed()

        self._sort_thread.completed.connect(_on_thread_completed)
        self._sort_thread.start()
        info_print(f"▶️ 정렬 스레드 시작: {sort_order.value}")


# ============================================
# 폴더 새로고침
# ============================================

    def reload(self) -> None:
        """폴더 새로고침"""
        if not self.current_folder:
            return
        
        current_file = self.current()
        info_print(f"폴더 새로고침: {self.current_folder}")
        
        # 항상 비동기
        self._index_folder_async(self.current_folder, current_file)
    

    def reload_async(self) -> None:
        """폴더 새로고침 (reload 별칭)"""
        self.reload()


# ============================================
# 네비게이션 (이동)
# ============================================

    def go_to(self, index: int) -> Optional[Path]:
        """
         [신규 추가] 특정 인덱스로 이동하는 단일 공개 API.
        모든 외부 코드(MainWindow, ThumbnailBar 등)는 반드시 이 메서드를 통해
        current_index를 변경해야 함 → navigator 내부 상태 일관성 보장.

        Returns: 해당 Path (범위 초과 시 None)
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
            self.current_index = 0
        else:
            self.current_index = -1
        
        self._temp_scan_prev_index = self.current_index
        self.index_changed.emit(self.current_index)


    def get_sort_order(self) -> SortOrder:
        """현재 정렬 순서"""
        return self.current_sort_order
    

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
                    count += 1
        
        return count


    def clear_highlights(self) -> None:
        """모든 하이라이트 해제"""
        if self._highlighted:
            self._highlighted.clear()
            self.highlights_cleared.emit() 
                

    # [신규 추가] 일괄 설정 API — Shift+클릭의 깜빡임 제거용 (TB-9와 공유)
    def set_highlights(self, file_paths: set) -> None:
        """
        하이라이트를 file_paths 집합으로 완전 교체 (일괄 처리).
        toggle() 반복 대신 이 메서드를 사용하면 중간 상태가 외부에 노출되지 않음.
        """
        self._highlighted = set(file_paths)
        self.highlights_set.emit(self._highlighted.copy())


# ============================================
# 임시 하이라이트 관리
# ============================================

    def set_temporary_highlights(self, files: List[Path]) -> None:
        """
        임시 하이라이트 설정 (붙여넣기 등)
        기존 임시 하이라이트는 자동으로 제거됨
        
        Args:
            files: 임시 하이라이트할 파일 목록
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
        
        Args:
            file_path: 확인할 파일 (None이면 현재 파일)
        
        Returns:
            True if 임시 하이라이트됨
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
        
        Returns:
            현재 폴더 Path 또는 None
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
            if old_path in self.image_files:
                index = self.image_files.index(old_path)
                self.image_files[index] = new_path
                debug_print(f"경로 갱신: {old_path.name} → {new_path.name} (index={index})")

                # ── 정렬 스레드의 복사본도 갱신 ──────────────────
                if self._sort_thread and self._sort_thread.isRunning():
                    self._sort_thread.cancel()
                    try:
                        self._sort_thread.completed.disconnect()
                    except (RuntimeError, TypeError):
                        pass
                    debug_print("update_file_path: 진행 중 정렬 취소 (파일명 변경)")

            # highlight set도 갱신
            if old_path in self._highlighted:
                self._highlighted.remove(old_path)
                self._highlighted.add(new_path)
            return True
        except Exception as e:
            error_print(f"update_file_path 오류: {e}")
            return False


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

