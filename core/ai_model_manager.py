# -*- coding: utf-8 -*-
# core/ai_model_manager.py

from __future__ import annotations

import traceback
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from utils.debug import debug_print, error_print
from utils.lang_manager import t


# ──────────────────────────────────────────────────────────────────
# 모델 레지스트리
# ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelInfo:

    key:        str
    filename:   str
    hf_repo:    str
    hf_file:    str
    min_size:   int
    label:      str
    is_zip:     bool = False
    zip_inner:  str  = ""

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.hf_repo}/resolve/main/{self.hf_file}"


MODEL_REGISTRY: dict[str, ModelInfo] = {
    "lama": ModelInfo(
        key      = "lama",
        filename = "lama_fp32.onnx",
        hf_repo  = "Carve/LaMa-ONNX",
        hf_file  = "lama_fp32.onnx",
        min_size = 150_000_000,
        label    = "LaMa Inpainting",
    ),
}


# ──────────────────────────────────────────────────────────────────
# 경로 유틸
# ──────────────────────────────────────────────────────────────────

def get_ai_model_dir(key: str) -> Path:
    from utils.paths import get_user_data_dir
    return get_user_data_dir() / "models" / key


def get_onnx_path(key: str) -> Path:
    info = MODEL_REGISTRY[key]
    return get_ai_model_dir(key) / info.filename


def is_model_cached(key: str) -> bool:
    try:
        p    = get_onnx_path(key)
        info = MODEL_REGISTRY[key]
        return p.exists() and p.stat().st_size >= info.min_size
    except Exception:
        return False


def check_dependencies() -> tuple[bool, list[str]]:
    missing: list[str] = []
    try:
        import onnxruntime
    except ImportError:
        missing.append("onnxruntime")
    return len(missing) == 0, missing


def get_ort_providers() -> list[str]:
    try:
        import onnxruntime as ort
        available = ort.get_available_providers()
        preferred = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return [p for p in preferred if p in available]
    except Exception:
        return ["CPUExecutionProvider"]


# ──────────────────────────────────────────────────────────────────
# 범용 모델 다운로드 워커
# ──────────────────────────────────────────────────────────────────

class AIModelDownloadWorker(QThread):
    progress: Signal = Signal(int, int)
    failed:   Signal = Signal(str)


    def __init__(self, model_key: str, parent=None) -> None:
        super().__init__(parent)
        self._key  = model_key
        self._stop = False


    def cancel(self) -> None:
        self._stop = True
        self.requestInterruption()


    def run(self) -> None:
        try:
            import zipfile
            import requests

            info      = MODEL_REGISTRY[self._key]
            model_dir = get_ai_model_dir(self._key)
            model_dir.mkdir(parents=True, exist_ok=True)
            final_dest = model_dir / info.filename

            if final_dest.exists() and final_dest.stat().st_size >= info.min_size:
                self.progress.emit(info.min_size, info.min_size)
                self.finished.emit()
                return

            url      = info.url
            tmp_path = model_dir / (info.filename + ".tmp")

            debug_print(t('ai_model_manager.download_start', label=info.label, url=url))
            self.progress.emit(0, info.min_size)

            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()

            total      = int(resp.headers.get("content-length", info.min_size))
            downloaded = 0

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65_536):
                    if self._stop or self.isInterruptionRequested():
                        debug_print(t('ai_model_manager.download_cancelled', label=info.label))
                        tmp_path.unlink(missing_ok=True)
                        return
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        self.progress.emit(downloaded, total)

            tmp_path.replace(final_dest)
            debug_print(t('ai_model_manager.download_complete', label=info.label, path=final_dest))
            self.progress.emit(final_dest.stat().st_size, final_dest.stat().st_size)

            if info.is_zip:
                debug_print(t('ai_model_manager.zip_extracting', label=info.label))
                with zipfile.ZipFile(final_dest, "r") as zf:
                    target = info.zip_inner if info.zip_inner else None
                    if target:
                        matched = next(
                            (n for n in zf.namelist() if n.endswith(target)), None
                        )
                        if matched is None:
                            raise FileNotFoundError(
                                t('ai_model_manager.zip_inner_not_found',
                                  target=target, contents=zf.namelist())
                            )
                        extracted = model_dir / info.filename
                        with zf.open(matched) as src, open(extracted, "wb") as dst:
                            dst.write(src.read())
                    else:
                        onnx_files = [n for n in zf.namelist() if n.endswith(".onnx")]
                        if not onnx_files:
                            raise FileNotFoundError(t('ai_model_manager.zip_no_onnx'))
                        extracted = model_dir / info.filename
                        with zf.open(onnx_files[0]) as src, open(extracted, "wb") as dst:
                            dst.write(src.read())
                final_dest.unlink(missing_ok=True)
                debug_print(t('ai_model_manager.zip_extract_complete', label=info.label, path=extracted))

            self.finished.emit()

        except Exception:
            msg = traceback.format_exc(limit=6)
            error_print(t('ai_model_manager.download_failed', model_key=self._key, msg=msg))
            try:
                info = MODEL_REGISTRY[self._key]
                for suffix in (".tmp", ""):
                    p = get_ai_model_dir(self._key) / (info.filename + suffix)
                    p.unlink(missing_ok=True)
            except Exception:
                pass
            self.failed.emit(msg)
