# -*- coding: utf-8 -*-
# ui\editor\watermark_mixin.py

"""
WatermarkMixin
- 템플릿 기반 메타데이터 워터마크
- 한글 PIL 렌더링 대응
- outside 밴드 실제 적용
- 기본 패널 위치: 우상단
"""

from __future__ import annotations

import os
import re
import weakref
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor

from ui.editor.watermark_item import WatermarkItem
from ui.editor.watermark_panel import WatermarkPanel
from utils.debug import debug_print, error_print, warning_print
from utils.watermark_utils import flatten_watermark_metadata, resolve_template
            

class WatermarkMixin:
    
    # ------------------------------------------------------------------
    # init
    # ------------------------------------------------------------------

    def _init_watermark(self) -> None:
        self._watermark_panel: Optional[WatermarkPanel] = None
        self._watermark_item: Optional[WatermarkItem] = None
        self._wm_reader = None
        self._wm_file_path: Optional[Path] = None
        self._wm_metadata: Dict[str, object] = {}
        self._wm_panel_user_moved: bool = False
        self._wm_scene_expanded: bool = False

        # outside 밴드 활성화 직전 뷰어 줌 상태 저장용
        self._wm_saved_zoom_mode: str = "fit"
        self._wm_saved_zoom_factor: float = 1.0
        self._wm_saved_user_zoomed: bool = False
        self._wm_saved_transform = None 
        self._wm_original_img_h: float = 0.0 
        self._wm_outside_bg_item = None

    # ------------------------------------------------------------------
    # source path
    # ------------------------------------------------------------------

    def set_watermark_file_path(self, path: Path) -> None:
        self._wm_file_path = path


    def _resolve_source_file_path(self) -> Optional[Path]:
        if self._wm_file_path and self._wm_file_path.is_file():
            return self._wm_file_path

        mw = getattr(self, "main_window", None)
        if mw is None:
            return None

        attrs = (
            "_current_file",
            "current_file",
            "current_path",
            "file_path",
            "current_image_path",
        )
        for attr in attrs:
            value = getattr(mw, attr, None)
            if not value:
                continue
            try:
                p = Path(str(value))
                if p.is_file():
                    self._wm_file_path = p
                    return p
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # panel
    # ------------------------------------------------------------------

    def _ensure_watermark_panel(self) -> None:
        if self._watermark_panel is not None:
            return

        panel = WatermarkPanel(self.viewport())  # type: ignore[arg-type]
        panel.setVisible(False)
        panel.config_changed.connect(self._on_wm_config_changed)
        panel.apply_requested.connect(self._on_wm_apply_requested)
        panel.panel_closed.connect(self._on_wm_panel_closed)
        panel.drag_moved.connect(self._on_wm_panel_dragged)

        self._watermark_panel = panel
        debug_print("[WM] WatermarkPanel 생성")


    def _position_watermark_panel(self, force: bool = False) -> None:
        panel = self._watermark_panel
        if panel is None or not panel.isVisible():
            return

        if self._wm_panel_user_moved and not force:
            return

        panel.adjustSize()

        vp = self.viewport()  # type: ignore[attr-defined]
        tb_h = self._edit_toolbar.height() if getattr(self, "_edit_toolbar", None) else 90  # type: ignore[attr-defined]

        panel_w = panel.width()
        x_margin = 12
        y_margin = 10

        x = vp.width() - panel_w - x_margin
        y = tb_h + y_margin

        fp = getattr(self, "_filter_panel_widget", None)
        if fp is not None and fp.isVisible():
            x -= (fp.width() + 10)

        x = max(8, x)
        y = max(tb_h + 6, y)

        panel.move(x, y)
        panel.raise_()


    def _on_watermark_panel_toggle(self, visible: bool) -> None:
        self._ensure_watermark_panel()
        panel = self._watermark_panel
        if panel is None:
            return

        if visible:
            panel.setVisible(True)
            self._load_wm_metadata()
            self._position_watermark_panel(force=not self._wm_panel_user_moved)
            panel.raise_()

            self_ref = weakref.ref(self)

            def _deferred_config(p=panel, r=self_ref) -> None:
                obj = r()
                if obj is not None:
                    obj._on_wm_config_changed(p.build_config())

            QTimer.singleShot(0, _deferred_config)

        else:   
            panel.setVisible(False)  
            self._remove_watermark_item()  


    def _on_wm_panel_closed(self) -> None:
        self._remove_watermark_item()
        tb = getattr(self, "_edit_toolbar", None)
        if tb is not None and hasattr(tb, "btn_watermark"):
            tb.btn_watermark.blockSignals(True)
            tb.btn_watermark.setChecked(False)
            tb.btn_watermark.blockSignals(False)


    def _on_wm_panel_dragged(self) -> None:
        self._wm_panel_user_moved = True

    # ------------------------------------------------------------------
    # metadata
    # ------------------------------------------------------------------

    def _load_wm_metadata(self) -> None:
        panel = self._watermark_panel
        if panel is None:
            return

        metadata: Dict[str, object] = {}
        mw = getattr(self, "main_window", None)

        if mw is not None:
            mp = getattr(mw, "metadata_panel", None)
            current_file = getattr(mw, "_current_file", None)

            if current_file:
                try:
                    self._wm_file_path = Path(str(current_file))
                except Exception:
                    pass

            if mp is not None:
                try:
                    if hasattr(mp, "get_current_metadata"):
                        metadata = mp.get_current_metadata() or {}
                    else:
                        metadata = getattr(mp, "current_metadata", {}) or {}
                except Exception:
                    metadata = {}

                if not metadata and self._wm_file_path and hasattr(mp, "load_metadata"):
                    try:
                        metadata = mp.load_metadata(self._wm_file_path) or {}
                    except Exception:
                        metadata = {}

        if not metadata:
            file_path = self._resolve_source_file_path()
            if file_path is None:
                warning_print("[WM] 소스 파일 경로를 찾을 수 없음")
                metadata = {"file": {}, "camera": {}, "exif": {}, "gps": None}
            else:
                try:
                    reader = None
                    if mw is not None:
                        mp = getattr(mw, "metadata_panel", None)
                        if mp is not None:
                            reader = getattr(mp, "metadata_reader", None)

                    if reader is None:
                        if self._wm_reader is None:
                            from core.metadata_reader import MetadataReader
                            self._wm_reader = MetadataReader(use_cache=True)
                        reader = self._wm_reader

                    metadata = reader.read(file_path) or {}
                except Exception as e:
                    error_print(f"[WM] 메타데이터 읽기 실패: {e}")
                    metadata = {"file": {}, "camera": {}, "exif": {}, "gps": None}

        self._wm_metadata = metadata
        panel.load_metadata(metadata)


    def _flatten_metadata(self, metadata: dict) -> Dict[str, str]:
        return flatten_watermark_metadata(metadata)


    def _render_template_lines(self, template_text: str, metadata: dict) -> List[str]:
        flat = flatten_watermark_metadata(metadata)
        return resolve_template(template_text, flat)


    def _build_item_config(self, cfg: dict) -> Optional[dict]:
        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is None:
            return None

        lines = self._render_template_lines(cfg.get("template_text", ""), self._wm_metadata)
        if not lines:
            return None

        out = dict(cfg)
        out["lines"] = lines 
        out["show_labels"] = False
        out["band_width"] = float(pi.pixmap().width())
        return out

    # ------------------------------------------------------------------
    # watermark item
    # ------------------------------------------------------------------

    def _ensure_watermark_item(self) -> WatermarkItem:
        if self._watermark_item is None:
            item = WatermarkItem()
            item.about_to_change.connect(self._push_undo)  # type: ignore[attr-defined]
            self.graphics_scene.addItem(item)              # type: ignore[attr-defined]
            self._edit_shapes.append(item)                 # type: ignore[attr-defined]
            self._watermark_item = item
        return self._watermark_item


    def _remove_watermark_item(self) -> None:
        item = self._watermark_item
        if item is None:
            return
        try:
            self.graphics_scene.removeItem(item)   # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            self._edit_shapes.remove(item)         # type: ignore[attr-defined]
        except Exception:
            pass
        self._watermark_item = None
        self._restore_scene_rect()     


    def _update_watermark_preview(self, cfg: dict) -> None:
        item_cfg = self._build_item_config(cfg)
        if item_cfg is None:
            self._remove_watermark_item()  
            return

        is_outside = (
            bool(item_cfg.get("band_enabled", False))
            and item_cfg.get("band_mode", "inside") == "outside"
        )

        item = self._ensure_watermark_item()
        item.update_config(item_cfg)
        item.setPos(self._compute_watermark_pos(item_cfg))

        if is_outside:
            self._expand_scene_for_outside_band() 
        else:
            self._restore_scene_rect()          


    def _compute_watermark_pos(self, cfg: dict) -> QPointF:
        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is None or self._watermark_item is None:
            return QPointF(0, 0)

        rect = QRectF(
            0.0,
            0.0,
            float(pi.pixmap().width()),
            float(pi.pixmap().height()),
        )
        w, h = self._watermark_item.content_size()
        margin = int(cfg.get("margin", 20))
        anchor = cfg.get("anchor", "br")
        band_enabled = bool(cfg.get("band_enabled", False))
        band_mode = cfg.get("band_mode", "inside")

        if band_enabled and band_mode == "outside":
            return QPointF(rect.left(), rect.bottom())

        if band_enabled and band_mode == "inside":
            x = rect.left()
            if anchor.startswith("t"):
                y = rect.top()
            elif anchor.startswith("m"):
                y = rect.center().y() - h / 2
            else:
                y = rect.bottom() - h
            return QPointF(x, y)

        mapping = {
            "tl": QPointF(rect.left() + margin, rect.top() + margin),
            "tc": QPointF(rect.center().x() - w / 2, rect.top() + margin),
            "tr": QPointF(rect.right() - w - margin, rect.top() + margin),
            "ml": QPointF(rect.left() + margin, rect.center().y() - h / 2),
            "mc": QPointF(rect.center().x() - w / 2, rect.center().y() - h / 2),
            "mr": QPointF(rect.right() - w - margin, rect.center().y() - h / 2),
            "bl": QPointF(rect.left() + margin, rect.bottom() - h - margin),
            "bc": QPointF(rect.center().x() - w / 2, rect.bottom() - h - margin),
            "br": QPointF(rect.right() - w - margin, rect.bottom() - h - margin),
        }
        return mapping.get(anchor, mapping["br"])

    # ------------------------------------------------------------------
    # slots
    # ------------------------------------------------------------------

    def _on_wm_config_changed(self, cfg: dict) -> None:
        self._update_watermark_preview(cfg)


    def _on_wm_apply_requested(self, cfg: dict) -> None:
        lines = self._render_template_lines(cfg.get("template_text", ""), self._wm_metadata)
        if not lines:
            return

        self._push_undo()       # type: ignore[attr-defined]
        self._apply_watermark_pil(cfg)
        self._remove_watermark_item()
        self._invalidate_metadata_cache()  

        if self._watermark_panel is not None:
            self._watermark_panel.setVisible(False)

        tb = getattr(self, "_edit_toolbar", None)
        if tb is not None and hasattr(tb, "btn_watermark"):
            tb.btn_watermark.blockSignals(True)
            tb.btn_watermark.setChecked(False)
            tb.btn_watermark.blockSignals(False)


    def _invalidate_metadata_cache(self) -> None:
        """워터마크 적용 직후 메타데이터 캐시 무효화.
        
        mtime 검증이 추가됐으므로 다음 reader.read() 호출 시
        자동으로 재읽기하지만, 즉각 반영을 위해 명시적 무효화도 수행.
        """
        path = self._wm_file_path
        if path is None:
            return

        # 1) watermark 전용 reader
        if self._wm_reader is not None:
            self._wm_reader.invalidate(path)

        # 2) metadata panel reader
        mw = getattr(self, "main_window", None)
        mp = getattr(mw, "metadata_panel", None) if mw else None
        if mp is not None:
            reader = getattr(mp, "metadata_reader", None)
            if reader is not None:
                reader.invalidate(path) 

        # 3) watermark mixin 자체 캐시 초기화
        self._wm_metadata = {}

        debug_print(f"[WM] 메타데이터 캐시 무효화: {path.name}")

    # ------------------------------------------------------------------
    # PIL apply
    # ------------------------------------------------------------------

    def _apply_watermark_pil(self, cfg: dict) -> None:
        from PIL import Image, ImageDraw

        editor = getattr(self, "_editor", None)  # type: ignore[attr-defined]
        if editor is None or getattr(editor, "_working", None) is None:
            return

        pi = self.pixmap_item  # type: ignore[attr-defined]
        if pi is None:
            return

        lines = self._render_template_lines(cfg.get("template_text", ""), self._wm_metadata)
        if not lines:
            return

        src_img = editor._working
        base = src_img.convert("RGBA")

        font_family = cfg.get("font_family", "맑은 고딕")
        font_size = int(cfg.get("font_size", 28))
        bold = bool(cfg.get("bold", False))
        italic = bool(cfg.get("italic", False))
        text_color = cfg.get("text_color", QColor(255, 255, 255))
        alignment = cfg.get("alignment", Qt.AlignmentFlag.AlignLeft)
        line_spacing = float(cfg.get("line_spacing", 1.5))
        anchor = cfg.get("anchor", "br")
        margin = int(cfg.get("margin", 20))
        band_enabled = bool(cfg.get("band_enabled", False))
        band_mode = cfg.get("band_mode", "inside")
        band_color = cfg.get("band_color", QColor(0, 0, 0))
        band_alpha = int(cfg.get("band_alpha", 153))
        band_height_auto = bool(cfg.get("band_height_auto", True))
        band_height = int(cfg.get("band_height", 120))
        band_padding = int(cfg.get("band_padding", 18))

        pil_font = _get_pil_font(font_family, font_size, bold, italic)

        dm = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        dd = ImageDraw.Draw(dm)

        try:
            bbox = dd.textbbox((0, 0), "가Ay", font=pil_font)
            line_h = max(1, int((bbox[3] - bbox[1]) * line_spacing))
        except Exception:
            line_h = max(1, int(font_size * line_spacing))

        text_widths: List[float] = []
        for line in lines:
            try:
                bb = dd.textbbox((0, 0), line, font=pil_font)
                text_widths.append(float(max(1, bb[2] - bb[0])))
            except Exception:
                text_widths.append(float(max(1, int(font_size * len(line) * 0.6))))

        block_w = max(text_widths) if text_widths else 1
        text_h = line_h * len(lines)
        auto_band_h = text_h + band_padding * 2
        actual_band_h = band_height if not band_height_auto else auto_band_h

        iw, ih = base.size
        canvas = base.copy()

        # ----------------------------------------------------------
        # band / canvas
        # ----------------------------------------------------------
        band_top = 0
        text_top = margin

        if band_enabled and band_mode == "outside":
            new_h = ih + actual_band_h
            expanded = Image.new("RGBA", (iw, new_h), (255, 255, 255, 255)) 
            expanded.alpha_composite(base, (0, 0))

            overlay = Image.new("RGBA", (iw, new_h), (0, 0, 0, 0))
            ov_draw = ImageDraw.Draw(overlay)
            fill = (
                band_color.red(),
                band_color.green(),
                band_color.blue(),
                band_alpha,
            )
            band_top = ih
            ov_draw.rectangle([0, band_top, iw, new_h], fill=fill)

            canvas = Image.alpha_composite(expanded, overlay)
            text_top = band_top + max(band_padding, (actual_band_h - text_h) // 2)

        elif band_enabled and band_mode == "inside":
            overlay = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
            ov_draw = ImageDraw.Draw(overlay)
            fill = (
                band_color.red(),
                band_color.green(),
                band_color.blue(),
                band_alpha,
            )

            if anchor.startswith("t"):
                band_top = 0
            elif anchor.startswith("m"):
                band_top = max(0, (ih - actual_band_h) // 2)
            else:
                band_top = max(0, ih - actual_band_h)

            band_bottom = min(ih, band_top + actual_band_h)
            ov_draw.rectangle([0, band_top, iw, band_bottom], fill=fill)
            canvas = Image.alpha_composite(canvas, overlay)
            text_top = band_top + max(band_padding, (actual_band_h - text_h) // 2)

        # ----------------------------------------------------------
        # text origin
        # ----------------------------------------------------------
        if not band_enabled:
            item = self._watermark_item
            if item is not None:
                block_left = int(item.pos().x())
                text_top = int(item.pos().y())
            else:
                if anchor.endswith("l"):
                    block_left = margin
                elif anchor.endswith("c"):
                    block_left = (iw - block_w) // 2
                else:
                    block_left = iw - block_w - margin

                if anchor.startswith("t"):
                    text_top = margin
                elif anchor.startswith("m"):
                    text_top = (ih - text_h) // 2
                else:
                    text_top = ih - text_h - margin
        else:
            block_left = band_padding
            if alignment == Qt.AlignmentFlag.AlignHCenter:
                block_left = (iw - block_w) // 2
            elif alignment == Qt.AlignmentFlag.AlignRight:
                block_left = iw - block_w - band_padding

        # ----------------------------------------------------------
        # text draw
        # ----------------------------------------------------------
        draw = ImageDraw.Draw(canvas)
        fill_text = (
            text_color.red(),
            text_color.green(),
            text_color.blue(),
            255,
        )

        for i, line in enumerate(lines):
            y = text_top + i * line_h
            tw = text_widths[i]

            if band_enabled:
                if alignment == Qt.AlignmentFlag.AlignHCenter:
                    x = (iw - tw) // 2
                elif alignment == Qt.AlignmentFlag.AlignRight:
                    x = iw - tw - band_padding
                else:
                    x = band_padding
            else:
                if alignment == Qt.AlignmentFlag.AlignHCenter:
                    x = block_left + (block_w - tw) // 2
                elif alignment == Qt.AlignmentFlag.AlignRight:
                    x = block_left + (block_w - tw)
                else:
                    x = block_left

            draw.text((int(x), int(y)), line, font=pil_font, fill=fill_text)

        editor._working = canvas
        from core.qt_pil import pil_to_qpixmap
        pi.setPixmap(pil_to_qpixmap(canvas))

    # ------------------------------------------------------------------
    # cleanup
    # ------------------------------------------------------------------

    def _cleanup_watermark(self) -> None:
        self._remove_watermark_item()
        if self._watermark_panel is not None:
            self._watermark_panel.setVisible(False)
        self._wm_metadata = {}


    def _expand_scene_for_outside_band(self) -> None:
        pi = self.pixmap_item                          # type: ignore[attr-defined]
        item = self._watermark_item
        if pi is None or item is None:
            return
        px = pi.pixmap()
        if px.isNull():
            return

        iw      = float(px.width())
        ih      = float(px.height())
        _, band_h = item.content_size()
        total_h = ih + band_h

        # ── 씬 rect 확장 ─────────────────────────────────────────────
        self.graphics_scene.setSceneRect(              # type: ignore[attr-defined]
            QRectF(0.0, 0.0, iw, total_h)
        )

        # ── 흰 배경 rect 갱신 (band_h 변경에도 대응) ─────────────────
        self._update_outside_band_bg(iw, ih, band_h)

        # ── 첫 진입 시에만 줌 상태 저장 ──────────────────────────────
        if not self._wm_scene_expanded:
            self._wm_saved_zoom_mode    = getattr(self, "zoom_mode",       "fit")
            self._wm_saved_zoom_factor  = getattr(self, "zoom_factor",     1.0)
            self._wm_saved_user_zoomed  = getattr(self, "_user_has_zoomed", False)
            self._wm_saved_transform    = self.transform()  # type: ignore[attr-defined]
            self._wm_original_img_h     = ih

        self._wm_scene_expanded = True

        # ── 전체 씬 fitInView ─────────────────────────────────────────
        vp = self.viewport().rect()                    # type: ignore[attr-defined]
        if vp.width() <= 0 or vp.height() <= 0 or iw <= 0 or total_h <= 0:
            return

        scale = min(vp.width() / iw, vp.height() / total_h)
        self.resetTransform()                          # type: ignore[attr-defined]
        self.scale(scale, scale)                       # type: ignore[attr-defined]
        self.zoom_factor      = scale                  # type: ignore[attr-defined]
        self.zoom_mode        = "fit"                  # type: ignore[attr-defined]
        self._user_has_zoomed = False                  # type: ignore[attr-defined]
        self.centerOn(iw / 2.0, total_h / 2.0)        # type: ignore[attr-defined]
        self._update_cursor()                          # type: ignore[attr-defined]
        self._calculate_and_emit_zoom()                # type: ignore[attr-defined]


    def _restore_scene_rect(self) -> None:
        if not self._wm_scene_expanded:
            return

        # ── 흰 배경 rect 제거 ────────────────────────────────────────
        self._remove_outside_band_bg()

        pi = self.pixmap_item                          # type: ignore[attr-defined]
        applied = False

        if pi is not None and not pi.pixmap().isNull():
            px = pi.pixmap()
            self.graphics_scene.setSceneRect(          # type: ignore[attr-defined]
                QRectF(0.0, 0.0, float(px.width()), float(px.height()))
            )
            applied = float(px.height()) > self._wm_original_img_h + 1.0

        self._wm_scene_expanded = False
        self._wm_original_img_h = 0.0

        if applied:
            self.set_zoom_mode("fit")                  # type: ignore[attr-defined]
            self._wm_saved_transform = None
        else:
            if self._wm_saved_transform is not None:
                self.setTransform(self._wm_saved_transform)  # type: ignore[attr-defined]
                self._wm_saved_transform = None
            self.zoom_mode        = self._wm_saved_zoom_mode   # type: ignore[attr-defined]
            self.zoom_factor      = self._wm_saved_zoom_factor # type: ignore[attr-defined]
            self._user_has_zoomed = self._wm_saved_user_zoomed # type: ignore[attr-defined]
            self._update_cursor()                              # type: ignore[attr-defined]
            self._calculate_and_emit_zoom()                    # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # outside 밴드 미리보기 배경
    # ------------------------------------------------------------------

    def _update_outside_band_bg(self, iw: float, ih: float, band_h: float) -> None:
        """outside 밴드 영역에 흰 배경 rect를 추가/갱신.

        band_h가 바뀌어도 재호출 시 자동 교체되므로 항상 정확한 크기 유지.
        z=99 → WatermarkItem(z=100) 아래에 위치.
        """
        from PySide6.QtWidgets import QGraphicsRectItem  # type: ignore[attr-defined]
        from PySide6.QtGui import QBrush                 # type: ignore[attr-defined]
        from PySide6.QtCore import Qt                    # type: ignore[attr-defined]

        self._remove_outside_band_bg()   # 기존 항목 먼저 제거

        bg = QGraphicsRectItem(QRectF(0.0, ih, iw, band_h))
        bg.setBrush(QBrush(QColor(255, 255, 255)))
        bg.setPen(Qt.PenStyle.NoPen)
        bg.setZValue(99)
        self.graphics_scene.addItem(bg)  # type: ignore[attr-defined]
        self._wm_outside_bg_item = bg


    def _remove_outside_band_bg(self) -> None:
        """outside 밴드 배경 rect를 씬에서 제거."""
        if self._wm_outside_bg_item is None:
            return
        try:
            self.graphics_scene.removeItem(self._wm_outside_bg_item)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._wm_outside_bg_item = None
                                

# ----------------------------------------------------------------------
# font helpers
# ----------------------------------------------------------------------

def _get_pil_font(family: str, size: int, bold: bool, italic: bool):
    from PIL import ImageFont

    # 1) Windows 파일명 직접 우선 탐색
    for candidate in _font_candidates(family, bold, italic):
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            pass

    # 2) Windows Fonts 폴더 직접 탐색
    for path in _font_search_paths(family, bold, italic):
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception:
            pass

    # 3) matplotlib 가능 시 family 매핑
    try:
        import matplotlib.font_manager as fm  # type: ignore[import-untyped]
        props = fm.FontProperties(
            family=family,
            weight="bold" if bold else "normal",
            style="italic" if italic else "normal",
        )
        font_path = fm.findfont(props, fallback_to_default=True)
        if font_path:
            return ImageFont.truetype(font_path, size=size)
    except Exception:
        pass

    # 4) 최후 fallback - 한글 가능한 후보 재시도
    for fallback in (
        "malgun.ttf",
        "malgunbd.ttf",
        "NanumGothic.ttf",
        "NotoSansCJKkr-Regular.otf",
        "arialuni.ttf",
    ):
        try:
            return ImageFont.truetype(fallback, size=size)
        except Exception:
            pass

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _font_candidates(family: str, bold: bool, italic: bool) -> List[str]:
    fam = (family or "").lower().strip()
    out: List[str] = []

    if "맑은 고딕" in family or "malgun" in fam:
        out += ["malgunbd.ttf" if bold else "malgun.ttf"]
    if "나눔" in family or "nanum" in fam:
        out += ["NanumGothicBold.ttf" if bold else "NanumGothic.ttf"]
    if "noto" in fam:
        out += [
            "NotoSansCJKkr-Bold.otf" if bold else "NotoSansCJKkr-Regular.otf",
            "NotoSansKR-Bold.otf" if bold else "NotoSansKR-Regular.otf",
        ]
    if "gulim" in fam or "굴림" in family:
        out += ["gulim.ttc"]
    if "batang" in fam or "바탕" in family:
        out += ["batang.ttc"]

    out += [
        "malgunbd.ttf" if bold else "malgun.ttf",
        "NanumGothicBold.ttf" if bold else "NanumGothic.ttf",
        "NotoSansCJKkr-Bold.otf" if bold else "NotoSansCJKkr-Regular.otf",
        "arialuni.ttf",
    ]
    return out


def _font_search_paths(family: str, bold: bool, italic: bool) -> List[Path]:
    win_dir = Path(os.environ.get("WINDIR", "C:/Windows"))
    fonts_dir = win_dir / "Fonts"
    if not fonts_dir.exists():
        return []

    exact: List[Path] = []
    fuzzy: List[Path] = []

    for name in _font_candidates(family, bold, italic):
        p = fonts_dir / name
        if p.exists():
            exact.append(p)

    fam = (family or "").lower().replace(" ", "")
    for p in fonts_dir.iterdir():
        if not p.is_file():
            continue
        low = p.name.lower().replace(" ", "")
        if fam and fam in low:
            fuzzy.append(p)

    return exact + fuzzy

