# -*- coding: utf-8 -*-
# ui\viewer\minimap_mixin.py
"""
MiniMapMixin — 미니맵 기능 Mixin.

사용하는 클래스에서 반드시 다음이 정의되어 있어야 합니다:
  - 속성:  pixmap_item, current_pixmap, current_movie
  - 메서드: get_current_pixmap(), viewport(), mapToScene(),
            centerOn(), horizontalScrollBar(), verticalScrollBar()
"""

from PySide6.QtCore import QRectF, QTimer

from ui.viewer.minimap_widget import MiniMapWidget


class MiniMapMixin:

    MINIMAP_UPDATE_DELAY:  int = 50  
    MINIMAP_MARGIN_RIGHT:  int = 20  
    MINIMAP_MARGIN_BOTTOM: int = 20  
    
    # ============================================
    # 미니맵 초기화
    # ============================================

    def _init_minimap(self) -> None:
        """미니맵 관련 상태 초기화. __init__ 에서 호출."""
        self.minimap = MiniMapWidget(self)               # type: ignore[arg-type]
        self.minimap.position_clicked.connect(self._on_minimap_clicked)
        self._position_minimap()

        self.minimap_update_timer = QTimer(self)         # type: ignore[arg-type]
        self.minimap_update_timer.setSingleShot(True)
        self.minimap_update_timer.timeout.connect(self._update_minimap)

        self.horizontalScrollBar().valueChanged.connect(self._on_scrollbar_changed)  # type: ignore[attr-defined]
        self.verticalScrollBar().valueChanged.connect(self._on_scrollbar_changed)    # type: ignore[attr-defined]

    # ============================================
    # 미니맵 업데이트
    # ============================================

    def _update_minimap(self) -> None:
        """미니맵 업데이트 — 현재 보이는 영역을 비율로 계산해 표시."""
        if not self.pixmap_item:                         # type: ignore[attr-defined]
            self.minimap.hide()
            return

        # 정적 이미지 / 애니메이션 중 유효한 크기 결정
        if self.current_pixmap and not self.current_pixmap.isNull():  # type: ignore[attr-defined]
            image_width  = self.current_pixmap.width()   # type: ignore[attr-defined]
            image_height = self.current_pixmap.height()  # type: ignore[attr-defined]
        elif self.current_movie:                         # type: ignore[attr-defined]
            ref = self.current_movie.currentPixmap()     # type: ignore[attr-defined]
            if ref.isNull():
                self.minimap.hide()
                return
            image_width  = ref.width()
            image_height = ref.height()
        else:
            self.minimap.hide()
            return

        # 전체화면
        # if self.main_window and getattr(self.main_window, 'is_fullscreen', False):
        #     self.minimap.hide()
        #     return
        
        # 스크롤바 없으면 미니맵 불필요
        has_scrollbars = (
            self.horizontalScrollBar().isVisible() or    # type: ignore[attr-defined]
            self.verticalScrollBar().isVisible()         # type: ignore[attr-defined]
        )
        if not has_scrollbars or image_width == 0 or image_height == 0:
            self.minimap.hide()
            return

        # 현재 보이는 영역 → scene 좌표 → 비율 (0.0 ~ 1.0)
        visible = self.mapToScene(                       # type: ignore[attr-defined]
            self.viewport().rect()                       # type: ignore[attr-defined]
        ).boundingRect()

        rx = max(0.0, min(1.0,               visible.x()      / image_width))
        ry = max(0.0, min(1.0,               visible.y()      / image_height))
        rw = max(0.0, min(1.0 - rx,          visible.width()  / image_width))
        rh = max(0.0, min(1.0 - ry,          visible.height() / image_height))

        self._position_minimap()
        self.minimap.set_visible_rect(QRectF(rx, ry, rw, rh))
        self.minimap.show()
        self.minimap.raise_()


    def _on_minimap_clicked(self, ratio_x: float, ratio_y: float) -> None:
        """미니맵 클릭 → 해당 비율 위치를 뷰포트 중앙으로 이동."""
        px = self.get_current_pixmap()                   # type: ignore[attr-defined]
        if not px or px.isNull():
            return
        self.centerOn(                                   # type: ignore[attr-defined]
            ratio_x * px.width(),
            ratio_y * px.height(),
        )
        self._update_minimap()


    def _position_minimap(self) -> None:
        x = self.width()  - self.minimap.width()  - self.MINIMAP_MARGIN_RIGHT    # type: ignore[attr-defined]
        y = self.height() - self.minimap.height() - self.MINIMAP_MARGIN_BOTTOM   # type: ignore[attr-defined]
        self.minimap.move(x, y)


    def _on_scrollbar_changed(self, _value: int = 0) -> None:
        """스크롤바 값 변경 → 미니맵 지연 업데이트."""
        try:
            self.minimap_update_timer.start(self.MINIMAP_UPDATE_DELAY)
        except RuntimeError:
            pass
