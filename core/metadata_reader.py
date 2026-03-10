# -*- coding: utf-8 -*-
# core/metadata_reader.py

"""
메타데이터 읽기 - EXIF, IPTC, XMP (HEIC 지원 포함)
"""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from datetime import datetime
from math import gcd
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from core.rotation_manager import get_raw_sidecar_rotation
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t

try:
    import winrt.windows.storage
    import winrt.windows.graphics.imaging
    _WINRT_META_AVAILABLE = True
except ImportError:
    _WINRT_META_AVAILABLE = False


_HEIF_EXTS: frozenset = frozenset({'.heic', '.heif', '.avif'})
_RAW_EXTS: frozenset = frozenset({
    '.dng', '.nef', '.cr2', '.cr3', '.arw', '.orf',
    '.rw2', '.raf', '.raw', '.rwl', '.srw', '.x3f',
    '.3fr', '.mef', '.erf', '.kdc', '.dcr',
})


class MetadataReader:
    """이미지 메타데이터 읽기"""

    def _raw_flip_to_degrees(self, flip: int) -> int:
        """LibRaw sizes.flip → QTransform.rotate() 각도 (image_loader.py와 동일)"""
        return {0: 0, 3: 180, 5: 270, 6: 90}.get(int(flip), 0)


    def _get_raw_rotation_degrees(self, file_path: Path) -> int:
        """
        RAW 파일 방향각 탐색 (4단계):
        1. XMP 사이드카
        2. rawpy sizes.flip
        3. EXIF tag 274 (piexif)  ← Sony DNG 등
        4. 내장 JPEG 썸네일 EXIF  ← Nikon NEF 등
        
        ⚠️ rawpy: extract_thumb()는 postprocess() 없이 호출해야 함
        (여기서는 postprocess 호출 없으므로 안전)
        """
        _orient_map: dict[int, int] = {1: 0, 3: 180, 6: 90, 8: 270}

        # 1단계: XMP 사이드카
        try:
            ang = get_raw_sidecar_rotation(file_path)
            if ang and ang in (90, 180, 270):
                return ang
        except Exception:
            pass

        try:
            import rawpy
            import io as _io
            with rawpy.imread(str(file_path)) as raw:

                # 2단계: rawpy sizes.flip
                flip_deg = self._raw_flip_to_degrees(raw.sizes.flip)
                if flip_deg != 0:
                    return flip_deg

                # 3단계: EXIF tag 274 (piexif)
                try:
                    import piexif
                    exif_dict = piexif.load(str(file_path))
                    ori = exif_dict.get("0th", {}).get(piexif.ImageIFD.Orientation, 1)
                    deg = _orient_map.get(int(ori), 0)
                    if deg:
                        return deg
                except Exception:
                    pass

                # 4단계: 내장 JPEG 썸네일 EXIF (Nikon NEF 등 IFD0에 orientation 없는 포맷)
                # postprocess() 호출 전이므로 extract_thumb() 사용 가능
                try:
                    thumb = raw.extract_thumb()
                    if str(thumb.format).upper().endswith('JPEG'):
                        with Image.open(_io.BytesIO(thumb.data)) as timg:
                            exif = timg.getexif()
                            ori  = exif.get(274, 1)
                            deg  = _orient_map.get(int(ori), 0)
                            if deg:
                                debug_print(
                                    f"RAW 썸네일 orientation: "
                                    f"{file_path.name} tag={ori} → {deg}°"
                                )
                                return deg
                except Exception as e:
                    debug_print(f"썸네일 orientation 읽기 실패: {e}")

        except Exception as e:
            debug_print(f"RAW orientation 탐색 실패 ({file_path.name}): {e}")

        return 0
            
# ============================================
# 초기화
# ============================================

    def __init__(self, debug: bool = False, use_cache: bool = True, max_cache_size: int = 500) -> None:
        self.use_cache = use_cache
        self.max_cache_size = max_cache_size
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._cache_lock = RLock()
        
        info_print(f"✅ MetadataReader 초기화 (캐시: {use_cache}, 크기: {max_cache_size})")

# ============================================
# 메인 메타데이터 읽기
# ============================================

    def read(self, file_path: Path) -> Dict[str, Any]:
        """메타데이터 읽기 - 스레드 안전"""
        
        # ===== 1. 캐시 키 안전하게 생성 =====
        try:
            # resolve()는 파일이 없어도 작동하지만, 느릴 수 있음
            # 대신 absolute() 사용 (더 빠름)
            key = str(file_path.absolute()).lower()
        except Exception as e:
            error_print(f"파일 경로 처리 실패: {e}")
            return self._create_error_metadata(file_path, t('metadata.error_path'))
        
        # ── 2. 캐시 확인 (mtime 검증 포함) ──────────────────────────────
        if self.use_cache:
            with self._cache_lock:
                if key in self._cache:
                    cached = self._cache[key]
                    try:
                        current_mtime = file_path.stat().st_mtime
                        cached_mtime  = cached.get("_mtime")          # ← 저장된 mtime
                        if cached_mtime is not None and current_mtime <= cached_mtime:
                            # 파일 미변경 → 캐시 히트
                            self._cache.move_to_end(key)
                            debug_print(f"캐시 히트: {file_path.name}")
                            result = cached.copy()
                            result.pop("_mtime", None)   
                            return result
                        else:
                            # mtime 변경 → 캐시 자동 무효화
                            del self._cache[key]
                            debug_print(
                                f"mtime 변경 → 캐시 무효화: {file_path.name} "
                                f"(저장={cached_mtime}, 현재={current_mtime})"
                            )
                    except OSError:
                        self._cache.move_to_end(key)
                        result = cached.copy()
                        result.pop("_mtime", None)
                        return result
        
        # ===== 3. 파일 존재 확인 (캐시 전) =====
        if not file_path.exists():
            error_print(f"파일 없음: {file_path}")
            return self._create_error_metadata(file_path, t('metadata.error_not_found'))
        
        # ===== 4. 파일 크기 확인 =====
        try:
            file_size = file_path.stat().st_size
            if file_size == 0:
                warning_print(f"빈 파일: {file_path.name}")
                return self._create_error_metadata(file_path, t('metadata.error_empty'))
        except OSError as e:
            error_print(f"파일 정보 읽기 실패: {e}")
            return self._create_error_metadata(file_path, t('metadata.error_read', error=e))
        
        # ===== 5. 메타데이터 읽기 =====
        metadata = self._read_metadata_unsafe(file_path)
        
        # ── 6. 캐시 저장 시 mtime 함께 기록 ─────────────────────────────
        if self.use_cache and 'file' in metadata and 'error' not in metadata['file']:
            with self._cache_lock:
                try:
                    metadata["_mtime"] = file_path.stat().st_mtime   # ← mtime 저장
                except OSError:
                    metadata["_mtime"] = None

                self._cache[key] = metadata
                self._cache.move_to_end(key)

                while len(self._cache) > self.max_cache_size:
                    oldest_key = next(iter(self._cache))
                    self._cache.pop(oldest_key)
                    debug_print(f"캐시 제거 (LRU): {Path(oldest_key).name}")

        return metadata
    

    def _create_error_metadata(
        self,
        file_path: Path,
        error_msg: str
    ) -> Dict[str, Any]:
        """
        오류 메타데이터 생성 (캐시하지 않음)
        """
        return {
            'file': {
                'filename': file_path.name,
                'error':    error_msg,
            },
            'camera': {},
            'exif': {},
            'gps': None
        }
    

    def _read_metadata_unsafe(self, file_path: Path) -> Dict[str, Any]:
        """
        실제 메타데이터 읽기 (캐시 락 없이)
        """
        metadata: Dict[str, Any] = {
            'file': {},
            'camera': {},
            'exif': {},
            'gps': None
        }
        
        # 파일 정보
        metadata['file'] = self._read_file_info(file_path)

        # HEIF: Image.open() 불가 → WinRT BitmapProperties로 EXIF 읽기
        if file_path.suffix.lower() in _HEIF_EXTS:
            if _WINRT_META_AVAILABLE:
                try:
                    _, _, cam, exf, gps = MetadataReader._run_winrt_sync(
                        MetadataReader._read_heif_metadata_async(file_path),
                        timeout=10.0,
                    )
                    metadata['camera'] = cam
                    metadata['exif']   = exf
                    metadata['gps']    = gps
                except Exception as e:
                    debug_print(f"HEIF 메타데이터 읽기 실패 ({file_path.name}): {e}")
            return metadata   # ← 아래 Image.open() 코드는 건너뜀

        # ===== EXIF 읽기 (타임아웃 추가) =====
        try:
            # PIL 이미지 열기 (타임아웃 없음 - 주의!)
            with Image.open(file_path) as img:
                # ===== 중요: load()를 조건부로만 호출 =====
                # RAW 파일이나 큰 파일은 스킵
                try:
                    # 파일 크기 제한 (50MB 이상은 EXIF만 읽기)
                    file_size = file_path.stat().st_size
                    if file_size < 50 * 1024 * 1024:  # 50MB
                        img.load()
                    else:
                        info_print(f"큰 파일 - EXIF만 읽기: {file_path.name} ({file_size / 1024 / 1024:.1f}MB)")
                except Exception as e:
                    warning_print(f"이미지 로드 스킵: {e}")
                
                exif_data = None
                
                # EXIF 읽기
                try:
                    exif = img.getexif()
                    if exif:
                        exif_data = dict(exif)
                        
                        # IFD 추가
                        try:
                            exif_ifd = exif.get_ifd(0x8769)
                            if exif_ifd:
                                exif_data.update(exif_ifd)
                        except:
                            pass
                        
                        try:
                            gps_ifd = exif.get_ifd(0x8825)
                            if gps_ifd:
                                exif_data[34853] = dict(gps_ifd)
                        except:
                            pass
                
                except Exception as e:
                    debug_print(f"EXIF 읽기 실패: {e}")
                
                # EXIF 파싱
                if exif_data:
                    metadata['camera'] = self._extract_camera_info(exif_data)
                    metadata['exif'] = self._extract_exif_info(exif_data)
                    metadata['gps'] = self._extract_gps_info(exif_data)

                # ===== RAW: PIL EXIF(274)가 비어있는 경우가 흔하므로 orientation 보강 =====
                if file_path.suffix.lower() in _RAW_EXTS:
                    # RAW는 항상 실제 방향 재탐색
                    # 이유: NEF처럼 EXIF tag 274=1(Normal)이지만 실제로는 회전이 필요한 경우가 있음
                    # (내장 썸네일 EXIF에 실제 orientation이 저장됨)
                    deg = self._get_raw_rotation_degrees(file_path)
                    if deg:
                        orient_map = {
                            90:  t('metadata.orient_rotate_90_cw'),
                            180: t('metadata.orient_rotate_180'),
                            270: t('metadata.orient_rotate_90_ccw'),
                        }
                        metadata['camera']['orientation'] = orient_map.get(deg, f"Rotate {deg}°")
                        debug_print(f"RAW orientation 보강: {file_path.name} = {deg}°")
                    elif 'orientation' not in metadata['camera']:
                        # 회전 없음(0°)이면 "정상" 표시 (기존 tag 274=1 값이 없는 경우에만)
                        metadata['camera']['orientation'] = t('metadata.orient_normal')

        except FileNotFoundError:
            metadata['file']['error'] = t('metadata.error_not_found')
        except PermissionError:
            metadata['file']['error'] = t('metadata.error_access')
        except Exception as e:
            warning_print(f"메타데이터 읽기 실패 {file_path.name}: {e}")
            metadata['file']['error'] = t('metadata.error_read', error=str(e)[:50])
        
        return metadata

# ============================================
# 파일 정보 추출
# ============================================

    def _read_file_info(self, file_path: Path) -> Dict[str, str]:
        """파일 기본 정보"""
        file_info: Dict[str, str] = {}
        
        try:
            stat = file_path.stat()
            
            # 파일명 줄바꿈 처리
            filename = file_path.name
            if len(filename) > 25:
                stem = file_path.stem
                ext = file_path.suffix
                
                wrapped_lines = []
                for i in range(0, len(stem), 25):
                    wrapped_lines.append(stem[i:i+25])

                if wrapped_lines:
                    wrapped_lines[-1] += ext
                else:
                    filename = ext

                filename = '\n'.join(wrapped_lines)
            
            file_info['filename'] = filename
            file_info['size']     = self._format_size(stat.st_size)
            file_info['modified'] = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            
            if file_path.suffix.lower() in _HEIF_EXTS:
                # HEIF: PIL이 열 수 없으므로 WinRT로 크기 읽기
                if _WINRT_META_AVAILABLE:
                    try:
                        async def _dims_only() -> Tuple[int, int]:
                            from winrt.windows.storage import StorageFile, FileAccessMode
                            from winrt.windows.graphics.imaging import BitmapDecoder
                            s  = await StorageFile.get_file_from_path_async(str(file_path.resolve()))
                            st = await s.open_async(FileAccessMode.READ)
                            d  = await BitmapDecoder.create_async(st)
                            return d.oriented_pixel_width, d.oriented_pixel_height

                        w, h = MetadataReader._run_winrt_sync(_dims_only())
                        file_info['resolution'] = f"{w} × {h}"
                    except Exception as e:
                        debug_print(f"이미지 정보 읽기 실패: {e}")
                        file_info['resolution'] = t('metadata.unknown')
                else:
                    file_info['resolution'] = t('metadata.unknown')
                file_info['format'] = file_path.suffix.upper().lstrip('.')
                file_info['mode']   = 'RGB'
            
            elif file_path.suffix.lower() in _RAW_EXTS:

                # RAW: rawpy로 실제 센서 해상도 읽기 (PIL은 임베디드 JPEG 크기 반환)
                try:
                    import rawpy
                    with rawpy.imread(str(file_path)) as raw:
                        s = raw.sizes
                        # iwidth/iheight = 마스크 제외 실제 출력 픽셀 수
                        w, h = s.iwidth, s.iheight
                        if w > 0 and h > 0:
                            file_info['resolution'] = f"{w} × {h}"
                            debug_print(f"RAW 해상도 (rawpy): {w}×{h}")
                        else:
                            file_info['resolution'] = t('metadata.unknown')
                except Exception as e:
                    debug_print(f"RAW 해상도 읽기 실패 (rawpy): {e}")
                    # fallback: PIL 프리뷰 크기라도 표시
                    try:
                        with Image.open(file_path) as img:
                            file_info['resolution'] = f"{img.width} × {img.height}"
                    except Exception:
                        file_info['resolution'] = t('metadata.unknown')
                file_info['format'] = file_path.suffix.upper().lstrip('.')
                file_info['mode']   = 'RGB'            
            
            else:
                try:
                    with Image.open(file_path) as img:
                        img.load()
                        file_info['resolution'] = f"{img.width} × {img.height}"
                        file_info['format']     = img.format or 'Unknown'
                        file_info['mode']       = img.mode
                        if hasattr(img, 'info') and 'icc_profile' in img.info:
                            file_info['color_profile'] = t('metadata.icc_profile')
                except Exception as e:
                    debug_print(f"이미지 정보 읽기 실패: {e}")
                    file_info['resolution'] = t('metadata.unknown')
                    file_info['format']     = t('metadata.format_fail')
                    if isinstance(e, PermissionError):
                        file_info['image_error'] = t('metadata.error_img_access')
                    elif isinstance(e, FileNotFoundError):
                        file_info['image_error'] = t('metadata.error_img_not_found')
                    else:
                        file_info['image_error'] = t('metadata.error_img_corrupted')
        
        except OSError as e:
            error_print(f"파일 정보 읽기 실패: {e}")
            file_info['filename'] = file_path.name
            file_info['error']    = t('metadata.error_info_read')
        
        return file_info

# ============================================
# EXIF 정보 추출 (카메라)
# ============================================

    def _extract_camera_info(self, exif_data: Dict[int, Any]) -> Dict[str, str]:
        """카메라 촬영 정보 추출"""
        camera_info: Dict[str, str] = {}
        
        # 촬영 날짜
        if 36867 in exif_data:  # DateTimeOriginal
            try:
                dt_str = self._safe_exif_value(exif_data[36867])
                dt = datetime.strptime(dt_str, '%Y:%m:%d %H:%M:%S')
                camera_info['date_taken'] = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                camera_info['date_taken'] = self._safe_exif_value(exif_data[36867])
        
        # 카메라 제조사
        if 271 in exif_data:  # Make
            camera_info['make'] = self._safe_exif_value(exif_data[271])
        
        # 카메라 모델
        if 272 in exif_data:  # Model
            camera_info['model'] = self._safe_exif_value(exif_data[272])
        
        # ISO
        if 34855 in exif_data:  # ISOSpeedRatings
            iso_value = exif_data[34855]
            if isinstance(iso_value, (tuple, list)):
                iso_value = iso_value[0] if iso_value else 0
            camera_info['iso'] = f"ISO {iso_value}"
        
        # ===== 노출 시간 (간소화) =====
        if 33434 in exif_data:  # ExposureTime
            exposure_value = self._rational_to_float(exif_data[33434])
            
            if 0 < exposure_value < 1.0:
                # 1초 미만: 분수 형태 (1/33s)
                shutter_speed = round(1 / exposure_value)
                camera_info['exposure_time'] = f"1/{shutter_speed}s"
            #elif exposure_value >= 1.0:
            elif exposure_value >= 1.0 and exposure_value != 0:    
                # 1초 이상
                if exposure_value == int(exposure_value):
                    camera_info['exposure_time'] = f"{int(exposure_value)}s"
                else:
                    camera_info['exposure_time'] = f"{exposure_value:.1f}s"
        
        # ===== F-Stop (간소화) =====
        if 33437 in exif_data:  # FNumber
            f_value = self._rational_to_float(exif_data[33437])
            if f_value > 0:
                camera_info['f_stop'] = f"f/{f_value:.1f}"
        
        # ===== 초점거리 (정리 및 간소화) =====
        # 35mm 환산 초점거리 우선
        if 41989 in exif_data:  # FocalLengthIn35mmFilm
            focal_35mm = exif_data[41989]
            if isinstance(focal_35mm, (int, float)) and focal_35mm > 0:
                camera_info['focal_length'] = f"{int(focal_35mm)}mm ({t('metadata.focal_35mm')})"
        elif 37386 in exif_data:  # FocalLength (fallback)
            focal_value = self._rational_to_float(exif_data[37386])
            if focal_value > 0:
                focal_rounded = round(focal_value)
                camera_info['focal_length'] = f"{focal_rounded}mm"
        
        # 플래시 모드
        if 37385 in exif_data:  # Flash
            flash_value = exif_data[37385]
            if isinstance(flash_value, int):
                flash_modes = {
                    0:  t('metadata.flash_none'),
                    1:  t('metadata.flash_fired'),
                    5:  t('metadata.flash_fired_no_ret'),
                    7:  t('metadata.flash_fired_ret'),
                    9:  t('metadata.flash_forced'),
                    13: t('metadata.flash_forced_no_ret'),
                    15: t('metadata.flash_forced_ret'),
                    16: t('metadata.flash_suppressed'),
                    24: t('metadata.flash_auto'),
                    25: t('metadata.flash_auto_fired'),
                }
                camera_info['flash'] = flash_modes.get(
                    flash_value, t('metadata.unknown_with_val', value=flash_value)
                )
        
        # 회전 값
        if 274 in exif_data:  # Orientation
            orientation_value = exif_data[274]
            if isinstance(orientation_value, int):
                orientations = {
                    1: t('metadata.orient_normal'),
                    2: t('metadata.orient_flip_h'),
                    3: t('metadata.orient_rotate_180'),
                    4: t('metadata.orient_flip_v'),
                    5: t('metadata.orient_flip_h_rotate_270'),
                    6: t('metadata.orient_rotate_90_cw'),
                    7: t('metadata.orient_flip_h_rotate_90'),
                    8: t('metadata.orient_rotate_90_ccw'),
                }
                camera_info['orientation'] = orientations.get(
                    orientation_value, t('metadata.unknown_with_val', value=orientation_value)
                )
        
        # ===== 렌즈 정보 =====
        if 42035 in exif_data:  # LensMake
            lens_make = self._safe_exif_value(exif_data[42035])
            if lens_make and lens_make != "알 수 없음" and lens_make.strip():
                camera_info['lens_make'] = lens_make.strip()
        
        # 렌즈 모델
        if 42036 in exif_data:  # LensModel
            lens_model = self._safe_exif_value(exif_data[42036])
            if lens_model and lens_model != "알 수 없음" and lens_model.strip():
                camera_info['lens_model'] = lens_model.strip()
        
        # LensModel이 없으면 LensSpecification 시도
        if 'lens_model' not in camera_info and 42034 in exif_data:  # LensSpecification
            try:
                lens_spec = exif_data[42034]
                if isinstance(lens_spec, (tuple, list)) and len(lens_spec) >= 4:
                    min_focal = self._rational_to_float(lens_spec[0])
                    max_focal = self._rational_to_float(lens_spec[1])
                    min_aperture = self._rational_to_float(lens_spec[2])
                    max_aperture = self._rational_to_float(lens_spec[3])
                    
                    if min_focal > 0:
                        if min_focal == max_focal:
                            lens_info = f"{int(min_focal)}mm"
                        else:
                            lens_info = f"{int(min_focal)}-{int(max_focal)}mm"
                        
                        if min_aperture > 0:
                            if min_aperture == max_aperture:
                                lens_info += f" f/{min_aperture:.1f}"
                            else:
                                lens_info += f" f/{min_aperture:.1f}-{max_aperture:.1f}"
                        
                        camera_info['lens_model'] = lens_info
            except Exception as e:
                    debug_print(f"LensSpecification 파싱 실패: {e}")
        
        return camera_info


    def _extract_exif_info(self, exif_data: Dict[int, Any]) -> Dict[str, str]:
        """기타 EXIF 정보"""
        exif_info: Dict[str, str] = {}
        
        # 편집 프로그램
        if 305 in exif_data:  # Software
            software = self._safe_exif_value(exif_data[305])
            if software and software != t('metadata.unknown'):
                exif_info['software'] = software
        
        # 화이트 밸런스
        if 41987 in exif_data:  # WhiteBalance
            wb_value = exif_data[41987]
            if isinstance(wb_value, int):
                wb_modes = {0: t('metadata.wb_auto'), 1: t('metadata.wb_manual')}
                exif_info['white_balance'] = wb_modes.get(wb_value, str(wb_value))
        
        # 측광 모드
        if 37383 in exif_data:  # MeteringMode
            metering_value = exif_data[37383]
            if isinstance(metering_value, int):
                metering_modes = {
                    0: t('metadata.metering_unknown'), 1: t('metadata.metering_avg'),
                    2: t('metadata.metering_center'),  3: t('metadata.metering_spot'),
                    4: t('metadata.metering_multi'),   5: t('metadata.metering_pattern'),
                    6: t('metadata.metering_partial'),
                }
                exif_info['metering_mode'] = metering_modes.get(metering_value, str(metering_value))
        
        # 노출 프로그램
        if 34850 in exif_data:  # ExposureProgram
            program_value = exif_data[34850]
            if isinstance(program_value, int):
                programs = {
                    0: t('metadata.prog_undefined'), 1: t('metadata.prog_manual'),
                    2: t('metadata.prog_normal'),    3: t('metadata.prog_aperture'),
                    4: t('metadata.prog_shutter'),   5: t('metadata.prog_creative'),
                    6: t('metadata.prog_action'),    7: t('metadata.prog_portrait'),
                    8: t('metadata.prog_landscape'),
                }
                exif_info['exposure_program'] = programs.get(program_value, str(program_value))
        
        return exif_info

# ============================================
# GPS 정보 추출
# ============================================

    def _extract_gps_info(self, exif_data: Dict[int, Any]) -> Optional[Dict[str, Any]]:
        """GPS 정보 추출"""
        if 34853 not in exif_data:
            return None
        
        gps_data = exif_data[34853]
        
        # GPS 데이터 타입 확인
        if not isinstance(gps_data, dict) or not gps_data:
            debug_print(f"GPS 데이터가 dict가 아니거나 비어있음")
            return None
        
        try:
            # 위도 파싱
            if 2 not in gps_data or 1 not in gps_data:
                debug_print(f"GPS 위도 데이터 없음")
                return None
            
            lat_ref = gps_data[1]
            lat_data = gps_data[2]
            lat = self._convert_to_degrees(lat_data)
            
            # lat_ref 처리 (bytes → str)
            if isinstance(lat_ref, bytes):
                lat_ref = lat_ref.decode('utf-8', errors='ignore')
            elif not isinstance(lat_ref, str):
                lat_ref = str(lat_ref)
            
            if lat_ref.upper() == 'S':
                lat = -lat
            
            # 경도 파싱
            if 4 not in gps_data or 3 not in gps_data:
                debug_print(f"GPS 경도 데이터 없음")
                return None
            
            lon_ref = gps_data[3]
            lon_data = gps_data[4]
            lon = self._convert_to_degrees(lon_data)
            
            # lon_ref 처리
            if isinstance(lon_ref, bytes):
                lon_ref = lon_ref.decode('utf-8', errors='ignore')
            elif not isinstance(lon_ref, str):
                lon_ref = str(lon_ref)
            
            if lon_ref.upper() == 'W':
                lon = -lon
            
            # GPS 좌표 검증
            if lat == 0.0 and lon == 0.0:
                debug_print(f"GPS 좌표가 (0, 0) - Null Island")
                return None
            
            if not (-90 <= lat <= 90):
                warning_print(f"위도 범위 초과: {lat}")
                return None
            
            if not (-180 <= lon <= 180):
                warning_print(f"경도 범위 초과: {lon}")
                return None
            
            # 결과 생성
            result = {
                'latitude': lat,
                'longitude': lon,
                'display': f"{abs(lat):.6f}° {'N' if lat >= 0 else 'S'}, {abs(lon):.6f}° {'E' if lon >= 0 else 'W'}"
            }
            
            # 고도 정보 (간소화)
            if 6 in gps_data:
                try:
                    altitude_raw = gps_data[6]
                    altitude_value = self._rational_to_float(altitude_raw)
                    
                    # 고도 기준 (0=해수면 위, 1=해수면 아래)
                    altitude_ref = gps_data.get(5, 0)
                    if isinstance(altitude_ref, bytes):
                        altitude_ref = int.from_bytes(altitude_ref, byteorder='big')
                    elif not isinstance(altitude_ref, int):
                        altitude_ref = int(altitude_ref) if str(altitude_ref).isdigit() else 0
                    
                    if altitude_ref == 1:
                        altitude_value = -altitude_value
                    
                    # 포맷팅
                    if abs(altitude_value) < 1:
                        result['altitude'] = f"{altitude_value:.1f}m"
                    else:
                        result['altitude'] = f"{int(round(altitude_value))}m"
                        #debug_print(f"GPS 고도: {result['altitude']}")
                
                except Exception as e:
                        error_print(f"고도 정보 파싱 실패: {e}")
            
            return result
        
        except Exception as e:
                error_print(f"GPS 정보 파싱 실패: {e}")
                return None


    def _convert_to_degrees(self, value: Any) -> float:
        """GPS 좌표를 도(degree)로 변환"""
        try:
            if not value:
                return 0.0
            
            if isinstance(value, (tuple, list)) and len(value) >= 3:
                d_part = value[0]
                m_part = value[1]
                s_part = value[2]
                
                d = self._rational_to_float(d_part)
                m = self._rational_to_float(m_part)
                s = self._rational_to_float(s_part)
                
                result = d + (m / 60.0) + (s / 3600.0)
                return result
            
            return 0.0
            
        except Exception as e:
            error_print(f"GPS 좌표 변환 실패: {e}")
            return 0.0
    
# ============================================
# 타입 변환 유틸리티
# ============================================

    def _safe_exif_value(self, value: Any) -> str:
        """EXIF 값을 안전하게 문자열로 변환"""
        try:
            if isinstance(value, IFDRational):
                # IFDRational 타입
                if value.denominator == 0:
                    return "0"
                return str(float(value.numerator) / float(value.denominator))
            elif isinstance(value, tuple) and len(value) == 2:
                # Rational 타입 (분수)
                if value[1] == 0:
                    return "0"
                return str(float(value[0]) / float(value[1]))
            elif isinstance(value, bytes):
                # 바이트 문자열
                try:
                    return value.decode('utf-8', errors='ignore').strip()
                except Exception:
                    return str(value)
            else:
                return str(value)
        except Exception as e:
            warning_print(f"EXIF 값 변환 실패: {e}")
            return t('metadata.unknown')


    def _rational_to_float(self, value: Any) -> float:
        """Rational 타입을 float로 변환"""
        try:
            if isinstance(value, IFDRational):
                if value.denominator == 0:
                    return 0.0
                return float(value.numerator) / float(value.denominator)
            elif isinstance(value, (int, float)):
                return float(value)
            elif isinstance(value, (tuple, list)) and len(value) == 2:
                numerator = value[0]
                denominator = value[1]
                if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
                    return 0.0
                if denominator == 0:
                    return 0.0
                return float(numerator) / float(denominator)
            
            # 기타 타입은 float 변환 시도
            try:
                return float(value)  # type: ignore
            except (TypeError, ValueError):
                return 0.0
                
        except Exception as e:
            warning_print(f"Rational 변환 실패: {value}, {e}")
            return 0.0


    def _format_size(self, size: int) -> str:
        """파일 크기 포맷"""
        size_float = float(size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_float < 1024.0:
                return f"{size_float:.2f} {unit}"
            size_float /= 1024.0
        return f"{size_float:.2f} TB"

# ============================================
# 캐시 관리
# ============================================

    def invalidate_all(self) -> None:
        """전체 캐시 무효화 (clear_cache의 별칭)"""
        self.clear_cache()


    def clear_cache(self) -> None:
        """캐시 삭제 - 스레드 안전"""
        with self._cache_lock:
            count = len(self._cache)
            self._cache.clear()
            info_print(f"메타데이터 캐시 삭제: {count}개")
    

    def invalidate(self, file_path: Path) -> None:
        """특정 파일 캐시 무효화 - 스레드 안전"""
        try:
            key = str(file_path.absolute()).lower()
            with self._cache_lock:
                if key in self._cache:
                    del self._cache[key]
                    debug_print(f"캐시 무효화: {file_path.name}")
        except Exception as e:
            error_print(f"캐시 무효화 실패: {e}")
    

    def get_cache_size(self) -> int:
        """캐시 크기 반환 - 스레드 안전"""
        with self._cache_lock:
            return len(self._cache)


    def get_cache_memory_estimate(self) -> str:
        """캐시 메모리 사용량 추정"""
        import sys
        import pickle
        total_bytes = 0
        
        for metadata in self._cache.values():
            total_bytes += len(pickle.dumps(metadata))
        
        return self._format_size(total_bytes)


    def get_cache_stats(self) -> Dict[str, Any]:
        """캐시 통계"""
        return {
            'size': len(self._cache),
            'memory_estimate': self.get_cache_memory_estimate(),
            'enabled': self.use_cache,
        }


    def get_from_cache(self, filepath: Path) -> Optional[Dict]:
        try:
            key = str(filepath.absolute()).lower()
        except Exception:
            return None
        with self._cache_lock:
            if key in self._cache:
                cached = self._cache[key]
                try:
                    current_mtime = filepath.stat().st_mtime
                    if cached.get("_mtime") is not None and current_mtime > cached["_mtime"]:
                        del self._cache[key]   # mtime 변경 → 무효화
                        return None
                except OSError:
                    pass
                self._cache.move_to_end(key)
                result = cached.copy()
                result.pop("_mtime", None)
                return result
        return None

    # ============================================
    # WinRT 유틸리티 (HEIF 전용)
    # ============================================

    @staticmethod
    def _run_winrt_sync(coro: Any, timeout: float = 10.0) -> Any:
        """WinRT 코루틴 동기 블로킹 실행"""
        result: list = [None]
        exc:    list = [None]

        def worker() -> None:
            loop = asyncio.new_event_loop()
            try:
                result[0] = loop.run_until_complete(coro)
            except Exception as e:
                exc[0] = e
            finally:
                loop.close()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout=timeout)

        if t.is_alive():
            raise TimeoutError(f"WinRT 타임아웃 ({timeout}s)")
        if exc[0] is not None:
            raise exc[0]
        return result[0]


    @staticmethod
    async def _read_heif_metadata_async(
        file_path: Path,
    ) -> Tuple[int, int, dict, dict, Any]:
        from winrt.windows.storage import StorageFile, FileAccessMode
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.foundation import IPropertyValue

        abs_path = str(file_path.resolve())
        storage  = await StorageFile.get_file_from_path_async(abs_path)
        stream   = await storage.open_async(FileAccessMode.READ)
        decoder  = await BitmapDecoder.create_async(stream)

        w = decoder.oriented_pixel_width
        h = decoder.oriented_pixel_height

        camera: dict = {}
        exif:   dict = {}
        gps           = None

        try:
            props = decoder.bitmap_properties

            requested = [
                "System.Photo.CameraManufacturer",
                "System.Photo.CameraModel",
                "System.Photo.ISOSpeed",
                "System.Photo.FNumber",
                "System.Photo.ExposureTime",
                "System.Photo.FocalLength",
                "System.Photo.FocalLength35mm",
                "System.Photo.DateTaken",
                "System.GPS.Latitude",
                "System.GPS.LatitudeRef",
                "System.GPS.Longitude",
                "System.GPS.LongitudeRef",
                "System.GPS.Altitude",
                "System.GPS.AltitudeRef",
                "System.Photo.FlashFired",
                "System.Photo.WhiteBalance",
                "System.Photo.MeteringMode",
                "System.Photo.ProgramMode",
                "System.Photo.LensManufacturer",
                "System.Photo.LensModel",
            ]

            prop_set = await props.get_properties_async(requested)

            # ──────────────────────────────────────────────────────
            # WinRT IPropertyValue 언박싱
            # PropertyType 값 (Windows.Foundation.PropertyType):
            #   UInt8=1  Int16=2  UInt16=3  Int32=4  UInt32=5
            #   Int64=6  UInt64=7  Single=8  Double=9
            #   Boolean=11  String=12  DateTime=14
            #   Array = base + 1024  (DoubleArray=1033)
            # ──────────────────────────────────────────────────────
            def safe_get(name: str) -> Any:
                try:
                    tv = prop_set.lookup(name)
                    if tv is None:
                        return None
                    raw = tv.value
                    if raw is None:
                        return None

                    pv = IPropertyValue._from(raw)  # type: ignore[attr-defined]
                    pt_val = int(pv.type)

                    if   pt_val == 12:   return pv.get_string()
                    elif pt_val == 11:   return pv.get_boolean()
                    elif pt_val ==  1:   return pv.get_uint8()
                    elif pt_val ==  2:   return pv.get_int16()
                    elif pt_val ==  3:   return pv.get_uint16()
                    elif pt_val ==  4:   return pv.get_int32()
                    elif pt_val ==  5:   return pv.get_uint32()
                    elif pt_val ==  6:   return pv.get_int64()
                    elif pt_val ==  7:   return pv.get_uint64()
                    elif pt_val ==  8:   return float(pv.get_single())
                    elif pt_val ==  9:   return float(pv.get_double())
                    elif pt_val == 14:   return pv.get_date_time()
                    elif pt_val == 1033: return list(pv.get_double_array())  # GPS DMS 배열
                    else:
                        debug_print(f"WinRT PropertyType {pt_val} 미지원: {name}")
                        return None
                except Exception as e:
                    return None

            # ── 카메라 정보 ──────────────────────────────────────
            make = safe_get("System.Photo.CameraManufacturer")
            if make:   camera['make']  = str(make).strip()

            model = safe_get("System.Photo.CameraModel")
            if model:  camera['model'] = str(model).strip()

            iso = safe_get("System.Photo.ISOSpeed")
            if iso is not None: camera['iso'] = f"ISO {iso}"

            fnumber = safe_get("System.Photo.FNumber")
            if fnumber is not None and float(fnumber) > 0:
                camera['f_stop'] = f"f/{float(fnumber):.1f}"

            exposure = safe_get("System.Photo.ExposureTime")
            if exposure is not None:
                exp_f = float(exposure)
                if 0 < exp_f < 1.0:
                    camera['exposure_time'] = f"1/{round(1 / exp_f)}s"
                elif exp_f >= 1.0:
                    camera['exposure_time'] = f"{int(exp_f)}s" if exp_f == int(exp_f) else f"{exp_f:.1f}s"

            focal35 = safe_get("System.Photo.FocalLength35mm")
            if focal35 and int(focal35) > 0:
                camera['focal_length']  = f"{int(focal35)}mm ({t('metadata.focal_35mm')})"
            else:
                focal = safe_get("System.Photo.FocalLength")
                if focal and float(focal) > 0:
                    camera['focal_length']  = f"{round(float(focal))}mm"

            # 촬영일시: WinRT DateTime 또는 Python datetime 양쪽 처리
            date_taken = safe_get("System.Photo.DateTaken")
            if date_taken is not None:
                try:
                    if hasattr(date_taken, 'strftime'):
                        camera['date_taken'] = date_taken.strftime('%Y-%m-%d %H:%M:%S')
                    elif hasattr(date_taken, 'universal_time'):
                        import datetime as _dt
                        # universal_time: 1601-01-01 기준 100ns 단위
                        ts = (date_taken.universal_time - 116444736000000000) / 10_000_000
                        camera['date_taken'] = _dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        camera['date_taken'] = str(date_taken)
                except Exception:
                    pass

            lens_make = safe_get("System.Photo.LensManufacturer")
            if lens_make: camera['lens_make'] = str(lens_make).strip()

            lens_model = safe_get("System.Photo.LensModel")
            if lens_model: camera['lens_model'] = str(lens_model).strip()

            flash_fired = safe_get("System.Photo.FlashFired")
            if flash_fired is not None:
                camera['flash'] = t('metadata.flash_fired') if flash_fired else t('metadata.flash_none')

            # ── EXIF 정보 ──────────────────────────────────────
            wb = safe_get("System.Photo.WhiteBalance")
            if wb is not None:
                exif['white_balance']    = {0: t('metadata.wb_auto'), 1: t('metadata.wb_manual')}.get(int(wb), str(wb))

            metering = safe_get("System.Photo.MeteringMode")
            if metering is not None:
                modes = {0: t('metadata.metering_unknown'), 1: t('metadata.metering_avg'),
                        2: t('metadata.metering_center'),  3: t('metadata.metering_spot'),
                        4: t('metadata.metering_multi'),   5: t('metadata.metering_pattern'),
                        6: t('metadata.metering_partial')}
                exif['metering_mode']    = modes.get(int(metering), str(metering))

            program = safe_get("System.Photo.ProgramMode")
            if program is not None:
                programs = {0: t('metadata.prog_undefined'), 1: t('metadata.prog_manual'),
                            2: t('metadata.prog_normal'),    3: t('metadata.prog_aperture'),
                            4: t('metadata.prog_shutter'),   5: t('metadata.prog_creative'),
                            6: t('metadata.prog_action'),    7: t('metadata.prog_portrait'),
                            8: t('metadata.prog_landscape')}
                exif['exposure_program'] = programs.get(int(program), str(program))

            # ── GPS 정보 ──────────────────────────────────────
            # DoubleArray [도, 분, 초] 형태로 반환됨
            lat_arr = safe_get("System.GPS.Latitude")
            lat_ref = safe_get("System.GPS.LatitudeRef")
            lon_arr = safe_get("System.GPS.Longitude")
            lon_ref = safe_get("System.GPS.LongitudeRef")

            if isinstance(lat_arr, list) and isinstance(lon_arr, list):
                try:
                    def dms_to_decimal(arr: list) -> float:
                        if len(arr) >= 3:
                            return arr[0] + arr[1] / 60.0 + arr[2] / 3600.0
                        return float(arr[0])

                    lat_f = dms_to_decimal(lat_arr)
                    lon_f = dms_to_decimal(lon_arr)

                    ref = (lat_ref or '').upper()
                    if ref == 'S': lat_f = -lat_f

                    ref = (lon_ref or '').upper()
                    if ref == 'W': lon_f = -lon_f

                    if not (lat_f == 0.0 and lon_f == 0.0):
                        gps = {
                            'latitude':  lat_f,
                            'longitude': lon_f,
                            'display': (
                                f"{abs(lat_f):.6f}° {'N' if lat_f >= 0 else 'S'}, "
                                f"{abs(lon_f):.6f}° {'E' if lon_f >= 0 else 'W'}"
                            ),
                        }
                        alt = safe_get("System.GPS.Altitude")
                        if alt is not None:
                            alt_ref = safe_get("System.GPS.AltitudeRef")
                            alt_f   = float(alt)
                            if alt_ref == 1: alt_f = -alt_f
                            gps['altitude'] = f"{int(round(alt_f))}m"

                except Exception as e:
                    debug_print(f"HEIF GPS 파싱 실패: {e}")

        except Exception as e:
            debug_print(f"HEIF BitmapProperties 실패 ({file_path.name}): {e}")

        return w, h, camera, exif, gps
    
    