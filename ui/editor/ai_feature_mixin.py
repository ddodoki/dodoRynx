# -*- coding: utf-8 -*-
# ui\editor\ai_feature_mixin.py

"""
AIFeatureMixin — BG 제거, AI 지우개, AI 패널 관리.

EditModeMixin이 다중 상속으로 사용하며,
_init_ai() 를 _init_edit_mode() 에서 호출해야 한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QPixmap, QMouseEvent

from core.qt_pil import qpixmap_to_pil
from core.ai_bg_remover import (
    BEN2Worker,
    ModelDownloadWorker,
    check_dependencies,
    is_model_cached,
    _ONNX_FILE,
)
from PySide6.QtWidgets import QWidget
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox
from ui.editor.edit_toolbar import EditToolbar
from ui.dialogs.model_download_dialog import ModelDownloadDialog

from utils.debug import debug_print, error_print
from utils.lang_manager import t
from utils.panel_opacity import apply_hover_opacity

class AIFeatureMixin:
    """BG 제거 · AI 지우개(LaMa) · AI 패널 생성/위치/토글."""

    if TYPE_CHECKING:
        from typing import Any
        from PySide6.QtCore import QPointF
        from PySide6.QtWidgets import QWidget as _QWidget
        from PySide6.QtWidgets import QGraphicsScene, QGraphicsPixmapItem
        from core.image_editor import ImageEditor
        from ui.editor.edit_toolbar import EditToolbar
        from ui.editor.ai_panel import AIPanel
        from ui.editor.ai_mask_item import AIMaskItem

        _ai_panel_widget: Optional["AIPanel"]
        _editor: Optional["ImageEditor"]
        _edit_tool: str
        _edit_toolbar: Optional["EditToolbar"]
        _mask_item: Optional["AIMaskItem"]
        pixmap_item: Optional["QGraphicsPixmapItem"]
        graphics_scene: "QGraphicsScene"

        def width(self) -> int: ...
        def height(self) -> int: ...
        def viewport(self) -> "_QWidget": ...
        def window(self) -> "_QWidget": ...
        def _replace_pixmap_inplace(self, pixmap: QPixmap) -> None: ...
        def _push_undo(self) -> None: ...
        def _pop_undo(self) -> None: ...

    # ------------------------------------------------------------------
    # 초기화 / 정리
    # ------------------------------------------------------------------

    def _init_ai(self) -> None:
        self._bg_workers:       list  = []
        self._ai_workers:       list  = []
        self._mask_item         = None       
        self._ai_brush_on:      bool  = False
        self._ai_brush_drawing: bool  = False
        self._ai_brush_size:    int   = 30
        self._ai_panel_widget   = None     


    def _cleanup_ai(self) -> None:
        self._remove_mask_item()
        self._ai_brush_on = False
        panel = getattr(self, '_ai_panel_widget', None)
        if panel is not None:
            panel.setVisible(False)

    # ------------------------------------------------------------------
    # AI 패널 — 생성 / 위치 / 토글
    # ------------------------------------------------------------------

    def _ensure_ai_panel(self) -> None:
        if getattr(self, '_ai_panel_widget', None) is not None:
            return
        from ui.editor.ai_panel import AIPanel
        panel = AIPanel(self)
        panel.setVisible(False)
        panel.bg_remove_requested.connect(self._on_bg_remove_requested)
        panel.erase_activate_requested.connect(self._on_ai_erase_activate)
        panel.erase_run_requested.connect(self._on_ai_erase_run)
        panel.erase_clear_requested.connect(self._on_ai_erase_clear)
        panel.brush_size_changed.connect(self._on_ai_brush_size_changed)
        panel.preload_requested.connect(self._start_ai_preloader)
        panel.panel_closed.connect(self._on_ai_panel_closed)

        apply_hover_opacity(panel, idle=0.18, hover=0.96)

        self._ai_panel_widget = panel
        debug_print("[AI Panel] 위젯 생성 완료")


    def _position_ai_panel(self) -> None:
        panel = getattr(self, '_ai_panel_widget', None)
        if panel is None or not panel.isVisible():
            return
        from ui.editor.ai_panel import PANEL_W
        tb_h = (
            self._edit_toolbar.height()
            if self._edit_toolbar
            else EditToolbar._BASE_H
        )
        fp = getattr(self, '_filter_panel_widget', None)
        if fp is not None and fp.isVisible():
            x = fp.x() - PANEL_W - 8
        else:
            x = self.width() - PANEL_W - 8
        panel.setGeometry(x, tb_h + 6, PANEL_W, panel.height())
        panel.raise_()


    def _on_ai_panel_toggle(self, visible: bool) -> None:
        self._ensure_ai_panel()
        panel = self._ai_panel_widget
        if panel is None:
            return
        panel.setVisible(visible)
        if not visible:
            self._deactivate_brush()
            return
        self._position_ai_panel()
        if hasattr(panel, 'set_brush_size'):
            panel.set_brush_size(self._ai_brush_size)


    def _on_ai_panel_closed(self) -> None:
        tb = getattr(self, '_edit_toolbar', None)
        if tb is not None and hasattr(tb, 'uncheck_ai'):
            tb.uncheck_ai()

    # ------------------------------------------------------------------
    # AI 모델 프리로더
    # ------------------------------------------------------------------

    def _start_ai_preloader(self) -> None:
        panel = getattr(self, '_ai_panel_widget', None)
        if panel is None:
            return

        already = any(
            type(w).__name__ == 'AIModelPreloader'
            for w in self._ai_workers
        )
        if already:
            debug_print("[AI Panel] Preloader 이미 실행 중 — 스킵")
            self._sync_ai_panel_state(panel)
            return

        from core.ai_eraser import AIModelPreloader
        loader = AIModelPreloader()
        loader.one_loading.connect(panel.set_model_loading)
        loader.one_no_model.connect(panel.set_model_not_installed)
        loader.one_ready.connect(panel.set_models_ready)
        loader.one_failed.connect(
            lambda name, msg: (
                error_print(f"[AI Preload] {name} 실패: {msg}"),
                panel.set_models_ready(name),
            )
        )
        self._ai_workers.append(loader)
        loader.finished.connect(
            lambda: self._ai_workers.remove(loader)
            if loader in self._ai_workers else None
        )
        loader.start()
        debug_print("[AI Panel] Preloader 시작")


    def _sync_ai_panel_state(self, panel: "AIPanel") -> None:
        try:
            from core.ai_eraser import _SESSION_CACHE
            from core.ai_bg_remover import (
                _BEN2_SESSION_CACHE,
                get_onnx_path as ben2_path,
            )
            from core.ai_model_manager import get_onnx_path
            if str(ben2_path()) in _BEN2_SESSION_CACHE:
                panel.set_models_ready("ben2")
            if str(get_onnx_path("lama")) in _SESSION_CACHE:
                panel.set_models_ready("lama")
        except ImportError:
            pass

    # ------------------------------------------------------------------
    # BG 제거
    # ------------------------------------------------------------------

    def _on_bg_remove_requested(self) -> None:
        ok, missing = check_dependencies()
        if not ok:
            self._show_dep_install_dialog(missing)
            ok, _ = check_dependencies()
            if not ok:
                return

        if not is_model_cached():
            dlworker = ModelDownloadWorker()
            dlg = ModelDownloadDialog(
                worker   = dlworker,
                title    = t('bg_remove.dl_title'),
                desc     = t('bg_remove.dl_desc'),
                filename = _ONNX_FILE,
                parent   = self.window(),
            )
            if dlg.exec() != 1:
                return
            if not is_model_cached():
                return

            ai_panel = getattr(self, '_ai_panel_widget', None)
            if ai_panel is not None:
                ai_panel.set_models_ready("ben2")

        pi = self.pixmap_item
        if pi is None:
            return
        src_pixmap = pi.pixmap()
        if src_pixmap.isNull():
            return

        ai_panel = getattr(self, '_ai_panel_widget', None)
        if ai_panel is not None and hasattr(ai_panel, 'set_bg_task_running'):
            ai_panel.set_bg_task_running(True)

        self._start_loading_overlay(t('loading_overlay.bg_loading'))
        self._push_undo()

        worker = BEN2Worker(src_pixmap)
        worker.progress.connect(self._on_bg_remove_progress)
        worker.finished.connect(self._on_bg_remove_done)
        worker.failed.connect(self._on_bg_remove_failed)
        worker.finished.connect(lambda _: self._cleanup_bg_worker(worker))
        worker.failed.connect(lambda _: self._cleanup_bg_worker(worker))
        worker.start()
        self._bg_workers.append(worker)
        debug_print("[BG Remove] Worker 시작")


    def _on_bg_remove_progress(self, key: str) -> None:
        overlay = getattr(self, '_loading_overlay', None)
        if overlay and hasattr(overlay, 'set_message'):
            if key == "model_loading":
                overlay.set_message(t('loading_overlay.bg_loading'))
            elif key == "inferring":
                overlay.set_message(t('loading_overlay.bg_inferring'))


    def _on_bg_remove_done(self, result: QPixmap) -> None:
        self._stop_loading_overlay()
        ai_panel = getattr(self, '_ai_panel_widget', None)
        if ai_panel is not None and hasattr(ai_panel, 'set_bg_task_running'):
            ai_panel.set_bg_task_running(False)
        if result.isNull():
            return
        self._replace_pixmap_inplace(result)
        if self._editor is not None:
            self._editor.set_working(qpixmap_to_pil(result))
            self._editor.commit()

        tb = self._edit_toolbar
        if tb and hasattr(tb, 'btn_fmt_webp'):
            tb.btn_fmt_webp.setChecked(True)
            tb._on_fmt_changed(1)

        _DarkMessageBox(
            self.window(), kind='info',
            title=t('bg_remove.done'),
            body=t('bg_remove.transparency_tip'),
        ).exec()
        debug_print("[BG Remove] 완료")


    def _on_bg_remove_failed(self, error: str) -> None:
        self._stop_loading_overlay()
        ai_panel = getattr(self, '_ai_panel_widget', None)
        if ai_panel is not None and hasattr(ai_panel, 'set_bg_task_running'):
            ai_panel.set_bg_task_running(False)
        self._pop_undo()
        error_print(f"[BG Remove] {error}")
        _DarkMessageBox(
            self.window(), kind='danger',
            title=t('bg_remove.confirm_title'),
            body=t('bg_remove.failed', error=error[:300]),
        ).exec()


    def _cleanup_bg_worker(self, worker: object) -> None:
        try:
            self._bg_workers.remove(worker)
        except ValueError:
            pass
        if hasattr(worker, 'deleteLater'):
            getattr(worker, 'deleteLater')()
        debug_print(f"BG워커 정리 완료 (남은: {len(self._bg_workers)})")

    # ------------------------------------------------------------------
    # AI 지우개 — 브러시 ON/OFF
    # ------------------------------------------------------------------

    def _on_ai_erase_activate(self) -> None:
        if self._ai_brush_on:
            self._deactivate_brush()
        else:
            self._activate_brush()


    def _activate_brush(self) -> None:
        pi = self.pixmap_item
        if pi is None:
            return
        if self._mask_item is None:
            from ui.editor.ai_mask_item import AIMaskItem
            w = int(pi.boundingRect().width())
            h = int(pi.boundingRect().height())
            self._mask_item = AIMaskItem(w, h)
            self._mask_item.brush_size = self._ai_brush_size
            self.graphics_scene.addItem(self._mask_item)
        self._ai_brush_on = True
        self._edit_tool   = 'ai_erase'
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)


    def _deactivate_brush(self) -> None:
        self._ai_brush_on = False
        if self._edit_tool == 'ai_erase':
            self._edit_tool = 'select'
        self.viewport().unsetCursor()
        panel = getattr(self, '_ai_panel_widget', None)
        if panel and hasattr(panel, 'set_brush_active'):
            panel.set_brush_active(False)


    def _remove_mask_item(self) -> None:
        if self._mask_item is not None:
            self.graphics_scene.removeItem(self._mask_item)
            self._mask_item = None


    def _on_ai_erase_clear(self) -> None:
        if self._mask_item is not None:
            self._mask_item.clear()
        self._deactivate_brush()


    def _on_ai_brush_size_changed(self, size: int) -> None:
        self._ai_brush_size = size
        if self._mask_item is not None:
            self._mask_item.brush_size = size

    # ------------------------------------------------------------------
    # AI 지우개 — 실행 / 완료
    # ------------------------------------------------------------------

    def _on_ai_erase_run(self) -> None:
        from core.ai_model_manager import check_dependencies, is_model_cached
        from core.ai_eraser import AIEraserWorker

        # 1. 의존성 체크 (먼저)
        ok, missing = check_dependencies()
        if not ok:
            self._show_dep_install_dialog(missing)
            ok, _ = check_dependencies()   
            if not ok:
                return

        # 2. 모델 다운로드 체크 (마스크 체크보다 먼저)
        if not is_model_cached("lama"):
            if not self._download_ai_model("lama"):
                return

            panel = getattr(self, '_ai_panel_widget', None)
            if panel is not None:
                panel.set_models_ready("lama")

        # 3. 마스크 체크 (모델 확인 후)
        if self._mask_item is None or self._mask_item.is_empty():
            _DarkMessageBox(
                self.window(), kind='info',
                title=t('edit_mode_mixin.ai_erase_title'),
                body=t('edit_mode_mixin.ai_erase_guide'),
            ).exec()
            return

        pi = self.pixmap_item
        if pi is None:
            return
        src      = pi.pixmap()
        mask_px  = self._mask_item.get_mask_pixmap()

        self._push_undo()
        self._deactivate_brush()
        self._start_loading_overlay(t('edit_mode_mixin.ai_erasing'))
        self._set_ai_btn_enabled(False)

        worker = AIEraserWorker(src, mask_px)
        worker.progress.connect(lambda k: None)
        worker.finished.connect(self._on_ai_erase_done)
        worker.failed.connect(self._on_ai_failed)
        worker.finished.connect(lambda _: self._cleanup_ai_worker(worker))
        worker.failed.connect(lambda _: self._cleanup_ai_worker(worker))
        worker.start()
        self._ai_workers.append(worker)
        self._remove_mask_item()
        debug_print("AIEraserWorker 시작")


    def _on_ai_erase_done(self, result: QPixmap) -> None:
        self._stop_loading_overlay()
        self._set_ai_btn_enabled(True)
        if result.isNull():
            return
        self._replace_pixmap_inplace(result)
        if self._editor is not None:
            self._editor.set_working(qpixmap_to_pil(result))
            self._editor.commit()
        debug_print("AI 지우개 완료")

    # ------------------------------------------------------------------
    # AI 브러시 마우스 이벤트
    # ------------------------------------------------------------------

    def _handle_ai_brush_event(self, event: object, et: object) -> bool:
        if (self._edit_tool != 'ai_erase'
                or self._mask_item is None):
            return False

        if not isinstance(event, QMouseEvent):
            return False

        if et == QEvent.Type.MouseMove:
            if self._ai_brush_drawing:
                sp = self.mapToScene(event.pos().x(), event.pos().y())  # type: ignore[attr-defined]
                pi = self.pixmap_item
                if pi is not None:
                    local = pi.mapFromScene(sp)
                    self._mask_item.paint_at(local.x(), local.y())
            return True

        elif et == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self._ai_brush_drawing = False
                self._mask_item.reset_stroke()
            return True

        return False

    # ------------------------------------------------------------------
    # 공통 헬퍼
    # ------------------------------------------------------------------

    def _download_ai_model(self, key: str) -> bool:
        from core.ai_model_manager import AIModelDownloadWorker, MODEL_REGISTRY, is_model_cached
        info   = MODEL_REGISTRY[key]
        worker = AIModelDownloadWorker(key)
        dlg    = ModelDownloadDialog(
            worker   = worker,      # type: ignore[arg-type]
            title    = t('edit_mode_mixin.model_download_title', label=info.label),
            desc     = t('edit_mode_mixin.model_download_desc', label=info.label),
            filename = info.filename,
            parent   = self.window(),
        )
        return dlg.exec() == 1 and is_model_cached(key)


    def _show_dep_install_dialog(self, missing: list) -> None:
        from ui.dialogs.dep_install_dialog import DepInstallDialog
        DepInstallDialog(missing, parent=self.window()).exec()


    def _start_loading_overlay(self, message: str = "") -> None:
        overlay = getattr(self, '_loading_overlay', None)
        if overlay:
            overlay.start(message)


    def _stop_loading_overlay(self) -> None:
        overlay = getattr(self, '_loading_overlay', None)
        if overlay:
            overlay.stop()


    def _set_ai_btn_enabled(self, enabled: bool) -> None:
        panel = getattr(self, '_ai_panel_widget', None)
        if panel is not None and hasattr(panel, 'set_erase_run_enabled'):
            panel.set_erase_run_enabled(enabled)


    def _on_ai_failed(self, msg: str) -> None:
        self._stop_loading_overlay()
        self._set_ai_btn_enabled(True)
        error_print(f"AI 처리 실패:\n{msg}")
        _DarkMessageBox(
            self.window(), kind='danger',
            title=t('edit_mode_mixin.ai_failed_title'),
            body=t('edit_mode_mixin.ai_failed_msg', msg=msg[:300]),
        ).exec()


    def _cleanup_ai_worker(self, worker: object) -> None:
        try:
            self._ai_workers.remove(worker)
        except ValueError:
            pass
        if hasattr(worker, 'deleteLater'):
            getattr(worker, 'deleteLater')() 

