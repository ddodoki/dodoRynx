# -*- coding: utf-8 -*-
# ui\editor\filter_mixin.py

"""
FilterMixin — 편집 모드 필터 패널, 프리뷰, 파이프라인.

EditModeMixin이 다중 상속으로 사용하며,
_init_filter() 를 _init_edit_mode() 에서 호출해야 한다.
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Optional

from PIL import Image

from PySide6.QtCore import QTimer

from core.image_filters import BasicParams, apply_basic, apply_pro, apply_style
from core.qt_pil import pil_to_qpixmap

from ui.editor.edit_filter_panel import EditFilterPanel, PANEL_TOTAL_H, PANEL_W
from ui.editor.edit_toolbar import EditToolbar

from utils.debug import debug_print, error_print

if TYPE_CHECKING:
    from core.image_editor import ImageEditor
    from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene


class FilterMixin:
    """필터 패널 표시/위치, 슬라이더 콜백, 프리뷰 스케줄링, 동기 적용."""

    # ------------------------------------------------------------------
    # 초기화
    # ------------------------------------------------------------------

    def _init_filter(self) -> None:
        """_init_edit_mode() 에서 호출."""
        self._filter_basic: BasicParams         = BasicParams()
        self._filter_style_name: str            = "none"
        self._filter_style_intensity: int       = 0
        self._filter_pro_name: str              = "none"
        self._filter_pro_intensity: int         = 0
        self._filter_preview_pending: bool      = False
        self._filter_timer_active: bool         = False
        self._filter_panel_widget: Optional[EditFilterPanel] = None

    def _reset_filter_state(self) -> None:
        """enter_edit_mode 재진입 / _edit_cancel 시 상태 일괄 초기화."""
        self._filter_basic                = BasicParams()
        self._filter_style_name           = "none"
        self._filter_style_intensity      = 0
        self._filter_pro_name             = "none"
        self._filter_pro_intensity        = 0
        self._filter_preview_pending      = False
        self._filter_timer_active         = False

    # ------------------------------------------------------------------
    # 패널 — 생성 / 위치 / 토글
    # ------------------------------------------------------------------

    def _ensure_filter_panel(self) -> None:
        if getattr(self, '_filter_panel_widget', None) is not None:
            return
        panel = EditFilterPanel(self)  # type: ignore[arg-type]
        panel.setVisible(False)
        panel.basic_changed.connect(self._on_filter_basic_changed)
        panel.style_changed.connect(self._on_filter_style_changed)
        panel.pro_changed.connect(self._on_filter_pro_changed)
        panel.reset_requested.connect(self._on_filter_reset_requested)
        panel.panel_closed.connect(self._on_filter_panel_closed)
        self._filter_panel_widget = panel
        debug_print("EditFilterPanel 생성 완료")

    def _position_filter_panel(self) -> None:
        fp = getattr(self, '_filter_panel_widget', None)
        if fp is None or not fp.isVisible():
            return
        tb_h = (
            self._edit_toolbar.height()  # type: ignore[attr-defined]
            if self._edit_toolbar        # type: ignore[attr-defined]
            else EditToolbar._BASE_H
        )
        vp_w = self.width()  # type: ignore[attr-defined]
        fp.setGeometry(vp_w - PANEL_W - 8, tb_h + 6, PANEL_W, PANEL_TOTAL_H)
        fp.raise_()

    def _on_filter_panel_toggle(self, visible: bool) -> None:
        self._ensure_filter_panel()
        fp = self._filter_panel_widget
        if fp is None:
            return
        fp.setVisible(visible)
        if visible:
            self._position_filter_panel()
        else:                                               
            tb = getattr(self, '_edit_toolbar', None)       
            if tb is not None and hasattr(tb, 'uncheck_filters'): 
                tb.uncheck_filters()                          

    def _on_filter_panel_closed(self) -> None:            
        tb = getattr(self, '_edit_toolbar', None)
        if tb is not None and hasattr(tb, 'uncheck_filters'):
            tb.uncheck_filters()

    # ------------------------------------------------------------------
    # 시그널 핸들러 — 패널 슬라이더 / 버튼
    # ------------------------------------------------------------------

    def _on_filter_basic_changed(self, d: dict) -> None:
        try:
            self._filter_basic = BasicParams(**{k: int(v) for k, v in d.items()})
        except Exception as e:
            error_print(f"필터 파라미터 오류: {e}")
            return
        self._schedule_filter_preview()

    def _on_filter_style_changed(self, name: str, intensity: int) -> None:
        self._filter_style_name      = name or "none"
        self._filter_style_intensity = int(intensity)
        self._schedule_filter_preview()

    def _on_filter_pro_changed(self, name: str, intensity: int) -> None:
        self._filter_pro_name      = name or "none"
        self._filter_pro_intensity = int(intensity)
        self._schedule_filter_preview()

    def _on_filter_reset_requested(self) -> None:
        self._reset_filter_state()
        self._schedule_filter_preview(force=True)

    # ------------------------------------------------------------------
    # 프리뷰 — 디바운스 + 해상도 다운샘플링
    # ------------------------------------------------------------------

    def _schedule_filter_preview(self, *, force: bool = False) -> None:
        if not getattr(self, '_edit_mode', False):
            return
        ed  = getattr(self, '_editor', None)
        pi  = getattr(self, 'pixmap_item', None)
        if ed is None or pi is None:
            return

        if (not force) and self._filter_timer_active:
            self._filter_preview_pending = True
            return

        self._filter_timer_active    = True
        self._filter_preview_pending = True

        # 2MP 초과 이미지는 80ms, 그 이하는 40ms 딜레이
        working = getattr(ed, '_working', None)
        delay_ms = (
            80 if working is not None and working.width * working.height > 2_000_000
            else 40
        )
        _weak = weakref.ref(self)
        QTimer.singleShot(delay_ms, lambda: (s := _weak()) and s._do_filter_preview())

    def _do_filter_preview(self) -> None:
        self._filter_timer_active = False
        if not self._filter_preview_pending:
            return
        self._filter_preview_pending = False

        if not getattr(self, '_edit_mode', False):
            return
        ed = getattr(self, '_editor', None)
        pi = getattr(self, 'pixmap_item', None)
        if ed is None or pi is None:
            return

        # 모자이크 프리뷰 진행 중이면 80ms 후 재시도
        if (getattr(self, '_mosaic_timer_active', False)
                or getattr(self, '_mosaic_preview_pending', False)):
            self._filter_preview_pending = True
            _weak = weakref.ref(self)
            QTimer.singleShot(80, lambda: (s := _weak()) and s._do_filter_preview())
            return

        no_filter = (
            self._filter_basic   == BasicParams()
            and self._filter_style_name == "none"
            and self._filter_pro_name   == "none"
        )
        if no_filter:
            try:
                px = pil_to_qpixmap(ed.get_working())
                if not px.isNull():
                    pi.setPixmap(px)
                    self.graphics_scene.update()  # type: ignore[attr-defined]
            except Exception:
                pass
            return

        try:
            base   = ed.get_working()
            iw, ih = base.size
            vp     = self.viewport()  # type: ignore[attr-defined]
            scale  = min(1.0, vp.width() / iw, vp.height() / ih)

            # 뷰포트 스케일이 77% 미만이고 원본이 800px 초과면 다운샘플 후 처리
            if scale < 0.77 and iw > 800:
                pw      = max(1, int(iw * scale * 1.5))
                ph      = max(1, int(ih * scale * 1.5))
                working = base.resize((pw, ph), Image.Resampling.BILINEAR)
            else:
                working = base.copy()

            out = apply_basic(working, self._filter_basic)
            out = apply_style(out, self._filter_style_name, self._filter_style_intensity)
            out = apply_pro(out,   self._filter_pro_name,   self._filter_pro_intensity)

            if working.size != (iw, ih):        # 업스케일 복원
                out = out.resize((iw, ih), Image.Resampling.BILINEAR)

            px = pil_to_qpixmap(out)
            if not px.isNull():
                pi.setPixmap(px)
                self.graphics_scene.update()  # type: ignore[attr-defined]

            # 슬라이더가 또 움직였으면 연속 재스케줄
            if self._filter_preview_pending:
                self._schedule_filter_preview(force=True)

        except Exception as e:
            error_print(f"_do_filter_preview 오류: {e}")

    # ------------------------------------------------------------------
    # 동기 파이프라인 적용 — _edit_apply() 직전 호출
    # ------------------------------------------------------------------

    def _apply_filter_pipeline_sync(self) -> None:
        """현재 필터 설정을 editor working 이미지에 완전 적용."""
        ed = getattr(self, '_editor', None)
        pi = getattr(self, 'pixmap_item', None)
        if ed is None or pi is None:
            return
        if (self._filter_basic   == BasicParams()
                and self._filter_style_name == "none"
                and self._filter_pro_name   == "none"):
            return
        try:
            base = ed.get_working().copy()
            out  = apply_basic(base, self._filter_basic)
            out  = apply_style(out, self._filter_style_name, self._filter_style_intensity)
            out  = apply_pro(out,   self._filter_pro_name,   self._filter_pro_intensity)
            px   = pil_to_qpixmap(out)
            if not px.isNull():
                pi.setPixmap(px)
        except Exception as e:
            error_print(f"_apply_filter_pipeline_sync 오류: {e}")
