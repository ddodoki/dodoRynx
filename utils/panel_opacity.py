# -*- coding: utf-8 -*-
# utils/panel_opacity.py

from __future__ import annotations
from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


class _HoverFadeFilter(QObject):
    """Enter/Leave 이벤트로 투명도를 부드럽게 전환하는 이벤트 필터."""

    def __init__(
        self,
        widget: QWidget,
        idle: float,
        hover: float,
        duration_ms: int,
    ) -> None:
        super().__init__(widget)
        self._idle = idle
        self._hover = hover

        self._effect = QGraphicsOpacityEffect(widget)
        self._effect.setOpacity(idle)
        widget.setGraphicsEffect(self._effect)

        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(duration_ms)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        widget.installEventFilter(self)

    def _fade(self, target: float) -> None:
        if abs(self._effect.opacity() - target) < 0.01:
            return
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(target)
        self._anim.start()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        t = event.type()
        if t == QEvent.Type.Enter:
            self._fade(self._hover)
        elif t == QEvent.Type.Leave:
            # 자식 위젯 이동 시에도 Leave가 발생하므로
            # 실제로 패널 영역을 벗어났을 때만 fade-out
            w = self.parent()
            if isinstance(w, QWidget):
                if not w.rect().contains(w.mapFromGlobal(QCursor.pos())):
                    self._fade(self._idle)
        return False  # 이벤트 소비 안 함


def apply_hover_opacity(
    widget: QWidget,
    *,
    idle: float = 0.18,
    hover: float = 0.96,
    duration_ms: int = 200,
) -> None:
    """위젯에 hover 투명도 전환 효과를 적용한다.

    Args:
        idle:        마우스 없을 때 불투명도 (0.0 ~ 1.0)
        hover:       마우스 올렸을 때 불투명도
        duration_ms: 전환 애니메이션 시간 (ms)
    """
    _HoverFadeFilter(widget, idle, hover, duration_ms)