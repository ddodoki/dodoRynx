# -*- coding: utf-8 -*-
# core/ai_eraser.py

from __future__ import annotations

import traceback

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PIL import Image, ImageFilter

from utils.debug import debug_print, error_print

MODEL_KEY  = "lama"
FIXED_SIZE = 512

_SESSION_CACHE: dict = {}


def _get_session():
    import onnxruntime as ort
    from core.ai_model_manager import get_onnx_path, get_ort_providers 
    onnx_path = str(get_onnx_path(MODEL_KEY))
    if onnx_path not in _SESSION_CACHE:
        debug_print(f"[LaMa] 세션 최초 로딩: {onnx_path}")
        _SESSION_CACHE[onnx_path] = ort.InferenceSession(
            onnx_path, providers=get_ort_providers()
        )
    return _SESSION_CACHE[onnx_path]


class AIModelPreloader(QThread):
    
    all_ready   = Signal()          # type: ignore[assignment]
    one_ready   = Signal(str)       # type: ignore[assignment]   로딩 완료
    one_loading = Signal(str)       # type: ignore[assignment]   로딩 시작 (파일 있음)
    one_no_model = Signal(str)      # type: ignore[assignment]   파일 없음
    one_failed  = Signal(str, str)  # type: ignore[assignment]   실패

    def run(self) -> None:
        # ── BEN2 ──────────────────────────────────────────────────
        try:
            from core.ai_bg_remover import (
                is_model_cached as ben2_cached,
                _get_session as ben2_load,
            )
            if ben2_cached():
                self.one_loading.emit("ben2")
                ben2_load()
                self.one_ready.emit("ben2")    
            else:
                self.one_no_model.emit("ben2")
                debug_print("[BEN2] 모델 미설치")
        except Exception as e:
            error_print(f"[BEN2] preload 실패: {e}")
            self.one_failed.emit("ben2", str(e))

        # ── LaMa ──────────────────────────────────────────────────
        try:
            from core.ai_model_manager import is_model_cached as lama_cached
            if lama_cached("lama"):
                self.one_loading.emit("lama")
                sess = _get_session()
                dummy_img  = np.full((1, 3, FIXED_SIZE, FIXED_SIZE), 0.5, dtype=np.float32)
                dummy_mask = np.zeros((1, 1, FIXED_SIZE, FIXED_SIZE), dtype=np.float32)
                dummy_mask[0, 0, 200:300, 200:300] = 1.0
                sess.run(None, {
                    sess.get_inputs()[0].name: dummy_img,
                    sess.get_inputs()[1].name: dummy_mask,
                })
                self.one_ready.emit("lama")     
            else:
                self.one_no_model.emit("lama")
                debug_print("[LaMa] 모델 미설치")
        except Exception as e:
            error_print(f"[LaMa] preload 실패: {e}")
            self.one_failed.emit("lama", str(e))

        self.all_ready.emit()


class AIEraserWorker(QThread):

    progress: Signal = Signal(str)     # type: ignore[assignment]
    finished: Signal = Signal(QPixmap) # type: ignore[assignment]
    failed:   Signal = Signal(str)     # type: ignore[assignment]

    def __init__(self, pixmap: QPixmap, mask_pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self._orig_w = pixmap.width()
        self._orig_h = pixmap.height()

        def _to_raw(px: QPixmap) -> bytes:
            qimg = px.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            return bytes(
                np.frombuffer(qimg.bits(), dtype=np.uint8)
                .reshape((px.height(), px.width(), 4))
                .copy()
                .tobytes()
            )

        self._raw_img  = _to_raw(pixmap)
        self._raw_mask = _to_raw(mask_pixmap)

    # ── 추론 ──────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            self.progress.emit("inferring")
            sess = _get_session()

            arr_rgba  = np.frombuffer(self._raw_img,  dtype=np.uint8).reshape(
                (self._orig_h, self._orig_w, 4))
            mask_rgba = np.frombuffer(self._raw_mask, dtype=np.uint8).reshape(
                (self._orig_h, self._orig_w, 4))

            alpha      = arr_rgba[:, :, 3:4].astype(np.float32) / 255.0
            rgb_f      = arr_rgba[:, :, :3].astype(np.float32)
            bg         = np.full_like(rgb_f, 128.0)
            composited = (rgb_f * alpha + bg * (1.0 - alpha)).astype(np.uint8)
            pil_rgb    = Image.fromarray(composited, "RGB")

            pil_mask = Image.fromarray(mask_rgba, "RGBA").convert("L")
            mask_arr = (np.array(pil_mask) > 128).astype(np.float32)

            if mask_arr.sum() == 0:
                _buf = self._raw_img
                qimg = QImage(_buf, self._orig_w, self._orig_h,
                            QImage.Format.Format_RGBA8888)
                self.finished.emit(QPixmap.fromImage(qimg))
                return

            result_rgb = self._inpaint(sess, pil_rgb, mask_arr)

            result_rgba = np.dstack(
                [result_rgb, arr_rgba[:, :, 3]]
            ).astype(np.uint8)

            # bytes 객체 참조 유지 (QImage 메모리 안전)
            _buf = result_rgba.tobytes()
            qimg_out = QImage(_buf, self._orig_w, self._orig_h,
                            QImage.Format.Format_RGBA8888)
            self.finished.emit(QPixmap.fromImage(qimg_out))
            debug_print("AIEraser 추론 완료")

        except Exception:
            msg = traceback.format_exc(limit=8)
            error_print(f"AIEraserWorker 오류:\n{msg}")
            self.failed.emit(msg)

    # ── 인페인팅 ──────────────────────────────────────────────────

    def _inpaint(
        self,
        sess,
        pil_rgb:  Image.Image,
        mask_arr: np.ndarray,  
    ) -> np.ndarray:          
        w, h    = pil_rgb.size
        img_arr = np.array(pil_rgb, dtype=np.float32) / 255.0 

        if w <= FIXED_SIZE and h <= FIXED_SIZE:
            inpainted_raw = self._run_inference(sess, img_arr, mask_arr)
            inpainted_out = inpainted_raw.astype(np.float32) / 255.0

            m3 = mask_arr[:, :, np.newaxis]
            inpainted = inpainted_out * m3 + img_arr * (1.0 - m3)

        else:
            ys, xs = np.where(mask_arr > 0)

            PAD = 32 
            bx1 = max(0, int(xs.min()) - PAD)
            bx2 = min(w, int(xs.max()) + PAD)
            by1 = max(0, int(ys.min()) - PAD)
            by2 = min(h, int(ys.max()) + PAD)

            patch_img  = img_arr[by1:by2, bx1:bx2]  
            patch_mask = mask_arr[by1:by2, bx1:bx2] 

            inpainted_patch = self._run_inference(sess, patch_img, patch_mask)
            patch_out = inpainted_patch.astype(np.float32) / 255.0

            pm3 = patch_mask[:, :, np.newaxis]
            safe_patch = patch_out * pm3 + patch_img * (1.0 - pm3)

            inpainted = img_arr.copy()
            inpainted[by1:by2, bx1:bx2] = safe_patch

        # ── feather 블렌딩 (마스크 경계 자연스럽게) ──────────────────────
        mask_pil = Image.fromarray((mask_arr * 255).astype(np.uint8))
        mask_feather = np.array(
            mask_pil.filter(ImageFilter.GaussianBlur(radius=6))
        ).astype(np.float32) / 255.0
        fm = mask_feather[:, :, np.newaxis]
        blended = inpainted * fm + img_arr * (1.0 - fm)
        return (blended.clip(0, 1) * 255).astype(np.uint8)

    # ── ONNX 단일 추론 ────────────────────────────────────────────

    def _run_inference(
        self,
        sess,
        img_arr:  np.ndarray,  
        mask_arr: np.ndarray,
    ) -> np.ndarray:       
        ph, pw = img_arr.shape[0], img_arr.shape[1]

        pil_img  = Image.fromarray((img_arr  * 255).astype(np.uint8))
        pil_mask = Image.fromarray((mask_arr * 255).astype(np.uint8))

        pil_img_512  = pil_img.resize ((FIXED_SIZE, FIXED_SIZE), Image.Resampling.LANCZOS)
        pil_mask_512 = pil_mask.resize((FIXED_SIZE, FIXED_SIZE), Image.Resampling.NEAREST)

        img_in  = np.array(pil_img_512,  dtype=np.float32) / 255.0
        mask_in = (np.array(pil_mask_512, dtype=np.float32) / 255.0 > 0.5).astype(np.float32)

        inp_img  = img_in.transpose(2, 0, 1)[np.newaxis]
        inp_mask = mask_in[np.newaxis, np.newaxis]     

        inputs = {
            sess.get_inputs()[0].name: inp_img,
            sess.get_inputs()[1].name: inp_mask,
        }

        raw = np.asarray(sess.run(None, inputs)[0])
        out = raw[0].transpose(1, 2, 0)            

        if out.max() > 2.0:
            out_u8 = out.clip(0, 255).astype(np.uint8)
        else:
            out_u8 = (out.clip(0, 1) * 255).astype(np.uint8)

        result_pil = Image.fromarray(out_u8).resize(
            (pw, ph), Image.Resampling.LANCZOS
        )
        return np.array(result_pil)
