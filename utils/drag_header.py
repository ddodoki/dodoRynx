# -*- coding: utf-8 -*-
# utils/drag_header.py

from __future__ import annotations
from typing import Callable, Optional
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

_HDR_SS = """
QWidget#drag_header {
    background: rgba(28,28,28,255);
    border-bottom: 1px solid rgba(255,255,255,0.07);
    border-top-left-radius:  7px;
    border-top-right-radius: 7px;
}
"""


class DragHeader(QWidget):
    """재사용 가능한 드래그 헤더.

    panel 의 부모 위젯 경계 안에서 panel 을 이동시킨다.

    Args:
        panel:    이동시킬 QWidget (이 헤더의 부모이기도 함)
        title:    헤더에 표시할 제목 문자열
        icon:     타이틀 앞 아이콘 문자 (예: "◈")
        on_close: 닫기 버튼 콜백 — None 이면 닫기 버튼 없음
        on_drag:  드래그로 이동할 때마다 호출되는 콜백
        height:   헤더 고정 높이 (px)
    """

    def __init__(
        self,
        panel: QWidget,
        *,
        title: str = "",
        icon: str = "",
        on_close: Optional[Callable] = None,
        on_drag:  Optional[Callable] = None,
        height: int = 26,
    ) -> None:
        super().__init__(panel)
        self.setObjectName("drag_header")
        self.setFixedHeight(height)
        self.setStyleSheet(_HDR_SS)
        self._panel       = panel
        self._drag_offset: Optional[QPoint] = None
        self._on_drag     = on_drag

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 6, 0)
        lay.setSpacing(5)

        if icon:
            lbl_icon = QLabel(icon)
            lbl_icon.setStyleSheet(
                "color:#4a9eff; font-size:10px; background:transparent;"
            )
            lay.addWidget(lbl_icon)

        if title:
            lbl_title = QLabel(title)
            lbl_title.setStyleSheet(
                "color:#5e5e5e; font-size:9px; font-weight:700;"
                "letter-spacing:1.1px; background:transparent;"
            )
            lay.addWidget(lbl_title)

        lay.addStretch()

        if on_close is not None:
            btn = QPushButton("✕")
            btn.setFixedSize(18, 18)
            btn.setStyleSheet(
                "QPushButton{background:transparent;color:#5e5e5e;"
                "border:none;font-size:12px;padding:0;}"
                "QPushButton:hover{color:#e35b5b;}"
            )
            btn.clicked.connect(on_close)
            lay.addWidget(btn)

        self.setCursor(Qt.CursorShape.SizeAllCursor)

    # ── 드래그 ──────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = self._panel.mapFromGlobal(
                event.globalPosition().toPoint()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_offset:
            p = self._panel.parent()
            if p is None:
                return
            loc = p.mapFromGlobal(event.globalPosition().toPoint())  # type: ignore[attr-defined]
            nx = max(0, min(loc.x() - self._drag_offset.x(), p.width()  - self._panel.width()))   # type: ignore[attr-defined]
            ny = max(0, min(loc.y() - self._drag_offset.y(), p.height() - self._panel.height()))  # type: ignore[attr-defined]
            self._panel.move(nx, ny)
            if self._on_drag:
                self._on_drag()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)