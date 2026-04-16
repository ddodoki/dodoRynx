# -*- coding: utf-8 -*-
# ui\viewer\zoom_mixin.py
"""
ZoomMixin — QGraphicsView 계열 줌 기능 Mixin.

사용하는 클래스에서 반드시 다음이 정의되어 있어야 합니다:
  - Signal:   zoom_changed = Signal(float)
  - 속성:     pixmap_item, current_pixmap, current_movie,
              current_image_id, config_manager
  - 메서드:   _update_cursor()
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QTimer, Signal

from utils.debug import debug_print, warning_print

class ZoomMixin:

    if TYPE_CHECKING:
        from PySide6.QtGui import QMovie, QPixmap, QTransform
        from PySide6.QtWidgets import QGraphicsPixmapItem, QWidget
        from utils.config_manager import ConfigManager

        pixmap_item:      Optional[QGraphicsPixmapItem]
        current_pixmap:   Optional[QPixmap]
        current_movie:    Optional[QMovie]
        current_image_id: int
        config_manager:   ConfigManager
        zoom_changed:     Signal

        def _update_cursor(self) -> None: ...
        def viewport(self) -> QWidget: ...       
        def resetTransform(self) -> None: ...
        def scale(self, sx: float, sy: float) -> None: ...
        def centerOn(self, *args: object) -> None: ...
        def transform(self) -> QTransform: ...    

    # ============================================
    # 줌 초기화
    # ============================================

    def _init_zoom_state(self) -> None:
        """줌 관련 상태 변수 초기화. __init__ 에서 호출."""
        self.zoom_mode: str = 'fit'
        self.zoom_factor: float = 1.0
        self.min_zoom: float = 0.1
        self.max_zoom: float = 10.0
        self._user_has_zoomed: bool = False

        self._suppress_fit_in_view: bool = False
        self._suppress_start_ms: float = 0.0
        self._zoom_intent_stack: list = []

        self.zoom_apply_timer = QTimer(self)           # type: ignore[arg-type]
        self.zoom_apply_timer.setSingleShot(True)
        self.zoom_apply_timer.timeout.connect(self._delayed_apply_zoom)

    # ============================================
    # 줌 모드 외부 제어
    # ============================================

    def set_zoom_mode(self, mode: str) -> None:

        debug_print(f"set_zoom_mode: {self.zoom_mode} → {mode}")
        self.zoom_mode = mode
        if mode == 'fit':
            self._user_has_zoomed = False
            self._fit_in_view()
        elif mode == 'actual':
            self._user_has_zoomed = True
            self._actual_size()
        elif mode == 'width':
            self._user_has_zoomed = True
            self._fit_width()
        self._update_cursor()


    def zoom_in(self) -> None:
        self._user_has_zoomed = True
        self.zoom_factor = min(self.zoom_factor * 1.2, self.max_zoom)
        self.zoom_mode = 'manual'
        self.resetTransform()                          # type: ignore[attr-defined]
        self.scale(self.zoom_factor, self.zoom_factor) # type: ignore[attr-defined]
        self._update_cursor()
        self._calculate_and_emit_zoom()
        debug_print(f"줌 인: {self.zoom_factor:.2f}, mode=manual")


    def zoom_out(self) -> None:
        new_factor = self.zoom_factor / 1.2

        if getattr(self, '_user_has_zoomed', False) and self.pixmap_item:
            vp = self.viewport().rect()                # type: ignore[attr-defined]
            pr = self.pixmap_item.boundingRect()
            if pr.width() > 0 and pr.height() > 0:
                fit_scale = min(vp.width() / pr.width(), vp.height() / pr.height())
                already_at_floor = (self.zoom_factor <= self.min_zoom)
                should_fit = (new_factor <= fit_scale) or already_at_floor
                if should_fit:
                    self._user_has_zoomed = False
                    self.zoom_mode = 'fit'
                    self._fit_in_view()
                    self._update_cursor()
                    debug_print(
                        f"줌 아웃: fit 복귀 "
                        f"(new={new_factor:.3f}, fit_scale={fit_scale:.3f}, "
                        f"at_floor={already_at_floor})"
                    )
                    return

        ABSOLUTE_MIN = 0.01
        self.zoom_factor = max(new_factor, ABSOLUTE_MIN)
        self.zoom_mode = 'manual'
        self.resetTransform()                          # type: ignore[attr-defined]
        self.scale(self.zoom_factor, self.zoom_factor) # type: ignore[attr-defined]
        self._update_cursor()
        self._calculate_and_emit_zoom()
        debug_print(f"줌 아웃: {self.zoom_factor:.3f}, mode=manual")


    def _on_fit_key(self) -> None:
        """F 키 — fit 모드 명시적 전환 (manual 상태 리셋)"""
        self._user_has_zoomed = False
        self.zoom_mode = 'fit'
        self._fit_in_view()


    def set_fullscreen_mode(self, enabled: bool) -> None:
        self._fullscreen_mode = enabled

    # ============================================
    # 자동 줌 모드 결정
    # ============================================

    def _auto_zoom_mode(self, img_w: int, img_h: int) -> str:
        use_auto = self.config_manager.get('viewer.auto_zoom_on_small', True)
        if not use_auto:
            return 'fit'
        if self.zoom_mode == 'manual':
            debug_print("_auto_zoom_mode: 수동 줌 상태 → manual 유지")
            return 'manual'
        if getattr(self, '_fullscreen_mode', False):
            debug_print(
                f"_auto_zoom_mode: {img_w}×{img_h} / "
                f"{self.viewport().width()}×{self.viewport().height()} → fit (풀스크린)" # type: ignore[attr-defined]
            )
            return 'fit'
        if not self._user_has_zoomed:
            debug_print(
                f"_auto_zoom_mode: {img_w}×{img_h} / "
                f"{self.viewport().width()}×{self.viewport().height()} → fit (미줌 상태)" # type: ignore[attr-defined]
            )
            return 'fit'

        vp_w = self.viewport().width()  # type: ignore[attr-defined]
        vp_h = self.viewport().height() # type: ignore[attr-defined]
        use_diag = self.config_manager.get('viewer.auto_zoom_diagonal', False)
        if use_diag:
            fits = math.hypot(img_w, img_h) <= math.hypot(vp_w, vp_h) * 0.95
        else:
            fits = (img_w <= vp_w * 0.95 and img_h <= vp_h * 0.95)
        mode = 'actual' if fits else 'fit'
        debug_print(
            f"_auto_zoom_mode: {img_w}×{img_h} / {vp_w}×{vp_h} → {mode}"
            + (" (대각선)" if use_diag else " (가로·세로)")
        )
        return mode

    # ============================================
    # Intent 스택
    # ============================================

    def _consume_zoom_intent(self) -> Optional[str]:
        """줌 intent 스택에서 꺼냄. 없으면 None 반환."""
        if self._zoom_intent_stack:
            return self._zoom_intent_stack.pop()
        return None

    # ============================================
    # 줌 모드 적용 (핵심 3종)
    # ============================================

    def _fit_in_view(self) -> None:
        if getattr(self, '_suppress_fit_in_view', False):
            elapsed = time.monotonic() - getattr(self, '_suppress_start_ms', 0.0)
            if elapsed < 0.15:
                debug_print("_fit_in_view: suppress 중 → 건너뜀")
                return
            debug_print("_fit_in_view: suppress 타임아웃(150ms) → 강제 해제")
            self._suppress_fit_in_view = False

        if not self.pixmap_item:
            return

        vp_rect   = self.viewport().rect()              # type: ignore[attr-defined]
        px_rect   = self.pixmap_item.boundingRect()
        if px_rect.width() == 0 or px_rect.height() == 0:
            return

        scale = min(vp_rect.width() / px_rect.width(),
                    vp_rect.height() / px_rect.height())
        if getattr(self, '_user_has_zoomed', False):
            scale = max(scale, self.min_zoom)

        self.resetTransform()                           # type: ignore[attr-defined]
        self.scale(scale, scale)                        # type: ignore[attr-defined]
        self.zoom_factor = scale
        self.centerOn(self.pixmap_item)                 # type: ignore[attr-defined]
        self._update_cursor()
        self._calculate_and_emit_zoom()
        debug_print(f"Fit: scale={scale:.2f}")


    def _actual_size(self) -> None:
        """실제 크기 (1:1)"""
        self.resetTransform()                           # type: ignore[attr-defined]
        self.scale(1.0, 1.0)                            # type: ignore[attr-defined]
        self.zoom_factor = 1.0
        if self.pixmap_item:
            self.centerOn(self.pixmap_item)             # type: ignore[attr-defined]
        self._update_cursor()
        self._calculate_and_emit_zoom()
        debug_print("Actual Size: 1.0")


    def _fit_width(self) -> None:
        """이미지 폭을 뷰포트에 맞춤"""
        if not self.pixmap_item:
            return
        vp_rect = self.viewport().rect()                # type: ignore[attr-defined]
        px_rect = self.pixmap_item.boundingRect()
        if px_rect.width() == 0:
            return
        scale = max(vp_rect.width() / px_rect.width(), self.min_zoom)
        self.resetTransform()                           # type: ignore[attr-defined]
        self.scale(scale, scale)                        # type: ignore[attr-defined]
        self.zoom_factor = scale
        self.centerOn(px_rect.center().x(), 0)          # type: ignore[attr-defined]
        self._update_cursor()
        self._calculate_and_emit_zoom()
        debug_print(f"Fit Width: scale={scale:.2f}")


    def _apply_zoom_mode(self) -> None:
        """현재 zoom_mode 즉시 적용"""
        if not self.pixmap_item:
            return
        if self.zoom_mode == 'fit':
            self._fit_in_view()
        elif self.zoom_mode == 'actual':
            self._actual_size()
        elif self.zoom_mode == 'width':
            self._fit_width()
        self._update_cursor()
        debug_print(f"줌 모드 즉시 적용: {self.zoom_mode}, factor={self.zoom_factor:.2f}")


    def _delayed_apply_zoom(self) -> None:
        """딜레이 후 줌 적용 (2단계 렌더링 완료 후 타이머 콜백)"""
        self._suppress_fit_in_view = False
        self._suppress_start_ms = 0.0

        if not self.pixmap_item:
            warning_print("[WARN] pixmap_item 없음, 줌 적용 불가")
            return

        # 이미지 ID 검증 (stale 타이머 방지)
        if self.current_pixmap:
            if id(self.current_pixmap) != self.current_image_id:
                warning_print("줌 타이머 무효 (이미지 변경됨)")
                return
        elif self.current_movie:
            if id(self.current_movie) != self.current_image_id:
                warning_print("줌 타이머 무효 (애니메이션 변경됨)")
                return

        intent = self._consume_zoom_intent() or self.zoom_mode
        if intent == 'fit' and getattr(self, '_user_has_zoomed', False):
            debug_print("줌 적용 스킵: _user_has_zoomed=True (actual 모드 유지)")
            return

        debug_print(f"줌 적용: intent={intent}, ID={self.current_image_id}")
        if intent == 'actual':
            self._actual_size()
        elif intent == 'fit':
            self._fit_in_view()
        elif intent == 'width':
            self._fit_width()
        elif intent == 'manual':
            self.resetTransform()                       # type: ignore[attr-defined]
            self.scale(self.zoom_factor, self.zoom_factor) # type: ignore[attr-defined]
            self._calculate_and_emit_zoom()
        self._update_cursor()
        debug_print(f"줌 적용 완료: zoom_factor={self.zoom_factor:.2f}")


    def _calculate_and_emit_zoom(self) -> None:
        """Transform 행렬에서 줌 레벨 계산 후 zoom_changed 시그널 발생"""
        if not self.pixmap_item:
            return
        transform  = self.transform()                            # type: ignore[attr-defined]
        actual_zoom = (transform .m11() + transform .m22()) / 2.0
        self.zoom_factor = actual_zoom
        self.zoom_changed.emit(actual_zoom)             # type: ignore[attr-defined]
        debug_print(f"줌 레벨: {actual_zoom:.3f}x ({actual_zoom * 100:.1f}%)")
