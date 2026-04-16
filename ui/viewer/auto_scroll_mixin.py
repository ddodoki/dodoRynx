# -*- coding: utf-8 -*-
# ui\viewer\auto_scroll_mixin.py

"""
AutoScrollMixin — 중클릭 자동 스크롤(오토 스크롤) 기능 Mixin.

사용하는 클래스에서 반드시 다음이 정의되어 있어야 합니다:
  - 메서드: horizontalScrollBar(), verticalScrollBar(),
            _update_cursor(), cursor(), mapFromGlobal()
  - 옵션:   minimap (_update_minimap 호출용, hasattr 가드로 보호됨)
"""

from PySide6.QtCore import QPoint, QTimer, Qt

from utils.debug import debug_print


class AutoScrollMixin:

    AUTO_SCROLL_INTERVAL: int = 16   # ms (~60 FPS)
    AUTO_SCROLL_SPEED: float = 0.2   # 픽셀/프레임 배율
    AUTO_SCROLL_DEAD_ZONE: int = 5   # 데드존 반경 (픽셀)

    # ============================================
    # 초기화
    # ============================================

    def _init_auto_scroll(self) -> None:
        """자동 스크롤 상태 초기화. __init__ 에서 호출."""
        self.auto_scroll_active: bool = False
        self.auto_scroll_origin: QPoint = QPoint()
        self.auto_scroll_timer = QTimer(self)               # type: ignore[arg-type]
        self.auto_scroll_timer.timeout.connect(self._auto_scroll)
        self.auto_scroll_timer.setInterval(self.AUTO_SCROLL_INTERVAL)

    # ============================================
    # 자동 스크롤 로직
    # ============================================

    def _auto_scroll(self) -> None:
        """자동 스크롤 틱 (타이머 콜백, ~60 FPS)."""
        if not self.auto_scroll_active:
            return

        current_pos = self.mapFromGlobal(self.cursor().pos())   # type: ignore[attr-defined]
        delta = current_pos - self.auto_scroll_origin

        scroll_x = int(delta.x() * self.AUTO_SCROLL_SPEED)
        scroll_y = int(delta.y() * self.AUTO_SCROLL_SPEED)

        if abs(delta.x()) < self.AUTO_SCROLL_DEAD_ZONE:
            scroll_x = 0
        if abs(delta.y()) < self.AUTO_SCROLL_DEAD_ZONE:
            scroll_y = 0

        if scroll_x != 0:
            hbar = self.horizontalScrollBar()                   # type: ignore[attr-defined]
            hbar.setValue(hbar.value() + scroll_x)
        if scroll_y != 0:
            vbar = self.verticalScrollBar()                     # type: ignore[attr-defined]
            vbar.setValue(vbar.value() + scroll_y)

        if hasattr(self, 'minimap') and (scroll_x != 0 or scroll_y != 0):
            self._update_minimap()                              # type: ignore[attr-defined]


    def _stop_auto_scroll(self) -> None:
        """자동 스크롤 종료."""
        self.auto_scroll_active = False
        self.auto_scroll_timer.stop()
        self._update_cursor()                                   # type: ignore[attr-defined]
        debug_print("[DEBUG] 자동 스크롤 종료")


    def _start_auto_scroll(self, origin: QPoint) -> None:
        """자동 스크롤 시작. mousePressEvent 에서 호출."""
        self.auto_scroll_active = True
        self.auto_scroll_origin = origin
        self.setCursor(Qt.CursorShape.SizeAllCursor)            # type: ignore[attr-defined]
        self.auto_scroll_timer.start()

