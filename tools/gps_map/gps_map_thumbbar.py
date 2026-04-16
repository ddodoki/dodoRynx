# -*- coding: utf-8 -*-
# tools\gps_map\gps_map_thumbbar.py

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPixmap, QAction
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)
from PySide6.QtWidgets import QMenu

from tools.gps_map.gps_map_thumbs import (
    DISPLAY_THUMBBAR_SIZE,
    GpsMapPhoto,
    GpsThumbProvider,
)
from utils.lang_manager import t

_ITEM_W   = DISPLAY_THUMBBAR_SIZE + 4  
_ITEM_H   = DISPLAY_THUMBBAR_SIZE + 4  
_THUMB_GAP = 6                       
_SCROLL_H = _ITEM_H + _THUMB_GAP + 6  
_BAR_H    = _SCROLL_H + 8 + 22       
_CELL_STEP = _ITEM_W + 4

_ARROW_STYLE = """
    QPushButton {
        background: transparent;
        border: none;
        color: rgba(136, 136, 136, 0.8);
        font-size: 18px;
    }
    QPushButton:hover {
        color: #4a9eff;
        background: rgba(74, 158, 255, 0.10);
        border-radius: 4px;
    }
    QPushButton:pressed {
        color: #2a7ed3;
        background: rgba(74, 158, 255, 0.20);
    }
"""

_SCROLL_STYLE = """
    QScrollArea {
        background-color: #202020;
        border: none;
        border-top: 1px solid rgba(255, 255, 255, 0.06);
    }
    QScrollBar:horizontal {
        height: 6px;
        background: transparent;
        margin: 0px;
    }
    QScrollBar::handle:horizontal {
        background: rgba(255, 255, 255, 0.18);
        border-radius: 3px;
        min-width: 30px;
    }
    QScrollBar::handle:horizontal:hover {
        background: rgba(255, 255, 255, 0.30);
    }
    QScrollBar::handle:horizontal:pressed {
        background: rgba(74, 158, 255, 0.60);
    }
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal { width: 0px; }
    QScrollBar::add-page:horizontal,
    QScrollBar::sub-page:horizontal { background: none; }
"""


class _ThumbCell(QLabel):

    def __init__(
        self,
        filepath: str,
        filename: str,
        parent: QWidget,
        bar: "GpsMapThumbBar",
    ) -> None:
        super().__init__(parent)
        self._filepath = filepath
        self._filename = filename
        self._bar = bar
        self.setFixedSize(_ITEM_W, _ITEM_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip(filename)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._highlighted = False
        self._cluster_state: str = 'normal' 
        self._badge: Optional[QLabel] = None    
        self._apply_style()


    @property
    def filepath(self) -> str:
        return self._filepath


    @property
    def filename(self) -> str:
        return self._filename


    def set_pixmap(self, qimg: QImage) -> None:
        if qimg.isNull():
            return
        pix = QPixmap.fromImage(qimg).scaled(
            DISPLAY_THUMBBAR_SIZE, DISPLAY_THUMBBAR_SIZE,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if pix.width() > DISPLAY_THUMBBAR_SIZE or pix.height() > DISPLAY_THUMBBAR_SIZE:
            x = (pix.width()  - DISPLAY_THUMBBAR_SIZE) // 2
            y = (pix.height() - DISPLAY_THUMBBAR_SIZE) // 2
            pix = pix.copy(x, y, DISPLAY_THUMBBAR_SIZE, DISPLAY_THUMBBAR_SIZE)
        self.setPixmap(pix)
        if self._badge is not None and self._badge.isVisible():
            self._badge.raise_()


    def set_highlighted(self, v: bool) -> None:
        if self._highlighted == v:
            return
        self._highlighted = v
        self._apply_style()


    def set_cluster_state(self, state: str) -> None:
        """'normal' | 'member' | 'rep' """
        if self._cluster_state == state:
            return
        self._cluster_state = state
        self._apply_style()
        self._update_badge()


    def _update_badge(self) -> None:
        """rep 상태일 때 ★ 뱃지 오버레이 표시."""
        if self._cluster_state == 'rep':
            if self._badge is None:
                self._badge = QLabel("★", self)
                self._badge.setFixedSize(16, 16)
                self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._badge.setStyleSheet(
                    "QLabel{"
                    "background: rgba(0,0,0,0.70);"
                    "color: #ffd700;"
                    "font-size: 9px;"
                    "font-weight: bold;"
                    "border-radius: 8px;"
                    "border: none;"
                    "padding: 0px;}"
                )
            self._badge.move(self.width() - 18, 2)
            self._badge.show()
            self._badge.raise_()
        else:
            if self._badge is not None:
                self._badge.hide()


    def _apply_style(self) -> None:
        if self._highlighted:
            border_top = "#ff6b35"
            border_side = "rgba(255,107,53,0.45)"
            bg = "rgba(255,107,53,0.13)"
            hover_bg = "rgba(255,107,53,0.22)"       
            hover_border_top = "#ff8c5a"
        elif self._cluster_state == 'rep':
            border_top = "#ffd700"
            border_side = "rgba(255,215,0,0.45)"
            bg = "rgba(255,215,0,0.10)"
            hover_bg = "rgba(255,215,0,0.20)"    
            hover_border_top = "#ffe033"
        elif self._cluster_state == 'member':
            border_top = "#55aacc"
            border_side = "rgba(85,170,204,0.45)"
            bg = "rgba(85,170,204,0.10)"
            hover_bg = "rgba(85,170,204,0.20)"    
            hover_border_top = "#77ccee"
        else:
            border_top = "transparent"
            border_side = "#3c3c3c"
            bg = "#252525"
            hover_bg = "#2e2e2e"               
            hover_border_top = "rgba(255,255,255,0.12)"

        hover_extra = (
            "QLabel:hover{"
            f"border-top: 3px solid {hover_border_top};"
            f"border-left: 1px solid {border_side};"
            f"border-right: 1px solid {border_side};"
            f"border-bottom: 1px solid {border_side};"
            f"background: {hover_bg};}}"
        )

        self.setStyleSheet(
            "QLabel{"
            f"border-top: 3px solid {border_top};"
            f"border-left: 1px solid {border_side};"
            f"border-right: 1px solid {border_side};"
            f"border-bottom: 1px solid {border_side};"
            "border-radius: 3px;"
            f"background: {bg};"
            "padding: 1px;}"
            + hover_extra
        )
        self._update_badge()


    def enterEvent(self, ev) -> None:
        self._bar._emit_hovered(self._filepath)
        self._bar._update_info_strip(self._filepath, self._filename)
        super().enterEvent(ev)


    def leaveEvent(self, ev) -> None:
        self._bar._emit_hovered("")
        self._bar._restore_info_strip()
        super().leaveEvent(ev)


    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._bar._emit_activated(self._filepath)
        elif ev.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(ev.globalPosition().toPoint())
        super().mousePressEvent(ev)


    def _show_context_menu(self, global_pos) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #252525; color: #d0d0d0;
                border: 1px solid #444; border-radius: 4px;
                padding: 4px 0px; font-size: 11px;
            }
            QMenu::item { padding: 6px 20px 6px 12px; }
            QMenu::item:selected { background: #2a5a8a; color: #fff; }
            QMenu::item:disabled { color: #555; }
            QMenu::separator { height: 1px; background: #3a3a3a; margin: 3px 0; }
        """)

        act_open = QAction(t('gps_map.thumbbar.menu_open', filename=self.filename), self)
        act_open.triggered.connect(lambda: self._bar._emit_activated(self._filepath))
        menu.addAction(act_open)

        menu.addSeparator()

        in_cluster = self._filepath in self._bar._cluster_members
        is_rep = self._filepath == self._bar._cluster_rep

        act_rep = QAction(t('gps_map.thumbbar.menu_set_rep'),   self)
        act_rep.triggered.connect(lambda: self._bar._set_representative(self._filepath))
        act_rep.setEnabled(in_cluster and not is_rep) 
        menu.addAction(act_rep)

        act_clear = QAction(t('gps_map.thumbbar.menu_clear_rep'), self)
        act_clear.triggered.connect(lambda: self._bar._clear_representative(self._filepath))
        act_clear.setEnabled(is_rep) 
        menu.addAction(act_clear)

        menu.exec(global_pos)


class GpsMapThumbBar(QFrame):

    photo_activated = Signal(str)
    photo_hovered   = Signal(str)
    set_representative_requested = Signal(str)   
    clear_representative_requested = Signal(str)


    def __init__(self, provider: GpsThumbProvider, parent: QWidget) -> None:
        super().__init__(parent)
        self._provider = provider
        self._photos: List[GpsMapPhoto] = []
        self._cells: Dict[str, _ThumbCell] = {}
        self._current_fp: str = ""
        self._current_filename: str = ""
        self._cluster_members: set[str] = set()
        self._cluster_rep:     str      = ""

        self._scroll_pending_fp: str = ""
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(50)
        self._scroll_timer.timeout.connect(self._do_scroll_pending)
        
        self._fp_index: Dict[str, int] = {}

        self._provider.thumb_ready.connect(
            self._on_thumb_ready, Qt.ConnectionType.QueuedConnection
        )
        self._build_ui()

    # ── UI ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setFixedHeight(_BAR_H)
        self.setStyleSheet(
            "GpsMapThumbBar{"
            "background-color: #1c1c1c;"
            "border-top: 1px solid rgba(255,255,255,0.07);}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── info strip ───────────────────────────────────────
        self._info_strip = QLabel()
        self._info_strip.setFixedHeight(22)
        self._info_strip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._info_strip.setTextFormat(Qt.TextFormat.RichText)
        self._info_strip.setStyleSheet("""
            QLabel {
                background-color: #161616;
                color: rgba(150, 150, 150, 0.85);
                font-size: 11px;
                padding: 0px 12px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            }
        """)
        self._info_strip.setText(
            '<span style="color: rgba(120,120,120,0.6);">—</span>'
        )
        root.addWidget(self._info_strip)

        # ── 썸네일 행 ─────────────────────────────────────────
        thumb_row = QWidget()
        thumb_row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(thumb_row)
        row_layout.setContentsMargins(0, 4, 0, 4)
        row_layout.setSpacing(4)

        self._left_btn = QPushButton("‹")
        self._left_btn.setFixedSize(24, _ITEM_H)
        self._left_btn.setStyleSheet(_ARROW_STYLE)
        self._left_btn.clicked.connect(self._scroll_left)
        row_layout.addWidget(self._left_btn)

        self._scroll = QScrollArea()
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidgetResizable(False)
        self._scroll.setFixedHeight(_SCROLL_H)
        self._scroll.setStyleSheet(_SCROLL_STYLE)
        self._scroll.installEventFilter(self)
        self._scroll.viewport().installEventFilter(self)

        self._container = QWidget()
        self._container.setFixedHeight(_ITEM_H + _THUMB_GAP)  
        self._container.setStyleSheet("background: transparent;")
        self._layout = QHBoxLayout(self._container)
        self._layout.setContentsMargins(4, 0, 4, 0)
        self._layout.setSpacing(4)
        self._layout.addStretch()

        self._scroll.setWidget(self._container)
        row_layout.addWidget(self._scroll)

        self._right_btn = QPushButton("›")
        self._right_btn.setFixedSize(24, _ITEM_H)
        self._right_btn.setStyleSheet(_ARROW_STYLE)
        self._right_btn.clicked.connect(self._scroll_right)
        row_layout.addWidget(self._right_btn)

        root.addWidget(thumb_row)

    # ── 공개 API ────────────────────────────────────────────────

    def set_photos(self, photos: List[GpsMapPhoto], current_fp: str) -> None:
        self._cluster_members.clear()
        self._cluster_rep = ""
        self._photos = photos
        self._current_filename = ""

        fp_set = {p.filepath for p in photos}
        self._current_fp = current_fp if current_fp in fp_set else ""

        self._cells.clear()
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item is None:
                break
            w = item.widget()    
            if w is not None:
                w.deleteLater()   

        self._provider.clear_pending()

        for photo in photos:
            cell = _ThumbCell(photo.filepath, photo.filename, self._container, bar=self)
            cell.set_highlighted(photo.filepath == self._current_fp)
            if photo.filepath == self._current_fp:
                self._current_filename = photo.filename
            self._layout.insertWidget(self._layout.count() - 1, cell)
            self._cells[photo.filepath] = cell

        self._fp_index = {fp: i + 1 for i, fp in enumerate(self._cells)}

        self._update_container_width()
        self._provider.request_many([p.filepath for p in photos])
        self._restore_info_strip()
        if self._current_fp:
            self._scroll_to(self._current_fp)


    def set_current_file(self, filepath: str) -> None:
        old = self._current_fp
        self._current_fp = filepath
        if old in self._cells:
            self._cells[old].set_highlighted(False)
        if filepath in self._cells:
            cell = self._cells[filepath]
            cell.set_highlighted(True)
            self._current_filename = cell.filename
        self._restore_info_strip()
        self._scroll_to(filepath)


    def set_cluster_selection(self, member_fps: list[str], rep_fp: str) -> None:
        self._cluster_members = set(member_fps)
        self._cluster_rep     = rep_fp
        self._refresh()
        if rep_fp:
            self._scroll_to(rep_fp)       


    def clear_cluster_selection(self) -> None:
        self._cluster_members.clear()
        self._cluster_rep = ""
        self._refresh()

    # ── Info Strip ──────────────────────────────────────────────

    def _refresh(self) -> None:
        """클러스터 상태 변경 후 모든 셀 스타일 갱신."""
        for fp, cell in self._cells.items():
            if fp == self._cluster_rep:
                cell.set_cluster_state('rep')
            elif fp in self._cluster_members:
                cell.set_cluster_state('member')
            else:
                cell.set_cluster_state('normal')


    def _update_info_strip(self, filepath: str, filename: str) -> None:
        idx = self._fp_index.get(filepath, 0)  
        total = len(self._cells)
        self._info_strip.setText(
            f'<span style="color:#ccc">{filename}</span>'
            f'<span style="color:#666"> · {idx} / {total}</span>'
        )


    def _restore_info_strip(self) -> None:
        if self._current_fp and self._current_filename:
            idx = self._fp_index.get(self._current_fp, 0)
            total = len(self._cells)
            self._info_strip.setText(
                f'<span style="color:#ff6b35;font-weight:600">▶</span> '
                f'<span style="color:#ccc">{self._current_filename}</span>'
                f'<span style="color:#666"> · {idx} / {total}</span>'
            )
        else:
            self._info_strip.setText('<span style="color:#555">—</span>')

    # ── 스크롤 ──────────────────────────────────────────────────

    def _scroll_left(self) -> None:
        sb = self._scroll.horizontalScrollBar()
        sb.setValue(sb.value() - _CELL_STEP)


    def _scroll_right(self) -> None:
        sb = self._scroll.horizontalScrollBar()
        sb.setValue(sb.value() + _CELL_STEP)


    def eventFilter(self, obj, event) -> bool:
        """휠 → 가로 스크롤 변환 (메인 썸네일바와 동일)"""
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y()
            sb = self._scroll.horizontalScrollBar()
            scroll_amount = _ITEM_W // 2
            if delta > 0:
                sb.setValue(sb.value() - scroll_amount)
            else:
                sb.setValue(sb.value() + scroll_amount)
            return True
        return super().eventFilter(obj, event)


    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_container_width()


    def _update_container_width(self) -> None:
        n = len(self._cells)
        items_w = n * _ITEM_W + max(0, n - 1) * 4 + 8  
        vp_w = self._scroll.viewport().width()
        self._container.setFixedWidth(max(items_w, vp_w))


    def _scroll_to(self, filepath: str) -> None:
        if self._cells.get(filepath) is None:
            return
        self._scroll_pending_fp = filepath
        self._scroll_timer.start()  


    def _do_scroll_pending(self) -> None:
        filepath = self._scroll_pending_fp
        cell = self._cells.get(filepath)
        if cell is None:
            return
        sb = self._scroll.horizontalScrollBar()
        vw = self._scroll.viewport().width()
        target = max(0, cell.x() - (vw - cell.width()) // 2)
        sb.setValue(target)

    # ── 시그널 emit ─────────────────────────────────────────────

    @Slot(str, QImage, int)
    def _on_thumb_ready(self, filepath: str, qimg: QImage, generation: int) -> None:
        if generation != self._provider.generation:
            return
        cell = self._cells.get(filepath)
        if cell is None or qimg.isNull():
            return
        cell.set_pixmap(qimg)


    def _emit_activated(self, fp: str) -> None:
        self.photo_activated.emit(fp)


    def _emit_hovered(self, fp: str) -> None:
        self.photo_hovered.emit(fp)


    def _set_representative(self, filepath: str) -> None:
        self.set_representative_requested.emit(filepath)


    def _clear_representative(self, filepath: str) -> None:
        self.clear_representative_requested.emit(filepath)
        