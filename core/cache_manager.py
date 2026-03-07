# -*- coding: utf-8 -*-
# core/cache_manager.py

"""
이미지 캐시 관리자 - 2단계 로딩 (빠른 프리뷰 → 고품질)
"""

from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QMutex, QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QPixmap

from core.image_loader import ImageLoader
from utils.debug import debug_print, error_print, info_print, warning_print


# ============================================
# _LoadSignalBridge(QObject)
# ============================================

class _LoadSignalBridge(QObject):
    """QRunnable은 Signal을 가질 수 없어서 QObject 브릿지 사용.
    Signal은 자동으로 QueuedConnection으로 처리되어
    메인 스레드에서 슬롯이 실행됨을 보장한다."""
    loaded = Signal(int, QPixmap, int, str, bool)


# ============================================
# ImageLoadWorker(QRunnable)
# ============================================

class ImageLoadWorker(QRunnable):
    """백그라운드 이미지 로딩"""

    # ── 초기화 ──────────────────────────────────

    def __init__(
        self,
        index: int,
        file_path: Path,
        loader: ImageLoader,
        max_size: Optional[Tuple[int, int]],
        bridge: _LoadSignalBridge,
        expected_file: Optional[Path] = None,
        is_preview: bool = False,
    ) -> None:
        super().__init__()
        self.index = index
        self.file_path = file_path
        self.expected_file = expected_file or file_path
        self.loader = loader
        self.max_size = max_size
        self.bridge = bridge 
        self.cancelled = False
        self.setAutoDelete(True)  
        self.is_preview = is_preview

    # ── 실행 ────────────────────────────────────

    def run(self) -> None:
        if self.cancelled:
            return

        try:
            pixmap = self.loader.load(self.file_path, self.max_size)
            if self.cancelled or not pixmap:
                return
            
            if self.cancelled:
                return

            self.bridge.loaded.emit(self.index, pixmap, 0, str(self.file_path), self.is_preview)

        except Exception as e:
            error_print(f"백그라운드 로딩 실패 [{self.file_path.name}]: {e}")


    def cancel(self) -> None:
        self.cancelled = True


# ============================================
# CacheManager(QObject)
# ============================================

class CacheManager(QObject):
    """이미지 캐시 관리"""
    
    cache_hit = Signal(int)
    cache_miss = Signal(int)
    full_image_loaded = Signal(int)

    # ── 초기화 및 설정 ─────────────────────────── 

    def __init__(
        self,
        ahead_count: int = 25,
        behind_count: int = 5,
        max_memory_mb: int = 500
    ) -> None:
        super().__init__()
        
        self.ahead_count = ahead_count
        self.behind_count = behind_count
        self.max_memory_mb = max_memory_mb
        
        # 캐시 (OrderedDict: LRU)
        self.cache: OrderedDict[int, QPixmap] = OrderedDict()
        self.preview_cache: OrderedDict[int, QPixmap] = OrderedDict()
        
        # 이미지 목록
        self.image_list: list[Path] = []

        self.current_index: int = -1

        # 이미지 로더
        self.loader = ImageLoader()
        
        # 스레드 풀
        self.thread_pool = QThreadPool.globalInstance()
        
        self.loading_indices: set[int] = set()
        self.loading_mutex = QMutex()
        self.active_workers = {}
        self.worker_mutex = QMutex()
        self.cache_mutex = QMutex()

        # 통계
        self.hit_count = 0
        self.miss_count = 0
    
        # 브릿지 생성 (CacheManager 1개당 브릿지 1개)
        self._bridge = _LoadSignalBridge()
        self._bridge.loaded.connect(self._on_image_loaded)


    def set_image_list(self, image_list: list[Path]) -> None:   
        """이미지 목록 설정 - 완전한 정리"""
        info_print(f"🧹 CacheManager: 이미지 목록 변경 ({len(image_list)}개)")
        
        # ===== 1. 모든 워커 즉시 취소 =====
        with self._lock(self.worker_mutex):
            worker_count = len(self.active_workers)
            for worker in self.active_workers.values():
                worker.cancel()
            self.active_workers.clear()
            if worker_count > 0:
                info_print(f"   🛑 {worker_count}개 워커 취소")

        # ===== 2. loading_indices 초기화 =====
        with self._lock(self.loading_mutex):
            self.loading_indices.clear()

        # ===== 3. 캐시 완전 클리어 =====
        with self._lock(self.cache_mutex):
            cache_count = len(self.cache)
            preview_count = len(self.preview_cache)
            self.cache.clear()
            self.preview_cache.clear()
            info_print(f"   🗑️ 캐시 제거: 고품질 {cache_count}개, 프리뷰 {preview_count}개")
        
        # ===== 4. 새 목록 설정 =====
        self.image_list = image_list
        self.current_index = -1
        
        info_print(f"✅ CacheManager 준비 완료: {len(image_list)}개")


    def cancel_loading(self, index: int) -> None:
        with self._lock(self.worker_mutex):
            worker = self.active_workers.pop(index, None)
        if worker:
            worker.cancel()    


    @contextmanager
    def _lock(self, mutex):
        """Mutex context manager - 예외 발생 시에도 안전한 unlock"""
        mutex.lock()
        try:
            yield
        finally:
            mutex.unlock()


    def get_exif_rotation_angle(self, file_path: Path) -> int:
        """외부 인터페이스 유지 — ImageLoader에 위임"""
        return self.loader.get_exif_rotation_angle(file_path)

    # ── 캐시 조회 ──────────────────────────────── 

    def get(
        self,
        index: int,
        viewport_size: Tuple[int, int],
        load_full: bool = True
    ) -> Optional[QPixmap]:
        """
        이미지 가져오기 (2단계 로딩)
        """
        if not (0 <= index < len(self.image_list)):
            return None
        
        self.current_index = index
        file_path = self.image_list[index]
        
        # 1. 고품질 캐시 확인
        pixmap = None  # 명시적 초기화
        with self._lock(self.cache_mutex):
            if index in self.cache:
                self.cache.move_to_end(index)
                pixmap = self.cache[index]
        
        if pixmap:  # locals() 대신 직접 체크
            self.hit_count += 1
            self.cache_hit.emit(index)
            return pixmap
        
        # 2. 프리뷰 캐시 확인
        preview = None  # 명시적 초기화
        with self._lock(self.cache_mutex):
            if index in self.preview_cache:
                preview = self.preview_cache[index]
        
        if preview:
            if load_full:
                self._load_full_async(index, file_path)
            
            self.hit_count += 1
            self.cache_hit.emit(index)
            return preview
        
        # 3. 캐시 미스 - 동기적으로 프리뷰 로드
        self.miss_count += 1
        self.cache_miss.emit(index)

        preview_size = (int(viewport_size[0] * 1.5), int(viewport_size[1] * 1.5))
        pixmap = self.loader.load(file_path, preview_size)
        
        if pixmap:

            debug_print(f"[preview before] {pixmap.width()}x{pixmap.height()}")
            pixmap = self.loader.apply_exif_rotation(file_path, pixmap)
            debug_print(f"[preview after ] {pixmap.width()}x{pixmap.height()}")

            with self._lock(self.cache_mutex):
                self.preview_cache[index] = pixmap
            
            if load_full:
                self._load_full_async(index, file_path)
            
            self._prefetch(index, viewport_size)
            self._manage_memory()
            
            return pixmap
        
        return None

    # ── 비동기 로딩 ────────────────────────────── 

    def _load_full_async(self, index: int, file_path: Path) -> None:
        with self._lock(self.worker_mutex):
            if index in self.active_workers: 
                return
            with self._lock(self.cache_mutex):
                if index in self.cache:
                    return
            worker = ImageLoadWorker(
                index=index, 
                file_path=file_path,
                loader=self.loader, 
                max_size=None, 
                bridge=self._bridge,
            )
            self.active_workers[index] = worker 
        self.thread_pool.start(worker)


    @Slot(int, QPixmap, int, str, bool)
    def _on_image_loaded(
        self, index: int, pixmap: QPixmap, angle: int, file_path: str, is_preview: bool = False
    ) -> None:
        with self._lock(self.worker_mutex):
            self.active_workers.pop(index, None)
        with self._lock(self.loading_mutex):
            self.loading_indices.discard(index)

        if not pixmap or pixmap.isNull():
            return
        if not (0 <= index < len(self.image_list)):
            return

        # stale worker 방어 — 폴더 교체 후 이전 워커 결과 무시
        if str(self.image_list[index]) != file_path:
            debug_print(
                f"Stale worker 무시: idx={index} "
                f"기대={self.image_list[index].name} 실제={Path(file_path).name}"
            )
            return

        pixmap = self.loader.apply_exif_rotation(Path(file_path), pixmap)

        with self._lock(self.cache_mutex):
            if is_preview:
                # 프리뷰 프리페치 → preview_cache에만 저장 (풀캐시 오염 방지)
                if index not in self.cache:          # 이미 풀이 있으면 덮지 않음
                    self.preview_cache[index] = pixmap
            else:
                # 풀 로드 → cache에 저장, preview 제거
                self.cache[index] = pixmap
                self.cache.move_to_end(index)
                self.preview_cache.pop(index, None)

        self._manage_memory()

        if not is_preview:
            self.full_image_loaded.emit(index)


    # ── 프리페칭 ─────────────────────────────────

    def _prefetch(self, current_index: int, viewport_size: Tuple[int, int]) -> None:
        preview_size = (int(viewport_size[0] * 1.5), int(viewport_size[1] * 1.5))

        # 후보 목록 먼저 확정 (락 없이)
        candidates: list[tuple[int, Optional[Tuple[int, int]]]] = []
        for i in range(1, self.ahead_count + 1):
            idx = current_index + i
            if idx >= len(self.image_list):
                break
            candidates.append((idx, None if i <= 5 else preview_size))
        for i in range(1, self.behind_count + 1):
            idx = current_index - i
            if idx < 0:
                break
            candidates.append((idx, None if i <= 3 else preview_size))

        for idx, max_size in candidates:
            with self._lock(self.cache_mutex):
                if idx in self.cache:
                    continue
                if max_size is not None and idx in self.preview_cache:
                    continue

            with self._lock(self.worker_mutex):
                with self._lock(self.cache_mutex):
                    if idx in self.cache: continue
                    if max_size is not None and idx in self.preview_cache: continue
                if idx in self.active_workers: continue
                worker = ImageLoadWorker(
                    idx, self.image_list[idx], 
                    #ImageLoader(), 
                    self.loader,
                    max_size, 
                    bridge=self._bridge,
                    is_preview=(max_size is not None), 
                )
                self.active_workers[idx] = worker
            self.thread_pool.start(worker)


    # ── 메모리 관리 ──────────────────────────────

    def _calculate_cache_memory(self, cache_dict: OrderedDict[int, QPixmap]) -> float:
        """
        캐시 메모리 사용량 계산 (MB)
        """
        memory_mb = 0.0
        for pixmap in cache_dict.values():
            if not pixmap.isNull():
                memory_mb += (pixmap.width() * pixmap.height() * 4) / (1024 * 1024)
        return memory_mb


    def _manage_memory(self) -> None:
        try:
            with self._lock(self.cache_mutex):  
                preview_memory = self._calculate_cache_memory(self.preview_cache)
                full_memory    = self._calculate_cache_memory(self.cache)
                total_memory   = preview_memory + full_memory

                if total_memory <= self.max_memory_mb:
                    return

                protected_indices = set()
                if self.current_index >= 0:
                    for offset in range(-5, 6):
                        idx = self.current_index + offset
                        if 0 <= idx < len(self.image_list):
                            protected_indices.add(idx)

                removed = self._remove_old_cache(
                    self.preview_cache, protected_indices, total_memory, min_keep=5
                )
                total_memory -= removed

                if total_memory > self.max_memory_mb:
                    self._remove_old_cache(
                        self.cache, protected_indices, total_memory, min_keep=3
                    )
        except Exception as e:
            error_print(f"메모리 관리 실패: {e}")


    def _remove_old_cache(
        self,
        cache_dict: OrderedDict[int, QPixmap],
        protected_indices: set[int],
        current_memory: float,
        min_keep: int,
    ) -> float:
        removed_memory = 0.0

        # 제거 가능한 키를 한 번만 수집 — O(n)
        removable = [k for k in cache_dict if k not in protected_indices]

        for index in removable:
            if current_memory <= self.max_memory_mb or len(cache_dict) <= min_keep:
                break
            pixmap = cache_dict.pop(index)
            if not pixmap.isNull():
                mb = (pixmap.width() * pixmap.height() * 4) / (1024 * 1024)
                removed_memory += mb
                current_memory -= mb
                debug_print(f"캐시 제거: 인덱스 {index} ({mb:.1f}MB)")

        return removed_memory

    # ── 캐시 무효화 / 클리어 ─────────────────────

    def clear(self) -> None:
        """캐시 클리어 — 진행 중인 워커 먼저 취소"""
        with self._lock(self.worker_mutex):
            for worker in self.active_workers.values():
                worker.cancel()
            self.active_workers.clear()
        with self._lock(self.loading_mutex):
            self.loading_indices.clear()
        with self._lock(self.cache_mutex):
            self.cache.clear()
            self.preview_cache.clear()
        self.hit_count = 0
        self.miss_count = 0
        self.current_index = -1
        info_print("캐시 클리어됨")


    def invalidate(self, index: int) -> None:
        """특정 인덱스의 캐시 무효화"""
        with self._lock(self.cache_mutex):
            if index in self.cache:
                del self.cache[index]
                info_print(f"고품질 캐시 무효화: 인덱스 {index}")
            
            if index in self.preview_cache:
                del self.preview_cache[index]
                info_print(f"프리뷰 캐시 무효화: 인덱스 {index}")
        
        with self._lock(self.loading_mutex):
            self.loading_indices.discard(index)
        
        self.cancel_loading(index)


    def invalidate_range(self, start_index: int, end_index: int) -> None:
        """범위 캐시 무효화"""
        removed_count = 0
        
        with self._lock(self.cache_mutex):
            for idx in range(start_index, end_index + 1):
                if idx in self.cache:
                    del self.cache[idx]
                    removed_count += 1
                
                if idx in self.preview_cache:
                    del self.preview_cache[idx]
                    removed_count += 1
        
        with self._lock(self.loading_mutex):
            for idx in range(start_index, end_index + 1):
                self.loading_indices.discard(idx)
        
        if removed_count > 0:
            info_print(f"캐시 무효화 범위: {start_index}~{end_index} ({removed_count}개 제거)")


    def invalidate_from_index(self, start_index: int) -> None:
        """특정 인덱스부터 끝까지 캐시 무효화"""
        removed_count = 0
        
        with self._lock(self.cache_mutex):
            keys_to_remove = [k for k in self.cache.keys() if k >= start_index]
            for key in keys_to_remove:
                del self.cache[key]
                removed_count += 1
            
            keys_to_remove = [k for k in self.preview_cache.keys() if k >= start_index]
            for key in keys_to_remove:
                del self.preview_cache[key]
                removed_count += 1
        
        with self._lock(self.loading_mutex):
            loading_to_remove = [k for k in self.loading_indices if k >= start_index]
            for key in loading_to_remove:
                self.loading_indices.discard(key)
        
        if removed_count > 0:
            info_print(f"캐시 무효화: 인덱스 {start_index}부터 {removed_count}개 제거")


    # ── 통계 ─────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        with self._lock(self.cache_mutex):  
            preview_memory = self._calculate_cache_memory(self.preview_cache)
            full_memory    = self._calculate_cache_memory(self.cache)
            cache_size     = len(self.cache)
            preview_size   = len(self.preview_cache)

        with self._lock(self.loading_mutex):  
            loading_count = len(self.loading_indices)

        total_memory   = full_memory + preview_memory
        total_requests = self.hit_count + self.miss_count
        hit_rate       = (self.hit_count / total_requests * 100) if total_requests > 0 else 0

        return {
            'cache_size': cache_size,
            'preview_cache_size': preview_size,
            'loading_count': loading_count,
            'memory_mb': f"{total_memory:.1f}",
            'full_memory_mb': f"{full_memory:.1f}",
            'preview_memory_mb': f"{preview_memory:.1f}",
            'hit_rate': f"{hit_rate:.1f}",
            'hit_count': self.hit_count,
            'miss_count': self.miss_count,
            'total_requests': total_requests,
            'total_images': len(self.image_list),
            'memory_usage_percent': f"{(total_memory / self.max_memory_mb * 100):.1f}" if self.max_memory_mb > 0 else "0.0",
            'current_index': self.current_index,
        }
    
    