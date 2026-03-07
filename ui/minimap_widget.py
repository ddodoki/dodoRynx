# -*- coding: utf-8 -*-
# ui/minimap_widget.py

from typing import Optional, Tuple

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from utils.debug import debug_print


class MiniMapWidget(QWidget):
    """줌 시 현재 위치를 보여주는 미니맵"""
    
    position_clicked = Signal(float, float) 

    # ============================================
    # 초기화
    # ============================================
   
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.thumbnail: Optional[QPixmap] = None
        self.visible_rect_ratio: QRectF = QRectF() 

        self.is_dragging: bool = False
        self.drag_start_pos: QPoint = QPoint()
        
        self.max_size = 200 
        self.setFixedSize(self.max_size, self.max_size) 
        
        self._last_pixmap_cache_key: Optional[str] = None
        
        self.setMouseTracking(True)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()
    
    # ============================================
    # 설정 (투명도, 썸네일)
    # ============================================

    def set_opacity(self, opacity: float) -> None:
        """투명도 설정 (미구현 - 향후 개선 예정)"""
        pass 
    

    def get_opacity(self) -> float:
        """현재 투명도 반환 (고정값)"""
        return 0.8
    

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        """전체 이미지의 썸네일 설정 (이미지 비율에 맞춰 미니맵 크기 조정)"""
        if pixmap.isNull():
            self.thumbnail = None
            self._last_pixmap_cache_key = None
            self.hide()
            return
        
        cache_key = f"{pixmap.width()}x{pixmap.height()}_{pixmap.cacheKey()}"
        
        if self._last_pixmap_cache_key == cache_key:
            debug_print(f"미니맵 썸네일 캐시 히트 - 재사용")
            return 
        
        self._last_pixmap_cache_key = cache_key
        debug_print(f"미니맵 썸네일 생성: {cache_key}")
        
        img_width = pixmap.width()
        img_height = pixmap.height()
        
        if img_height == 0 or img_width == 0:
            self.thumbnail = None
            self._last_pixmap_cache_key = None
            self.hide()
            return
                
        aspect_ratio = img_width / img_height
        
        debug_print(f"원본 이미지 크기: {img_width}x{img_height}, 비율: {aspect_ratio:.2f}")
        
        MIN_SIZE = 80  # 최소 크기
        
        # 미니맵 크기 계산 (비율 유지)
        if aspect_ratio > 1.0:
            # 가로가 긴 이미지
            minimap_width = self.max_size
            minimap_height = int(self.max_size / aspect_ratio)
            
            # 높이가 너무 작으면 비율 유지하면서 높이를 MIN_SIZE로
            if minimap_height < MIN_SIZE:
                minimap_height = MIN_SIZE
                minimap_width = int(MIN_SIZE * aspect_ratio)
                
                # 너비가 max_size를 넘으면 다시 조정
                if minimap_width > self.max_size:
                    minimap_width = self.max_size
                    minimap_height = int(self.max_size / aspect_ratio)
        else:
            # 세로가 긴 이미지
            minimap_height = self.max_size
            minimap_width = int(self.max_size * aspect_ratio)
            
            # 너비가 너무 작으면 비율 유지하면서 너비를 MIN_SIZE로
            if minimap_width < MIN_SIZE:
                minimap_width = MIN_SIZE
                minimap_height = int(MIN_SIZE / aspect_ratio)
                
                # 높이가 max_size를 넘으면 다시 조정
                if minimap_height > self.max_size:
                    minimap_height = self.max_size
                    minimap_width = int(self.max_size * aspect_ratio)
        
        # 최종 최소 크기 보장 (극단적인 경우)
        minimap_width = max(MIN_SIZE, minimap_width)
        minimap_height = max(MIN_SIZE, minimap_height)
        
        # 최종 최대 크기 제한
        minimap_width = min(self.max_size, minimap_width)
        minimap_height = min(self.max_size, minimap_height)
        
        # 미니맵 위젯 크기 조정
        self.setFixedSize(minimap_width, minimap_height)
        
        # 썸네일 생성 (여백 10px씩)
        max_thumb_width = minimap_width - 20
        max_thumb_height = minimap_height - 20
        
        # 최소 썸네일 크기 보장
        max_thumb_width = max(60, max_thumb_width)
        max_thumb_height = max(60, max_thumb_height)
        
        self.thumbnail = pixmap.scaled(
            max_thumb_width, max_thumb_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.update()
    

    def set_visible_rect(self, rect: QRectF) -> None:
        """
        현재 보이는 영역 설정 (비율)
        rect: QRectF(x, y, width, height) - 각 값은 0.0 ~ 1.0
        """
        self.visible_rect_ratio = rect
        self.update()
    

    # ============================================
    # 그리기
    # ============================================

    def paintEvent(self, event):
        """미니맵 그리기"""
        if self.thumbnail is None or self.thumbnail.isNull():
            return
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        opacity = 0.8
        
        bg_color = QColor(30, 30, 30, int(200 * opacity))
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 5, 5)
        
        border_color = QColor(100, 100, 100, int(180 * opacity))
        border_pen = QPen(border_color, 2)
        painter.setPen(border_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 5, 5)
        
        painter.setOpacity(opacity)
        
        # 썸네일 중앙 정렬
        x = (self.width() - self.thumbnail.width()) // 2
        y = (self.height() - self.thumbnail.height()) // 2
        
        painter.drawPixmap(x, y, self.thumbnail)
        
        if not self.visible_rect_ratio.isEmpty():
            # 비율을 픽셀 좌표로 변환
            thumb_rect = QRect(
                x + int(self.visible_rect_ratio.x() * self.thumbnail.width()),
                y + int(self.visible_rect_ratio.y() * self.thumbnail.height()),
                int(self.visible_rect_ratio.width() * self.thumbnail.width()),
                int(self.visible_rect_ratio.height() * self.thumbnail.height())
            )
            
            # 드래그 중일 때 색상 변경
            if self.is_dragging:
                pen_color = QColor(74, 158, 255, int(255 * opacity))
                pen = QPen(pen_color, 3)
                brush_color = QColor(74, 158, 255, int(80 * opacity))
                brush = QBrush(brush_color)
            else:
                pen_color = QColor(74, 158, 255, int(200 * opacity))
                pen = QPen(pen_color, 2)
                brush_color = QColor(74, 158, 255, int(50 * opacity))
                brush = QBrush(brush_color)
            
            painter.setPen(pen)
            painter.setBrush(brush)
            painter.drawRect(thumb_rect)


    # ============================================
    # 헬퍼 메소드 (내부)
    # ============================================

    def _get_thumbnail_rect(self) -> QRect:
        """썸네일 영역 계산"""
        if self.thumbnail is None or self.thumbnail.isNull():
            return QRect()
        
        x = (self.width() - self.thumbnail.width()) // 2
        y = (self.height() - self.thumbnail.height()) // 2
        return QRect(x, y, self.thumbnail.width(), self.thumbnail.height())
    

    def _pos_to_ratio(self, pos: QPoint) -> Tuple[float, float]: 
        """마우스 위치를 비율로 변환"""
        thumb_rect = self._get_thumbnail_rect()
        
        if thumb_rect.isEmpty() or thumb_rect.width() == 0 or thumb_rect.height() == 0:
            debug_print(f"[WARN] 썸네일 영역이 유효하지 않음 - 중앙 반환")
            return 0.5, 0.5 
        
        # 클릭 위치를 썸네일 기준으로 변환
        click_x = pos.x() - thumb_rect.x()
        click_y = pos.y() - thumb_rect.y()
        
        # 범위 제한 (0 ~ thumbnail 크기)
        click_x = max(0, min(click_x, thumb_rect.width()))
        click_y = max(0, min(click_y, thumb_rect.height()))
        
        ratio_x = click_x / thumb_rect.width() if thumb_rect.width() > 0 else 0.5
        ratio_y = click_y / thumb_rect.height() if thumb_rect.height() > 0 else 0.5
        
        # 0.0 ~ 1.0 범위로 클램핑
        ratio_x = max(0.0, min(1.0, ratio_x))
        ratio_y = max(0.0, min(1.0, ratio_y))
        
        return ratio_x, ratio_y
    

    # ============================================
    # 마우스 이벤트
    # ============================================

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """미니맵 클릭/드래그 시작"""
        if self.thumbnail is None or event.button() != Qt.MouseButton.LeftButton:
            return
        
        thumb_rect = self._get_thumbnail_rect()
        
        if thumb_rect.isEmpty():
            return
        
        # 썸네일 영역 내부인지 확인
        if thumb_rect.contains(event.pos()):
            # 드래그 시작
            self.is_dragging = True
            self.drag_start_pos = event.pos()
            
            # 커서 변경
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            
            # 클릭 위치로 즉시 이동
            ratio_x, ratio_y = self._pos_to_ratio(event.pos())
            self.position_clicked.emit(ratio_x, ratio_y)
            
            self.update()
            event.accept()
    

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """마우스 이동 (드래그 중)"""
        if self.thumbnail is None:
            return
        
        thumb_rect = self._get_thumbnail_rect()
        
        if thumb_rect.isEmpty():
            return
        
        # 커서 변경 (썸네일 위에서만)
        if not self.is_dragging:
            if thumb_rect.contains(event.pos()):
                self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        
        # 드래그 중일 때만 위치 업데이트
        if self.is_dragging:
            # 위치를 비율로 변환
            ratio_x, ratio_y = self._pos_to_ratio(event.pos())
            
            # 시그널 발생 (실시간 이동)
            self.position_clicked.emit(ratio_x, ratio_y)
            
            self.update()
            event.accept()
    

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """마우스 버튼 릴리즈 (드래그 종료)"""
        if event.button() == Qt.MouseButton.LeftButton and self.is_dragging:
            self.is_dragging = False
            
            # 커서 복원
            thumb_rect = self._get_thumbnail_rect()
            
            if not thumb_rect.isEmpty() and thumb_rect.contains(event.pos()):
                self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            
            self.update()
            event.accept()
    

    def leaveEvent(self, event) -> None:
        """마우스가 위젯을 벗어날 때"""
        if not self.is_dragging:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        super().leaveEvent(event)
