# -*- coding: utf-8 -*-
# core/ai_bg_remover.py
"""
BEN2 Base ONNX 추론 (MIT License)
의존성: onnxruntime (~15MB) — torch / ben2 패키지 불필요
모델:   BEN2_Base.onnx (~223MB, 최초 1회 자동 다운로드)
"""
from __future__ import annotations

import traceback
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage, QPixmap

from utils.debug import debug_print, error_print

_MODEL_ID   = "PramaLLC/BEN2"
_BASE_URL   = f"https://huggingface.co/{_MODEL_ID}/resolve/main"
_ONNX_FILE  = "BEN2_Base.onnx"      
_INPUT_SIZE = 1024                     

# ImageNet 정규화 (BEN2 학습 전처리와 동일)
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_BEN2_SESSION_CACHE: dict = {}


# ──────────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────────

def _get_session():
    """BEN2 세션 캐시 조회 — 최초 1회만 InferenceSession 생성."""
    import onnxruntime as ort

    onnx_path = str(get_onnx_path())
    if onnx_path not in _BEN2_SESSION_CACHE:

        debug_print(f"[BEN2] 세션 최초 로딩: {onnx_path}")
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if ort.get_device() == "GPU"
            else ["CPUExecutionProvider"]
        )
        _BEN2_SESSION_CACHE[onnx_path] = ort.InferenceSession(
            onnx_path, providers=providers
        )
    return _BEN2_SESSION_CACHE[onnx_path]


def get_model_dir() -> Path:
    """
    개발: <project_root>/.dodoRynx/models/ben2/
    배포: <exe_dir>/lib/app/models/ben2/
    """
    from utils.paths import get_user_data_dir
    return get_user_data_dir() / "models" / "ben2"


def get_onnx_path() -> Path:
    return get_model_dir() / _ONNX_FILE


def is_model_cached() -> bool:
    p = get_onnx_path()
    return p.exists() and p.stat().st_size > 200_000_000  # 200MB (실제 크기 ~223MB)


def check_dependencies() -> tuple[bool, list[str]]:
    """
    반환: (모두 설치됨, 누락 패키지 목록)
    onnxruntime 만 필요 — torch / ben2 불필요
    """
    missing: list[str] = []
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        missing.append("onnxruntime")
    return (len(missing) == 0), missing


# ──────────────────────────────────────────────────────────────────
# 모델 다운로드 워커 (ONNX 파일 1개만)
# ──────────────────────────────────────────────────────────────────

class ModelDownloadWorker(QThread):
    """
    BEN2_Base.onnx 한 파일만 다운로드.
    Signals:
        progress(downloaded_bytes, total_bytes)
        finished()
        failed(error_message)
    """
    progress: Signal = Signal(int, int)   # type: ignore[assignment]
    finished: Signal = Signal()           # type: ignore[assignment]
    failed:   Signal = Signal(str)        # type: ignore[assignment]


    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop = False


    def cancel(self) -> None:
        self._stop = True
        self.requestInterruption()


    def run(self) -> None:
    
        dest = get_model_dir() / _ONNX_FILE
        tmp_path = dest.parent / (dest.name + ".tmp")
    
        try:
            import requests

            model_dir = get_model_dir()
            model_dir.mkdir(parents=True, exist_ok=True)

            url      = f"{_BASE_URL}/{_ONNX_FILE}"

            session = requests.Session()
            session.headers["User-Agent"] = "dodoRynx/1.0"

            # ── 총 크기 확인 ─────────────────────────────────────
            head = session.head(url, allow_redirects=True, timeout=15)
            total = int(head.headers.get("content-length", 0))
            debug_print(f"BEN2 ONNX 크기: {total/1024/1024:.1f} MB")

            # ── 이미 완전히 받았으면 스킵 ────────────────────────
            if dest.exists() and total > 0 and dest.stat().st_size == total:
                self.progress.emit(total, total)
                self.finished.emit()
                return

            # ── 스트리밍 다운로드 ─────────────────────────────────
            downloaded = 0
            resp = session.get(url, stream=True, timeout=60)
            resp.raise_for_status()

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=131_072):  # 128 KB
                    if self._stop:
                        tmp_path.unlink(missing_ok=True)
                        return
                    f.write(chunk)
                    downloaded += len(chunk)
                    self.progress.emit(downloaded, total)

            tmp_path.replace(dest)
            debug_print(f"BEN2 ONNX 다운로드 완료: {dest}")
            self.finished.emit()

        except Exception:
            msg = traceback.format_exc(limit=6)
            error_print(f"ONNX 다운로드 실패:\n{msg}")
            # tmp 정리
            tmp = dest.parent / (dest.name + ".tmp")
            tmp.unlink(missing_ok=True)
            self.failed.emit(msg)


# ──────────────────────────────────────────────────────────────────
# BEN2 ONNX 추론 워커
# ──────────────────────────────────────────────────────────────────

class BEN2Worker(QThread):
    """
    onnxruntime으로 BEN2_Base.onnx 추론.
    Signals:
        progress(str)       상태 키
        finished(QPixmap)   결과 RGBA 픽스맵
        failed(str)         오류 메시지
    """
    progress: Signal = Signal(str)       # type: ignore[assignment]
    finished: Signal = Signal(QPixmap)   # type: ignore[assignment]
    failed:   Signal = Signal(str)       # type: ignore[assignment]


    def __init__(self, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)

        self._orig_w = pixmap.width()
        self._orig_h = pixmap.height()
        qimg = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        self._raw: bytes = bytes(
            np.frombuffer(qimg.bits(), dtype=np.uint8)
            .reshape((self._orig_h, self._orig_w, 4))
            .copy()
            .tobytes()
        )


    def run(self) -> None:
        dest = get_model_dir() / _ONNX_FILE 
        tmp_path = dest.parent / (dest.name + ".tmp")

        try:
            from PIL import Image

            # ── 세션 초기화 ───────────────────────────────────────
            # 캐시 없을 때만 "model_loading" 표시
            if str(get_onnx_path()) not in _BEN2_SESSION_CACHE:
                self.progress.emit("model_loading")

            sess = _get_session()   

            input_name  = sess.get_inputs()[0].name
            output_name = sess.get_outputs()[0].name
            debug_print(f"[BEN2] input={input_name}, output={output_name}")

            # ── 전처리 ────────────────────────────────────────────
            self.progress.emit("inferring")
            arr_rgba = np.frombuffer(self._raw, dtype=np.uint8).reshape(
                (self._orig_h, self._orig_w, 4)
            )

            alpha      = arr_rgba[:, :, 3:4].astype(np.float32) / 255.0
            rgb_arr    = arr_rgba[:, :, :3].astype(np.float32)
            bg         = np.full_like(rgb_arr, 128.0)
            composited = (rgb_arr * alpha + bg * (1.0 - alpha)).astype(np.uint8)
            pil_rgb    = Image.fromarray(composited, "RGB")

            pil_resized = pil_rgb.resize(
                (_INPUT_SIZE, _INPUT_SIZE), Image.Resampling.LANCZOS
            )

            inp = np.array(pil_resized, dtype=np.float32) / 255.0
            inp = (inp - _MEAN) / _STD
            inp = inp.transpose(2, 0, 1)[np.newaxis]

            # ── 추론 ─────────────────────────────────────────────
            onnx_out = sess.run([output_name], {input_name: inp})
            out: np.ndarray = np.asarray(onnx_out[0])

            # ── 후처리 ────────────────────────────────────────────
            mask: np.ndarray = out[0, 0]

            # logit 출력 여부 감지 — 음수 또는 1 초과면 sigmoid 적용
            if mask.min() < -0.1 or mask.max() > 1.1:
                mask = 1.0 / (1.0 + np.exp(-mask))

            mask = (mask * 255).clip(0, 255).astype(np.uint8)

            mask_pil = Image.fromarray(mask).resize(
                (self._orig_w, self._orig_h), Image.Resampling.LANCZOS
            )

            result_pil = Image.fromarray(arr_rgba, "RGBA")
            result_pil.putalpha(mask_pil)

            _buf = result_pil.tobytes("raw", "RGBA")
            qimg_out = QImage(
                _buf, self._orig_w, self._orig_h,
                QImage.Format.Format_RGBA8888,
            )
            self.finished.emit(QPixmap.fromImage(qimg_out))
            debug_print("[BEN2] 추론 완료")

        except Exception:
            msg = traceback.format_exc(limit=8)
            error_print(f"BEN2Worker 오류:\n{msg}")
            tmp_path.unlink(missing_ok=True)    
            self.failed.emit(msg)
