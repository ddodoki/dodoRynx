# -*- coding: utf-8 -*-
# tools\gps_map\gps_map_thumbs.py

from __future__ import annotations

import random
import hashlib
import io
import json
import math
import os
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib.parse import urlparse

from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

from core.hybrid_cache import HybridCache
from utils.debug import error_print, info_print, warning_print

SOURCE_THUMB_SIZE = 72
DISPLAY_THUMBBAR_SIZE = 56
DISPLAY_PIN_THUMB_SIZE = 42


@dataclass(slots=True)
class GpsMapPhoto:

    filepath: str
    filename: str
    lat: float
    lon: float
    date_taken: str = ""
    model: str = ""
    is_current: bool = False

    def to_map_point(self, thumb_url: str = "") -> dict:
        """
        JS points 배열 한 항목으로 직렬화.

        [설계 원칙]
        - 클러스터 대표 오버라이드(rep_overrides)는 포인트 단위가 아닌
          전역 JS 변수 REP_OVERRIDES로 주입됨 (html_params → build_html).
        - 따라서 이 메서드는 thumb_url만 외부에서 받는다.
        """
        return {
            "filepath":   self.filepath,
            "filename":   self.filename,
            "lat":        self.lat,
            "lon":        self.lon,
            "date_taken":  self.date_taken,
            "model":      self.model,
            "is_current": self.is_current,
            "thumb_url":  thumb_url,
        }


class _ThumbBridge(QObject):
    loaded = Signal(str, QImage, int)


class _ThumbJob(QRunnable):
    def __init__(
        self,
        filepath: str,
        cache: HybridCache,
        generation: int,
        bridge: _ThumbBridge,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.filepath = filepath    
        self._path = Path(filepath)    
        self.cache = cache
        self.generation = generation
        self.bridge = bridge
        self.cancelled = False


    def cancel(self) -> None:
        self.cancelled = True


    def run(self) -> None:
        if self.cancelled:
            self.bridge.loaded.emit(self.filepath, QImage(), self.generation)
            return
        qimg = _load_thumb_qimage_sync(self._path, self.cache)  
        if self.cancelled:
            self.bridge.loaded.emit(self.filepath, QImage(), self.generation)
            return
        self.bridge.loaded.emit(
            self.filepath,                                       
            qimg if qimg is not None else QImage(),
            self.generation,
        )


class GpsThumbProvider(QObject):

    thumb_ready = Signal(str, QImage, int)

    def __init__(self, parent: Optional[QObject] = None, memory_mb: int = 50, disk_mb: int = 300) -> None:
        super().__init__(parent)
        self._cache = HybridCache(namespace="thumbnails", max_memory_mb=memory_mb, max_disk_mb=disk_mb, expiry_days=0)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, min((os.cpu_count() or 4) - 2, 4)))
        self._bridge = _ThumbBridge()
        self._bridge.loaded.connect(self._on_loaded)
        self._generation = 0
        self._pending: set[str] = set()
        self._jobs: list[_ThumbJob] = []


    def bump_generation(self) -> int:
        self._generation += 1
        return self._generation


    @property
    def generation(self) -> int:
        return self._generation


    def request(self, filepath: str) -> None:
        if filepath in self._pending:
            return

        if any(j.filepath == filepath and not j.cancelled for j in self._jobs):
            return
        self._pending.add(filepath)
        job = _ThumbJob(filepath, self._cache, self._generation, self._bridge)
        self._jobs.append(job)
        self._pool.start(job)


    def request_many(self, filepaths: Iterable[str]) -> None:
        for fp in filepaths:
            self.request(fp)


    def clear_pending(self) -> None:
        self._generation += 1
        self._pending.clear()
        for job in self._jobs:
            job.cancel()
        self._jobs.clear()


    def get_pixmap_sync(self, filepath: str) -> Optional[QPixmap]:
        """UI 위젯용 QPixmap 동기 반환. (변경 없음)"""
        qimg = _load_thumb_qimage_sync(Path(filepath), self._cache)
        if qimg is None or qimg.isNull():
            return None
        return QPixmap.fromImage(qimg)


    def get_bytes_sync(self, filepath: str) -> Optional[bytes]:
        """
        HTTP 썸네일 서버 전용 JPEG bytes 반환.

        처리 우선순위
        ─────────────
        ① cache.get_raw()   : 디스크 캐시 파일 직독 (변환 없음) 대부분의 경우
        ② _load_thumb_qimage_sync() 후 cache.get_raw() 재시도
        → _load_thumb_qimage_sync 내부에서 cache.put(raw_bytes)를 호출하므로
            이후 get_raw()는 반드시 성공한다.
        ③ 최후 수단: qimg → HybridCache.qimage_to_bytes()
        → cache.put()이 실패한 극단적 케이스(디스크 풀, 권한 오류 등)에서만 진입.

        get_pixmap_sync()를 경유하지 않으므로
        QPixmap → QImage → RGB888 → PIL → JPEG 4단계 변환이 완전히 사라진다.
        """
        fp_path = Path(filepath)
        key = _cache_key(fp_path)

        if key is not None:
            try:
                raw = self._cache.get_raw(key)
                if raw:
                    return raw
            except Exception as e:
                warning_print(
                    f"[GpsMapThumb] get_raw 실패 (①): "
                    f"{fp_path.name} / {e}"
                )

        qimg = _load_thumb_qimage_sync(fp_path, self._cache)
        if qimg is None or qimg.isNull():
            return None

        if key is not None:
            try:
                raw = self._cache.get_raw(key)
                if raw:
                    return raw
            except Exception as e:
                warning_print(
                    f"[GpsMapThumb] get_raw 실패 (②): "
                    f"{fp_path.name} / {e}"
                )

        warning_print(
            f"[GpsMapThumb] cache 저장 실패로 직접 인코딩 (③): {fp_path.name}"
        )
        return HybridCache.qimage_to_bytes(qimg, fmt="JPEG", quality=60)


    def _on_loaded(self, filepath: str, qimg: QImage, generation: int) -> None:
        self._pending.discard(filepath)
        self._jobs = [
            j for j in self._jobs
            if not (j.filepath == filepath and (j.cancelled or generation >= j.generation))
        ]
        if generation != self._generation:
            return
        self.thumb_ready.emit(filepath, qimg, generation)


def _cache_key(filepath: Path, thumb_size: int = SOURCE_THUMB_SIZE) -> Optional[str]:
    try:
        stat = filepath.stat()
    except OSError:
        return None
    xmp_mtime = 0.0
    if filepath.suffix.lower() in {".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef", ".srw", ".raf"}:
        try:
            xmp_mtime = filepath.with_suffix(filepath.suffix + ".xmp").stat().st_mtime
        except OSError:
            xmp_mtime = 0.0
    return f"{filepath.resolve()}|{int(stat.st_mtime)}|{int(xmp_mtime)}|{thumb_size}"


def _load_thumb_qimage_sync(filepath: Path, cache: HybridCache) -> Optional[QImage]:
    key = _cache_key(filepath)
    if key is None:
        return None
    try:
        pix = cache.get(key)
    except Exception as e:
        warning_print(f"[GpsMapThumb] cache get 실패: {filepath.name} / {e}")
        pix = None
    if pix and not pix.isNull():
        return pix.toImage()

    qimg = _generate_qimage(filepath, SOURCE_THUMB_SIZE)
    if qimg is None or qimg.isNull():
        return None
    try:
        raw = HybridCache.qimage_to_bytes(qimg, fmt="JPEG", quality=60)
        if raw:
            cache.put(key, QPixmap.fromImage(qimg), raw, source_mtime=filepath.stat().st_mtime)
    except Exception as e:
        warning_print(f"[GpsMapThumb] cache put 실패: {filepath.name} / {e}")
    return qimg


def _generate_qimage(filepath: Path, thumb_size: int) -> Optional[QImage]:
    try:
        ext = filepath.suffix.lower()
        if ext in {".jpg", ".jpeg"}:
            qimg = _try_extract_exif_thumbnail(filepath, thumb_size)
            if qimg is not None and not qimg.isNull():
                return qimg
        with Image.open(filepath) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            w, h = img.size
            minside = min(w, h)
            img = img.crop(((w - minside) // 2, (h - minside) // 2, (w + minside) // 2, (h + minside) // 2))
            img = img.resize((thumb_size, thumb_size), Image.Resampling.LANCZOS) 
            data = img.tobytes()
            qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
            return qimg.copy()
    except Exception as e:
        warning_print(f"[GpsMapThumb] 생성 실패: {filepath.name} / {e}")
        return None


def _try_extract_exif_thumbnail(filepath: Path, thumb_size: int) -> Optional[QImage]:
    try:
        with open(filepath, "rb") as f:           
            # PIL이 파일 핸들을 받아 EXIF 파싱
            with Image.open(f) as main_img:
                main_img.load()  
                exif = main_img.getexif()
                orientation = exif.get(274, 1)
                try:
                    ifd1 = exif.get_ifd(1)
                except Exception:
                    ifd1 = {}
                offset = ifd1.get(513)
                length = ifd1.get(514)
                if not offset or not length or length < 2000:
                    return None

            # 같은 파일 핸들로 썸네일 위치 탐색
            f.seek(offset)
            thumb_bytes = f.read(length)

        thumb = Image.open(io.BytesIO(thumb_bytes))
        try:
            thumb = ImageOps.exif_transpose(thumb)
        except Exception:
            thumb = _apply_orientation_manual(thumb, orientation)

        if min(thumb.size) < thumb_size:
            return None

        thumb = thumb.convert("RGB")
        w, h = thumb.size
        minside = min(w, h)
        thumb = thumb.crop((
            (w - minside) // 2, (h - minside) // 2,
            (w + minside) // 2, (h + minside) // 2
        ))
        thumb = thumb.resize((thumb_size, thumb_size), Image.Resampling.BILINEAR)
        data = thumb.tobytes()
        qimg = QImage(data, thumb.width, thumb.height,
                      thumb.width * 3, QImage.Format.Format_RGB888)
        return qimg.copy()
    except Exception:
        return None


def _apply_orientation_manual(img: Image.Image, orientation: int) -> Image.Image:
    mapping = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }
    op = mapping.get(orientation)
    return img.transpose(op) if op else img


class PinThumbRegistry:

    def __init__(self, save_path: Path) -> None:
        self._save_path = save_path
        self._rep_overrides: Dict[str, str] = {}
        self.load()


    @property
    def representative_overrides(self) -> Dict[str, str]:
        return dict(self._rep_overrides)


    def load(self) -> None:
        try:
            if self._save_path.exists():
                data = json.loads(self._save_path.read_text(encoding="utf-8"))
                raw = data.get("cluster_representatives", {})
                if isinstance(raw, dict):
                    self._rep_overrides = {str(k): str(v) for k, v in raw.items()}
        except Exception as e:
            warning_print(f"[GpsMapThumb] 대표 이미지 로드 실패: {e}")
            self._rep_overrides = {}


    def save(self) -> None:
        try:
            self._save_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"cluster_representatives": self._rep_overrides}
            self._save_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            error_print(f"[GpsMapThumb] 대표 이미지 저장 실패: {e}")


    def set_representative(self, cluster_key: str, filepath: str) -> None:
        self._rep_overrides[cluster_key] = filepath
        self.save()


    def representative_for(self, cluster_key: str, members: list[str]) -> Optional[str]:
        """대표 이미지 반환. 미설정 시 members 중 랜덤 1개."""
        if not members:
            return None
        chosen = self._rep_overrides.get(cluster_key)
        if chosen and chosen in members:
            return chosen

        rng = random.Random(cluster_key)  
        return rng.choice(members)


    def clear_representative(self, cluster_key: str) -> None:
        self._rep_overrides.pop(cluster_key, None)
        self.save()


class GpsThumbHttpServer:

    def __init__(self, provider: GpsThumbProvider) -> None:
        self._provider = provider
        self._token_to_path: Dict[str, str] = {}
        self._port = 0
        self._server = None
        self._lock = threading.Lock()
        self._map_html: str = ""
        self._start()


    def set_map_html(self, html: str) -> None:
        self._map_html = html
        

    @property
    def port(self) -> int:
        return self._port


    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"


    def register_files(self, filepaths: Iterable[str]) -> None:
        with self._lock:
            for fp in filepaths:
                token = self.token_for(fp)
                self._token_to_path[token] = fp


    def token_for(self, filepath: str) -> str:
        return hashlib.sha256(filepath.encode("utf-8", "ignore")).hexdigest()[:24]


    def thumb_url(self, filepath: str) -> str:
        token = self.token_for(filepath)
        with self._lock:
            self._token_to_path[token] = filepath
        return f"{self.base_url()}/thumb/{token}.jpg"


    def resolve(self, token: str) -> Optional[str]:
        with self._lock:
            return self._token_to_path.get(token)


    def _start(self) -> None:
        import http.server
        owner = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_OPTIONS(self) -> None:
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.end_headers()

            def do_GET(self) -> None:
                path = urlparse(self.path).path

                if not path.startswith("/thumb/"):
                    self.send_response(404)
                    self.end_headers()
                    return

                token = Path(path).stem
                fp = owner.resolve(token)
                if not fp:
                    self.send_response(404)
                    self.end_headers()
                    return

                payload = owner._provider.get_bytes_sync(fp)
                if not payload:
                    self.send_response(404)
                    self.end_headers()
                    return

                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError,
                        ConnectionAbortedError, OSError):
                    pass

            def log_message(self, format: str, *args: object) -> None:
                pass

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self._port = s.getsockname()[1]
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", self._port), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True, name="gps-thumb-http").start()
        info_print(f"[GpsMapThumb] HTTP 서버 시작: {self.base_url()}")


    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None


def thumb_cluster_cell_size(zoom: float, cell_px: float = 46.0) -> float:
    px_per_deg = 256 * (2 ** zoom) / 360
    geo_deg    = cell_px / px_per_deg
    if zoom >= 17: return max(0.00008, geo_deg)
    if zoom >= 13: return max(0.00035, geo_deg)
    if zoom >= 9:  return max(0.0015,  geo_deg)
    return max(0.003, geo_deg)


def make_cluster_key(lat: float, lon: float, zoom: float) -> str:
    cz   = int(round(min(zoom, 17)))
    cell = thumb_cluster_cell_size(float(cz)) 
    gy   = math.floor(lat / cell)
    gx   = math.floor(lon / cell)
    return f"z{cz}:{gy}:{gx}"


def build_cluster_groups(points: Iterable[GpsMapPhoto], zoom: float) -> dict[str, list[GpsMapPhoto]]:
    grouped: dict[str, list[GpsMapPhoto]] = {}
    for pt in points:
        key = make_cluster_key(pt.lat, pt.lon, zoom)
        grouped.setdefault(key, []).append(pt)
    return grouped
