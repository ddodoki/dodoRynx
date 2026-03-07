# -*- coding: utf-8 -*-
# core/rotation_manager.py

from __future__ import annotations

import io
import os
import shutil
import tempfile
import traceback
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Optional
from xml.etree.ElementTree import Element, SubElement

from PIL import Image
from PIL.Image import Exif
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QTransform

from utils.debug import debug_print, error_print, info_print, warning_print


RAW_EXTS = {'.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf',
            '.rw2', '.pef', '.srw', '.raf'}
JPEG_EXTS = {'.jpg', '.jpeg'}

EXIF_ORIENTATION_TAG = 274  # Orientation

ORIENT_TO_DEG = {
    1: 0,
    3: 180,
    6: 90,
    8: 270,
}

DEG_TO_ORIENT = {
    0: 1,      # 정상
    90: 6,     # 시계방향 90도
    180: 3,    # 180도
    270: 8,    # 시계방향 270도
}

# EXIF Orientation 1-8 → CW 90° 1회 전환 테이블
_ORIENT_CW: dict[int, int] = {
    1: 6,  # normal        → CW90
    2: 7,  # H-flip        → H-flip+CW90 (transverse)
    3: 8,  # 180°          → CCW90
    4: 5,  # V-flip        → transpose
    5: 2,  # transpose     → H-flip
    6: 3,  # CW90          → 180°
    7: 4,  # transverse    → V-flip
    8: 1,  # CCW90         → normal
}

XMP_NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XMP_NS_CRS = "http://ns.adobe.com/camera-raw-settings/1.0/"


# ============================================
# 모듈 수준 유틸리티
# ============================================

def _raw_flip_to_degrees(flip: int) -> int:
    """LibRaw sizes.flip → QTransform.rotate() 각도 (image_loader.py와 동일)"""
    return {0: 0, 3: 180, 5: 270, 6: 90}.get(int(flip), 0)


def get_raw_sidecar_rotation(raw_path: Path) -> Optional[int]:    
    """
    RAW 파일 옆의 XMP 사이드카에 저장된 회전 각도를 읽는다.
    없으면 0 반환.
    """
    xmp_path = raw_path.with_suffix(raw_path.suffix + ".xmp")
    info_print(f"[XMP] check: {xmp_path}")
    if not xmp_path.exists():
        debug_print(f"XMP 파일 없음: {xmp_path}")
        return None

    try:
        tree = ET.parse(xmp_path)
        root = tree.getroot()

        # 네임스페이스 무시하고 모든 Description 찾기
        for desc in root.iter():
            if desc.tag.endswith('Description'):
                # 모든 속성에서 'Rotation' 찾기
                for key, val in desc.attrib.items():
                    if 'Rotation' in key:
                        angle = int(val) % 360
                        if angle in (0, 90, 180, 270):
                            info_print(f"XMP 회전 읽음: {xmp_path.name} = {angle}°")
                            return angle
                        else:
                            warning_print(f"XMP 회전 각도 비정상: {angle}")
                            return angle

        debug_print(f"XMP에 회전 정보 없음: {xmp_path.name}")
        return None

    except Exception as e:
        warning_print(f"XMP 회전 읽기 실패: {xmp_path.name} {e}")
        return None


def _get_raw_initial_angle(file_path: Path) -> int:
    _orient_map = {1: 0, 3: 180, 6: 90, 8: 270}

    try:
        import rawpy
        with rawpy.imread(str(file_path)) as raw:
            # 1단계: rawpy flip
            flip_angle = _raw_flip_to_degrees(raw.sizes.flip)
            if flip_angle != 0:
                debug_print(f"RAW initial_angle (flip): {file_path.name} {flip_angle}°")
                return flip_angle

            # 2단계: EXIF tag 274
            try:
                import piexif
                exif_dict = piexif.load(str(file_path))
                ori = exif_dict.get("0th", {}).get(piexif.ImageIFD.Orientation, 1)
                deg = _orient_map.get(int(ori), 0)
                if deg:
                    debug_print(f"RAW initial_angle (EXIF): {file_path.name} {deg}°")
                    return deg
            except Exception:
                pass

            # 3단계: 내장 JPEG 썸네일 (Nikon NEF 등)
            # postprocess() 호출 없으므로 extract_thumb() 호출 가능
            try:
                from PIL import Image as PILImage
                thumb = raw.extract_thumb()
                # str 비교로 ThumbFormat Pylance 오류 회피
                if str(thumb.format).upper().endswith('JPEG'):
                    with PILImage.open(io.BytesIO(thumb.data)) as timg:
                        exif = timg.getexif()
                        ori  = exif.get(274, 1)
                        deg  = _orient_map.get(int(ori), 0)
                        if deg:
                            debug_print(
                                f"RAW initial_angle (썸네일): "
                                f"{file_path.name} {deg}°"
                            )
                            return deg
            except Exception as e:
                debug_print(f"썸네일 orientation 읽기 실패: {e}")

    except Exception as e:
        warning_print(f"RAW initial_angle 읽기 실패 ({file_path.name}): {e}")

    return 0


# ============================================
# RotationState(dataclass)
# ============================================

@dataclass
class RotationState:
    file_path: Path
    original_orientation: int
    initial_angle: int = 0        
    current_angle: int = 0
    preview_angle: int = 0
    has_animation: bool = False
    preview_pixmap: Optional[QPixmap] = None


# ============================================
# RotationManager
# ============================================

class RotationManager:
    """단일 이미지 회전 상태 + 저장 책임"""

    # ── 상태 관리 ────────────────────────────────

    def __init__(self) -> None:
        self._state: Optional[RotationState] = None
        self._lock = RLock() 


    def set_current_file(
        self,
        file_path: Path,
        is_animated: bool,
    ) -> None:
        """현재 선택 파일 변경 시 호출 (이미지 변경마다 리셋)."""
        ext = file_path.suffix.lower()
        orientation = 1
        initial_angle = 0  # 초기 회전 각도
        
        if ext in JPEG_EXTS:
            orientation = self._read_exif_orientation(file_path)
            # JPEG도 현재 EXIF orientation을 각도로 변환하여 저장
            initial_angle = ORIENT_TO_DEG.get(orientation, 0)
            debug_print(f"JPEG 파일 초기 회전 각도: {initial_angle}° (EXIF={orientation})")

        elif ext in RAW_EXTS:
            xmp_angle = get_raw_sidecar_rotation(file_path)
            if xmp_angle is not None:
                initial_angle = xmp_angle
            else:
                initial_angle = _get_raw_initial_angle(file_path)  

        self._state = RotationState(
            file_path=file_path,
            original_orientation=orientation,
            initial_angle=initial_angle,   
            current_angle=initial_angle,
            preview_angle=0,
            has_animation=is_animated,
        )
        debug_print(f"RotationManager: set_current_file {file_path.name}, "
                    f"ori={orientation}, initial_angle={initial_angle}, animated={is_animated}")


    def get_state(self) -> Optional[RotationState]:
        return self._state

    # ── 회전 조작 ────────────────────────────────

    def rotate_left(self) -> None:
        with self._lock:            
            if not self._state or self._state.has_animation:
                return
            self._state.current_angle  = (self._state.current_angle - 90) % 360
            self._state.preview_pixmap = None
            debug_print(f"rotate_left → {self._state.current_angle}")


    def rotate_right(self) -> None:
        """우측 90도 - 스레드 안전"""
        with self._lock:
            if not self._state or self._state.has_animation:
                return
            self._state.current_angle = (self._state.current_angle + 90) % 360
            self._state.preview_pixmap = None
            debug_print(f"rotate_right → {self._state.current_angle}")


    def reset(self) -> None:
        """원본 방향으로 리셋."""
        if not self._state:
            return
        # initial_angle 사용 — 파일 재읽기 불필요, apply() 이후에도 올바른 기준점
        self._state.current_angle  = self._state.initial_angle
        self._state.preview_angle  = 0
        self._state.preview_pixmap = None
        debug_print(f"rotation reset to {self._state.current_angle}")
                
    # ── 저장 (apply) ─────────────────────────────

    def apply(self) -> bool:
        """
        현재 상태를 원본 파일에 저장.
        True = 성공, False = 실패/변경 없음
        """
        if not self._state:
            return False
        st = self._state
        if st.has_animation:
            warning_print("애니메이션 이미지: 회전 저장 불가")
            return False
        
        angle = st.current_angle % 360
        ext = st.file_path.suffix.lower()
        
        # 원본과 비교하여 변경 여부 확인
        original_angle = st.initial_angle   # set_current_file() 시점의 기준값 (None 없음)

        if angle == original_angle:
            debug_print(f"회전 각도 변경 없음: {angle}° (원본: {original_angle}°)")
            return False

        debug_print(f"회전 적용: {original_angle}° → {angle}°")

        try:
            if ext in JPEG_EXTS:
                info_print("JPEG: EXIF Orientation만 변경")
                return self._apply_jpeg_exif(st, angle)
            elif ext in RAW_EXTS:
                info_print("RAW: XMP 사이드카 생성/갱신")
                return self._apply_raw_xmp(st, angle)
            else:
                info_print("기타 포맷: 픽셀 회전 후 덮어쓰기")
                return self._apply_pixel_rotation(st, angle)
        except Exception as e:
            error_print(f"회전 적용 실패: {e}")
            return False


    def _apply_jpeg_exif(self, st: RotationState, angle: int) -> bool:
        import piexif

        original_angle = ORIENT_TO_DEG.get(st.original_orientation, 0)
        delta_steps = ((angle - original_angle) % 360) // 90

        new_orient = st.original_orientation
        for _ in range(delta_steps):
            new_orient = _ORIENT_CW.get(new_orient, new_orient)

        info_print(
            f"{st.file_path.name} EXIF Orientation "
            f"{st.original_orientation} → {new_orient} "
            f"(delta={delta_steps}×CW, angle={angle}°)"
        )

        # 픽셀 재인코딩 없이 EXIF 바이트만 교체
        exif_dict = piexif.load(str(st.file_path))
        exif_dict["0th"][piexif.ImageIFD.Orientation] = new_orient
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, str(st.file_path))

        return True


    def _apply_raw_xmp(self, st: RotationState, angle: int) -> bool:
        """RAW: XMP 사이드카 안전하게 저장"""
        raw_path = st.file_path
        xmp_path = raw_path.with_suffix(raw_path.suffix + ".xmp")
        
        # ===== 1. 임시 파일에 저장 =====
        temp_fd, temp_xmp = tempfile.mkstemp(
            suffix='.xmp',
            dir=raw_path.parent
        )
        
        try:
            os.close(temp_fd)
            
            # ===== 2. XMP 데이터 생성 =====
            ET.register_namespace('x', 'adobe:ns:meta/')
            ET.register_namespace('rdf', 'http://www.w3.org/1999/02/22-rdf-syntax-ns#')
            ET.register_namespace('crs', 'http://ns.adobe.com/camera-raw-settings/1.0/')
            
            if xmp_path.exists():
                try:
                    tree = ET.parse(xmp_path)
                    root = tree.getroot()
                except Exception as e:
                    warning_print(f"XMP 파싱 실패, 새로 생성: {e}")
                    root = self._create_empty_xmp_root()
                    tree = ET.ElementTree(root)
            else:
                root = self._create_empty_xmp_root()
                tree = ET.ElementTree(root)
            
            # RDF/Description 처리
            rdf_ns = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
            crs_ns = "{http://ns.adobe.com/camera-raw-settings/1.0/}"
            
            rdf = root.find(f".//{rdf_ns}RDF")
            if rdf is None:
                rdf = SubElement(root, f"{rdf_ns}RDF")
            
            desc = rdf.find(f"{rdf_ns}Description")
            if desc is None:
                desc = SubElement(rdf, f"{rdf_ns}Description")
                desc.set(f"{rdf_ns}about", "")
            
            desc.set(f"{crs_ns}Rotation", str(angle))
            
            # ===== 3. 임시 파일에 저장 =====
            tree.write(temp_xmp, encoding="utf-8", xml_declaration=True, method="xml")
            
            # ===== 4. 검증 =====
            temp_xmp_path = Path(temp_xmp)
            if not temp_xmp_path.exists() or temp_xmp_path.stat().st_size == 0:
                error_print("XMP 임시 파일 생성 실패")
                return False
            
            # ===== 5. 원본 교체 =====
            if xmp_path.exists():
                backup_xmp = xmp_path.with_suffix(xmp_path.suffix + '.bak')
                if backup_xmp.exists():
                    backup_xmp.unlink()
                shutil.move(str(xmp_path), str(backup_xmp))
            
            shutil.move(temp_xmp, str(xmp_path))
            
            if xmp_path.exists():
                # 백업 삭제
                backup_xmp = xmp_path.with_suffix(xmp_path.suffix + '.bak')
                if backup_xmp.exists():
                    backup_xmp.unlink()
            
            info_print(f"✅ XMP 회전 저장 완료: {xmp_path.name}, angle={angle}")
            return True
        
        except Exception as e:
            error_print(f"XMP 저장 실패: {e}")
            traceback.print_exc()
            # 백업이 있고 xmp_path가 없으면 복원
            backup_xmp = xmp_path.with_suffix(xmp_path.suffix + '.bak')
            if backup_xmp.exists() and not xmp_path.exists():
                try:
                    shutil.move(str(backup_xmp), str(xmp_path))
                    warning_print(f"XMP 백업 복원 완료: {xmp_path.name}")
                except Exception as restore_e:
                    error_print(f"XMP 백업 복원도 실패: {restore_e}")
            return False
        
        finally:
            try:
                temp_file = Path(temp_xmp)
                if temp_file.exists():
                    temp_file.unlink()
            except Exception as e:
                warning_print(f"임시 파일 삭제 실패: {e}")


    def _create_empty_xmp_root(self):
        """Adobe 표준 XMP 구조 생성"""
        rdf_ns = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
        
        # 루트 요소
        xmp = Element("{adobe:ns:meta/}xmpmeta")
        xmp.set("{adobe:ns:meta/}xmptk", "Python-XMP")
        
        # RDF 요소
        rdf = SubElement(xmp, f"{rdf_ns}RDF")
        
        # Description 요소 (rdf:about 필수)
        desc = SubElement(rdf, f"{rdf_ns}Description")
        desc.set(f"{rdf_ns}about", "")
        
        return xmp
    

    def _apply_pixel_rotation(self, st: RotationState, angle: int) -> bool:
        file_path = st.file_path
        with Image.open(file_path) as img:
            # EXIF 적용된 상태로 로드하는 것이 좋으면 transpose 사용 가능
            # 여기서는 그냥 rotate 사용 (expand=True)
            rotated = img.rotate(-angle, expand=True)  # 화면 기준과 맞추려면 부호 조정
            rotated.save(file_path)
            info_print(f"{file_path.name} 픽셀 회전 저장: {angle}°")
        return True

    # ── 미리보기 ─────────────────────────────────

    def get_preview_pixmap(self, base_pixmap: QPixmap) -> QPixmap:
        if base_pixmap.isNull() or not self._state:
            return base_pixmap

        st = self._state

        # initial_angle 직접 사용 — 파일 재읽기 없음, None 위험 없음
        original_rotation = st.initial_angle

        total_rotation = (st.current_angle - original_rotation) % 360

        delta = total_rotation - st.preview_angle
        if delta > 180:
            delta -= 360
        elif delta < -180:
            delta += 360

        incremental = delta

        debug_print(f"미리보기 회전: original={original_rotation}, "
                    f"current={st.current_angle}, "
                    f"preview_was={st.preview_angle}, "
                    f"incremental={incremental}")

        if incremental == 0:
            return base_pixmap if st.preview_pixmap is None else st.preview_pixmap

        source = st.preview_pixmap if st.preview_pixmap else base_pixmap

        transform = QTransform()
        transform.rotate(incremental)
        rotated = source.transformed(transform, Qt.TransformationMode.SmoothTransformation)

        st.preview_pixmap = rotated
        st.preview_angle  = total_rotation

        return rotated
    
    # ── EXIF 헬퍼 ────────────────────────────────

    def _read_exif_orientation(self, file_path: Path) -> int:
        try:
            with Image.open(file_path) as img:
                exif = img.getexif()
                if not exif:
                    return 1
                ori = int(exif.get(EXIF_ORIENTATION_TAG, 1))
                return ori if 1 <= ori <= 8 else 1
        except Exception as e:
            warning_print(f"EXIF Orientation 읽기 실패: {file_path.name} {e}")
            return 1



