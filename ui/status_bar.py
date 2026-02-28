# -*- coding: utf-8 -*-
# ui/status_bar.py

"""
상태바 모듈 - 완전 재구현 (멀티태스크 + FloatingToast + PulseGauge)

구성:
  SortMenuButton        : 파일 정렬 드롭업 버튼
  FloatingToast         : 완료 알림 애니메이션 위젯 (fade-up-out)
  ToastManager          : FloatingToast 생명주기·스태킹 관리
  PulseGaugeWidget      : Pulse(불확정) + Gauge(확정) 단일 위젯
  StatusProgressWidget  : 우측 고정 240px — TaskQueue + PulseGauge 통합
  PerfOverlayWidget     : 성능 정보 플로팅 라벨 (타이틀바 하단 우상단)
  AppStatusBar          : QStatusBar 래퍼 (위젯 생성·레이아웃)
  StatusBarController   : MainWindow ↔ AppStatusBar 브릿지 (로직 전담)
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen, QAction
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core.folder_navigator import SortOrder
from utils.debug import debug_print, error_print, info_print
from utils.lang_manager import t


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 상수 · 열거형
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TaskPriority(IntEnum):
    FILE_OP  = 0   # 최상위: 복사·이동·삭제
    SORT     = 1   # 정렬
    SCAN     = 2   # 폴더 스캔
    THUMB    = 3   # 썸네일 로딩 (백그라운드)


# 우선순위별 색상 (게이지 fill 색)
_PRIORITY_COLORS: dict[TaskPriority, tuple[str, str]] = {
    #                          fill_color   anim_color(pulse)
    TaskPriority.FILE_OP: ("#c0782a", "#e09040"),
    TaskPriority.SORT:    ("#2a7cc0", "#4a9eff"),
    TaskPriority.SCAN:    ("#2a9c6a", "#3ac88a"),
    TaskPriority.THUMB:   ("#7a5ab8", "#9a7ad8"),
}

def _get_task_labels() -> dict[TaskPriority, str]:
    """ 런타임 호출 — 임포트 시점 평가 금지 (LangManager 미초기화 방지)"""
    return {
        TaskPriority.FILE_OP: t('statusbar.task.file_op'),
        TaskPriority.SORT:    t('statusbar.task.sort'),
        TaskPriority.SCAN:    t('statusbar.task.scan'),
        TaskPriority.THUMB:   t('statusbar.task.thumb'),
    }

@dataclass
class TaskInfo:
    priority:  TaskPriority
    text:      str       = ""
    current:   int       = 0
    total:     int       = 0   # 0 = 불확정
    active:    bool      = True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SortMenuButton
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SortMenuButton(QPushButton):
    """
    파일 정렬 기준 드롭업 버튼.
    sort_requested(sort_type: str, reverse: bool) 시그널 방출.
    """

    sort_requested = Signal(str, bool)

    _STYLE = """
        QPushButton {
            color: #fff; font-size: 11px; font-weight: bold;
            padding: 5px 10px; background: #3b3b3b;
            border: 1px solid #555; border-radius: 3px;
        }
        QPushButton:hover   { background: #4b4b4b; border-color: #4a9eff; }
        QPushButton:pressed { background: #555; }
    """

    # 활성 항목: 텍스트 파란색 + 볼드, 체크 인디케이터 자체 아이콘 제거
    _MENU_STYLE = """
        QMenu {
            min-width: 180px;
            font-size: 12px;
            padding: 4px 0;
        }
        QMenu::item {
            padding: 6px 20px 6px 28px;
        }
        QMenu::item:selected {
            background: #4a9eff;
            color: #fff;
        }
        QMenu::item:checked {
            color: #4a9eff;
            font-weight: bold;
        }
        QMenu::indicator {
            width: 0px;
        }
        QMenu::separator {
            height: 1px;
            background: #444;
            margin: 3px 8px;
        }
    """

    # 버튼 텍스트용 약식 레이블
    _SHORT_LABELS: dict[tuple[str, bool], str] = {
        ("highlight",    False): "★",
        ("name",         False): "⇅",   # 기본값 — 원래 아이콘 유지
        ("name",         True):  "Az↓",
        ("created",      True):  "C↓",
        ("created",      False): "C↑",
        ("modified",     True):  "M↓",
        ("modified",     False): "M↑",
        ("size",         True):  "S↓",
        ("size",         False): "S↑",
        ("exif_date",    True):  "E↓",
        ("exif_date",    False): "E↑",
        ("camera_model", False): "📷",
    }

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("⇅", parent)
        self.setToolTip(t('statusbar.sort_btn_tooltip'))
        self.setStyleSheet(self._STYLE)
        self.setFixedWidth(34)

        # (sort_type, reverse) → QAction 참조 보관
        self._action_map: dict[tuple[str, bool], QAction] = {}

        self._menu = self._build_menu()
        self.clicked.connect(self._show_menu)

    def _build_menu(self) -> QMenu:
        from PySide6.QtGui import QAction  # 지역 임포트 (순환 방지)
        m = QMenu(self)
        m.setStyleSheet(self._MENU_STYLE)

        def _add(label: str, key: str, rev: bool) -> None:
            action: QAction = m.addAction(label)
            action.setCheckable(True)

            action.triggered.connect(
                lambda checked=False, k=key, r=rev: self.sort_requested.emit(k, r)
            )
            self._action_map[(key, rev)] = action

        _add(t('statusbar.sort_menu.highlight'),    "highlight",    False); m.addSeparator()
        _add(t('statusbar.sort_menu.name_asc'),     "name",         False)
        _add(t('statusbar.sort_menu.name_desc'),    "name",         True);  m.addSeparator()
        _add(t('statusbar.sort_menu.created_new'),  "created",      True)
        _add(t('statusbar.sort_menu.created_old'),  "created",      False); m.addSeparator()
        _add(t('statusbar.sort_menu.modified_new'), "modified",     True)
        _add(t('statusbar.sort_menu.modified_old'), "modified",     False); m.addSeparator()
        _add(t('statusbar.sort_menu.size_large'),   "size",         True)
        _add(t('statusbar.sort_menu.size_small'),   "size",         False); m.addSeparator()
        _add(t('statusbar.sort_menu.exif_new'),     "exif_date",    True)
        _add(t('statusbar.sort_menu.exif_old'),     "exif_date",    False)
        _add(t('statusbar.sort_menu.camera'),       "camera_model", False)

        self._apply_check("name", False)

        return m

    def _apply_check(self, sort_type: str, reverse: bool) -> None:
        """기존 체크 전부 해제 → 해당 항목만 체크."""
        for action in self._action_map.values():
            action.setChecked(False)
        target = self._action_map.get((sort_type, reverse))
        if target:
            target.setChecked(True)

    def update_active_sort(self, sort_type: str, reverse: bool) -> None:
        """
        외부 호출용 — 현재 적용된 정렬 옵션을 메뉴와 버튼 텍스트에 반영.
        정렬 완료 시, 폴더 변경(리셋) 시 호출.
        """
        self._apply_check(sort_type, reverse)
        label = self._SHORT_LABELS.get((sort_type, reverse), "⇅")
        self.setText(label)

    def _show_menu(self) -> None:
        pos = self.mapToGlobal(QPoint(0, 0))
        mh  = self._menu.sizeHint().height()
        self._menu.exec(QPoint(pos.x(), pos.y() - mh))


class ZoomMenuButton(QPushButton):
    """
    배율/맞춤 통합 드롭업 버튼.
    zoom_action_requested(action: str) 시그널 방출.
    action: "fit" | "actual" | "25" | "50" | "75" | "150" | "200"
    """

    zoom_action_requested = Signal(str)

    _BASE_STYLE = """
        QPushButton {{
            color: {color}; font-size: 11px; font-weight: bold;
            padding: 5px 10px; background: #3b3b3b;
            border: 1px solid #555; border-radius: 3px;
        }}
        QPushButton:hover   {{ background: #4b4b4b; border-color: {color}; }}
        QPushButton:pressed {{ background: #555; }}
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("🔍 100%", parent)
        self.setToolTip(t('statusbar.zoom_btn_tooltip'))
        self.setMinimumWidth(90)
        self._apply_style("#050505")
        self._menu = self._build_menu()
        self.clicked.connect(self._show_menu)

    def _apply_style(self, color: str) -> None:
        self.setStyleSheet(self._BASE_STYLE.format(color=color))

    def _build_menu(self) -> QMenu:
        m = QMenu(self)
        m.setStyleSheet("""
            QMenu {
                min-width: 180px;
                font-size: 12px;
                padding: 4px 0;
            }
            QMenu::item {
                padding: 6px 20px 6px 12px;
            }
            QMenu::item:selected {
                background: #4a9eff;
                color: #fff;
            }
            QMenu::separator {
                height: 1px;
                background: #444;
                margin: 3px 8px;
            }
        """)

        _add = lambda label, action: m.addAction(label).triggered.connect(
            lambda: self.zoom_action_requested.emit(action)
        )
        _add(t('statusbar.zoom_menu.fit'),    "fit")
        m.addSeparator()
        _add(t('statusbar.zoom_menu.actual'), "actual")
        return m

    def _show_menu(self) -> None:
        pos = self.mapToGlobal(QPoint(0, 0))
        mh  = self._menu.sizeHint().height()
        self._menu.exec(QPoint(pos.x(), pos.y() - mh))

    def update_zoom(self, zoom_factor: float) -> None:
        pct   = int(zoom_factor * 100)
        color = "#4a9eff" if pct == 100 else "#ffaa00"
        self.setText(f"🔍 {pct}%")
        self._apply_style(color)
        self.setToolTip(t('statusbar.zoom_current_tooltip', pct=pct))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FloatingToast  — 완료 알림 fade-up-out 위젯
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FloatingToast(QLabel):
    """
    완료 알림 토스트 위젯.

    생명주기:
      1. 생성 → fade-in  150ms
      2. 대기              1350ms
      3. fade-up-out       500ms  (위로 20px 이동하며 투명)
      4. deleteLater()
    """

    _STYLE = """
        QLabel {{
            color: #ffffff;
            font-size: 11px;
            font-weight: bold;
            padding: 4px 12px;
            background: rgba(40, 40, 40, 220);
            border: 1px solid {border};
            border-radius: 4px;
        }}
    """

    finished = Signal(object)   # self 전달 → ToastManager 에서 제거


    def __init__(
        self,
        text:   str,
        color:  str,     # 테두리·아이콘 강조색
        parent: QWidget,
    ) -> None:
        super().__init__(text, parent)
        self.setStyleSheet(self._STYLE.format(border=color))
        self.adjustSize()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # Opacity effect
        self._opacity_eff = QGraphicsOpacityEffect(self)
        self._opacity_eff.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_eff)

        self._color = color
        self._start_y = 0   # ToastManager 에서 설정

        self._run()


    def _run(self) -> None:
        # Phase 1: fade in
        self._anim_in = QPropertyAnimation(self._opacity_eff, b"opacity", self)
        self._anim_in.setDuration(150)
        self._anim_in.setStartValue(0.0)
        self._anim_in.setEndValue(1.0)
        self._anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim_in.start()

        # Phase 2: 대기 후 Phase 3
        # parent=self 로 설정 → deleteLater() 시 Qt가 자동 취소
        self._wait_timer = QTimer(self)
        self._wait_timer.setSingleShot(True)
        self._wait_timer.timeout.connect(self._start_fadeout)
        self._wait_timer.start(1500)


    def _start_fadeout(self) -> None:
        # Phase 3-A: fade out
        self._anim_out = QPropertyAnimation(self._opacity_eff, b"opacity", self)
        self._anim_out.setDuration(500)
        self._anim_out.setStartValue(1.0)
        self._anim_out.setEndValue(0.0)
        self._anim_out.setEasingCurve(QEasingCurve.Type.InCubic)

        # Phase 3-B: 위로 이동
        self._anim_move = QPropertyAnimation(self, b"pos", self)
        self._anim_move.setDuration(500)
        self._anim_move.setStartValue(self.pos())
        self._anim_move.setEndValue(self.pos() + QPoint(0, -20))
        self._anim_move.setEasingCurve(QEasingCurve.Type.InCubic)

        self._anim_out.finished.connect(self._on_done)
        self._anim_out.start()
        self._anim_move.start()


    def _on_done(self) -> None:
        self.finished.emit(self)
        self.deleteLater()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ToastManager  — FloatingToast 스태킹·위치 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ToastManager:
    """
    main_window 자식으로 FloatingToast 를 생성·배치한다.

    - 최대 3개 동시 표시 (초과 시 가장 오래된 것 즉시 제거)
    - 상태바 위쪽에 수직 스태킹
    - anchor_widget: 기준 위젯 (StatusProgressWidget)
    """

    MAX_TOASTS  = 3
    GAP         = 4    # 토스트 사이 여백 px
    BOTTOM_MARGIN = 4  # 상태바 상단과의 여백 px


    def __init__(self, main_window: QWidget, anchor_widget: QWidget) -> None:
        self._mw     = main_window
        self._anchor = anchor_widget
        self._active: list[FloatingToast] = []


    def show(self, text: str, priority: TaskPriority) -> None:
        """완료 토스트 발사."""
        fill, _ = _PRIORITY_COLORS[priority]

        # 최대 초과 시 가장 오래된 것 즉시 제거
        if len(self._active) >= self.MAX_TOASTS:
            oldest = self._active[0]
            self._active.remove(oldest)
            oldest.deleteLater()

        toast = FloatingToast(text, fill, self._mw)
        toast.finished.connect(self._on_toast_done)
        toast.show()
        self._active.append(toast)
        self._reposition()


    def _on_toast_done(self, toast: FloatingToast) -> None:
        if toast in self._active:
            self._active.remove(toast)
        self._reposition()


    def _reposition(self) -> None:
        """활성 토스트를 상태바 위쪽에 수직 스태킹 배치."""
        if not self._active:
            return

        # anchor 위젯의 전역 좌표 → main_window 기준 좌표
        anchor_global = self._anchor.mapToGlobal(QPoint(0, 0))
        anchor_local  = self._mw.mapFromGlobal(anchor_global)

        # 기준: anchor 좌측 정렬, 상태바 위에서부터 쌓아올림
        base_y = anchor_local.y() - self.BOTTOM_MARGIN
        base_x = anchor_local.x()

        # 아래에서 위로 쌓기 (active[-1] 이 맨 아래)
        for toast in reversed(self._active):
            th = toast.height() if toast.height() > 0 else 26
            base_y -= th
            toast.move(base_x, base_y)
            base_y -= self.GAP


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PulseGaugeWidget  — Pulse(불확정) + Gauge(확정) 단일 위젯
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PulseGaugeWidget(QWidget):
    """
    애니메이션 2종만 사용하는 최소 게이지 위젯.

    불확정 모드 (total=0):
        배경이 sin 곡선으로 opacity 0.30 ↔ 0.80 맥동 (Pulse)
        주기: 1200ms

    확정 모드 (total > 0):
        좌→우 직선 rect 게이지 채움 (Stripe 없음)
        애니메이션 타이머 정지

    공통:
        - 테두리 없음 (활성 시 fill_color 1px 테두리)
        - setVisible() 호출 없음 → QGraphicsOpacityEffect 로만 전환
          (레이아웃 절대 변경 없음)
    """

    _PULSE_PERIOD_MS = 1200    # 맥동 주기 ms
    _PULSE_MIN       = 0.30    # opacity 최솟값
    _PULSE_MAX       = 0.80    # opacity 최댓값
    _TIMER_INTERVAL  = 16      # ~60fps

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._text:      str   = ""
        self._current:   int   = 0
        self._total:     int   = 0
        self._elapsed_ms: int  = 0

        # 색상 (TaskPriority 에 따라 외부에서 설정)
        self._fill_color  = QColor("#c0782a")
        self._bg_color    = QColor("#1e1e1e")
        self._text_color  = QColor("#ffffff")

        # Pulse 타이머
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(self._TIMER_INTERVAL)
        self._pulse_timer.timeout.connect(self._tick)

        # Fade 전용 opacity effect (이 위젯 자체의 fade in/out)
        self._opacity_eff = QGraphicsOpacityEffect(self)
        self._opacity_eff.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_eff)

        # Fade 애니메이션
        self._fade_anim = QPropertyAnimation(self._opacity_eff, b"opacity", self)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # 현재 pulse opacity (paintEvent 에서 참조)
        self._pulse_opacity: float = 0.0

        # 추가: 취소 가능한 fade-out 전용 타이머
        self._fadeout_timer = QTimer(self)
        self._fadeout_timer.setSingleShot(True)
        self._fadeout_timer.timeout.connect(self._fade_out)

        self.setFixedHeight(26)
        self.setMinimumWidth(250)

    # ── 공개 API ──────────────────────────────────────────────

    def set_task(
        self,
        text:     str,
        current:  int,
        total:    int,
        priority: TaskPriority,
    ) -> None:
        """작업 정보 설정 및 위젯 활성화."""
        self._fadeout_timer.stop()   # 추가: 잔존 fade-out 예약 취소
        self._text    = text
        self._current = current
        self._total   = total
        fill, _ = _PRIORITY_COLORS[priority]
        self._fill_color = QColor(fill)

        if total == 0:
            # 불확정 → Pulse 시작
            self._elapsed_ms = 0
            self._pulse_opacity = self._PULSE_MAX   # ← 추가
            if not self._pulse_timer.isActive():
                self._pulse_timer.start()
        else:
            # 확정 → Pulse 정지
            self._pulse_timer.stop()
            self._pulse_opacity = self._PULSE_MAX

        self._fade_in()
        self.update()


    def update_progress(self, text: str, current: int, total: int) -> None:
        """진행값만 갱신 (fade 없이)."""
        self._text    = text
        self._current = current
        self._total   = total
        if total > 0 and self._pulse_timer.isActive():
            self._pulse_timer.stop()
            self._pulse_opacity = self._PULSE_MAX
        self.update()


    def finish(self, text: str, priority: TaskPriority) -> None:
        """완료: 게이지 100% → 텍스트 교체 → fade-out."""
        self._text    = text
        self._current = 1
        self._total   = 1
        fill, _ = _PRIORITY_COLORS[priority]
        self._fill_color = QColor(fill)
        self._pulse_timer.stop()
        self._pulse_opacity = self._PULSE_MAX
        self.update()
        self._fadeout_timer.start(600)


    def clear_immediate(self) -> None:
        """즉시 숨김 (다음 작업으로 전환 전 crossfade 용)."""
        self._fadeout_timer.stop()
        self._pulse_timer.stop()
        self._opacity_eff.setOpacity(0.0)

    # ── 내부 ──────────────────────────────────────────────────

    def _tick(self) -> None:
        self._elapsed_ms = (
            self._elapsed_ms + self._TIMER_INTERVAL
        ) % self._PULSE_PERIOD_MS

        # 변수명 phase로 변경 — lang_manager.t 음영화 방지
        phase = self._elapsed_ms / self._PULSE_PERIOD_MS          # 0.0 ~ 1.0
        v = (math.sin(phase * 2 * math.pi - math.pi / 2) + 1) / 2  # 0.0 ~ 1.0
        self._pulse_opacity = (
            self._PULSE_MIN + (self._PULSE_MAX - self._PULSE_MIN) * v
        )
        self.update()


    def _fade_in(self) -> None:
        self._fade_anim.stop()
        self._fade_anim.setDuration(200)
        self._fade_anim.setStartValue(self._opacity_eff.opacity())
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()


    def _fade_out(self) -> None:
        self._fade_anim.stop()
        self._fade_anim.setDuration(500)
        self._fade_anim.setStartValue(self._opacity_eff.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()


    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        r = 4

        # 배경
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._bg_color)
        painter.drawRoundedRect(0, 0, w, h, r, r)

        # 게이지 / Pulse
        if self._total > 0:
            ratio  = max(0.0, min(1.0, self._current / self._total))
            fill_w = int(w * ratio)
            if fill_w > 0:
                painter.setBrush(self._fill_color)
                painter.drawRoundedRect(0, 0, fill_w, h, r, r)
        else:
            # Pulse: fill_color 를 전체 width 에 opacity 맥동
            painter.setOpacity(self._pulse_opacity)
            painter.setBrush(self._fill_color)
            painter.drawRoundedRect(0, 0, w, h, r, r)
            painter.setOpacity(1.0)

        # 테두리 (활성 시만, fill_color 기반)
        tc = QColor(self._fill_color)
        tc.setAlpha(180)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(tc, 1))
        painter.drawRoundedRect(0, 0, w - 1, h - 1, r, r)

        # 텍스트
        painter.setPen(self._text_color)
        painter.setFont(self.font())
        text_rect = QRect(8, 0, w - 16, h)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._text,
        )
        painter.end()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# StatusProgressWidget  — 우측 고정 240px, TaskQueue 통합 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class StatusProgressWidget(QWidget):
    """
    상태바 우측 고정 위젯 (addPermanentWidget 으로 등록).

    - PulseGaugeWidget 1개 (항상 공간 점유, opacity 로만 전환)
    - TaskQueue: dict[TaskPriority, TaskInfo]
      가장 낮은 priority 값(높은 우선순위)의 active 태스크를 표시
    - 현재 표시 중인 태스크가 아닌 다른 태스크가 완료되면
      → ToastManager 로 완료 토스트 발사
    - 현재 표시 중인 태스크가 완료되면
      → gauge.finish() → fade-out → 다음 태스크 있으면 crossfade
    """

    _WIDTH = 260

    def __init__(self, main_window: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(self._WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(0)

        self._gauge = PulseGaugeWidget(self)
        layout.addWidget(self._gauge)

        self._queue: dict[TaskPriority, TaskInfo] = {}
        self._current_priority: Optional[TaskPriority] = None
        self._toast_mgr: Optional[ToastManager] = None   # connect() 로 주입

    def set_toast_manager(self, mgr: ToastManager) -> None:
        self._toast_mgr = mgr

    # ── 태스크 시작 ───────────────────────────────────────────

    def task_start(self, priority: TaskPriority, text: str, total: int = 0) -> None:
        """태스크 시작 등록."""
        self._queue[priority] = TaskInfo(
            priority=priority, text=text, current=0, total=total, active=True
        )
        self._refresh()

    # ── 태스크 진행 ───────────────────────────────────────────

    def task_progress(
        self, priority: TaskPriority, text: str, current: int, total: int
    ) -> None:
        """진행값 갱신."""
        if priority not in self._queue:
            return
        t = self._queue[priority]
        t.text    = text
        t.current = current
        t.total   = total

        # 현재 표시 중인 태스크만 즉시 갱신
        if priority == self._current_priority:
            self._gauge.update_progress(text, current, total)

    # ── 태스크 완료 ───────────────────────────────────────────

    def task_finish(
        self,
        priority:     TaskPriority,
        finish_text:  str,
        toast_text:   str,
    ) -> None:
        if priority not in self._queue:
            return
        self._queue[priority].active = False

        if priority == self._current_priority:
            self._gauge.finish(finish_text, priority)

            # 현재 TaskInfo 인스턴스를 캡처해서 전달
            finished_task = self._queue.get(priority)
            QTimer.singleShot(
                1500,
                lambda p=priority, ti=finished_task: self._next_task(p, ti)
            )
        else:
            if self._toast_mgr:
                self._toast_mgr.show(toast_text, priority)
            del self._queue[priority]

    # ── 내부 로직 ─────────────────────────────────────────────

    def _refresh(self) -> None:
        """큐에서 가장 높은 우선순위 active 태스크를 선택해 표시."""
        best = self._best_active()

        if best is None:
            # 모든 태스크 완료
            self._current_priority = None
            return

        if best.priority == self._current_priority:
            # 이미 표시 중 → 값만 갱신
            self._gauge.update_progress(best.text, best.current, best.total)
            return

        # 새 태스크로 전환 (crossfade)
        if self._current_priority is not None:
            self._gauge.clear_immediate()

        self._current_priority = best.priority
        self._gauge.set_task(best.text, best.current, best.total, best.priority)


    def _next_task(
        self,
        finished_priority: TaskPriority,
        finished_task: Optional[TaskInfo] = None,
    ) -> None:
        if finished_priority in self._queue:
            # 인스턴스가 교체됐으면 건드리지 않음 (새 태스크 보호)
            if finished_task is not None and self._queue[finished_priority] is not finished_task:
                return
            del self._queue[finished_priority]

        if self._current_priority != finished_priority:
            return

        self._current_priority = None
        best = self._best_active()
        if best:
            self._current_priority = best.priority
            self._gauge.set_task(best.text, best.current, best.total, best.priority)


    def _best_active(self) -> Optional[TaskInfo]:
        """active=True 중 가장 낮은 priority 값(= 가장 높은 우선순위) 반환."""
        actives = [t for t in self._queue.values() if t.active]
        return min(actives, key=lambda t: t.priority) if actives else None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PerfOverlayWidget  — 성능 정보 플로팅 라벨 (우상단)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PerfOverlayWidget(QLabel):
    """
    MainWindow 를 parent 로 가지는 플로팅 라벨.
    WA_TransparentForMouseEvents → 클릭 방해 없음.
    reposition() 으로 항상 우상단 유지.
    """

    _MARGIN_RIGHT = 8
    _MARGIN_TOP   = 4

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("""
            QLabel {
                color: #888;
                font-size: 10px;
                background: transparent;
                padding: 1px 6px;
            }
        """)
        self.setVisible(False)

    def reposition(self) -> None:
        p = self.parent()
        if not isinstance(p, QWidget):  # QObject → QWidget 타입 가드
            return
        self.adjustSize()
        x = p.width() - self.width() - self._MARGIN_RIGHT
        self.move(max(0, x), self._MARGIN_TOP)
        self.raise_()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AppStatusBar  — QStatusBar 래퍼 (위젯 생성·레이아웃 전담)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AppStatusBar:
    """
    QStatusBar 와 그 안의 모든 위젯을 생성·배치하는 팩토리 클래스.

    Public Attributes:
        statusbar              : QStatusBar
        status_message_label   : 임시 메시지 라벨
        status_message_timer   : 자동 숨김 타이머
        progress_label         : 파일 번호 라벨
        zoom_label             : 줌 수준 QPushButton
        fit_btn                : 창 맞춤 QPushButton
        sort_btn               : SortMenuButton
        rotate_left/right/reset/apply_btn
        open_file_btn          : 파일 열기 QPushButton
        progress_widget        : StatusProgressWidget (우측 고정)
        left_container         : 좌측 QWidget
    """

    _LABEL_STYLE = """
        QLabel {
            color: #fff; font-size: 11px; font-weight: bold;
            padding: 5px 10px; background: #3b3b3b;
            border: 1px solid #555; border-radius: 3px; min-height: 16px;
        }
    """
    _BTN_STYLE = """
        QPushButton {
            color: #fff; font-size: 11px; font-weight: bold;
            padding: 5px 10px; background: #3b3b3b;
            border: 1px solid #555; border-radius: 3px; min-height: 16px;
        }
        QPushButton:hover   { background: #4b4b4b; border-color: #4a9eff; }
        QPushButton:pressed { background: #555; }
    """
    _SEP_STYLE = "color: #555; font-size: 14px; padding: 0 1px;"

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent
        self._build()

        self.op_label    = QLabel() 
        self.thumb_label = QLabel()
        self.op_label.hide()
        self.thumb_label.hide()

    def _hide_status_message(self) -> None:
        """하위 호환: 상태 메시지 숨기기."""
        try:
            self.status_message_label.clear()
            self.status_message_label.setVisible(False)
            if self.status_message_timer.isActive():
                self.status_message_timer.stop()
        except Exception:
            pass

    def _build(self) -> None:
        self.statusbar = QStatusBar()
        self.statusbar.setStyleSheet("""
            QStatusBar { background: #2b2b2b; border-top: 1px solid #555; }
            QStatusBar::item { border: none; }
        """)
        self._build_message_timer()
        self._build_left_container()
        # 우측 고정 위젯 (StatusProgressWidget) 은 StatusBarController 에서 주입
        QTimer.singleShot(0, self._add_widgets)
        debug_print("AppStatusBar: 빌드 완료")

    def _build_message_timer(self) -> None:
        self.status_message_timer = QTimer()
        self.status_message_timer.setSingleShot(True)
        self.status_message_timer.timeout.connect(
            lambda: self.status_message_label.setVisible(False)
        )

    def _build_left_container(self) -> None:
        self.left_container = QWidget()
        lay = QHBoxLayout(self.left_container)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)

        # 줌
        self.zoom_btn = ZoomMenuButton()
        lay.addWidget(self.zoom_btn)
        lay.addWidget(self._sep())

        # 파일 번호
        self.progress_label = QLabel("📄 0 / 📦 0")
        self.progress_label.setStyleSheet(self._LABEL_STYLE)
        self.progress_label.setMinimumWidth(120)
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.progress_label)
        lay.addWidget(self._sep())

        # 파일 열기
        self.open_file_btn = QPushButton("📂")
        self.open_file_btn.setFixedWidth(34)
        self.open_file_btn.setToolTip(t('statusbar.open_file_tooltip'))
        self.open_file_btn.setStyleSheet(self._BTN_STYLE)
        lay.addWidget(self.open_file_btn)

        # 정렬
        self.sort_btn = SortMenuButton()
        lay.addWidget(self.sort_btn)

        # 편집 모드
        self.edit_mode_btn = QPushButton("🎨")
        self.edit_mode_btn.setFixedWidth(34)
        self.edit_mode_btn.setToolTip(t('statusbar.edit_mode_tooltip'))
        self.edit_mode_btn.setStyleSheet(self._BTN_STYLE)
        lay.addWidget(self.edit_mode_btn)
        lay.addWidget(self._sep())

        # 회전 4종
        self.rotate_left_btn  = QPushButton("↶")
        self.rotate_right_btn = QPushButton("↷")
        self.rotate_reset_btn = QPushButton("↺")
        self.rotate_apply_btn = QPushButton("✔")
        _tips = [
            t('statusbar.rotate_left_tooltip'),
            t('statusbar.rotate_right_tooltip'),
            t('statusbar.rotate_reset_tooltip'),
            t('statusbar.rotate_apply_tooltip'),
        ]
        for btn, tip in zip(
            (self.rotate_left_btn, self.rotate_right_btn,
             self.rotate_reset_btn, self.rotate_apply_btn), _tips
        ):
            btn.setFixedWidth(30)
            btn.setToolTip(tip)
            btn.setStyleSheet(self._BTN_STYLE)
            lay.addWidget(btn)

        lay.addWidget(self._sep())

        # 임시 메시지
        self.status_message_label = QLabel("")
        self.status_message_label.setStyleSheet("""
            QLabel {
                color: #4affb4; font-size: 11px; font-weight: bold;
                padding: 5px 10px; background: #2b4b3b;
                border: 1px solid #4affb4; border-radius: 3px; min-height: 16px;
            }
        """)
        self.status_message_label.setVisible(False)
        lay.addWidget(self.status_message_label)

        lay.addStretch()

    def _add_widgets(self) -> None:
        try:
            self.statusbar.addWidget(self.left_container, 1)
            debug_print("AppStatusBar: 위젯 배치 완료")
        except Exception as e:
            error_print(f"AppStatusBar._add_widgets: {e}")

    # ── 헬퍼 ──────────────────────────────────────────────────

    def _sep(self) -> QLabel:
        lbl = QLabel("|")
        lbl.setStyleSheet(self._SEP_STYLE)
        return lbl

    def show_message(self, message: str, duration: int = 2000) -> None:
        max_length = 50

        if len(message) > max_length:
            name, ext = os.path.splitext(message)
            cut_length = max_length - len(ext) - 3  # "..." 3자 확보

            # cut_length 음수 방어 — ext가 너무 길면 단순 절단
            if cut_length > 0:
                message = name[:cut_length] + "..." + ext
            else:
                message = message[:max_length - 3] + "..."

        self.status_message_label.setText(message)
        self.status_message_label.setVisible(True)
        self.status_message_timer.start(duration)

    def update_progress(self, current: int, total: int) -> None:
        self.progress_label.setText(f"📄 {current} / 📦 {total}")

    def update_zoom(self, zoom_factor: float) -> None:
        self.zoom_btn.update_zoom(zoom_factor)

    def set_visible(self, visible: bool) -> None:
        self.statusbar.setVisible(visible)

    def is_visible(self) -> bool:
        return self.statusbar.isVisible()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# StatusBarController  — MainWindow ↔ AppStatusBar 브릿지
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class StatusBarController:
    """
    MainWindow 의 모든 상태바 로직을 위임받는 컨트롤러.

    초기화 순서:
        ctrl = StatusBarController(main_window, status_bar)
        ctrl.connect_signals()
    """

    # 제거 — _on_sort_requested() 에서 t() 로 직접 조회

    def __init__(self, main_window, status_bar: AppStatusBar) -> None:
        self._mw = main_window
        self._sb = status_bar

        # ── StatusProgressWidget 생성 (상태바 우측 고정) ──────
        self.progress_widget = StatusProgressWidget(main_window, None)

        # ── ToastManager ─────────────────────────────────────
        self._toast_mgr = ToastManager(main_window, self.progress_widget)
        self.progress_widget.set_toast_manager(self._toast_mgr)

        # ── PerfOverlayWidget ─────────────────────────────────
        self._perf_overlay = PerfOverlayWidget(main_window)

        # progress_widget 을 상태바에 permanent 등록
        # (_add_widgets 보다 늦게 실행되도록 singleShot)
        QTimer.singleShot(50, self._register_permanent)
        #self._register_permanent()

    def _register_permanent(self) -> None:
        try:
            #self.progress_widget.show()
            self._sb.statusbar.addPermanentWidget(self.progress_widget)
            debug_print("StatusBarController: progress_widget permanent 등록 완료")
        except Exception as e:
            error_print(f"StatusBarController._register_permanent: {e}")

    # ── 시그널 연결 ────────────────────────────────────────────

    def connect_signals(self) -> None:
        mw, sb = self._mw, self._sb
        sb.zoom_btn.zoom_action_requested.connect(self._on_zoom_action)

        sb.open_file_btn.clicked.connect(mw._open_file_dialog)
        sb.sort_btn.sort_requested.connect(self._on_sort_requested)
        sb.rotate_left_btn.clicked.connect(mw._on_rotate_left)
        sb.rotate_right_btn.clicked.connect(mw._on_rotate_right)
        sb.rotate_reset_btn.clicked.connect(mw._on_rotate_reset)
        sb.rotate_apply_btn.clicked.connect(mw._on_rotate_apply)
        sb.edit_mode_btn.clicked.connect(mw.enter_edit_mode)

        # 폴더 변경 시 정렬 버튼 기본값으로 리셋
        mw.navigator.folder_changed.connect(self._on_folder_changed)

        # 오버레이 스케일 초기값 적용
        saved_scale = mw.config.get_overlay_scale()
        mw.overlay_widget.set_scale(saved_scale / 100.0)

        debug_print("StatusBarController: 시그널 연결 완료")


    def _on_folder_changed(self, folder) -> None:
        """폴더 변경 → 정렬 표시 기본값(이름순)으로 리셋."""
        self._sb.sort_btn.update_active_sort("name", False)


    def _on_zoom_action(self, action: str) -> None:
        """배율 메뉴 선택 처리."""
        iv = self._mw.image_viewer
        if action == "fit":
            iv.set_zoom_mode("fit")
        elif action == "actual":
            iv.set_zoom_mode("actual")
        else:
            try:
                iv.set_zoom(int(action) / 100.0)
            except (ValueError, AttributeError):
                pass
            

    # ── 상태바 토글 ────────────────────────────────────────────

    def toggle(self, visible: Optional[bool] = None) -> None:
        if visible is None:
            visible = not self._sb.is_visible()
        self._sb.set_visible(visible)
        self._mw.image_viewer.statusbar_visible = visible
        self._mw.config.set_ui_visibility("status_bar", visible)
        info_print(f"상태바: {'표시' if visible else '숨김'}")

    # ── 진행·줌 업데이트 ──────────────────────────────────────

    def update_progress(self) -> None:
        if not self._mw._current_file:
            return
        current, total = self._mw.navigator.get_progress()
        self._sb.update_progress(current, total)

    def show_message(self, message: str, duration: int = 2000) -> None:
        self._sb.show_message(message, duration)

    def on_zoom_changed(self, zoom_factor: float) -> None:
        self._sb.update_zoom(zoom_factor)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 정렬 (SORT  P1)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    _SORT_MAP = {
        "highlight":    "HIGHLIGHT",
        "name":         "NAME",
        "created":      "CREATED",
        "modified":     "MODIFIED",
        "size":         "SIZE",
        "exif_date":    "EXIF_DATE",
        "camera_model": "CAMERA_MODEL",
    }

    def _on_sort_requested(self, sort_type: str, reverse: bool) -> None:
        """정렬 버튼 클릭 → 비동기 정렬 시작"""
        if not self._mw.navigator.image_files:
            return

        sort_order = SortOrder(sort_type)
        sort_name  = t(f'statusbar.sort_name.{sort_type}') or sort_type
        direction  = t('statusbar.sort_desc') if reverse else t('statusbar.sort_asc')
        label      = f"{sort_name} {direction}"

        self.progress_widget.task_start(
            TaskPriority.SORT,
            t('statusbar.sort_progress', label=label),
            total=0,
        )

        mw = self._mw

        def on_sort_done() -> None:
            try:
                image_list    = mw.navigator.get_image_list()
                current_index = mw.navigator.current_index

                mw.cache_manager.set_image_list(image_list)
                mw.thumbnail_bar.reorder_for_sort(image_list, current_index)
                mw._load_current_image()
                mw._sync_highlight_state(force_full_sync=True)

                info_print(f"정렬 완료: {label}")
                self.progress_widget.task_finish(
                    TaskPriority.SORT,
                    finish_text=t('statusbar.sort_done', label=label),
                    toast_text=t('statusbar.sort_done', label=label),
                )
                # 정렬 완료 후 버튼 활성 표시 갱신
                self._sb.sort_btn.update_active_sort(sort_type, reverse)

            except Exception as e:
                error_print(f"on_sort_done 오류: {e}")
                self.progress_widget.task_finish(
                    TaskPriority.SORT,
                    finish_text=t('statusbar.sort_error'),
                    toast_text=t('statusbar.sort_error'),
                )

        mw.navigator.sort_files_async(sort_order, reverse, on_completed=on_sort_done)


    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 파일 작업 (FILE_OP  P0)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def on_file_op_started(self, text: str = "") -> None:
        text = text or t('statusbar.file_op_default')
        self.progress_widget.task_start(
            TaskPriority.FILE_OP, f"📁 {text}", total=0
        )

    def on_file_op_progress(self, text: str, current: int, total: int) -> None:
        if total > 0:
            display = t('statusbar.file_op_progress_total', text=text, current=current, total=total)
        else:
            display = t('statusbar.file_op_progress_count', text=text, count=current)
        self.progress_widget.task_progress(TaskPriority.FILE_OP, display, current, total)

    def on_file_op_finished(self, toast: str = "") -> None:
        self.progress_widget.task_finish(
            TaskPriority.FILE_OP,
            finish_text=t('statusbar.file_op_done'),
            toast_text=toast or t('statusbar.file_op_done_short'),
        )
    

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 폴더 스캔 (SCAN  P2)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def on_folder_scan_started(self) -> None:
        self.progress_widget.task_start(
            TaskPriority.SCAN, t('statusbar.scan_start'), total=0
        )

    def on_folder_scan_progress(self, current: int, total: int) -> None:
        if total > 0:
            text = t('statusbar.scan_progress_total', current=current, total=total)
        else:
            text = t('statusbar.scan_progress_count', count=current)
        self.progress_widget.task_progress(TaskPriority.SCAN, text, current, total)

    def on_folder_scan_completed(self, total: int) -> None:
        self.progress_widget.task_finish(
            TaskPriority.SCAN,
            finish_text=t('statusbar.scan_done_finish', total=total),
            toast_text=t('statusbar.scan_done_toast',   total=total),
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 썸네일 로딩 (THUMB  P3)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def on_thumb_load_started(self, total: int) -> None:
        self.progress_widget.task_start(
            TaskPriority.THUMB, t('statusbar.thumb_start', total=total), total=total
        )

    def on_thumb_load_progress(self, done: int, total: int) -> None:
        self.progress_widget.task_progress(
            TaskPriority.THUMB,
            t('statusbar.thumb_progress', done=done, total=total),
            done, total,
        )

    def on_thumb_load_finished(self, total: int) -> None:
        self.progress_widget.task_finish(
            TaskPriority.THUMB,
            finish_text=t('statusbar.thumb_done', total=total),
            toast_text=t('statusbar.thumb_done', total=total),
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 성능 오버레이
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def toggle_performance_overlay(self, visible: Optional[bool] = None) -> None:
        if visible is None:
            visible = not self._perf_overlay.isVisible()
        self._perf_overlay.setVisible(visible)
        self._mw.config.set_ui_visibility("perf_overlay", visible)
        if visible:
            self.reposition_perf_overlay()
        debug_print(f"성능 오버레이: {'ON' if visible else 'OFF'}")

    def reposition_perf_overlay(self) -> None:
        if self._perf_overlay.isVisible():
            self._perf_overlay.reposition()

    def update_performance_info(
        self,
        load_time_ms:  float,
        memory_mb:     float,
        cpu_usage:     float,
        cache_size:    int,
        hit_rate:      float,
        max_memory_mb: int,
    ) -> None:
        if not self._perf_overlay.isVisible():
            return
        lc = "#4caf50" if load_time_ms < 50  else "#ff9800" if load_time_ms < 150 else "#f44336"
        mp = memory_mb / max_memory_mb * 100 if max_memory_mb > 0 else 0
        mc = "#4caf50" if mp < 70 else "#ff9800" if mp < 90 else "#f44336"
        cc = "#4caf50" if cpu_usage < 50 else "#ff9800" if cpu_usage < 80 else "#f44336"
        rc = "#4caf50" if hit_rate > 80  else "#ff9800" if hit_rate > 50  else "#f44336"
        self._perf_overlay.setText(
            f'''<span style="color:{lc}">{load_time_ms:.0f}ms</span>  '''
            f'''<span style="color:{mc}">{memory_mb:.1f}MB</span>  '''
            f'''<span style="color:{cc}">CPU {cpu_usage:.1f}%</span>  '''
            f'''<span style="color:{rc}">{cache_size} ({hit_rate:.0f}%)</span>'''
        )
        self._perf_overlay.setTextFormat(Qt.TextFormat.RichText)
        self.reposition_perf_overlay()
        