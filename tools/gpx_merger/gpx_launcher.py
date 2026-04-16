# -*- coding: utf-8 -*-
# tools\gpx_merger\gpx_launcher.py

"""
포토맵 메인 앱에서 GPX 유틸리티를 여는 진입점.

사용법 (gps_map_window.py 또는 메인 툴바):
    from tools.gpx_merger.gpx_launcher import open_gpx_merger
    open_gpx_merger(parent=self)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore    import Qt, Slot
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from .gpx_merger_window import GpxMergerWindow

_window: Optional[GpxMergerWindow] = None


def open_gpx_merger(parent: Optional[QWidget] = None) -> None:
    global _window
    # 무거운 WebEngine 의존 모듈을 첫 호출 시점까지 지연 로딩
    from .gpx_merger_window import GpxMergerWindow

    if _window is not None:
        try:
            if not _window.isVisible():
                _window.show()
            _window.raise_()
            _window.activateWindow()
            return
        except RuntimeError:
            _window = None

    _window = GpxMergerWindow(parent)
    _window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    _window.destroyed.connect(_on_closed)
    _window.show()


@Slot()
def _on_closed() -> None:
    global _window
    _window = None
