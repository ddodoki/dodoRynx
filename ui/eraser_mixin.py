# -*- coding: utf-8 -*-
# ui/eraser_mixin.py

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPen, QBrush
from PySide6.QtWidgets import QGraphicsRectItem
from PySide6.QtWidgets import (
    QColorDialog, QHBoxLayout, QLabel,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

from utils.debug import debug_print
from utils.lang_manager import t

if TYPE_CHECKING:
    pass

_SS = """
QWidget#eraser_overlay {
    background: rgba(18, 18, 18, 220);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 7px;
}
QLabel#sec {
    color: #555; font-size: 9px; font-weight: 700;
    letter-spacing: 0.5px; background: transparent;
}
QLabel {
    color: #aaa; font-size: 11px; background: transparent;
}
QLabel#val {
    color: #fff; font-size: 11px; font-weight: 700;
    min-width: 38px; background: transparent;
}
QSlider::groove:horizontal {
    background: #2e2e2e; height: 3px; border-radius: 1px;
}
QSlider::handle:horizontal {
    background: #ffffff; width: 11px; height: 11px;
    margin: -4px 0; border-radius: 6px; border: 1px solid #888;
}
QSlider::sub-page:horizontal { background: #4a9eff; border-radius: 1px; }
QPushButton#colbtn {
    min-width: 22px; max-width: 22px;
    min-height: 22px; max-height: 22px;
    border: 1px solid #555; border-radius: 3px;
}
QPushButton#colbtn:hover { border-color: #aaa; }
"""

def _make_color_btn(color: QColor) -> QPushButton:
    btn = QPushButton()
    btn.setObjectName("colbtn")
    _update_color_btn(btn, color)
    return btn


def _update_color_btn(btn: QPushButton, color: QColor) -> None:
    btn.setStyleSheet(
        f"QPushButton#colbtn {{ background:{color.name()}; "
        f"border:1px solid #555; border-radius:3px; }}"
        f"QPushButton#colbtn:hover {{ border-color:#aaa; }}"
    )


class EraserMixin:
    """흰색(또는 선택색) 사각 지우개 도구 Mixin."""

    def _init_eraser(self) -> None:
        self._eraser_size: int = 50
        self._eraser_color: QColor = QColor(255, 255, 255)
        self._eraser_drawing: bool = False
        self._eraser_cursor_item: Optional[QGraphicsRectItem] = None
        self._eraser_overlay: Optional[QWidget] = None
        self._eraser_overlay_slider: Optional[QSlider] = None
        self._eraser_overlay_val: Optional[QLabel] = None
        self._eraser_color_btn: Optional[QPushButton] = None


    def _show_eraser_overlay(self) -> None:
        if self._eraser_overlay is not None:
            self._eraser_overlay.setVisible(True)
            self._reposition_eraser_overlay()
            self._eraser_overlay.raise_()
            return

        vp = self.viewport()  # type: ignore[attr-defined]
        ov = QWidget(vp)
        ov.setObjectName("eraser_overlay")
        ov.setStyleSheet(_SS)
        ov.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ov.setCursor(Qt.CursorShape.ArrowCursor)

        root = QVBoxLayout(ov)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        sec = QLabel(t('eraser_mixin.section_title'))
        sec.setObjectName("sec")
        root.addWidget(sec)

        size_row = QHBoxLayout(); size_row.setSpacing(5)
        lbl_sz = QLabel("↔")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(4, 500)
        slider.setValue(self._eraser_size)
        slider.setFixedWidth(100)
        slider.setToolTip(t('eraser_mixin.size_tip'))
        val_lbl = QLabel(f"{self._eraser_size} px"); val_lbl.setObjectName("val")

        def _on_slider(v: int) -> None:
            self._eraser_size = v
            val_lbl.setText(f"{v} px")
            self._refresh_eraser_cursor_size()

        slider.valueChanged.connect(_on_slider)
        size_row.addWidget(lbl_sz)
        size_row.addWidget(slider)
        size_row.addWidget(val_lbl)
        root.addLayout(size_row)

        # 색상 행 — 레이블 없이 프리셋 + 커스텀 버튼만
        col_row = QHBoxLayout(); col_row.setSpacing(4)
        col_btn = _make_color_btn(self._eraser_color)
        col_btn.setToolTip(t('eraser_mixin.color_pick_tip'))

        def _pick_color() -> None:
            c = QColorDialog.getColor(
                self._eraser_color, ov,
                t('eraser_mixin.color_dialog_title')
            )
            if c.isValid():
                self._eraser_color = c
                _update_color_btn(col_btn, c)
                if self._eraser_cursor_item:
                    pen = QPen(QColor(
                        255 - c.red(), 255 - c.green(), 255 - c.blue(), 200
                    ), 0)
                    self._eraser_cursor_item.setPen(pen)

        col_btn.clicked.connect(_pick_color)

        for preset, tip_key in (
            (QColor(255, 255, 255), 'eraser_mixin.color_white'),
            (QColor(0, 0, 0),       'eraser_mixin.color_black'),
            (QColor(128, 128, 128), 'eraser_mixin.color_gray'),
        ):
            pb = _make_color_btn(preset)
            pb.setToolTip(t(tip_key))

            def _on_preset(_, c=preset) -> None:
                self._eraser_color = QColor(c)
                _update_color_btn(col_btn, self._eraser_color)

            pb.clicked.connect(_on_preset)
            col_row.addWidget(pb)

        col_row.addWidget(col_btn)
        col_row.addStretch()
        root.addLayout(col_row)

        self._eraser_overlay        = ov
        self._eraser_overlay_slider = slider
        self._eraser_overlay_val    = val_lbl
        self._eraser_color_btn      = col_btn

        ov.adjustSize()
        self._reposition_eraser_overlay()
        ov.show(); ov.raise_()


    def _hide_eraser_overlay(self) -> None:
        if self._eraser_overlay is not None:
            self._eraser_overlay.setVisible(False)


    def _reposition_eraser_overlay(self) -> None:
        ov = self._eraser_overlay
        if ov is None:
            return
        tb   = getattr(self, '_edit_toolbar', None)
        tb_h = tb.height() if tb is not None else 40
        ov.adjustSize()
        ov.move(10, tb_h + 10)


    def _sync_overlay_slider(self) -> None:
        if self._eraser_overlay_slider is not None:
            self._eraser_overlay_slider.blockSignals(True)
            self._eraser_overlay_slider.setValue(self._eraser_size)
            self._eraser_overlay_slider.blockSignals(False)
        if self._eraser_overlay_val is not None:
            self._eraser_overlay_val.setText(f"{self._eraser_size} px")


    def _on_eraser_tool_enter(self) -> None:
        self._eraser_drawing = False
        self.viewport().setCursor(Qt.CursorShape.BlankCursor)  # type: ignore[attr-defined]
        self._show_eraser_overlay()


    def _on_eraser_tool_leave(self) -> None:
        self._cleanup_eraser()


    def _eraser_draw_at(self, scene_pos: QPointF) -> None:
        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is None:
            return
        px = pi.pixmap()
        if px.isNull():
            return
        x, y   = int(scene_pos.x()), int(scene_pos.y())
        half   = self._eraser_size // 2
        from PySide6.QtCore import QRect
        from PySide6.QtGui  import QPainter
        painter = QPainter(px)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(
            QRect(x - half, y - half, self._eraser_size, self._eraser_size),
            self._eraser_color,
        )
        painter.end()
        pi.setPixmap(px)


    def _eraser_stroke_end(self) -> None:
        if self._editor is None:  # type: ignore[attr-defined]
            return
        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is None:
            return
        from core.qt_pil import qpixmap_to_pil
        self._editor._working = qpixmap_to_pil(pi.pixmap())  # type: ignore[attr-defined]
        debug_print("[Eraser] PIL 동기화 완료")


    def _update_eraser_cursor(self, scene_pos: QPointF) -> None:
        half = self._eraser_size / 2.0
        rect = QRectF(
            scene_pos.x() - half, scene_pos.y() - half,
            float(self._eraser_size), float(self._eraser_size),
        )
        c = self._eraser_color
        if self._eraser_cursor_item is None:
            item = QGraphicsRectItem(rect)
            item.setPen(QPen(QColor(0, 0, 0, 200), 0))
            item.setBrush(QBrush(c))
            item.setZValue(200)
            self.graphics_scene.addItem(item)  # type: ignore[attr-defined]
            self._eraser_cursor_item = item
        else:
            self._eraser_cursor_item.setRect(rect)
            self._eraser_cursor_item.setBrush(QBrush(c))


    def _refresh_eraser_cursor_size(self) -> None:
        item = self._eraser_cursor_item
        if item is None:
            return
        c    = item.rect().center()
        half = self._eraser_size / 2.0
        item.setRect(QRectF(c.x() - half, c.y() - half,
                            float(self._eraser_size), float(self._eraser_size)))


    def _remove_eraser_cursor(self) -> None:
        if self._eraser_cursor_item is not None:
            self.graphics_scene.removeItem(self._eraser_cursor_item)  # type: ignore[attr-defined]
            self._eraser_cursor_item = None


    def _handle_eraser_event(self, event: QMouseEvent, et: QEvent.Type) -> bool:
        if et == QEvent.Type.Wheel:
            from PySide6.QtGui import QWheelEvent
            if isinstance(event, QWheelEvent):
                delta = event.angleDelta().y()
                step  = 4 if abs(delta) >= 120 else 2
                self._eraser_size = max(4, min(200,
                    self._eraser_size + (step if delta > 0 else -step)))
                self._refresh_eraser_cursor_size()
                self._sync_overlay_slider()
            return True

        if et == QEvent.Type.MouseMove:
            sp = self.mapToScene(event.pos().x(), event.pos().y())  # type: ignore[attr-defined]
            self._update_eraser_cursor(sp)
            if self._eraser_drawing and (event.buttons() & Qt.MouseButton.LeftButton):
                self._eraser_draw_at(sp)
            return True

        if et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._push_undo()  # type: ignore[attr-defined]
                self._eraser_drawing = True
                sp = self.mapToScene(event.pos().x(), event.pos().y())  # type: ignore[attr-defined]
                self._eraser_draw_at(sp)
            return True

        if et == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self._eraser_drawing = False
                self._eraser_stroke_end()
            return True

        if et == QEvent.Type.Leave:
            self._remove_eraser_cursor()
            return False

        return False


    def _cleanup_eraser(self) -> None:
        self._remove_eraser_cursor()
        self._hide_eraser_overlay()
        self._eraser_drawing = False
        self.viewport().unsetCursor()  # type: ignore[attr-defined]

