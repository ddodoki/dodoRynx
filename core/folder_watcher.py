# -*- coding: utf-8 -*-
# core/folder_watcher.py

"""
폴더 감시자 - 파일 추가/삭제 자동 감지
"""

from pathlib import Path
from time import time
from typing import Any, Dict, Optional, Set

from PySide6.QtCore import QObject, Qt, QTimer, Signal, SignalInstance, Slot
from watchdog.events import FileSystemEvent, FileSystemEventHandler

from utils.debug import debug_print, error_print, info_print, warning_print


class FolderWatcherHandler(FileSystemEventHandler):
    """파일 시스템 이벤트 핸들러 (중복 필터링 포함)"""
    
    def __init__(
        self,
        supported_extensions: Set[str],
        file_added_signal: SignalInstance,
        file_deleted_signal: SignalInstance,
        file_modified_signal: SignalInstance,
        file_moved_signal: SignalInstance,
    ) -> None:
        super().__init__()
        self.supported_extensions = supported_extensions
        
        # 타입 힌트도 변경
        self.file_added_signal: SignalInstance = file_added_signal
        self.file_deleted_signal: SignalInstance = file_deleted_signal
        self.file_modified_signal: SignalInstance = file_modified_signal
        self.file_moved_signal: SignalInstance = file_moved_signal
        
        self.last_events: Dict[str, float] = {}
        self.event_debounce_seconds = 0.5

        self._file_stat_cache: Dict[str, tuple] = {}
        self._known_files: Set[str] = set()
        

    def _is_duplicate_event(self, file_path: Path, event_type: str) -> bool:
        file_key = f"{file_path}:{event_type}"
        current_time = time()

        if file_key in self.last_events:
            last_time = self.last_events[file_key]
            if (current_time - last_time) < self.event_debounce_seconds:
                return True

        self.last_events[file_key] = current_time

        if len(self.last_events) > 500:
            cutoff = time() - self.event_debounce_seconds * 2
            self.last_events = {k: v for k, v in self.last_events.items() if v > cutoff}

        return False


    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        file_path = Path(str(event.src_path))
        if file_path.suffix.lower() not in self.supported_extensions:
            return

        if self._is_duplicate_event(file_path, "created"):
            debug_print(f"중복 이벤트 무시 (created): {file_path.name}")
            return

        path_key = str(file_path)
        self._known_files.add(path_key)
        try:
            stat = file_path.stat()
            self._file_stat_cache[path_key] = (stat.st_size, round(stat.st_mtime, 2))
        except OSError:
            pass

        info_print(f"파일 추가 감지: {file_path.name}")
        self.file_added_signal.emit(file_path)


    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        file_path = Path(str(event.src_path))
        if file_path.suffix.lower() not in self.supported_extensions:
            return

        if self._is_duplicate_event(file_path, "deleted"):
            return

        path_key = str(file_path)
        self._known_files.discard(path_key)
        self._file_stat_cache.pop(path_key, None)

        info_print(f"파일 삭제 감지: {file_path.name}")
        self.file_deleted_signal.emit(file_path)


    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        file_path = Path(str(event.src_path))
        if file_path.suffix.lower() not in self.supported_extensions:
            return

        path_key = str(file_path)

        # ── ① 신규 파일 판별 ──────────────────────────────────────
        # known_files에 없으면 → 감시 시작 이후 새로 생긴 파일
        # NAS/SMB는 on_created 없이 on_modified만 발생하는 경우가 있음
        if path_key not in self._known_files:
            if not file_path.exists():
                return  # 아직 완전히 쓰여지지 않은 상태
            if self._is_duplicate_event(file_path, "created"):
                return
            # known에 등록하고 added로 라우팅
            self._known_files.add(path_key)
            try:
                stat = file_path.stat()
                self._file_stat_cache[path_key] = (stat.st_size, round(stat.st_mtime, 2))
            except OSError:
                pass
            info_print(f"파일 추가 감지 (modified→added): {file_path.name}")
            self.file_added_signal.emit(file_path)
            return

        # ── ② 기존 파일: stat 비교로 가짜 이벤트 차단 ────────────
        if self._is_duplicate_event(file_path, "modified"):
            return

        try:
            stat = file_path.stat()
            new_sig = (stat.st_size, round(stat.st_mtime, 2))
            old_sig = self._file_stat_cache.get(path_key)
            if old_sig == new_sig:
                # 크기·mtime 동일 → 읽기에 의한 가짜 이벤트
                debug_print(f"가짜 modified 무시 (stat 동일): {file_path.name}")
                return
            self._file_stat_cache[path_key] = new_sig
        except OSError:
            return

        info_print(f"파일 수정 감지: {file_path.name}")
        self.file_modified_signal.emit(file_path)
        

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        src_path = Path(str(event.src_path))
        dest_path = Path(str(event.dest_path))

        if self._is_duplicate_event(dest_path, "moved"):
            return

        src_is_image = src_path.suffix.lower() in self.supported_extensions
        dest_is_image = dest_path.suffix.lower() in self.supported_extensions

        if src_is_image and dest_is_image:
            src_key, dst_key = str(src_path), str(dest_path)
            self._known_files.discard(src_key)
            self._known_files.add(dst_key)
            old_stat = self._file_stat_cache.pop(src_key, None)
            if old_stat:
                self._file_stat_cache[dst_key] = old_stat
            self.file_moved_signal.emit(src_path, dest_path)

        elif src_is_image and not dest_is_image:
            # 이미지가 비이미지 이름으로 변경 = 삭제와 동일
            src_key = str(src_path)
            self._known_files.discard(src_key)        
            self._file_stat_cache.pop(src_key, None)   
            self.file_deleted_signal.emit(src_path)

        elif not src_is_image and dest_is_image:
            # 비이미지가 이미지 이름으로 변경 = 추가와 동일
            dst_key = str(dest_path)
            self._known_files.add(dst_key)        
            try:
                stat = dest_path.stat()
                self._file_stat_cache[dst_key] = (stat.st_size, round(stat.st_mtime, 2))
            except OSError:
                pass                                
            self.file_added_signal.emit(dest_path)


class FolderWatcher(QObject):
    """폴더 감시 및 파일 변경 이벤트 관리"""
    
    file_added = Signal(Path)
    file_deleted = Signal(Path)
    file_modified = Signal(Path)
    file_moved = Signal(Path, Path)

    batch_added   = Signal(list)    
    batch_deleted = Signal(list) 
    batch_modified = Signal(list)
    batch_moved    = Signal(list)


    def __init__(self, supported_extensions: Set[str]) -> None:
        super().__init__()
        self.supported_extensions = supported_extensions
        self.observer: Any = None
        self.watching_path: Optional[Path] = None
        self._active: bool = True

        # 이벤트 큐
        self.pending_added: Set[Path] = set()
        self.pending_deleted: Set[Path] = set()
        self.pending_modified: Set[Path] = set()
        self.pending_moved: Set[tuple[Path, Path]] = set()

        # 이벤트 처리 타이머
        self.event_timer = QTimer(self)
        self.event_timer.setSingleShot(True)
        self.event_timer.timeout.connect(self._process_pending_events)
        
        # 내부 시그널 연결 (QueuedConnection - 스레드 세이프)
        self.file_added.connect(self._on_file_added, Qt.ConnectionType.QueuedConnection)
        self.file_deleted.connect(self._on_file_deleted, Qt.ConnectionType.QueuedConnection)
        self.file_modified.connect(self._on_file_modified, Qt.ConnectionType.QueuedConnection)
        self.file_moved.connect(self._on_file_moved, Qt.ConnectionType.QueuedConnection)
        
        info_print(f" ✅ FolderWatcher 내부 시그널 연결 완료 (QueuedConnection)")


    def cleanup(self) -> None:
        """리소스 정리"""
        self.stop_watching()


    def start_watching(self, folder_path: Path) -> None:
        """폴더 감시 시작"""

        self._active = True
        self.pending_added.clear()
        self.pending_deleted.clear()
        self.pending_modified.clear()
        self.pending_moved.clear()

        debug_print(f"start_watching() 호출: {folder_path}")

        if not folder_path:
            error_print(f"folder_path가 None입니다")
            return
        
        if not folder_path.exists():
            error_print(f"폴더가 존재하지 않음: {folder_path}")
            return
        
        if not folder_path.is_dir():
            error_print(f"폴더가 아님: {folder_path}")
            return
        
        if (
            self.watching_path
            and str(self.watching_path).lower() == str(folder_path).lower()
            and self.is_watching()
        ):        
            debug_print(f"이미 감시 중: {folder_path}")
            return

        self.stop_watching()
        self.watching_path = folder_path
        
        event_handler = FolderWatcherHandler(
            self.supported_extensions,
            self.file_added, 
            self.file_deleted,
            self.file_modified,
            self.file_moved,
        )
        debug_print(f"FolderWatcherHandler 생성 완료")
                
        # 감시 시작 시점의 파일 목록을 known으로 등록
        try:
            event_handler._known_files = {
                str(p) for p in folder_path.iterdir()
                if p.is_file() and p.suffix.lower() in self.supported_extensions
            }
            debug_print(f"known_files 초기화: {len(event_handler._known_files)}개")
        except Exception:
            pass

        # 네트워크/클라우드 경로 debounce 강화
        path_str = str(folder_path).lower()
        is_network = (
            not Path(folder_path).drive.endswith(":")
            or path_str.startswith("n:")
            or "onedrive" in path_str
            or "dropbox" in path_str
            or "google drive" in path_str
        )
        event_handler.event_debounce_seconds = 3.0 if is_network else 0.5
        debug_print(f"debounce: {event_handler.event_debounce_seconds}s (network={is_network})")

        try:
            from watchdog.observers import Observer
            observer = Observer()
            observer.schedule(event_handler, str(folder_path), recursive=False)
            observer.start()
            self.observer = observer
            info_print(f"✅ 폴더 감시 시작: {folder_path}")
        except Exception as e:
            error_print(f"❌ 폴더 감시 시작 실패: {e}")
            self.observer = None
            self.watching_path = None


    def stop_watching(self) -> None:
        """폴더 감시 중지"""
        self._active = False   
        self.event_timer.stop()   
        self.pending_added.clear()
        self.pending_deleted.clear()
        self.pending_modified.clear()
        self.pending_moved.clear()

        if self.observer:
            try:
                self.observer.stop()
                self.observer.join(timeout=1.0)
                info_print(f"폴더 감시 중지: {self.watching_path}")
            except Exception as e:
                error_print(f"감시 중지 실패: {e}")
            finally:
                self.observer = None
                self.watching_path = None


    def watch_folder(self, folder_path: Path) -> None:
        """폴더 감시 시작 (별칭)"""
        self.start_watching(folder_path)


    def set_folder(self, folder_path: Path) -> None:
        """폴더 설정 및 감시 시작 (별칭)"""
        self.start_watching(folder_path)


    def is_watching(self) -> bool:
        """감시 중인지 확인"""
        return self.observer is not None and self.observer.is_alive()


    def get_watching_path(self) -> Optional[Path]:
        """현재 감시 중인 폴더 반환"""
        return self.watching_path if self.is_watching() else None


    def get_status(self) -> dict:
        """감시자 상태 반환"""
        return {
            'is_watching': self.is_watching(),
            'watching_path': str(self.watching_path) if self.watching_path else None,
            'supported_extensions_count': len(self.supported_extensions),
        }


    # ============================================
    # 내부 슬롯 (메인 스레드에서 실행)
    # ============================================

    @Slot(Path)
    def _on_file_added(self, file_path: Path) -> None:
        """파일 추가 핸들러 (메인 스레드)"""
        if not self._active: 
            return
        info_print(f"🔔 _on_file_added (메인 스레드): {file_path.name}")
        self.pending_added.add(file_path)
        
        if not self.event_timer.isActive():
            info_print(f"   ⏰ 타이머 시작 (300ms)")
            self.event_timer.start(300)


    @Slot(Path)
    def _on_file_deleted(self, file_path: Path) -> None:
        """파일 삭제 핸들러 (메인 스레드)"""
        if not self._active: 
            return
        info_print(f"🔔 _on_file_deleted (메인 스레드): {file_path.name}")
        self.pending_deleted.add(file_path)
        info_print(f"   pending_deleted 크기: {len(self.pending_deleted)}개")
        
        if not self.event_timer.isActive():
            info_print(f"   ⏰ 타이머 시작 (300ms)")
            self.event_timer.start(300)
        else:
            info_print(f"   ⏰ 타이머 이미 실행 중")


    @Slot(Path)
    def _on_file_modified(self, file_path: Path) -> None:
        """파일 수정 핸들러 (메인 스레드)"""
        if not self._active: 
            return
        debug_print(f"_on_file_modified (메인 스레드): {file_path.name}")
        self.pending_modified.add(file_path)
        
        if not self.event_timer.isActive():
            self.event_timer.start(300)

    @Slot(Path, Path)
    def _on_file_moved(self, src_path: Path, dest_path: Path) -> None:
        """파일 이동 핸들러 (메인 스레드)"""
        if not self._active: 
            return
        info_print(f"_on_file_moved (메인 스레드): {src_path.name} → {dest_path.name}")
        self.pending_moved.add((src_path, dest_path))
        
        if not self.event_timer.isActive():
            self.event_timer.start(300)


    # ============================================
    # 이벤트 일괄 처리
    # ============================================

    @Slot()
    def _process_pending_events(self) -> None:
        if self.pending_added:
            added_list = sorted(self.pending_added)
            self.pending_added.clear()
            self.batch_added.emit(added_list)

        if self.pending_deleted:
            deleted_list = sorted(self.pending_deleted)
            self.pending_deleted.clear()
            self.batch_deleted.emit(deleted_list)

        if self.pending_modified:
            modified_list = sorted(self.pending_modified)
            self.pending_modified.clear()
            self.batch_modified.emit(modified_list) 
                                                  
        if self.pending_moved:
            moved_list = list(self.pending_moved)   
            self.pending_moved.clear()
            self.batch_moved.emit(moved_list)     

