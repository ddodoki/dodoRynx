# -*- coding: utf-8 -*-
# core/hybrid_cache.py

"""
범용 하이브리드 LRU 캐시 (메모리 + 디스크)

지원 용도:
  tiles       : HTTP 원격 소스, ETag/Last-Modified 검증, 시간 기반 만료
  thumbnails  : 로컬 파일 소스, source_mtime 변경 감지
  기타 QPixmap 을 키-값으로 저장하는 모든 용도

저장 위치: ~/.cache/dodoRynx/{namespace}/
"""

import hashlib
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Optional

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage, QPixmap

from utils.debug import debug_print, info_print, warning_print
from utils.paths import get_cache_dir as _get_cache_dir


# ============================================
# 모듈 수준 유틸리티
# ============================================

def _pixmap_bytes(pixmap: QPixmap) -> int:
    """QPixmap 메모리 사용량 추정 (RGBA 4 bytes/pixel)"""
    return pixmap.width() * pixmap.height() * 4


# ============================================
# HybridCache
# ============================================

class HybridCache:
    """
    범용 하이브리드 LRU 캐시 (메모리 + 디스크)

    Parameters
    ----------
    namespace     : 캐시 격리 이름 — 별도 디렉토리·DB 사용
                    예) "tiles", "thumbnails"
    max_memory_mb : 메모리 상한 (MB)
    max_disk_mb   : 디스크 상한 (MB)
    expiry_days   : 시간 기반 만료 일수.
                    0 이면 시간 만료 없음 (source_mtime 방식만 사용)

    신선도 판단 (is_stale)
    ─────────────────────
    - 키 없음                    → stale
    - expiry_days > 0, 시간 경과 → stale  (타일 용도)
    - source_mtime 제공, 불일치  → stale  (썸네일 용도)
    - 위 조건 모두 통과           → fresh
    """

    # ── 초기화 / DB 관리 ─────────────────────────

    def __init__(
        self,
        namespace: str,
        max_memory_mb: int,
        max_disk_mb: int,
        expiry_days: int = 30,
    ) -> None:
        self.namespace        = namespace
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.max_disk_bytes   = max_disk_mb   * 1024 * 1024
        self.expiry_seconds   = expiry_days * 86400 if expiry_days > 0 else 0

        self._cache_dir = _get_cache_dir() / namespace
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._cache_dir / "index.db"

        # 메모리 캐시 (LRU)
        self._memory: OrderedDict[str, QPixmap] = OrderedDict()
        self._memory_bytes: int = 0
        self._mem_lock = Lock()

        # 디스크 쓰기 직렬화
        self._db_lock = Lock()

        self._setup_db()


    # ── DB 초기화 ────────────────────────────────────────────

    def _setup_db(self) -> None:
        with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    cache_key     TEXT PRIMARY KEY,
                    file_name     TEXT NOT NULL,
                    file_size     INTEGER NOT NULL DEFAULT 0,
                    etag          TEXT,
                    last_modified TEXT,
                    source_mtime  REAL,
                    cached_at     REAL NOT NULL,
                    accessed_at   REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lru ON entries(accessed_at)"
            )
            conn.commit()


    def _ensure_db(self) -> None:
        """
        캐시 디렉토리·DB·테이블이 외부에서 삭제됐을 때 자동 복구.
        설정 다이얼로그에서 폴더 자체를 삭제했을 때 싱글턴이 살아있는 경우 대비.
        """
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._setup_db()
        info_print(f"[{self.namespace}] DB 자동 복구 완료")


    # ── 메모리 캐시 (LRU) ────────────────────────

    def _mem_get(self, key: str) -> Optional[QPixmap]:
        with self._mem_lock:
            if key in self._memory:
                self._memory.move_to_end(key)
                return self._memory[key]
        return None


    def _mem_put(self, key: str, pixmap: QPixmap) -> None:
        new_size = _pixmap_bytes(pixmap)
        with self._mem_lock:
            # 기존 항목 교체 시 사이즈를 제거 전에 저장
            if key in self._memory:
                old_size = _pixmap_bytes(self._memory[key])
                del self._memory[key]
                self._memory_bytes -= old_size

            # 용량 초과 시 LRU 제거 (제거 전 사이즈 저장)
            while self._memory_bytes + new_size > self.max_memory_bytes and self._memory:
                _, evicted = self._memory.popitem(last=False)
                self._memory_bytes -= _pixmap_bytes(evicted)

            self._memory[key] = pixmap
            self._memory_bytes += new_size


    def _mem_remove(self, key: str) -> None:
        with self._mem_lock:
            if key in self._memory:
                self._memory_bytes -= _pixmap_bytes(self._memory.pop(key))


    # ── 디스크 캐시 ──────────────────────────────

    @staticmethod
    def _to_filename(key: str) -> str:
        """임의의 키 → 안전한 파일명 (MD5 해시 + .cache)"""
        return hashlib.md5(key.encode("utf-8")).hexdigest() + ".cache"


    def _entry_path(self, key: str) -> Path:
        return self._cache_dir / self._to_filename(key)


    def _db_get_meta(self, key: str) -> Optional[dict]:
        try:
            with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM entries WHERE cache_key = ?", (key,)
                ).fetchone()
                return dict(row) if row else None
        except sqlite3.OperationalError as e:
            err = str(e)
            if "no such table" in err:
                self._ensure_db()
            elif "database is locked" in err:
                warning_print(f"[{self.namespace}] DB 읽기 락 충돌 (스킵): {e}")
                # _ensure_db() 호출 안 함 — 테이블 문제 아님
            else:
                warning_print(f"[{self.namespace}] DB 읽기 오류: {e}")
            return None
        except Exception as e:
            warning_print(f"[{self.namespace}] DB 읽기 오류: {e}")
            return None


    def _db_touch(self, key: str) -> None:
        # 락 획득 실패 시 skip (touch는 LRU 순서 업데이트일 뿐, 실패해도 무해)
        if not self._db_lock.acquire(blocking=False):
            return
        try:
            with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                conn.execute(
                    "UPDATE entries SET accessed_at = ? WHERE cache_key = ?",
                    (time.time(), key),
                )
                conn.commit()
        except Exception as e:
            warning_print(f"[{self.namespace}] DB touch 오류: {e}")
        finally:
            self._db_lock.release()


    def _db_save(
        self,
        key: str,
        data: bytes,
        etag: Optional[str],
        last_modified: Optional[str],
        source_mtime: Optional[float],
    ) -> None:
        """파일 저장 + DB 등록 + 용량 초과 시 LRU 제거"""
        # 디렉토리가 외부에서 삭제됐을 수 있으므로 항상 보장
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        path = self._entry_path(key)
        path.write_bytes(data)
        now = time.time()

        def _execute(conn: sqlite3.Connection) -> None:
            conn.execute("""
                INSERT OR REPLACE INTO entries
                (cache_key, file_name, file_size,
                etag, last_modified, source_mtime,
                cached_at, accessed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key, path.name, len(data),
                etag, last_modified, source_mtime,
                now, now,
            ))
            conn.commit()

        with self._db_lock:
            for attempt in range(2):
                try:
                    with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                        _execute(conn)
                    self._evict_disk_if_needed()
                    return
                except sqlite3.OperationalError as e:
                    if "no such table" in str(e) and attempt == 0:
                        self._ensure_db()
                    else:
                        warning_print(f"[{self.namespace}] DB 저장 실패: {e}")
                        path.unlink(missing_ok=True) 
                        return
                except Exception as e:
                    warning_print(f"[{self.namespace}] DB 저장 실패: {e}")
                    path.unlink(missing_ok=True)  
                    return


    def _db_refresh_expiry(self, key, etag=None, last_modified=None):
        now = time.time()
        with self._db_lock:     
            try:
                with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                    conn.execute("""
                        UPDATE entries
                        SET cached_at     = ?,
                            accessed_at   = ?,
                            etag          = COALESCE(?, etag),
                            last_modified = COALESCE(?, last_modified)
                        WHERE cache_key = ?
                    """, (now, now, etag, last_modified, key))
                    conn.commit()
            except Exception as e:
                warning_print(f"[{self.namespace}] DB refresh_expiry 오류: {e}")
                

    def _load_from_disk(self, key: str) -> Optional[QPixmap]:
        meta = self._db_get_meta(key)
        if not meta:
            return None
        path = self._cache_dir / meta["file_name"]
        if not path.exists():
            self.invalidate(key)
            return None
        try:
            pixmap = QPixmap()
            if pixmap.loadFromData(path.read_bytes()):
                return pixmap
            warning_print(f"[{self.namespace}] 디스크 캐시 손상: {key}")
            self.invalidate(key)   
        except Exception as e:
            warning_print(f"[{self.namespace}] 디스크 읽기 오류 ({key}): {e}")
            self.invalidate(key)   
        return None

        
    def _evict_disk_if_needed(self) -> None:
        """디스크 LRU 제거. 반드시 _db_lock 보유 상태에서 호출."""
        try:
            with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                total: int = (
                    conn.execute("SELECT COALESCE(SUM(file_size), 0) FROM entries")
                    .fetchone()[0]
                )
                if total <= self.max_disk_bytes:
                    return
                rows = conn.execute(
                    "SELECT cache_key, file_name, file_size "
                    "FROM entries ORDER BY accessed_at ASC"
                ).fetchall()
                to_delete = []
                for cache_key, file_name, file_size in rows:
                    if total <= self.max_disk_bytes:
                        break
                    (self._cache_dir / file_name).unlink(missing_ok=True)
                    total -= file_size
                    to_delete.append(cache_key)
                if to_delete:
                    conn.execute(
                        f"DELETE FROM entries WHERE cache_key IN "
                        f"({','.join('?' * len(to_delete))})",
                        to_delete,
                    )
                    conn.commit()
                    debug_print(f"[{self.namespace}] 디스크 LRU 제거: {len(to_delete)}개")
        except Exception as e:
            warning_print(f"[{self.namespace}] 디스크 정리 오류: {e}")


    # ── 공개 API ─────────────────────────────────

    def get(self, key: str) -> Optional[QPixmap]:
        """메모리 → 디스크 순 조회. 디스크 히트 시 메모리로 승격."""
        pixmap = self._mem_get(key)
        if pixmap:
            return pixmap
        pixmap = self._load_from_disk(key)
        if pixmap:
            self._db_touch(key)
            self._mem_put(key, pixmap)
        return pixmap


    def get_meta(self, key: str) -> Optional[dict]:
        """메타데이터 조회 (etag, last_modified, source_mtime, cached_at 포함)"""
        return self._db_get_meta(key)


    def put(
        self,
        key: str,
        pixmap: QPixmap,
        raw_data: bytes,
        *,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        source_mtime: Optional[float] = None,
    ) -> None:
        """메모리 + 디스크에 저장"""
        self._mem_put(key, pixmap)
        self._db_save(key, raw_data, etag, last_modified, source_mtime)


    def get_raw(self, key: str) -> Optional[bytes]:
        meta = self._db_get_meta(key)
        if not meta:
            return None
        path = self._cache_dir / meta["file_name"]
        if not path.exists():
            self.invalidate(key) 
            return None
        try:
            raw = path.read_bytes()
            self._db_touch(key)
            return raw
        except Exception as e:
            warning_print(f"[{self.namespace}] get_raw 실패: {e}")
            self.invalidate(key) 
            return None
    

    def put_raw(
        self,
        key: str,
        data: bytes,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        source_mtime: Optional[float] = None,
    ) -> None:
        """
        bytes만 디스크 캐시에 저장 (메모리 캐시 QPixmap 건드리지 않음).
        MapWorker 등 비GUI 스레드에서 타일을 저장할 때 사용.
        """
        self._db_save(key, data, etag, last_modified, source_mtime)


    def is_stale(self, key: str, source_mtime: Optional[float] = None) -> bool:
        """
        캐시 신선도 판단.
          - 키 없음            → True
          - 시간 만료           → True  (expiry_days > 0)
          - 소스 파일 변경      → True  (source_mtime 제공 시)
          - 모두 통과           → False
        """
        meta = self._db_get_meta(key)
        if not meta:
            return True
        if self.expiry_seconds > 0:
            if (time.time() - meta["cached_at"]) > self.expiry_seconds:
                return True
        if source_mtime is not None:
            cached = meta.get("source_mtime")
            if cached is None or abs(cached - source_mtime) > 1.0:
                return True
        return False


    def refresh_expiry(
        self,
        key: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> None:
        """HTTP 304 응답 후 유효기간 갱신 — 타일 전용"""
        self._db_refresh_expiry(key, etag, last_modified)


    def invalidate(self, key: str) -> None:
        """특정 키 캐시 삭제 (메모리 + 디스크)"""
        self._mem_remove(key)
        with self._db_lock:
            try:
                with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                    row = conn.execute(
                        "SELECT file_name FROM entries WHERE cache_key = ?", (key,)
                    ).fetchone()
                    if row:
                        (self._cache_dir / row[0]).unlink(missing_ok=True)
                        conn.execute(
                            "DELETE FROM entries WHERE cache_key = ?", (key,)
                        )
                        conn.commit()
            except Exception as e:
                warning_print(f"[{self.namespace}] invalidate 오류: {e}")


    def _db_vacuum(self) -> None:
        """종료 시 DB 파편화 제거 (VACUUM) + WAL 정리"""
        with self._db_lock:
            try:
                with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.execute("VACUUM")
                    conn.commit()
                debug_print(f"[{self.namespace}] DB VACUUM 완료")
            except Exception as e:
                warning_print(f"[{self.namespace}] DB VACUUM 실패: {e}")


    def clear(self):
        with self._mem_lock:
            with self._db_lock:
                # ① 메모리 초기화
                self._memory.clear()
                self._memory_bytes = 0

                # ② 디스크 + DB 초기화
                try:
                    with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                        names = [
                            r[0] for r in conn.execute("SELECT file_name FROM entries")
                        ]
                        for name in names:
                            (self._cache_dir / name).unlink(missing_ok=True)
                        conn.execute("DELETE FROM entries")
                        conn.commit()
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        conn.commit()
                except Exception as e:
                    warning_print(f"[{self.namespace}] clear 오류: {e}")

        info_print(f"[{self.namespace}] 캐시 전체 삭제")


    # ── 유틸리티 (static) ────────────────────────
    @staticmethod
    def pixmap_to_bytes(pixmap: QPixmap, fmt: str = "PNG") -> Optional[bytes]:
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buf, fmt.upper())
        buf.close()
        return bytes(ba.data()) if ba.size() > 0 else None 

    @staticmethod
    def qimage_to_bytes(
        q_image: QImage, fmt: str = "JPEG", quality: int = 75
    ) -> Optional[bytes]:  
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = q_image.save(buf, fmt.upper(), quality)    # type: ignore[arg-type]
        buf.close()

        if not ok or ba.size() == 0:
            # JPEG 실패 시 PNG 폴백
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            q_image.save(buf, "PNG", -1)    # type: ignore[arg-type]
            buf.close()

        return bytes(ba.data()) if ba.size() > 0 else None


    # ── 메모리 전용 ──────────────────────────────
    def clear_memory(self) -> None:
        """메모리 캐시만 삭제 (디스크 캐시 유지).
        폴더 전환 시 이전 썸네일 메모리 해제용."""
        with self._mem_lock:
            self._memory.clear()
            self._memory_bytes = 0
        debug_print(f"[{self.namespace}] 메모리 캐시 삭제")


    # ── 통계 ─────────────────────────────────────────────────
    def memory_count(self) -> int:
        with self._mem_lock:
            return len(self._memory)


    def memory_bytes_used(self) -> int:
        with self._mem_lock:
            return self._memory_bytes


    def disk_count(self) -> int:
        try:
            with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                return conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        except Exception as e:
            warning_print(f"[{self.namespace}] disk_count 오류: {e}") 
            return 0


    def disk_bytes_used(self) -> int:
        try:
            with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
                return (
                    conn.execute("SELECT COALESCE(SUM(file_size), 0) FROM entries")
                    .fetchone()[0]
                )
        except Exception:
            return 0


    def stats(self) -> dict:
        return {
            "namespace":     self.namespace,
            "memory_count":  self.memory_count(),
            "memory_mb":     f"{self.memory_bytes_used() / 1024 / 1024:.1f}",
            "disk_count":    self.disk_count(),
            "disk_mb":       f"{self.disk_bytes_used() / 1024 / 1024:.1f}",
            "max_memory_mb": self.max_memory_bytes // 1024 // 1024,
            "max_disk_mb":   self.max_disk_bytes // 1024 // 1024,
            "expiry_days":   self.expiry_seconds // 86400 if self.expiry_seconds else 0,
            "cache_dir":     str(self._cache_dir),
        }

