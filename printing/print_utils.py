# -*- coding: utf-8 -*-
# printing/print_utils.py (기존 코드에 추가)

"""
인쇄 유틸리티 함수들
"""

import traceback
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QMarginsF, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPageLayout,
    QPageSize,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtPrintSupport import QPrinter

from core.image_loader import ImageLoader
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t
        

class PaperSize(Enum):
    """용지 크기"""
    A4 = "A4"
    LETTER = "Letter"
    A3 = "A3"
    A5 = "A5"
    LEGAL = "Legal"


class PrintQuality(Enum):
    HIGH     = (300, "high")
    STANDARD = (150, "standard")
    DRAFT    = (72,  "draft")

    dpi: int
    _key: str

    def __new__(cls, dpi: int, key: str) -> "PrintQuality":
        obj = object.__new__(cls)
        obj._value_ = key   # _value_ 명시 → Pylance가 클래스를 정상 인식
        obj.dpi = dpi
        obj._key = key
        return obj

    def get_label(self) -> str:
        return t(f'print_quality.{self._value_}')


class FitMode(Enum):
    """맞추기 옵션"""
    ORIGINAL = "original"  # 원본 크기 유지
    FIT_PAGE = "fit"       # 용지에 맞춤 (비율 유지)
    FILL_PAGE = "fill"     # 용지 채우기 (크롭)


class ImageLayout(Enum):
    ONE_PER_PAGE  = (1, 1, "one_per_page")
    TWO_PER_PAGE  = (2, 1, "two_per_page")
    FOUR_PER_PAGE = (2, 2, "four_per_page")

    cols: int
    rows: int
    _key: str

    def __new__(cls, cols: int, rows: int, key: str) -> "ImageLayout":
        obj = object.__new__(cls)
        obj._value_ = key   # _value_ 명시
        obj.cols = cols
        obj.rows = rows
        obj._key = key
        return obj

    def get_label(self) -> str:
        return t(f'image_layout.{self._value_}')


def mm_to_pixels(mm: float, dpi: int = 300) -> int:
    """밀리미터를 픽셀로 변환"""
    inches = mm / 25.4
    return int(inches * dpi)


def pixels_to_mm(pixels: int, dpi: int = 300) -> float:
    """픽셀을 밀리미터로 변환"""
    inches = pixels / dpi
    return inches * 25.4


def get_page_size_mm(paper_size: PaperSize) -> Tuple[float, float]:
    """용지 크기 반환 (mm 단위)"""
    sizes = {
        PaperSize.A4: (210, 297),
        PaperSize.LETTER: (215.9, 279.4),
        PaperSize.A3: (297, 420),
        PaperSize.A5: (148, 210),
        PaperSize.LEGAL: (215.9, 355.6),
    }
    return sizes.get(paper_size, (210, 297))


def calculate_print_size(
    image_size: Tuple[int, int],
    page_size: Tuple[int, int],
    margins: Tuple[int, int, int, int],
    fit_mode: FitMode,
    rotation: int = 0
) -> Tuple[int, int, int, int]:
    """
    인쇄 크기 및 위치 계산
    
    Args:
        image_size: 원본 이미지 크기 (width, height)
        page_size: 용지 크기 (픽셀)
        margins: 여백 (left, top, right, bottom) - 픽셀
        fit_mode: 맞추기 모드
        rotation: 회전 각도 (0, 90, 180, 270)
    
    Returns:
        (x, y, width, height) - 이미지 그릴 위치와 크기
    """
    img_w, img_h = image_size
    page_w, page_h = page_size
    margin_l, margin_t, margin_r, margin_b = margins
    
    # 회전 적용
    if rotation in (90, 270):
        img_w, img_h = img_h, img_w
    
    # 사용 가능한 영역
    available_w = page_w - margin_l - margin_r
    available_h = page_h - margin_t - margin_b
    
    if fit_mode == FitMode.ORIGINAL:
        # 원본 크기 유지 (중앙 정렬)
        if img_w > available_w or img_h > available_h:
            # 용지보다 크면 비율 유지하며 축소
            scale = min(available_w / img_w, available_h / img_h)
            final_w = int(img_w * scale)
            final_h = int(img_h * scale)
        else:
            final_w = img_w
            final_h = img_h
    
    elif fit_mode == FitMode.FIT_PAGE:
        # 용지에 맞춤 (비율 유지)
        scale = min(available_w / img_w, available_h / img_h)
        final_w = int(img_w * scale)
        final_h = int(img_h * scale)
    
    else:  # FitMode.FILL_PAGE
        # 용지 채우기 (크롭 가능)
        scale = max(available_w / img_w, available_h / img_h)
        final_w = int(img_w * scale)
        final_h = int(img_h * scale)
    
    # 중앙 정렬
    x = margin_l + (available_w - final_w) // 2
    y = margin_t + (available_h - final_h) // 2
    
    return (x, y, final_w, final_h)


def generate_overlay_text(metadata: dict) -> str:
    """메타데이터에서 오버레이 텍스트 생성"""
    lines = []
    
    # 파일 정보
    if 'file' in metadata:
        file_info = metadata['file']
        if file_info.get('filename'):
            lines.append(f"📄 {file_info['filename']}")
        if file_info.get('resolution'):
            lines.append(f"🖼 {file_info['resolution']}")
    
    # 카메라 정보
    if 'camera' in metadata:
        camera = metadata['camera']
        if camera.get('model'):
            lines.append(f"📷 {camera['model']}")
        if camera.get('date_taken'):
            lines.append(f"📅 {camera['date_taken']}")
    
    # EXIF 정보
    if 'camera' in metadata:
        camera = metadata['camera']
        exif_parts = []
        if camera.get('focal_length'):
            exif_parts.append(camera['focal_length'])
        if camera.get('f_stop'):
            exif_parts.append(camera['f_stop'])
        if camera.get('exposure_time'):
            exif_parts.append(camera['exposure_time'])
        if camera.get('iso'):
            exif_parts.append(camera['iso'])
        if exif_parts:
            lines.append(" | ".join(exif_parts))
    
    return "\n".join(lines)


def get_qpage_size(paper_size: PaperSize) -> QPageSize.PageSizeId:
    """PaperSize를 QPageSize로 변환"""
    mapping = {
        PaperSize.A4: QPageSize.PageSizeId.A4,
        PaperSize.LETTER: QPageSize.PageSizeId.Letter,
        PaperSize.A3: QPageSize.PageSizeId.A3,
        PaperSize.A5: QPageSize.PageSizeId.A5,
        PaperSize.LEGAL: QPageSize.PageSizeId.Legal,
    }
    return mapping.get(paper_size, QPageSize.PageSizeId.A4)


def create_printer(
    printer_name: str,
    paper_size: PaperSize,
    orientation: Qt.Orientation, 
    quality: PrintQuality
) -> Optional[QPrinter]:
    """QPrinter 인스턴스 생성"""
    try:       
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPrinterName(printer_name)
        
        # QPrinter 메서드로 직접 설정
        page_size = QPageSize(get_qpage_size(paper_size))
        printer.setPageSize(page_size)
        
        # Qt.Orientation → QPageLayout.Orientation 변환
        if orientation == Qt.Orientation.Horizontal:
            printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        else:
            printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        
        # 기본 여백 0
        printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        
        # 해상도 설정
        printer.setResolution(quality.dpi)
        
        info_print(f"프린터 생성: {printer_name}, {paper_size.value}, {quality.dpi} DPI")
        return printer
    
    except Exception as e:
        error_print(f"프린터 생성 실패: {e}")
        error_print(traceback.format_exc())
        return None


def render_image_with_overlay(
    image_path: Path,
    overlay_enabled: bool = False,
    metadata: Optional[dict] = None,
    scale: float = 1.0,
    rotation: int = 0,
    color_mode: str = "color"
) -> Optional[QPixmap]:
    """
    이미지 + 오버레이 합성
    """
    try:
        # 이미지 로드
        loader = ImageLoader()
        pixmap = loader.load(image_path, max_size=None)
        
        if not pixmap or pixmap.isNull():
            error_print(f"이미지 로드 실패: {image_path.name}")
            return None
        
        # EXIF 회전 적용
        pixmap = loader.apply_exif_rotation(image_path, pixmap)
        
        # 흑백 변환 (디버그 로그 추가)
        if color_mode == "grayscale":
            debug_print(f"[흑백] 변환 시작: {image_path.name}")
            pixmap = convert_to_grayscale(pixmap)
            if pixmap and not pixmap.isNull():
                debug_print(f"[흑백] 변환 성공: {image_path.name}")
            else:
                error_print(f"[흑백] 변환 실패: {image_path.name}")
        
        # 회전 적용
        if rotation != 0:
            transform = QTransform().rotate(rotation)
            pixmap = pixmap.transformed(transform, Qt.TransformationMode.SmoothTransformation)
        
        return pixmap
    
    except Exception as e:
        error_print(f"이미지 렌더링 실패 {image_path.name}: {e}")
        error_print(traceback.format_exc())
        return None


def convert_to_grayscale(pixmap: QPixmap) -> QPixmap:
    """ QPixmap을 그레이스케일로 변환 (개선)"""
    
    try:
        # QPixmap → QImage
        image = pixmap.toImage()
        
        # 그레이스케일 변환 시도
        if image.format() != QImage.Format.Format_Invalid:
            # Format_Grayscale8로 변환
            grayscale_image = image.convertToFormat(QImage.Format.Format_Grayscale8)
            
            if grayscale_image.isNull():
                # 변환 실패 시 수동 변환
                debug_print(f"[흑백] 자동 변환 실패, 수동 변환 시도")
                grayscale_image = _manual_grayscale_conversion(image)
            
            # QImage → QPixmap
            result = QPixmap.fromImage(grayscale_image)
            debug_print(f"[흑백] 변환 완료: {result.width()}x{result.height()}")
            return result
        else:
            error_print(f"[흑백] 이미지 포맷이 유효하지 않음")
            return pixmap
    
    except Exception as e:
        error_print(f"[흑백] 변환 실패: {e}")
        return pixmap


def _manual_grayscale_conversion(image: QImage) -> QImage:
    """ 수동 그레이스케일 변환 (RGB → Gray)"""
    
    width = image.width()
    height = image.height()
    
    # 새 그레이스케일 이미지 생성
    gray_image = QImage(width, height, QImage.Format.Format_RGB32)
    
    for y in range(height):
        for x in range(width):
            # 픽셀 색상 가져오기
            color = QColor(image.pixel(x, y))
            
            # 그레이스케일 값 계산 (ITU-R BT.601 표준)
            gray = int(0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue())
            
            # 그레이스케일 색상 설정
            gray_color = QColor(gray, gray, gray)
            gray_image.setPixel(x, y, gray_color.rgb())
    
    return gray_image


def draw_overlay_on_pixmap(
    pixmap: QPixmap,
    text: str,
    scale: float = 1.0,
    rotation: int = 0 
) -> QPixmap:
    """QPixmap에 오버레이 텍스트 그리기 (회전 대응)"""

    # 새 QPixmap 생성 (원본 복사)
    result = QPixmap(pixmap)
    
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    
    # 폰트 설정
    font = QFont("Consolas", int(11 * scale))
    painter.setFont(font)
    
    # 텍스트 영역 계산
    lines = text.split('\n')
    line_height = int(20 * scale)
    padding = int(15 * scale)
    
    # 회전에 따라 위치 조정
    bg_width = int(400 * scale)
    bg_height = len(lines) * line_height + padding * 2
    
    # 회전별 위치 (좌측 상단 기준)
    if rotation == 0:
        bg_x = padding
        bg_y = padding
    elif rotation == 90:
        bg_x = result.width() - bg_height - padding
        bg_y = padding
    elif rotation == 180:
        bg_x = result.width() - bg_width - padding
        bg_y = result.height() - bg_height - padding
    elif rotation == 270:
        bg_x = padding
        bg_y = result.height() - bg_width - padding
    else:
        bg_x = padding
        bg_y = padding
    
    # 배경 그리기
    bg_rect = QRect(bg_x, bg_y, bg_width, bg_height)
    painter.fillRect(bg_rect, QColor(0, 0, 0, 180))
    
    # 텍스트 그리기
    painter.setPen(QColor(255, 255, 255))
    y = bg_y + padding
    for line in lines:
        painter.drawText(QPoint(bg_x + padding, y), line)
        y += line_height
    
    painter.end()
    
    return result


def render_page_to_painter(
    painter: QPainter,
    image_paths: List[Path],
    metadata_list: List[dict],
    settings: Dict,
    page_size_px: Tuple[float, float],
    dpi: int,
    image_cache=None,
    cancel_check: Optional[Callable[[], bool]] = None
) -> bool:
    """공통 페이지 렌더링 함수 (사용자 지정 간격)"""
    try:
        page_w_px, page_h_px = page_size_px
        
        # 여백
        margins = settings['margins']
        margin_l = mm_to_pixels(margins['left'], dpi)
        margin_t = mm_to_pixels(margins['top'], dpi)
        margin_r = mm_to_pixels(margins['right'], dpi)
        margin_b = mm_to_pixels(margins['bottom'], dpi)
        
        # 콘텐츠 영역
        content_w = page_w_px - margin_l - margin_r
        content_h = page_h_px - margin_t - margin_b
        
        # 배치
        layout = settings['layout']
        cols = layout.cols
        rows = layout.rows
        
        # 사용자 지정 간격 (기본값 5mm)
        spacing_mm = settings.get('spacing', 5.0)
        
        # 1장에 1이미지면 간격 0
        if cols == 1 and rows == 1:
            gap_h_mm = 0
            gap_v_mm = 0
        else:
            # 2개 이상: 사용자 설정 간격 사용
            gap_h_mm = spacing_mm
            gap_v_mm = spacing_mm
        
        # 간격을 픽셀로 변환
        gap_h_px = mm_to_pixels(gap_h_mm, dpi)
        gap_v_px = mm_to_pixels(gap_v_mm, dpi)
        
        # 간격을 제외한 실제 이미지 영역 계산
        total_gap_w = (cols - 1) * gap_h_px
        total_gap_h = (rows - 1) * gap_v_px
        
        available_w = content_w - total_gap_w
        available_h = content_h - total_gap_h
        
        # 셀 크기
        cell_w = available_w / cols
        cell_h = available_h / rows
        
        debug_print(f"[배치] {cols}x{rows}, 간격: {spacing_mm}mm, 셀: {int(cell_w)}x{int(cell_h)}px")
        
        # 여백 표시
        if settings.get('show_margins', False):
            
            # 외곽 여백선
            painter.setPen(QPen(QColor(200, 200, 200), 2, Qt.PenStyle.DashLine))
            painter.drawRect(
                int(margin_l),
                int(margin_t),
                int(content_w),
                int(content_h)
            )
            
            # 셀 구분선 (간격 표시)
            if cols > 1 or rows > 1:
                painter.setPen(QPen(QColor(150, 150, 150), 1, Qt.PenStyle.DotLine))
                
                # 세로 구분선
                for col in range(1, cols):
                    x = margin_l + col * (cell_w + gap_h_px) - gap_h_px / 2
                    painter.drawLine(
                        int(x),
                        int(margin_t),
                        int(x),
                        int(margin_t + content_h)
                    )
                
                # 가로 구분선
                for row in range(1, rows):
                    y = margin_t + row * (cell_h + gap_v_px) - gap_v_px / 2
                    painter.drawLine(
                        int(margin_l),
                        int(y),
                        int(margin_l + content_w),
                        int(y)
                    )
        
        # 설정
        fit_mode_str = settings['fit_mode']
        if fit_mode_str == 'original':
            fit_mode = FitMode.ORIGINAL
        elif fit_mode_str == 'fill':
            fit_mode = FitMode.FILL_PAGE
        else:
            fit_mode = FitMode.FIT_PAGE
        
        rotation = settings['rotation']
        overlay = settings.get('overlay', False)
        color_mode = settings.get('color_mode', 'color')
        
        # 각 이미지 그리기
        for i, image_path in enumerate(image_paths):
            if cancel_check and cancel_check():
                debug_print(f"렌더링 취소됨")
                return False
            
            if i >= len(metadata_list):
                continue
            
            metadata = metadata_list[i]
            
            # 캐시 확인
            pixmap = None
            if image_cache:
                pixmap = image_cache.get(image_path, rotation, color_mode)
            
            # 캐시 미스 시 로드
            if not pixmap or pixmap.isNull():
                pixmap = render_image_with_overlay(
                    image_path,
                    overlay,
                    metadata,
                    scale=1.0,
                    rotation=rotation,
                    color_mode=color_mode
                )
                
                # 캐시 저장
                if pixmap and not pixmap.isNull() and image_cache:
                    image_cache.put(image_path, pixmap, rotation, color_mode)
            
            if not pixmap or pixmap.isNull():
                warning_print(f"이미지 로드 실패: {image_path.name}")
                continue
            
            # 셀 위치 (간격 포함)
            col = i % cols
            row = i // cols
            
            cell_x = margin_l + col * (cell_w + gap_h_px)
            cell_y = margin_t + row * (cell_h + gap_v_px)
            
            # 이미지 크기
            img_w = pixmap.width()
            img_h = pixmap.height()
            
            # 크기 계산
            if fit_mode == FitMode.ORIGINAL:
                if img_w > cell_w or img_h > cell_h:
                    scale_factor = min(cell_w / img_w, cell_h / img_h)
                    final_w = img_w * scale_factor
                    final_h = img_h * scale_factor
                else:
                    final_w = img_w
                    final_h = img_h
            
            elif fit_mode == FitMode.FIT_PAGE:
                scale_factor = min(cell_w / img_w, cell_h / img_h)
                final_w = img_w * scale_factor
                final_h = img_h * scale_factor
            
            else:  # FILL_PAGE
                scale_factor = max(cell_w / img_w, cell_h / img_h)
                final_w = img_w * scale_factor
                final_h = img_h * scale_factor
            
            # 중앙 정렬
            draw_x = cell_x + (cell_w - final_w) / 2
            draw_y = cell_y + (cell_h - final_h) / 2
            
            # 그리기
            scaled_pixmap = pixmap.scaled(
                int(final_w), int(final_h),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            
            target_rect = QRectF(draw_x, draw_y, final_w, final_h)
            source_rect = scaled_pixmap.rect()
            
            painter.drawPixmap(target_rect, scaled_pixmap, QRectF(source_rect))
        
        return True
    
    except Exception as e:
        error_print(f"페이지 렌더링 실패: {e}")
        error_print(traceback.format_exc())
        return False




