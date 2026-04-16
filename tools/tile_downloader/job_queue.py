# -*- coding: utf-8 -*-
# tools\tile_downloader\job_queue.py

"""
다운로드 작업 큐 — 데이터 모델만 담당.
UI와 엔진은 tile_downloader_window.py 에서 연결.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from utils.debug import error_print
from utils.lang_manager import t


def _t(key: str, **kw) -> str:
    return t(f"tile_downloader.{key}", **kw)


class JobStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    CANCELLED = "cancelled"
    SKIPPED   = "skipped"


@dataclass
class DownloadJob:
    job_id:         str   = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name:           str   = ""
    base_url:       str   = ""
    style_id:       str   = "light"
    tile_format:    str   = "webp"
    tile_size_mode: str   = "256"
    z_min:          int   = 0
    z_max:          int   = 10
    lon_min:        float = 0.0
    lat_min:        float = 0.0
    lon_max:        float = 0.0
    lat_max:        float = 0.0
    antimeridian:   bool  = False
    concurrency:    int   = 50
    out_root:       str   = ""
    retry_on_fail:  int   = 0     
    delay_after:    float = 0.0  
    status:         JobStatus = JobStatus.PENDING
    progress:       int   = 0   
    tiles_total:    int   = 0
    tiles_done:     int   = 0
    tiles_fail:     int   = 0
    started_at:     float | None = None
    finished_at:    float | None = None
    error_msg:      str   = ""
    preset_name: str = ""

    # ── 표시 이름 자동 생성 ─────────────────────────────────────

    def auto_name(self) -> str:
        loc = self.preset_name or _t("job.custom_area")
        return (f"[{loc}] {self.style_id} "
                f"Z{self.z_min}~{self.z_max} "
                f"{self.tile_format}/{self.tile_size_mode}")


    def elapsed_str(self) -> str:
        if self.started_at is None:
            return "--"
        end = self.finished_at or time.time()
        sec = int(end - self.started_at)
        h, rem = divmod(sec, 3600)
        m, s   = divmod(rem, 60)
        if h:
            return f"{h}h {m}m {s}s"
        return f"{m}m {s}s"


    def to_dict(self) -> dict:
        d = asdict(self)
        d['status'] = self.status.value
        return d


    @classmethod
    def from_dict(cls, d: dict) -> 'DownloadJob':
        d = dict(d)
        d['status'] = JobStatus(d.get('status', JobStatus.PENDING.value))
        if d['status'] in (JobStatus.DONE, JobStatus.RUNNING):
            d['status'] = JobStatus.PENDING
            d['progress'] = 0
            d['tiles_done'] = 0
            d['tiles_total'] = 0
            d['tiles_fail'] = 0

        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class JobQueue:
    """작업 리스트 관리 — 순서/추가/삭제/저장."""

    def __init__(self):
        self._jobs: list[DownloadJob] = []

    # ── CRUD ─────────────────────────────────────────────────────

    def add(self, job: DownloadJob) -> None:
        if not job.name:
            job.name = job.auto_name()
        self._jobs.append(job)


    def remove(self, job_id: str) -> bool:
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j.job_id != job_id]
        return len(self._jobs) < before


    def get(self, job_id: str) -> DownloadJob | None:
        return next((j for j in self._jobs if j.job_id == job_id), None)


    def move_up(self, index: int) -> bool:
        if index <= 0 or index >= len(self._jobs):
            return False
        self._jobs[index - 1], self._jobs[index] = \
            self._jobs[index], self._jobs[index - 1]
        return True


    def move_down(self, index: int) -> bool:
        if index < 0 or index >= len(self._jobs) - 1:
            return False
        self._jobs[index], self._jobs[index + 1] = \
            self._jobs[index + 1], self._jobs[index]
        return True


    def next_pending(self) -> DownloadJob | None:
        return next((j for j in self._jobs
                     if j.status == JobStatus.PENDING), None)


    def clear_done(self) -> int:
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs
                    if j.status not in (
                        JobStatus.DONE,
                        JobStatus.CANCELLED,
                        JobStatus.FAILED, 
                    )]
        return before - len(self._jobs)


    def reset_failed(self) -> int:
        count = 0
        for j in self._jobs:
            if j.status == JobStatus.FAILED:
                j.status     = JobStatus.PENDING
                j.progress   = 0
                j.tiles_done = j.tiles_fail = 0
                j.error_msg  = ""
                count += 1
        return count


    def reset_cancelled(self) -> int:
        """취소된 작업을 PENDING 으로 되돌린다. 반환값: 전환된 개수."""
        count = 0
        for j in self._jobs:
            if j.status == JobStatus.CANCELLED:
                j.status     = JobStatus.PENDING
                j.progress   = 0
                j.tiles_done = 0
                j.tiles_fail = 0
                j.error_msg  = ""
                count += 1
        return count


    @property
    def jobs(self) -> list[DownloadJob]:
        return list(self._jobs)


    @property
    def pending_count(self) -> int:
        return sum(1 for j in self._jobs if j.status == JobStatus.PENDING)

    # ── 저장/불러오기 ─────────────────────────────────────────────

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps([j.to_dict() for j in self._jobs],
                        ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        except OSError as e:
            error_print(f"[JobQueue] 저장 실패: {e}")


    def load(self, path: Path) -> int:
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            self._jobs = [DownloadJob.from_dict(d) for d in data]
            return len(self._jobs)
        except Exception as e:
            error_print(f"[JobQueue] 불러오기 실패: {e}") 
            return 0