# -*- coding: utf-8 -*-
# core/image_loader.py

"""
멀티 백엔드 이미지 로딩 엔진

TurboJPEG > OpenCV > Pillow 우선순위로 최적 디코더 선택

+ EXIF 회전 자동 적용
+ RAW 파일 지원
+ HEIF/HEIC: Windows WinRT API 사용 (pillow_heif 미사용, HEVC 특허 부담 없음)
"""

import asyncio
import io
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QMovie, QPixmap, QTransform

from core.rotation_manager import get_raw_sidecar_rotation
from utils.debug import debug_print, error_print, info_print, warning_print

try:
    import rawpy
    RAW_AVAILABLE = True
except ImportError:
    RAW_AVAILABLE = False
    warning_print("rawpy 없음 - RAW 파일 지원 불가")

try:
    import winrt.windows.storage
    import winrt.windows.graphics.imaging
    _WINRT_AVAILABLE = True
except ImportError:
    _WINRT_AVAILABLE = False
    warning_print("winrt 없음 - HEIF/HEIC 지원 불가")
    warning_print("설치: pip install winrt-runtime winrt-Windows.Storage winrt-Windows.Graphics.Imaging")


# HEIF 계열 확장자 집합
_HEIF_EXTS: frozenset = frozenset({'.heic', '.heif', '.avif'})

# EXIF Orientation 태그(274) 값 → 회전 각도(도) 변환 테이블
_EXIF_ORIENT_TO_DEG: dict[int, int] = {
    1: 0,    # 정상
    3: 180,  # 180° 회전
    6: 90,   # 90° CW  (세로 촬영, 카메라 오른쪽으로 기울임)
    8: 270,  # 270° CW (세로 촬영, 카메라 왼쪽으로 기울임)
}


# ============================================
# 모듈 수준 유틸리티
# ============================================

def _read_exif_orientation_deg(file_path: Path) -> int:
    """
    RAW/DNG 파일의 EXIF Orientation 태그(274)를 읽어 회전 각도(도)로 반환.
    
    rawpy.sizes.flip=0이지만 Sony DNG 등 일부 포맷에서
    EXIF 태그에 orientation이 별도 저장된 경우를 처리.
    
    Returns:
        0 / 90 / 180 / 270  (읽기 실패 시 0)
    """
    # 1차 시도: piexif (빠름, RAW/DNG TIFF 구조 직접 파싱)
    try:
        import piexif
        exif_dict = piexif.load(str(file_path))
        orientation = exif_dict.get("0th", {}).get(
            piexif.ImageIFD.Orientation, 1
        )
        deg = _EXIF_ORIENT_TO_DEG.get(int(orientation), 0)
        return deg
    except Exception:
        pass

    # 2차 시도: Pillow 폴백
    try:
        from PIL import Image
        with Image.open(str(file_path)) as img:
            exif = img.getexif()
            orientation = exif.get(274, 1)  # 274 = Orientation
            return _EXIF_ORIENT_TO_DEG.get(int(orientation), 0)
    except Exception:
        pass

    return 0


def _read_thumb_orientation_deg(raw_obj) -> int:
    """
    rawpy 내장 JPEG 썸네일 EXIF에서 orientation 읽기.

    ⚠️ 반드시 postprocess() 호출 전에 실행해야 함 (libraw 제약).
    rawpy.ThumbFormat 타입 스텁 미존재 → str() 비교로 우회.
    """
    try:
        thumb = raw_obj.extract_thumb()
        # rawpy.ThumbFormat에 타입 스텁 없음 → str 비교로 Pylance 오류 회피
        if not str(thumb.format).upper().endswith('JPEG'):
            return 0
        with Image.open(io.BytesIO(thumb.data)) as thumb_img:
            exif        = thumb_img.getexif()
            orientation = exif.get(274, 1)
            if orientation and orientation != 1:
                deg = _EXIF_ORIENT_TO_DEG.get(int(orientation), 0)
                debug_print(f"내장 JPEG 썸네일 orientation: {orientation} → {deg}°")
                return deg
    except Exception as e:
        debug_print(f"썸네일 orientation 읽기 실패: {e}")
    return 0


def _raw_flip_to_degrees(flip: int) -> int:
    """
    LibRaw imgdata.sizes.flip → QTransform.rotate()에 전달할 각도(도).
    LibRaw 정의: "required rotation to obtain properly oriented image"
      flip=0: 회전 불필요
      flip=3: 180° 회전
      flip=5: 90° CCW = QTransform.rotate(270)
      flip=6: 90° CW  = QTransform.rotate(90)
    """
    mapping = {0: 0, 3: 180, 5: 270, 6: 90}  
    return mapping.get(int(flip), 0)


# ============================================
# ImageFormat(Enum)
# ============================================

class ImageFormat(Enum):
    JPEG    = 'jpeg'
    PNG     = 'png'
    APNG    = 'apng'
    WEBP    = 'webp'
    GIF     = 'gif'
    HEIF    = 'heif'
    AVIF    = 'avif'
    BMP     = 'bmp'
    TIFF    = 'tiff'
    RAW     = 'raw'
    UNKNOWN = 'unknown'


# ============================================
# ImageLoader
# ============================================

class ImageLoader:
    """고성능 이미지 로더"""

    FORMAT_MAP = {
        '.jpg':  ImageFormat.JPEG,
        '.jpeg': ImageFormat.JPEG,
        '.png':  ImageFormat.PNG,
        '.apng': ImageFormat.APNG,
        '.webp': ImageFormat.WEBP,
        '.gif':  ImageFormat.GIF,
        '.heif': ImageFormat.HEIF,
        '.heic': ImageFormat.HEIF,
        '.avif': ImageFormat.AVIF,
        '.bmp':  ImageFormat.BMP,
        '.tif':  ImageFormat.TIFF,
        '.tiff': ImageFormat.TIFF,
        # RAW 포맷
        '.nef': ImageFormat.RAW,   # Nikon
        '.cr2': ImageFormat.RAW,   # Canon
        '.cr3': ImageFormat.RAW,   # Canon
        '.arw': ImageFormat.RAW,   # Sony
        '.dng': ImageFormat.RAW,   # Adobe DNG
        '.raf': ImageFormat.RAW,   # Fujifilm
        '.orf': ImageFormat.RAW,   # Olympus
        '.rw2': ImageFormat.RAW,   # Panasonic
        '.pef': ImageFormat.RAW,   # Pentax
        '.srw': ImageFormat.RAW,   # Samsung
    }

    # ── 초기화 ──────────────────────────────────

    def __init__(self):
        """이미지 로더 초기화"""
        self.error_count    = 0
        self.has_rawpy      = RAW_AVAILABLE
        self._rawpy_checked = True

    # ── 메인 로딩 (포맷 자동 선택) ──────────────

    def load(self, file_path: Path, max_size=None) -> Optional[QPixmap]:
        try:
            fmt = self._detect_format(file_path)
            debug_print(f"[ImageLoader.load] {file_path.name} fmt={fmt}, max_size={max_size}")

            if fmt == ImageFormat.GIF:
                return None

            if fmt == ImageFormat.APNG:
                return None

            if fmt == ImageFormat.RAW:
                return self._load_raw(file_path, max_size)

            if fmt in (ImageFormat.JPEG, ImageFormat.PNG, ImageFormat.WEBP):
                return self._load_opencv(file_path, max_size)

            elif fmt in (ImageFormat.HEIF, ImageFormat.AVIF):
                return self._load_heif_os_native(file_path, max_size)

            else:
                return self._load_qt_native(file_path, max_size)

        except Exception as e:
            error_print(f"이미지 로딩 실패 {file_path.name}: {e}")
            self.error_count += 1
            return None


    def _detect_format(self, file_path: Path) -> ImageFormat:
        """확장자로 포맷 감지"""
        suffix = file_path.suffix.lower()
        return self.FORMAT_MAP.get(suffix, ImageFormat.UNKNOWN)

    # ── 포맷별 백엔드 로더 ───────────────────────

    def _load_raw(self, file_path, max_size=None):
        if not self.has_rawpy:
            warning_print(f"RAW 지원 없음: {file_path.name}")
            return None

        try:
            import rawpy
            with rawpy.imread(str(file_path)) as raw:
                flip = raw.sizes.flip

                # postprocess 전에 썸네일 추출 (libraw 제약)
                thumb_deg = _read_thumb_orientation_deg(raw)

                # 그 다음 postprocess
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    half_size=False,
                    no_auto_bright=False,
                    user_flip=0,
                )
                debug_print(f"RAW postprocess rgb.shape={rgb.shape}, flip={flip}")

                height, width, _ = rgb.shape
                bytes_per_line   = 3 * width
                rgb_copy         = np.ascontiguousarray(rgb)
                raw_bytes        = rgb_copy.tobytes()

                q_image = QImage(raw_bytes, width, height,
                                bytes_per_line, QImage.Format.Format_RGB888)
                pixmap  = QPixmap.fromImage(q_image)

            # ── with 블록 종료 후 회전 결정 ──
            flip_angle = _raw_flip_to_degrees(flip)
            xmp_angle  = get_raw_sidecar_rotation(file_path)

            if xmp_angle is not None:
                base_angle = xmp_angle
                debug_print(f"RAW XMP 회전: {file_path.name} {xmp_angle}° (flip={flip})")

            elif flip_angle != 0:
                base_angle = flip_angle
                debug_print(f"RAW flip 회전: {file_path.name} {flip_angle}° (flip={flip})")

            else:
                # ① EXIF tag 274 (Sony DNG 등)
                base_angle = _read_exif_orientation_deg(file_path)
                if base_angle:
                    debug_print(f"RAW EXIF 폴백: {file_path.name} {base_angle}°")
                else:
                    # ② 내장 JPEG 썸네일 (Nikon NEF 등) — 이미 추출해둔 값 사용
                    base_angle = thumb_deg
                    if base_angle:
                        debug_print(f"RAW 썸네일 폴백: {file_path.name} {base_angle}°")
                    else:
                        debug_print(
                            f"RAW 회전 없음: {file_path.name} (flip={flip}, xmp=None)"
                        )

            if base_angle:
                t      = QTransform()
                t.rotate(base_angle)
                pixmap = pixmap.transformed(t, Qt.TransformationMode.SmoothTransformation)

            if max_size and pixmap and not pixmap.isNull():
                pixmap = self._resize_pixmap(pixmap, max_size)

            debug_print(
                f"RAW after rotate pixmap={pixmap.width()}x{pixmap.height()}, "
                f"base_angle={base_angle}"
            )
            return pixmap

        except Exception as e:
            error_print(f"RAW 파일 로딩 실패 {file_path.name}: {e}")
            return None


    def _load_opencv(
        self,
        file_path: Path,
        max_size: Optional[Tuple[int, int]]
    ) -> Optional[QPixmap]:
        try:
            if file_path.suffix.lower() == '.png':
                return self._load_pillow(file_path, max_size)

            with open(file_path, 'rb') as f:
                image_data = f.read()
            nparr = np.frombuffer(image_data, np.uint8)

            img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
            if img is None:
                raise ValueError("OpenCV decode failed")

            if max_size:
                h, w  = img.shape[:2]
                scale = min(max_size[0] / w, max_size[1] / h, 1.0)
                if scale < 1.0:
                    new_w = max(1, int(w * scale))
                    new_h = max(1, int(h * scale))
                    img   = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

            if len(img.shape) == 3:
                if img.shape[2] == 3:
                    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                elif img.shape[2] == 4:
                    img = cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)

            return self._numpy_to_qpixmap(img)

        except Exception as e:
            warning_print(f"OpenCV 실패 ({file_path.name}), Pillow 시도: {e}")
            return self._load_pillow(file_path, max_size)


    def _load_pillow(
        self,
        file_path: Path,
        max_size: Optional[Tuple[int, int]]
    ) -> Optional[QPixmap]:
        try:
            with Image.open(file_path) as img:
                img.load()
                # 16-bit PNG → 8-bit 변환
                if img.mode == 'I':
                    img = img.point(lambda x: x * (1 / 256)).convert('L')
                elif img.mode == 'I;16':
                    img = img.convert('L')
                elif img.mode == 'RGB;16':
                    img = img.convert('RGB')

                if max_size:
                    img.thumbnail(max_size, Image.Resampling.LANCZOS)

                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')

                return self._pil_to_qpixmap(img)

        except Exception as e:
            warning_print(f"Pillow 실패 ({file_path.name}), Qt 시도: {e}")
            return self._load_qt_native(file_path, max_size)


    def _load_heif_os_native(
        self,
        file_path: Path,
        max_size: Optional[Tuple[int, int]]
    ) -> Optional[QPixmap]:
        """WinRT Windows.Graphics.Imaging.BitmapDecoder로 HEIF/HEIC/AVIF 디코딩.

        특허 구조: Microsoft WinRT API에 완전 위임.
        HEIF Image Extensions + HEVC Video Extensions 설치 시 작동.
        winrt 미설치 또는 코덱 미설치 시 None 반환 (파일 무시).
        """
        if not _WINRT_AVAILABLE:
            debug_print(f"winrt 없음, HEIF 무시: {file_path.name}")
            return None

        try:
            return self._run_winrt(self._decode_heif_winrt(file_path, max_size))
        except Exception as e:
            error_print(f"WinRT HEIF 실패 ({file_path.name}): {e}")
            return None


    @staticmethod
    def _run_winrt(coro: Any) -> Any:
        """asyncio 코루틴을 동기 블로킹으로 실행.

        Qt 이벤트 루프(C++ 레이어)와 asyncio 이벤트 루프는 독립적이므로
        충돌하지 않음. 별도 스레드에서 새 이벤트 루프를 생성하여 실행.
        """
        result:    list[Any]                  = [None]
        exception: list[Optional[Exception]]  = [None]

        def worker() -> None:
            loop = asyncio.new_event_loop()
            try:
                result[0] = loop.run_until_complete(coro)
            except Exception as exc:
                exception[0] = exc
            finally:
                loop.close()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout=30)

        if exception[0] is not None:
            raise exception[0]
        return result[0]


    @staticmethod
    async def _decode_heif_winrt(
        file_path: Path,
        max_size: Optional[Tuple[int, int]]
    ) -> Optional[QPixmap]:
        """WinRT BitmapDecoder 비동기 디코딩 본체.

        함수 내부에서 import → Pylance 타입 추론 문제 완전 해소.
        ExifOrientationMode.RESPECT_EXIF_ORIENTATION 으로
        EXIF 회전을 OS 레벨에서 자동 처리.
        """
        from winrt.windows.storage import StorageFile, FileAccessMode
        from winrt.windows.graphics.imaging import (
            BitmapDecoder,
            BitmapPixelFormat,
            BitmapAlphaMode,
            BitmapTransform,
            ExifOrientationMode,
            ColorManagementMode,
        )

        abs_path = str(file_path.resolve())
        storage  = await StorageFile.get_file_from_path_async(abs_path)
        stream   = await storage.open_async(FileAccessMode.READ)
        decoder  = await BitmapDecoder.create_async(stream)

        # EXIF 회전 적용 후 실제 출력 크기
        out_w = decoder.oriented_pixel_width
        out_h = decoder.oriented_pixel_height

        # 다운샘플링 변환 계산
        transform = BitmapTransform()
        if max_size and (out_w > max_size[0] or out_h > max_size[1]):
            scale                   = min(max_size[0] / out_w, max_size[1] / out_h)
            transform.scaled_width  = max(1, int(out_w * scale))
            transform.scaled_height = max(1, int(out_h * scale))
            out_w                   = transform.scaled_width
            out_h                   = transform.scaled_height

        # RGBA8 픽셀 추출 (EXIF 회전 자동 반영)
        pixel_data = await decoder.get_pixel_data_transformed_async(
            BitmapPixelFormat.RGBA8,
            BitmapAlphaMode.STRAIGHT,
            transform,
            ExifOrientationMode.RESPECT_EXIF_ORIENTATION,
            ColorManagementMode.DO_NOT_COLOR_MANAGE,
        )

        raw = bytes(pixel_data.detach_pixel_data())
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(out_h, out_w, 4)
        arr = np.ascontiguousarray(arr)

        debug_print(f"WinRT HEIF 성공: {file_path.name} ({out_w}×{out_h})")
        return ImageLoader._numpy_to_qpixmap(arr)


    def _load_qt_native(
        self,
        file_path: Path,
        max_size: Optional[Tuple[int, int]]
    ) -> Optional[QPixmap]:
        """Qt 네이티브 이미지 로더 (최종 fallback)"""
        try:
            pixmap = QPixmap(str(file_path))
            if pixmap.isNull():
                return None

            if max_size:
                pixmap = self._resize_pixmap(pixmap, max_size)

            return pixmap

        except Exception as e:
            error_print(f"Qt 로더 실패 ({file_path.name}): {e}")
            return None

    # ── 이미지 변환 / 리사이징 ───────────────────

    def _resize_pixmap(self, pixmap: QPixmap, max_size: Tuple[int, int]) -> QPixmap:
        """픽스맵 리사이징"""
        max_w, max_h = max_size
        if pixmap.width() <= max_w and pixmap.height() <= max_h:
            return pixmap
        return pixmap.scaled(
            max_w, max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )


    @staticmethod
    def _numpy_to_qpixmap(img: np.ndarray) -> QPixmap:
        """NumPy 배열 → QPixmap 변환 (bytes() 복사로 dangling pointer 원천 차단)"""
        h, w = img.shape[:2]
        img  = np.ascontiguousarray(img)

        if len(img.shape) == 2:
            bytes_per_line = w
            fmt            = QImage.Format.Format_Grayscale8
        elif img.shape[2] == 3:
            bytes_per_line = 3 * w
            fmt            = QImage.Format.Format_RGB888
        elif img.shape[2] == 4:
            bytes_per_line = 4 * w
            fmt            = QImage.Format.Format_RGBA8888
        else:
            raise ValueError(f"Unsupported channel count: {img.shape[2]}")

        raw_bytes = img.tobytes()
        q_img     = QImage(raw_bytes, w, h, bytes_per_line, fmt)
        pix       = QPixmap.fromImage(q_img)
        if pix.isNull():
            raise RuntimeError("QPixmap 생성 실패")
        return pix


    @staticmethod
    def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
        """PIL Image → QPixmap 변환 (numpy 경유)"""
        if img.mode == 'RGBA':
            arr = np.array(img)
        else:
            arr = np.array(img.convert('RGB'))
        return ImageLoader._numpy_to_qpixmap(arr)

    # ── EXIF 처리 ────────────────────────────────

    def apply_exif_rotation(self, file_path: Path, pixmap: QPixmap, max_size=None) -> QPixmap:
        if pixmap.isNull():
            return pixmap

        fmt = self._detect_format(file_path)
        if fmt == ImageFormat.RAW or file_path.suffix.lower() in _HEIF_EXTS:
            return pixmap

        # EXIF 태그만 읽기 — 픽셀 디코딩 없음 (piexif: ~1ms)
        orientation = self._read_orientation_tag(file_path)
        if orientation == 1:
            return pixmap

        return self._transform_by_orientation(pixmap, orientation)


    def _read_orientation_tag(self, file_path: Path) -> int:
        """EXIF Orientation 태그만 읽기 (전체 디코딩 없음)"""
        try:
            import piexif
            d = piexif.load(str(file_path))
            return int(d.get("0th", {}).get(piexif.ImageIFD.Orientation, 1))
        except Exception:
            pass
        try:
            with Image.open(file_path) as img:
                return int(img.getexif().get(274, 1))
        except Exception:
            return 1
                    

    def _transform_by_orientation(self, pixmap: QPixmap, orientation: int) -> QPixmap:
        """
        파일 재오픈 없이 QTransform으로 EXIF orientation 적용.
        orientation 5/7은 2단계 변환으로 정확히 처리.
        """
        sm = Qt.TransformationMode.SmoothTransformation
        w, h = pixmap.width(), pixmap.height()

        if orientation == 2:   # H-flip
            t = QTransform(-1, 0, 0, 1, w, 0)
            return pixmap.transformed(t, sm)

        elif orientation == 3: # 180°
            t = QTransform(); t.rotate(180)
            return pixmap.transformed(t, sm)

        elif orientation == 4: # V-flip
            t = QTransform(1, 0, 0, -1, 0, h)
            return pixmap.transformed(t, sm)

        elif orientation == 5: # Transpose (rotate90CW → hflip)
            t1 = QTransform(); t1.rotate(90)
            p = pixmap.transformed(t1, sm)
            t2 = QTransform(-1, 0, 0, 1, p.width(), 0)
            return p.transformed(t2, sm)

        elif orientation == 6: # 90° CW
            t = QTransform(); t.rotate(90)
            return pixmap.transformed(t, sm)

        elif orientation == 7: # Transverse (rotate90CW → vflip)
            t1 = QTransform(); t1.rotate(90)
            p = pixmap.transformed(t1, sm)
            t2 = QTransform(1, 0, 0, -1, 0, p.height())
            return p.transformed(t2, sm)

        elif orientation == 8: # 270° CW
            t = QTransform(); t.rotate(270)
            return pixmap.transformed(t, sm)

        return pixmap


    def get_exif_rotation_angle(self, file_path: Path) -> int:
        """EXIF orientation 태그만 읽어서 회전 각도(int) 반환.
        파일을 lazy open하므로 전체 디코딩 없이 빠르게 처리됨."""
        try:
            ext = file_path.suffix.lower()

            # RAW는 _load_raw()에서 이미 flip + XMP 회전이 적용됨 → 추가 회전 불필요
            if ext in ('.cr2', '.cr3', '.nef', '.arw', '.dng',
                       '.orf', '.rw2', '.pef', '.srw', '.raf'):
                return 0

            # HEIC/HEIF/AVIF: WinRT가 이미 EXIF 회전 처리
            if ext in _HEIF_EXTS:
                return 0

            with Image.open(file_path) as img:
                exif        = img.getexif()
                orientation = exif.get(274, 1) if exif else 1
                return {
                    1:   0,    # 정상
                    2:   0,    # 수평 flip (순수 mirror, 회전 없음)
                    3: 180,    # 180°
                    4:   0,    # 수직 flip (순수 mirror, 회전 없음)
                    5: -90,    # 90° CCW + flip → flip 무시 시 270° CW
                    6:  90,    # 90° CW ✓
                    7:  90,    # 90° CW + flip → flip 무시 시 90° CW
                    8: -90,    # 90° CCW = 270° CW ✓
                }.get(orientation, 0)

        except Exception:
            return 0

    # ── 애니메이션 지원 ─────────────────────────

    def load_animated(self, file_path: Path) -> Optional[QMovie]:
        """애니메이션 이미지 로딩 (GIF, APNG)"""
        try:
            movie = QMovie(str(file_path))
            if movie.isValid():
                return movie
            return None
        except Exception as e:
            error_print(f"애니메이션 로딩 실패 ({file_path.name}): {e}")
            return None


    def configure_movie(
        self,
        movie: QMovie,
        viewport_size: Optional[Tuple[int, int]] = None,
        scale_quality: str = 'high',
        cache_mode: bool = True,
    ) -> QMovie:
        """QMovie 성능 최적화"""
        if cache_mode:
            movie.setCacheMode(QMovie.CacheMode.CacheAll)
        else:
            movie.setCacheMode(QMovie.CacheMode.CacheNone)

        if scale_quality == 'medium' and viewport_size:
            movie.jumpToFrame(0)
            first_frame = movie.currentPixmap()
            if not first_frame.isNull():
                vw, vh      = viewport_size
                scaled_size = first_frame.size().scaled(
                    vw, vh, Qt.AspectRatioMode.KeepAspectRatio
                )
                movie.setScaledSize(scaled_size)
                debug_print(f"QMovie 스케일 적용: {scaled_size.width()}x{scaled_size.height()}")

        movie.setSpeed(100)
        return movie


    def _load_animation_frames(self, file_path: Path) -> Optional[Tuple[list, list]]:
        """WebP / APNG 공통 프레임 디코더"""
        frames, delays = [], []
        with Image.open(file_path) as img:
            n_frames = getattr(img, 'n_frames', 1)
            if n_frames <= 1:
                return None
            for i in range(n_frames):
                img.seek(i)
                frame     = img.convert('RGBA')
                arr       = np.ascontiguousarray(np.array(frame))
                h, w      = arr.shape[:2]
                qimg      = QImage(arr.tobytes(), w, h, 4*w, QImage.Format.Format_RGBA8888)
                frames.append(QPixmap.fromImage(qimg))
                delays.append(max(int(img.info.get('duration', 100)), 16))
        return frames, delays


    def load_webp_frames(self, file_path):
        try:    return self._load_animation_frames(file_path)
        except Exception as e:
            error_print(f"WebP 프레임 디코딩 실패 ({file_path.name}): {e}"); return None


    def load_apng_frames(self, file_path):
        try:    return self._load_animation_frames(file_path)
        except Exception as e:
            error_print(f"APNG 프레임 디코딩 실패 ({file_path.name}): {e}"); return None


    def is_animated(self, file_path: Path) -> bool:
        try:
            if file_path.stat().st_size > 100 * 1024 * 1024:
                warning_print(f"파일이 너무 큼: {file_path.name}")
                return False

            fmt = self._detect_format(file_path)

            if fmt == ImageFormat.GIF:
                with Image.open(file_path) as img:
                    return getattr(img, 'n_frames', 1) > 1

            if fmt == ImageFormat.WEBP:
                with open(file_path, 'rb') as f:
                    header = f.read(12)
                if len(header) < 12 or header[:4] != b'RIFF' or header[8:12] != b'WEBP':
                    return False
                with open(file_path, 'rb') as f:
                    f.seek(12)
                    while True:
                        chunk_header = f.read(8)
                        if len(chunk_header) < 8:
                            break
                        chunk_type = chunk_header[:4]
                        chunk_size = int.from_bytes(chunk_header[4:8], 'little')
                        if chunk_type == b'ANIM':
                            return True
                        f.seek(chunk_size + (chunk_size % 2), 1)
                return False

            if fmt == ImageFormat.APNG:
                return True

            if fmt == ImageFormat.PNG:
                with Image.open(file_path) as img:
                    return getattr(img, 'n_frames', 1) > 1

            return False

        except Exception as e:
            warning_print(f"애니메이션 확인 실패 ({file_path.name}): {e}")
            return False


    def is_apng(self, file_path: Path) -> bool:
        """.apng 확장자 또는 .png 중 n_frames > 1인 파일 판별.
        is_animated() 이후 GIF/WebP와 분기하기 위한 전용 메서드.
        is_animated()가 True인 경우에만 호출하면 불필요한 I/O 없음.
        """
        fmt = self._detect_format(file_path)
        if fmt == ImageFormat.APNG:
            return True
        if fmt == ImageFormat.PNG:
            try:
                with Image.open(file_path) as img:
                    return getattr(img, 'n_frames', 1) > 1
            except Exception:
                return False
        return False

    # ── 이미지 정보 ─────────────────────────────

    def get_image_info(self, file_path: Path) -> Dict[str, Any]:
        """이미지 기본 정보 반환"""
        try:
            stat = file_path.stat()
            info: Dict[str, Any] = {
                'file_name':     file_path.name,
                'file_size':     stat.st_size,
                'file_size_str': self._format_size(stat.st_size),
                'modified_time': stat.st_mtime,
            }

            fmt = self._detect_format(file_path)

            if fmt in (ImageFormat.HEIF, ImageFormat.AVIF):
                # WinRT로 크기 읽기 — Image.open() 절대 호출하지 않음
                w, h = 0, 0
                if _WINRT_AVAILABLE:
                    try:
                        async def _get_size_inner() -> Tuple[int, int]:
                            from winrt.windows.storage import StorageFile, FileAccessMode
                            from winrt.windows.graphics.imaging import BitmapDecoder
                            s = await StorageFile.get_file_from_path_async(
                                str(file_path.resolve())
                            )
                            st = await s.open_async(FileAccessMode.READ)
                            d  = await BitmapDecoder.create_async(st)
                            return d.oriented_pixel_width, d.oriented_pixel_height

                        w, h = ImageLoader._run_winrt(_get_size_inner())
                    except Exception as e:
                        debug_print(f"WinRT 크기 읽기 실패 ({file_path.name}): {e}")

                info['width']  = w
                info['height'] = h
                info['format'] = file_path.suffix.upper().lstrip('.')
                info['mode']   = 'RGB'

            else:
                with Image.open(file_path) as img:
                    info['width']  = img.width
                    info['height'] = img.height
                    info['format'] = img.format
                    info['mode']   = img.mode

            return info

        except Exception as e:
            error_print(f"이미지 정보 읽기 실패 ({file_path.name}): {e}")
            return {}


    @staticmethod
    def _format_size(size: int) -> str:
        """파일 크기 포맷"""
        size_float = float(size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_float < 1024.0:
                return f"{size_float:.2f} {unit}"
            size_float /= 1024.0
        return f"{size_float:.2f} TB"

    # ── 통계 ────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """로더 통계 반환"""
        return {
            'error_count': self.error_count,
            'raw_support':  self.has_rawpy,
        }


    def reset_stats(self) -> None:
        """통계 초기화"""
        self.error_count = 0
