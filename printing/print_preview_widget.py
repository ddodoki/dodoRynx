# -*- coding: utf-8 -*-
# printing/print_preview_widget.py

"""
인쇄 미리보기 위젯
"""

import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPoint, QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t

from .print_utils import (
    FitMode,
    calculate_print_size,
    get_page_size_mm,
    mm_to_pixels,
    render_image_with_overlay,
    render_page_to_painter,
)


class ImageCache:
    """이미지 캐시 관리자"""
    
    def __init__(self, max_cache_size: int = 50):
        self._cache: Dict[str, QPixmap] = {}
        self._access_order: List[str] = []
        self.max_cache_size = max_cache_size
        self.hit_count = 0
        self.miss_count = 0
    

    def _generate_key(self, image_path: Path, rotation: int, color_mode: str = "color") -> str:
        """ 캐시 키 생성 (color_mode 포함)"""
        return f"{image_path.resolve()}_{rotation}_{color_mode}"
    

    def get(self, image_path: Path, rotation: int, color_mode: str = "color") -> Optional[QPixmap]:
        """캐시에서 이미지 가져오기"""
        key = self._generate_key(image_path, rotation, color_mode)
        
        if key in self._cache:
            self._access_order.remove(key)
            self._access_order.append(key)
            
            self.hit_count += 1
            debug_print(f"[캐시] 히트: {image_path.name} (히트율: {self.get_hit_rate():.1f}%)")
            return self._cache[key]
        
        self.miss_count += 1
        return None
    

    def put(self, image_path: Path, pixmap: QPixmap, rotation: int, color_mode: str = "color"):
        """캐시에 이미지 저장"""
        if pixmap is None or pixmap.isNull():
            return
        
        key = self._generate_key(image_path, rotation, color_mode)
        
        if key in self._cache:
            self._access_order.remove(key)
        
        while len(self._cache) >= self.max_cache_size:
            if self._access_order:
                oldest_key = self._access_order.pop(0)
                if oldest_key in self._cache:
                    del self._cache[oldest_key]
                    debug_print(f"[캐시] 제거 (LRU): {oldest_key}")
        
        self._cache[key] = pixmap
        self._access_order.append(key)
        debug_print(f"[캐시] 저장: {image_path.name} (크기: {len(self._cache)}/{self.max_cache_size})")
    

    def clear(self):
        count = len(self._cache)
        self._cache.clear()
        self._access_order.clear()
        info_print(f"[캐시] 삭제: {count}개 항목")
    

    def get_hit_rate(self) -> float:
        total = self.hit_count + self.miss_count
        if total == 0:
            return 0.0
        return (self.hit_count / total) * 100
    

    def get_stats(self) -> dict:
        return {
            'size': len(self._cache),
            'max_size': self.max_cache_size,
            'hit_count': self.hit_count,
            'miss_count': self.miss_count,
            'hit_rate': self.get_hit_rate(),
        }

_image_cache = ImageCache(max_cache_size=50)


def get_image_cache() -> ImageCache:
    return _image_cache


class PreviewRenderThread(QThread):
    """미리보기 렌더링 스레드"""
    
    render_complete = Signal(int, QPixmap)
    render_failed = Signal(int, str)
    
    def __init__(
        self,
        page_index: int,
        image_paths: List[Path],
        settings: dict,
        metadata_list: List[dict],
        viewport_size: Tuple[int, int]  # 변경: preview_size → viewport_size
    ):
        super().__init__()
        
        self.page_index = page_index
        self.image_paths = image_paths
        self.settings = settings
        self.metadata_list = metadata_list
        self.viewport_size = viewport_size
        self._is_cancelled = False
        
        self.image_cache = get_image_cache()
    

    def run(self):
        try:
            if self._is_cancelled or self.isInterruptionRequested():
                debug_print(f"미리보기 렌더링 취소됨")
                return
            
            pixmap = self._render_preview()
            
            if not self._is_cancelled and not self.isInterruptionRequested():
                if pixmap and not pixmap.isNull():
                    self.render_complete.emit(self.page_index, pixmap)
                else:
                    self.render_failed.emit(self.page_index, "렌더링 실패")
        
        except Exception as e:
            if not self._is_cancelled:
                error_print(f"미리보기 렌더링 오류: {e}")
                self.render_failed.emit(self.page_index, str(e))
    

    def cancel(self):
        self._is_cancelled = True
        self.requestInterruption()
    

    def _render_preview(self) -> Optional[QPixmap]:
        """원본 크기(실제 DPI)로 전체 페이지 렌더링"""
        painter = None
        try:
            if self._is_cancelled or self.isInterruptionRequested():
                return None
            
            # 용지 크기 계산
            paper_size = self.settings['paper_size']
            page_w_mm, page_h_mm = get_page_size_mm(paper_size)
            
            # 방향 적용
            orientation = self.settings['orientation']
            if orientation == Qt.Orientation.Horizontal:
                page_w_mm, page_h_mm = page_h_mm, page_w_mm
            
            # 실제 DPI 사용
            dpi = self.settings['quality'].dpi
            
            # 원본 용지 크기 (픽셀) - 스케일링 없음
            page_w_px = mm_to_pixels(page_w_mm, dpi)
            page_h_px = mm_to_pixels(page_h_mm, dpi)
            
            debug_print(f"[렌더링] 원본 페이지 크기: {int(page_w_px)}x{int(page_h_px)} px ({dpi} DPI)")
            
            # 원본 크기로 렌더링
            original_pixmap = QPixmap(int(page_w_px), int(page_h_px))
            original_pixmap.fill(QColor(255, 255, 255))
            
            painter = QPainter(original_pixmap)
            if not painter.isActive():
                error_print(f"QPainter 시작 실패")
                return None
            
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            
            # 공통 렌더링 함수 사용
            layout = self.settings['layout']
            images_per_page = layout.cols * layout.rows
            
            render_settings = self.settings.copy()
            render_settings['show_margins'] = True  # 여백 표시
            
            render_page_to_painter(
                painter=painter,
                image_paths=self.image_paths[:images_per_page],
                metadata_list=self.metadata_list[:images_per_page],
                settings=render_settings,
                page_size_px=(page_w_px, page_h_px),
                dpi=dpi,
                image_cache=self.image_cache,
                cancel_check=lambda: self._is_cancelled or self.isInterruptionRequested()
            )
            
            painter.end()
            
            if self._is_cancelled or self.isInterruptionRequested():
                return None
            
            # 원본 크기 그대로 반환 (줌은 위젯에서 처리)
            debug_print(f"[렌더링] 완료: {original_pixmap.width()}x{original_pixmap.height()}")
            return original_pixmap
        
        except Exception as e:
            error_print(f"미리보기 렌더링 실패: {e}")
            if painter and painter.isActive():
                painter.end()
            return None
        
        finally:
            if painter is not None and painter.isActive():
                try:
                    painter.end()
                except:
                    pass


class PrintPreviewWidget(QWidget):
    """인쇄 미리보기 위젯 (캔버스 기준 줌 기능)"""
    

    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 원본 페이지 렌더링 결과 (줌 적용 전)
        self.original_pixmap: Optional[QPixmap] = None
        self.render_thread: Optional[PreviewRenderThread] = None
        
        # 미리보기 캐시 (페이지별 원본)
        self.preview_cache: Dict[str, QPixmap] = {}
        self.max_preview_cache = 10
        
        # 줌 기능
        self.zoom_level = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 5.0
        self.zoom_step = 0.1
        self.fit_zoom = 1.0

        # 드래그 스크롤 기능
        self.is_dragging = False
        self.drag_start_pos = QPoint()
        self.scroll_start_pos = QPoint()        

        self._current_page_index: int = -1

        self.init_ui()
    

    def init_ui(self):
        """UI 초기화"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 줌 정보 라벨
        self.zoom_label = QLabel(t('print_preview.zoom_initial'))
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setStyleSheet("""
            QLabel {
                background-color: #2b2b2b;
                color: #4a9eff;
                font-size: 11px;
                font-weight: bold;
                padding: 5px;
                border-bottom: 1px solid #555;
            }
        """)
        main_layout.addWidget(self.zoom_label)
        
        # 스크롤 영역
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #e0e0e0;
                border: 1px solid #aaa;
            }
            
            /*  스크롤바 스타일 */
            QScrollBar:vertical {
                background-color: #2b2b2b;
                width: 12px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #4a9eff;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #357abd;
            }
            
            QScrollBar:horizontal {
                background-color: #2b2b2b;
                height: 12px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background-color: #4a9eff;
                min-width: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: #357abd;
            }
        """)
        
        # 미리보기 라벨
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setScaledContents(False)
        self.preview_label.setStyleSheet("""
            QLabel {
                background-color: #e0e0e0;
            }
        """)
        self.preview_label.setText(t('print_preview.loading'))
        self.preview_label.setMinimumSize(100, 100)
        
        self.scroll_area.setWidget(self.preview_label)
        main_layout.addWidget(self.scroll_area)
        
        # 마우스 추적 활성화
        self.setMouseTracking(True)
        self.scroll_area.setMouseTracking(True)
        
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    

    def _generate_preview_cache_key(
        self,
        page_index: int,
        image_paths: List[Path],
        settings: dict
    ) -> str:
        """ 미리보기 캐시 키 생성 (spacing 추가)"""
        key_data = {
            'page': page_index,
            'images': [p.name for p in image_paths],
            'paper_size': settings['paper_size'].value,
            'orientation': settings['orientation'],
            'quality': settings['quality'].name,
            'layout': f"{settings['layout'].cols}x{settings['layout'].rows}",
            'fit_mode': settings['fit_mode'],
            'margins': tuple(settings['margins'].values()),
            'spacing': settings.get('spacing', 5.0), 
            'rotation': settings['rotation'],
            'color_mode': settings.get('color_mode', 'color'),
        }
        
        key_str = str(key_data)
        return hashlib.md5(key_str.encode()).hexdigest()


    def set_preview(
        self,
        page_index: int,
        image_paths: List[Path],
        settings: dict,
        metadata_list: List[dict]
    ):
        """미리보기 설정 및 렌더링 시작"""
        self._current_page_index = page_index
        cache_key = self._generate_preview_cache_key(page_index, image_paths, settings)
        
        # 캐시 확인
        if cache_key in self.preview_cache:
            cached_pixmap = self.preview_cache[cache_key]
            self.original_pixmap = cached_pixmap
            
            # 화면에 맞춤 줌 계산 후 적용
            self._calculate_fit_zoom()
            self.zoom_level = self.fit_zoom
            self._apply_zoom()
            
            debug_print(f"[미리보기] 캐시 히트: 페이지 {page_index + 1}")
            return
        
        # 안전한 스레드 종료
        if self.render_thread and self.render_thread.isRunning():
            debug_print(f"이전 렌더링 취소 중...")
            self.render_thread.cancel()
            
            if not self.render_thread.wait(1000):
                warning_print(f"렌더링 스레드가 응답하지 않음, 강제 종료")
                self.render_thread.terminate()
                self.render_thread.wait()
            
            self.render_thread = None
        
        self.preview_label.setText(t('print_preview.rendering', page=page_index + 1))
        
        # 뷰포트 크기 (줌 계산용)
        viewport_size = (
            int(self.scroll_area.viewport().width()),
            int(self.scroll_area.viewport().height())
        )
        
        # 렌더링 스레드 시작
        self.render_thread = PreviewRenderThread(
            page_index,
            image_paths,
            settings,
            metadata_list,
            viewport_size
        )
        
        self.render_thread.render_complete.connect(
            lambda idx, pm, key=cache_key: self._on_render_complete(idx, pm, key)
        )
        self.render_thread.render_failed.connect(self._on_render_failed)
        self.render_thread.start()
    

    @Slot(int, QPixmap, str)
    def _on_render_complete(self, page_index: int, pixmap: QPixmap, cache_key: str):
        """렌더링 완료 (원본 저장)"""

        if page_index != self._current_page_index:
            debug_print(f"오래된 렌더 결과 무시: {page_index} (현재: {self._current_page_index})")
            return

        self.original_pixmap = pixmap
        
        # 캐시 저장
        if len(self.preview_cache) >= self.max_preview_cache:
            first_key = next(iter(self.preview_cache))
            del self.preview_cache[first_key]
        
        self.preview_cache[cache_key] = pixmap
        
        # 화면에 맞춤 줌 계산 후 적용
        self._calculate_fit_zoom()
        self.zoom_level = self.fit_zoom
        self._apply_zoom()
        
        debug_print(f"미리보기 렌더링 완료: 페이지 {page_index + 1} (원본: {pixmap.width()}x{pixmap.height()})")
    

    @Slot(int, str)
    def _on_render_failed(self, page_index: int, error: str):
        """렌더링 실패"""
        self.preview_label.setText(t('print_preview.render_failed', error=error))
        error_print(f"미리보기 실패 (페이지 {page_index + 1}): {error}")
    

    def _calculate_fit_zoom(self):
        """화면에 맞춤 줌 레벨 계산"""
        if not self.original_pixmap or self.original_pixmap.isNull():
            self.fit_zoom = 1.0
            return
        
        # 스크롤 영역 크기 (여백 제외)
        viewport_w = self.scroll_area.viewport().width() - 40
        viewport_h = self.scroll_area.viewport().height() - 40
        
        # 원본 페이지 크기
        page_w = self.original_pixmap.width()
        page_h = self.original_pixmap.height()
        
        # 맞춤 줌 계산
        zoom_w = viewport_w / page_w
        zoom_h = viewport_h / page_h
        self.fit_zoom = min(zoom_w, zoom_h)
        
        debug_print(f"[줌] 화면 맞춤: {int(self.fit_zoom * 100)}%")


    def _apply_zoom(self):
        """전체 캔버스(페이지) 기준 줌 적용"""
        if not self.original_pixmap or self.original_pixmap.isNull():
            return
        
        # 줌 적용된 크기 계산
        zoomed_w = int(self.original_pixmap.width() * self.zoom_level)
        zoomed_h = int(self.original_pixmap.height() * self.zoom_level)
        
        # 전체 페이지 스케일링
        zoomed_pixmap = self.original_pixmap.scaled(
            zoomed_w, zoomed_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        
        # 라벨 크기 설정
        self.preview_label.setFixedSize(zoomed_w, zoomed_h)
        self.preview_label.setPixmap(zoomed_pixmap)
        
        # 줌 레벨 표시
        zoom_percentage = int(self.zoom_level * 100)
        fit_percentage = int(self.fit_zoom * 100)
        
        if abs(self.zoom_level - self.fit_zoom) < 0.01:
            self.zoom_label.setText(
                t('print_preview.zoom_fit', pct=zoom_percentage)
            )
        else:
            self.zoom_label.setText(
                t('print_preview.zoom_free', pct=zoom_percentage, fit=fit_percentage)
            )
        
        # 커서 업데이트 (스크롤 가능 여부에 따라)
        if self._is_scrollable():
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        
        debug_print(f"[줌] 적용: {zoom_percentage}% (캔버스: {zoomed_w}x{zoomed_h})")


    def _set_zoom(self, new_zoom: float, mouse_pos: Optional[QPoint] = None):
        """줌 레벨 설정 (마우스 위치 기준 유지)"""
        new_zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))
        
        if abs(new_zoom - self.zoom_level) < 0.001:
            return
        
        # 변수 초기화
        old_h_pos = 0
        old_v_pos = 0
        viewport_center_x = 0
        viewport_center_y = 0
        h_scroll = None
        v_scroll = None
        
        # 스크롤 위치 보존
        if mouse_pos:
            h_scroll = self.scroll_area.horizontalScrollBar()
            v_scroll = self.scroll_area.verticalScrollBar()
            
            old_h_pos = h_scroll.value()
            old_v_pos = v_scroll.value()
            
            viewport_center_x = mouse_pos.x()
            viewport_center_y = mouse_pos.y()
        
        # 줌 레벨 업데이트
        old_zoom = self.zoom_level
        self.zoom_level = new_zoom
        
        # 줌 적용
        self._apply_zoom()
        
        # 스크롤 위치 조정 (줌 중심점 유지)
        if mouse_pos and h_scroll and v_scroll:
            zoom_ratio = new_zoom / old_zoom
            
            new_h_pos = int((old_h_pos + viewport_center_x) * zoom_ratio - viewport_center_x)
            new_v_pos = int((old_v_pos + viewport_center_y) * zoom_ratio - viewport_center_y)
            
            h_scroll.setValue(new_h_pos)
            v_scroll.setValue(new_v_pos)


    def mousePressEvent(self, event):
        """ 마우스 클릭 시작 (드래그 준비)"""
        if event.button() == Qt.MouseButton.LeftButton:
            # 스크롤 영역 내부 클릭인지 확인
            if self.scroll_area.underMouse():
                self.is_dragging = True
                self.drag_start_pos = event.pos()
                
                # 현재 스크롤 위치 저장
                h_scroll = self.scroll_area.horizontalScrollBar()
                v_scroll = self.scroll_area.verticalScrollBar()
                self.scroll_start_pos = QPoint(h_scroll.value(), v_scroll.value())
                
                # 커서 변경
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        
        super().mousePressEvent(event)
    

    def mouseMoveEvent(self, event):
        """ 마우스 드래그 중 (스크롤 이동)"""
        if self.is_dragging:
            # 드래그 거리 계산
            delta = event.pos() - self.drag_start_pos
            
            # 스크롤 위치 업데이트
            h_scroll = self.scroll_area.horizontalScrollBar()
            v_scroll = self.scroll_area.verticalScrollBar()
            
            new_h = self.scroll_start_pos.x() - delta.x()
            new_v = self.scroll_start_pos.y() - delta.y()
            
            h_scroll.setValue(new_h)
            v_scroll.setValue(new_v)
            
            event.accept()
            return
        
        # 드래그 중이 아니면 커서 변경 (스크롤 가능 여부)
        if self.scroll_area.underMouse() and self._is_scrollable():
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        
        super().mouseMoveEvent(event)
    

    def mouseReleaseEvent(self, event):
        """ 마우스 버튼 해제 (드래그 종료)"""
        if event.button() == Qt.MouseButton.LeftButton:
            if self.is_dragging:
                self.is_dragging = False
                
                # 커서 복원
                if self.scroll_area.underMouse() and self._is_scrollable():
                    self.setCursor(Qt.CursorShape.OpenHandCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                
                event.accept()
                return
        
        super().mouseReleaseEvent(event)
    

    def _is_scrollable(self) -> bool:
        """ 스크롤 가능 여부 확인"""
        h_scroll = self.scroll_area.horizontalScrollBar()
        v_scroll = self.scroll_area.verticalScrollBar()
        
        return (h_scroll.maximum() > 0 or v_scroll.maximum() > 0)


    def wheelEvent(self, event):
        """마우스 휠 이벤트 (Ctrl + 휠로 줌)"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            
            if delta > 0:
                new_zoom = self.zoom_level + self.zoom_step
            else:
                new_zoom = self.zoom_level - self.zoom_step
            
            mouse_pos = event.position().toPoint()
            self._set_zoom(new_zoom, mouse_pos)
            
            event.accept()
        else:
            super().wheelEvent(event)


    def reset_zoom(self):
        """줌 레벨 리셋 (100%)"""
        self._set_zoom(1.0)
    

    def zoom_in(self):
        """줌 인"""
        self._set_zoom(self.zoom_level + self.zoom_step)
    

    def zoom_out(self):
        """줌 아웃"""
        self._set_zoom(self.zoom_level - self.zoom_step)
    

    def zoom_fit(self):
        """화면에 맞춤"""
        if not self.original_pixmap or self.original_pixmap.isNull():
            return
        
        self._calculate_fit_zoom()
        self._set_zoom(self.fit_zoom)


    def clear_preview(self):
        """미리보기 초기화"""
        if self.render_thread and self.render_thread.isRunning():
            self.render_thread.cancel()
            if not self.render_thread.wait(1000):
                warning_print("렌더링 스레드 강제 종료")
                self.render_thread.terminate()
                self.render_thread.wait(500)

        if self.render_thread:
            try:
                self.render_thread.render_complete.disconnect()
                self.render_thread.render_failed.disconnect()
            except RuntimeError:
                pass
            self.render_thread = None 

        self._current_page_index = -1
        self.original_pixmap = None
        self.preview_label.clear()
        self.preview_label.setText(t('print_preview.no_preview'))
        
        # 라벨 크기 제약 해제
        self.preview_label.setMinimumSize(100, 100)
        self.preview_label.setMaximumSize(16777215, 16777215)  # QWIDGETSIZE_MAX
        
        self.preview_cache.clear()
        get_image_cache().clear()
        
        self.zoom_level = 1.0
        self.fit_zoom = 1.0
        self.zoom_label.setText(t('print_preview.zoom_cleared'))


    def resizeEvent(self, event):
        """크기 조정 시 - 화면 맞춤 줌 재계산"""
        super().resizeEvent(event)
        
        # 원본이 있으면 화면 맞춤 줌 재계산
        if self.original_pixmap and not self.original_pixmap.isNull():
            old_fit_zoom = self.fit_zoom
            self._calculate_fit_zoom()
            
            # 현재 화면 맞춤 상태였다면 자동으로 다시 맞춤
            if abs(self.zoom_level - old_fit_zoom) < 0.01:
                self.zoom_level = self.fit_zoom
                self._apply_zoom()

