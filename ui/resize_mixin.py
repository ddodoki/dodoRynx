# -*- coding: utf-8 -*-
# ui/resize_mixin.py
"""
이미지 리사이즈 도구 Mixin.
EditModeMixin 이 이 클래스를 상속한다.

의존:
self._editor : ImageEditor (EditModeMixin)
self._edit_toolbar : EditToolbar (EditModeMixin)
self._push_undo() : EditModeMixin
self._replace_pixmap_inplace() : ImageViewer
self._schedule_filter_preview() : EditModeMixin
self._on_edit_tool_changed() : EditModeMixin
self.viewport() : ImageViewer
self.pixmap_item : ImageViewer
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from utils.debug import debug_print
from utils.lang_manager import t

if TYPE_CHECKING:
    pass

_SS = """
QWidget#resize_overlay {
    background: rgba(20, 20, 20, 215);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 6px;
}
QLabel {
    color: #aaa; font-size: 10px; background: transparent; min-height: 0px;
}
QLabel#title {
    color: #ccc; font-size: 12px; font-weight: 700; letter-spacing: 0.5px;
}
QSpinBox {
    background: #222; color: #eee; border: 1px solid #404040;
    border-radius: 3px; font-size: 11px;
    min-height: 24px; max-height: 24px; min-width: 72px; padding: 0px 2px;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 14px; background: #2e2e2e; border: none;
}
QPushButton {
    background: #2a2a2a; color: #ccc;
    border: 1px solid #404040; border-radius: 3px;
    font-size: 16px; font-weight: 700;
    min-height: 26px; max-height: 26px;
    min-width: 32px; max-width: 32px;
    padding: 0px;
}
QPushButton:hover  { background: #363636; color: #fff; }
QPushButton:pressed{ background: #1e1e1e; }
QPushButton#apply  {
    background: #1a4a1a; color: #7ddd7d; border-color: #2a6e2a;
}
QPushButton#apply:hover { background: #215c21; color: #a0f0a0; }
QPushButton#lock {
    min-width: 26px; max-width: 26px; font-size: 13px;
}
QPushButton#lock:checked {
    color: #6ab4ff; border-color: #2a68b0; background: #1a3f6b;
}
QFrame[frameShape="4"] { color: rgba(255,255,255,0.08); }
"""


class ResizeMixin:
    """이미지 리사이즈 도구 Mixin."""

    def _init_resize(self) -> None:
        self._resize_overlay: Optional[QWidget] = None
        self._resize_spin_w: Optional[QSpinBox] = None
        self._resize_spin_h: Optional[QSpinBox] = None
        self._resize_locked: bool = True
        self._resize_ratio: float = 1.0
        self._resize_updating: bool = False


    def _on_resize_tool_enter(self) -> None:
        w, h = self._get_current_image_size()
        self._resize_ratio = float(w) / float(h) if h > 0 else 1.0
        self._resize_locked = True
        self._show_resize_overlay(w, h)


    def _on_resize_tool_leave(self) -> None:
        self._hide_resize_overlay()


    def _cleanup_resize(self) -> None:
        if self._resize_overlay is not None:
            self._resize_overlay.setVisible(False)
        debug_print("[Resize] 정리 완료")


    def _get_current_image_size(self) -> tuple[int, int]:
        ed = self._editor  # type: ignore[attr-defined]
        if ed is not None and getattr(ed, '_working', None) is not None:
            return ed._working.size
        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is not None:
            px: QPixmap = pi.pixmap()
            return px.width(), px.height()
        return 1, 1


    def _do_resize(self, w: int, h: int) -> None:
        ed = self._editor  # type: ignore[attr-defined]
        if ed is None:
            return
        self._push_undo()  # type: ignore[attr-defined]
        resized = ed.resize(w, h)
        self._replace_pixmap_inplace(resized)  # type: ignore[attr-defined]
        self._schedule_filter_preview(force=True)  # type: ignore[attr-defined]
        debug_print(f"[Resize] {w}×{h} 완료")


    def _show_resize_overlay(self, w: int, h: int) -> None:
        if self._resize_overlay is not None:
            self._update_resize_overlay_values(w, h)
            self._resize_overlay.setVisible(True)
            self._reposition_resize_overlay()
            self._resize_overlay.raise_()
            return

        vp = self.viewport()  # type: ignore[attr-defined]
        ov = QWidget(vp)
        ov.setObjectName("resize_overlay")
        ov.setStyleSheet(_SS)
        ov.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ov.setCursor(Qt.CursorShape.ArrowCursor)

        root = QVBoxLayout(ov)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(7)

        # 타이틀 (아이콘)
        title = QLabel(t('resize_mixin.title'))
        title.setObjectName("title")
        root.addWidget(title)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # W / 🔒 / H
        size_row = QHBoxLayout(); size_row.setSpacing(4)

        spin_w = QSpinBox(); spin_w.setRange(1, 32000); spin_w.setValue(w); spin_w.setSuffix(" px")
        btn_lock = QPushButton("🔒"); btn_lock.setObjectName("lock")
        btn_lock.setCheckable(True); btn_lock.setChecked(True)
        btn_lock.setToolTip(t('resize_mixin.lock_tip'))
        spin_h = QSpinBox(); spin_h.setRange(1, 32000); spin_h.setValue(h); spin_h.setSuffix(" px")

        size_row.addWidget(QLabel("W"))
        size_row.addWidget(spin_w)
        size_row.addWidget(btn_lock)
        size_row.addWidget(QLabel("H"))
        size_row.addWidget(spin_h)
        root.addLayout(size_row)

        # 적용 / 취소 (아이콘)
        btn_row = QHBoxLayout(); btn_row.setSpacing(5)
        btn_apply  = QPushButton("✔"); btn_apply.setObjectName("apply")
        btn_cancel = QPushButton("✕")
        btn_apply.setToolTip(t('resize_mixin.apply_tip'))
        btn_cancel.setToolTip(t('resize_mixin.cancel_tip'))
        btn_row.addStretch(1)
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)

        # ── 시그널 연결 ──────────────────────────────────────────────────
        def _on_lock(checked: bool) -> None:
            self._resize_locked = checked
            btn_lock.setText("🔒" if checked else "🔓")
            if checked:
                cw, ch = spin_w.value(), spin_h.value()
                self._resize_ratio = float(cw) / float(ch) if ch > 0 else 1.0

        def _on_w_changed(v: int) -> None:
            if not self._resize_locked or self._resize_updating:
                return
            self._resize_updating = True
            try:
                spin_h.blockSignals(True)
                spin_h.setValue(max(1, round(v / self._resize_ratio)))
                spin_h.blockSignals(False)
            finally:
                self._resize_updating = False

        def _on_h_changed(v: int) -> None:
            if not self._resize_locked or self._resize_updating:
                return
            self._resize_updating = True
            try:
                spin_w.blockSignals(True)
                spin_w.setValue(max(1, round(v * self._resize_ratio)))
                spin_w.blockSignals(False)
            finally:
                self._resize_updating = False

        def _on_apply() -> None:
            self._do_resize(spin_w.value(), spin_h.value())
            self._deactivate_resize_tool()

        def _on_cancel() -> None:
            self._deactivate_resize_tool()

        btn_lock.clicked.connect(_on_lock)
        spin_w.valueChanged.connect(_on_w_changed)
        spin_h.valueChanged.connect(_on_h_changed)
        btn_apply.clicked.connect(_on_apply)
        btn_cancel.clicked.connect(_on_cancel)

        self._resize_overlay = ov
        self._resize_spin_w  = spin_w
        self._resize_spin_h  = spin_h

        ov.adjustSize()
        self._reposition_resize_overlay()
        ov.show(); ov.raise_()


    def _hide_resize_overlay(self) -> None:
        if self._resize_overlay is not None:
            self._resize_overlay.setVisible(False)


    def _update_resize_overlay_values(self, w: int, h: int) -> None:
        if self._resize_spin_w and self._resize_spin_h:
            for sp in (self._resize_spin_w, self._resize_spin_h):
                sp.blockSignals(True)
            self._resize_spin_w.setValue(w)
            self._resize_spin_h.setValue(h)
            for sp in (self._resize_spin_w, self._resize_spin_h):
                sp.blockSignals(False)
        self._resize_ratio   = float(w) / float(h) if h > 0 else 1.0
        self._resize_updating = False


    def _reposition_resize_overlay(self) -> None:
        ov = self._resize_overlay
        if ov is None:
            return
        tb   = getattr(self, '_edit_toolbar', None)
        tb_h = tb.height() if tb is not None else 40
        ov.adjustSize()
        ov.move(10, tb_h + 10)


    def _deactivate_resize_tool(self) -> None:
        self._hide_resize_overlay()
        tb = getattr(self, '_edit_toolbar', None)
        if tb is not None and hasattr(tb, 'btn_resize'):
            tb.btn_resize.blockSignals(True)
            tb.btn_resize.setChecked(False)
            tb.btn_resize.blockSignals(False)
        self._on_edit_tool_changed('select')  # type: ignore[attr-defined]

