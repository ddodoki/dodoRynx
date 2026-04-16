# -*- coding: utf-8 -*-
# tools\gpx_merger\gpx_merger_window.py

"""GPX Merger / Splitter 메인 윈도우"""
from __future__ import annotations

import copy
import io
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import (
    QBuffer, QByteArray, QIODevice,
    QObject, QRunnable, QThreadPool, QTimer,
    Qt, Signal, Slot,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem,
    QMainWindow, QPushButton,
    QSplitter, QStatusBar, QVBoxLayout, QWidget,
)

try:
    import piexif
    from PIL import Image as _PILImage
    _HAS_PIEXIF = True
except ImportError:
    piexif     = None   # type: ignore[assignment]
    _PILImage  = None   # type: ignore[assignment]
    _HAS_PIEXIF = False

from .gpx_logic           import (
    GpxFile, GpxPoint, MergeOptions, SplitResult,
    parse_gpx_file, merge_gpx_files, split_by_time_gap,
    split_by_date, split_by_distance, split_by_point_count,
    split_manual, save_gpx_file, make_output_filename,
    _assign_colors,
)
from .gpx_analyzer        import (
    compute_file_stats, detect_gaps,
    remove_anomalies, smooth_elevation, build_profile,
    downsample_for_display,
)
from .gpx_control_panel   import GpxControlPanel
from .gpx_map_preview     import GpxMapPreview
from .gpx_elevation_chart import GpxElevationChart
from utils.debug          import debug_print, error_print
from utils.lang_manager   import t
from utils.dark_dialog    import DarkMessageBox as _DarkMessageBox


# ────────────────────────────────────────────────────────────
# 백그라운드 파서
# ────────────────────────────────────────────────────────────


class _ParseSignals(QObject):
    finished = Signal(int, list, list)
    progress = Signal(int, int)



class _ParseWorker(QRunnable):
    def __init__(self, paths: List[Path], gen: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.paths   = paths
        self.signals = _ParseSignals()
        self.gen     = gen


    def run(self) -> None:
        results: List[GpxFile] = []
        errors:  List[str]     = []
        total = len(self.paths)
        for i, p in enumerate(self.paths):
            try:
                f = parse_gpx_file(p)
                results.append(f)
            except Exception as e:
                errors.append(f'{p.name}: {e}')
            self.signals.progress.emit(i + 1, total)
        _assign_colors(results)
        self.signals.finished.emit(self.gen, results, errors)



# ────────────────────────────────────────────────────────────
# 메인 윈도우
# ────────────────────────────────────────────────────────────


class GpxMergerWindow(QMainWindow):


    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t('gpx_merger.window.title'))
        self.resize(1200, 760)
        self._apply_theme()

        self._gpx_files:     List[GpxFile]         = []
        self._split_result:  Optional[SplitResult]  = None
        self._split_indices: List[int]              = []
        self._orig_idx_map:  dict                   = {}
        self._pool           = QThreadPool.globalInstance()
        self._parse_gen      = 0

        self._hover_debounce = QTimer(self)
        self._hover_debounce.setSingleShot(True)
        self._hover_debounce.setInterval(50)
        self._hover_debounce.timeout.connect(self._flush_hover)
        self._pending_hover_orig_idx: int = -1

        # ── 툴바 위젯 타입 선언 (_build_toolbar 에서 초기화) ──────────────
        self._btn_tb_preview: QPushButton
        self._btn_tb_save:    QPushButton
        self._lbl_mode_badge: QLabel

        self._build_ui()
        self._connect_signals()


    # ────────────────────────────────────────────────────────
    # 전역 테마
    # ────────────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #1e1e1e;
                color: #d4d4d4;
                font-size: 12px;
            }
            QSplitter::handle           { background: #2a2a2a; }
            QSplitter::handle:hover     { background: #3a3a3a; }
            QPushButton {
                background: #2d2d2d;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 4px 10px;
                color: #d4d4d4;
            }
            QPushButton:hover   { background: #3a3a3a; border-color: #555; }
            QPushButton:pressed { background: #1a1a1a; }
            QPushButton:disabled { color: #555; border-color: #2a2a2a; background: #1e1e1e; }
            QScrollBar:vertical            { background: #1e1e1e; width: 8px; border: none; }
            QScrollBar::handle:vertical    { background: #3a3a3a; border-radius: 4px; min-height: 24px; }
            QScrollBar::handle:vertical:hover { background: #555; }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical  { height: 0px; }
            QStatusBar {
                border-top: 1px solid #2e2e2e;
                color: #888;
                font-size: 11px;
            }
            QListWidget {
                background: #1a1a1a;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                color: #d4d4d4;
            }
            QListWidget::item:selected { background: #0e4a7a; color: #fff; }
            QListWidget::item:hover    { background: #2a2a2a; }
        """)


    # ────────────────────────────────────────────────────────
    # 툴바
    # ────────────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet(
            "QWidget { background: #252525; border-bottom: 1px solid #2e2e2e; }"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(6)

        self._btn_tb_preview = QPushButton(t('gpx_merger.panel.btn_preview'))
        self._btn_tb_preview.setFixedHeight(26)
        self._btn_tb_preview.setStyleSheet("""
            QPushButton {
                background: #0e4a7a; color: #9cdcfe;
                font-weight: 600; border: 1px solid #0e7fd4;
                border-radius: 4px; padding: 4px 14px;
            }
            QPushButton:hover   { background: #0e5a8a; }
            QPushButton:pressed { background: #093a5e; }
        """)

        self._btn_tb_save = QPushButton(t('gpx_merger.panel.btn_save'))
        self._btn_tb_save.setFixedHeight(26)
        self._btn_tb_save.setEnabled(False)
        self._btn_tb_save.setStyleSheet("""
            QPushButton {
                background: #1a6b2f; color: #fff;
                font-weight: bold; border: none;
                border-radius: 4px; padding: 4px 14px;
            }
            QPushButton:hover    { background: #1f8038; }
            QPushButton:pressed  { background: #145522; }
            QPushButton:disabled {
                background: #1e1e1e; color: #555;
                border: 1px solid #2a2a2a;
            }
        """)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #3a3a3a; margin: 6px 2px;")

        self._lbl_mode_badge = QLabel("MERGE")
        self._lbl_mode_badge.setStyleSheet(
            "color: #4CAF50; font-size: 10px; font-weight: 700;"
            " background: transparent; padding: 0 8px;"
        )

        lay.addWidget(self._btn_tb_preview)
        lay.addWidget(self._btn_tb_save)
        lay.addWidget(sep)
        lay.addWidget(self._lbl_mode_badge)
        lay.addStretch()
        return bar


    # ────────────────────────────────────────────────────────
    # 저장 버튼 활성화 헬퍼 (패널 + 툴바 동기화)
    # ────────────────────────────────────────────────────────

    def _set_save_enabled(self, enabled: bool) -> None:
        self._panel.set_save_enabled(enabled)
        self._btn_tb_save.setEnabled(enabled)


    # ────────────────────────────────────────────────────────
    # UI 구성
    # ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central  = QWidget()
        root_lay = QVBoxLayout(central)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)
        self.setCentralWidget(central)
        root_lay.addWidget(self._build_toolbar())

        body     = QWidget()
        main_lay = QHBoxLayout(body)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        self._panel = GpxControlPanel()

        self._right_splitter = QSplitter(Qt.Orientation.Vertical)
        self._map      = GpxMapPreview()
        self._chart    = GpxElevationChart()
        self._chart.setMinimumHeight(60)
        self._chart.setMaximumHeight(500)
        self._right_splitter.addWidget(self._map)
        self._right_splitter.addWidget(self._chart)
        self._right_splitter.setStretchFactor(0, 4)
        self._right_splitter.setStretchFactor(1, 1)
        self._right_splitter.setCollapsible(1, True)
        self._right_splitter.setSizes([600, 130])

        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.addWidget(self._panel)
        h_splitter.addWidget(self._right_splitter)
        h_splitter.setStretchFactor(0, 0)
        h_splitter.setStretchFactor(1, 1)
        h_splitter.setCollapsible(0, False)
        main_lay.addWidget(h_splitter)
        root_lay.addWidget(body, stretch=1)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage(t('gpx_merger.status.add_hint'))


    # ────────────────────────────────────────────────────────
    # 시그널 연결
    # ────────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        p = self._panel

        p.files_changed.connect(self._on_files_changed)
        p.operation_changed.connect(self._on_operation_changed)
        p.split_mode_changed.connect(self._on_split_mode_changed)
        p.manual_split_toggled.connect(self._map.set_split_mode)
        p.gaps_apply_requested.connect(self._on_apply_gaps)
        p.options_changed.connect(self._on_options_changed)
        p.preview_requested.connect(self._on_preview)
        p.save_requested.connect(self._on_save)
        p.clear_splits_requested.connect(self._on_clear_splits)

        self._map.split_point_added.connect(self._on_split_point_added)
        self._map.split_point_removed.connect(self._on_split_point_removed)

        self._chart.split_point_added.connect(self._on_split_point_added)
        self._chart.split_point_removed.connect(self._on_split_point_removed)

        self._chart.point_hovered.connect(self._on_chart_hover)
        self._chart.capture_requested.connect(self._do_capture)

        self._btn_tb_preview.clicked.connect(self._on_preview)
        self._btn_tb_save.clicked.connect(self._on_save)


    # ────────────────────────────────────────────────────────
    # 파일 로드
    # ────────────────────────────────────────────────────────

    @Slot(list)
    def _on_files_changed(self, paths: list) -> None:
        self._reset_split_state()
        self._map.clear_hover_point()
        if not paths:
            self._gpx_files     = []
            self._split_indices = []
            self._orig_idx_map  = {}
            self._map.load_tracks([])
            self._chart.load_profile([], [])
            self._panel.set_file_info(t('gpx_merger.panel.file_none'))
            self._set_save_enabled(False)  # type: ignore[attr-defined]
            return

        self._parse_gen += 1
        gen = self._parse_gen

        self._status.showMessage(t('gpx_merger.status.parsing', count=len(paths)))
        worker = _ParseWorker([Path(p) for p in paths], gen)
        worker.signals.finished.connect(self._on_parse_done)
        worker.signals.progress.connect(
            lambda cur, tot: self._status.showMessage(
                t('gpx_merger.status.parsing_progress', cur=cur, tot=tot)))
        self._pool.start(worker)


    @Slot(int, list, list)
    def _on_parse_done(self, gen: int, files: List[GpxFile], errors: List[str]) -> None:
        if gen != self._parse_gen:
            return

        if errors:
            _DarkMessageBox(
                self, kind='warning',
                title=t('gpx_merger.dialog.parse_error_title'),
                body=t('gpx_merger.dialog.parse_error_msg') + '\n\n' + '\n'.join(errors),
            ).exec()

        self._gpx_files     = files
        self._split_indices = []

        all_warnings = [
            f'[{f.path.name}] {w}'
            for f in files for w in f.warnings]
        if all_warnings:
            self._status.showMessage(
                t('gpx_merger.status.warning_msg', msg=all_warnings[0])
                if len(all_warnings) == 1
                else t('gpx_merger.status.warning_msg_more',
                    msg=all_warnings[0], n=len(all_warnings) - 1))

        total_pts = sum(f.point_count for f in files)
        self._panel.set_file_info(
            t('gpx_merger.status.file_info', count=len(files), points=f'{total_pts:,}'))

        self._rebuild_elevation_profile()
        self._map.load_tracks(files)
        self._run_gap_detection()

        self._set_save_enabled(bool(files))  # type: ignore[attr-defined]
        op = self._panel.get_operation()
        self._status.showMessage(
            t('gpx_merger.status.load_done', files=len(files), points=f'{total_pts:,}', op=op))


    # ────────────────────────────────────────────────────────
    # 작업 / 모드 변경
    # ────────────────────────────────────────────────────────

    @Slot(str)
    def _on_operation_changed(self, op: str) -> None:
        self._status.showMessage(
            t('gpx_merger.status.merge_mode') if op == 'merge'
            else t('gpx_merger.status.split_mode'))
        if op == 'merge':
            self._lbl_mode_badge.setText('MERGE')
            self._lbl_mode_badge.setStyleSheet(
                'color:#4CAF50; font-size:10px; font-weight:700; background:transparent; padding:0 8px;')
        else:
            self._lbl_mode_badge.setText('SPLIT')
            self._lbl_mode_badge.setStyleSheet(
                'color:#E6A817; font-size:10px; font-weight:700; background:transparent; padding:0 8px;')


    def _reset_split_state(self) -> None:
        self._split_indices = []
        self._map.set_split_points([])
        self._chart.set_split_indices([])
        self._panel.set_split_count_label(0)


    @Slot(str)
    def _on_split_mode_changed(self, mode: str) -> None:
        if mode != 'manual':
            self._reset_split_state()


    def _on_options_changed(self) -> None:
        self._set_save_enabled(bool(self._gpx_files))  # type: ignore[attr-defined]
        self._chart.set_utc_offset(self._panel.get_utc_offset())


    def closeEvent(self, event) -> None:
        self._hover_debounce.stop()
        self._map._cleanup_webengine()
        super().closeEvent(event)


    # ────────────────────────────────────────────────────────
    # 분할 지점 관리
    # ────────────────────────────────────────────────────────

    @Slot(int)
    def _on_split_point_added(self, orig_idx: int) -> None:
        if len(self._gpx_files) > 1:
            file0_count = len(self._gpx_files[0].all_points)
            if orig_idx >= file0_count:
                self._status.showMessage(
                    t('gpx_merger.status.split_first_only_warn',
                    file_no=self._get_file_idx(orig_idx) + 1), 3000)
                return

        if orig_idx not in self._split_indices:
            self._split_indices.append(orig_idx)
            self._split_indices.sort()
        self._map.set_split_points(self._split_indices)
        self._chart.set_split_indices(self._split_indices)
        self._panel.set_split_count_label(len(self._split_indices))


    def _get_file_idx(self, flat_orig_idx: int) -> int:
        """flat orig_idx 가 몇 번째 파일에 속하는지 반환."""
        offset = 0
        for i, f in enumerate(self._gpx_files):
            offset += len(f.all_points)
            if flat_orig_idx < offset:
                return i
        return len(self._gpx_files) - 1


    @Slot(int)
    def _on_split_point_removed(self, orig_idx: int) -> None:
        if orig_idx in self._split_indices:
            self._split_indices.remove(orig_idx)
        self._map.set_split_points(self._split_indices)
        self._chart.set_split_indices(self._split_indices)
        self._panel.set_split_count_label(len(self._split_indices))


    def _on_clear_splits(self) -> None:
        self._split_indices = []
        self._map.set_split_points([])
        self._chart.set_split_indices([])
        self._panel.set_split_count_label(0)


    # ────────────────────────────────────────────────────────
    # 갭 감지
    # ────────────────────────────────────────────────────────

    def _run_gap_detection(self) -> None:
        if not self._gpx_files:
            return
        f = self._gpx_files[0]
        if not f.has_timestamps:
            return
        gap_min = self._panel.get_gap_minutes()
        gaps    = detect_gaps(f.all_points, min_gap_seconds=gap_min * 60)
        self._panel.populate_gaps(gaps)


    def _on_apply_gaps(self) -> None:
        if not self._gpx_files:
            return
        f       = self._gpx_files[0]
        gap_min = self._panel.get_gap_minutes()
        gaps    = detect_gaps(f.all_points, min_gap_seconds=gap_min * 60)
        self._split_indices = sorted({g.split_index for g in gaps})
        self._map.set_split_points(self._split_indices)
        self._chart.set_split_indices(self._split_indices)
        self._panel.set_split_count_label(len(self._split_indices))


    # ────────────────────────────────────────────────────────
    # 고도 프로파일 (멀티파일 통합)
    # ────────────────────────────────────────────────────────

    def _rebuild_elevation_profile(self) -> None:
        all_points:      List[GpxPoint]             = []
        file_boundaries: List[int]                  = []
        track_segments:  List[Tuple[int, int, str]] = []
        file_ranges:     List[Tuple[int, int, str]] = []
        self._orig_idx_map                          = {}

        flat_idx = 0
        for fi, f in enumerate(self._gpx_files):
            file_start = flat_idx

            if fi > 0 and f.all_points:
                file_boundaries.append(flat_idx)

            for trk in f.tracks:
                seg_start = flat_idx
                for s in trk.segments:
                    for p in s.points:
                        p_copy          = copy.deepcopy(p)
                        p_copy.orig_idx = flat_idx
                        self._orig_idx_map[flat_idx] = (p.lat, p.lon)
                        all_points.append(p_copy)
                        flat_idx += 1
                seg_end = flat_idx
                if seg_start < seg_end:
                    track_segments.append((seg_start, seg_end, trk.color))

            file_color = f.tracks[0].color if f.tracks else '#888888'
            file_ranges.append((file_start, flat_idx, file_color))

        if not all_points:
            self._chart.load_profile([], [])
            return

        disp = (downsample_for_display(all_points, 5000)
                if len(all_points) > 5000 else all_points)
        try:
            profile = build_profile(disp)
        except Exception as e:
            error_print(f'[GpxMerger] build_profile 오류: {e}')
            profile = []

        self._chart.load_profile(
            profile, self._split_indices,
            track_segments=track_segments,
            file_boundaries=file_boundaries,
            file_ranges=file_ranges,
        )

        file_items = [
            (f.path.name, [trk.color for trk in f.tracks] or ['#888888'])
            for f in self._gpx_files
        ]
        self._panel.set_file_items(file_items)


    # ────────────────────────────────────────────────────────
    # 차트 ↔ 지도 호버 동기화
    # ────────────────────────────────────────────────────────

    @Slot(int)
    def _on_chart_hover(self, orig_idx: int) -> None:
        if orig_idx < 0:
            self._hover_debounce.stop()
            self._map.clear_hover_point()
            return
        self._pending_hover_orig_idx = orig_idx
        if not self._hover_debounce.isActive():
            self._hover_debounce.start()


    def _flush_hover(self) -> None:
        idx = self._pending_hover_orig_idx
        if idx < 0:
            return
        coords = self._orig_idx_map.get(idx)
        if coords:
            self._map.show_hover_point(coords[0], coords[1])


    # ────────────────────────────────────────────────────────
    # 미리보기
    # ────────────────────────────────────────────────────────

    def _on_preview(self) -> None:
        if not self._gpx_files:
            return
        try:
            result = self._compute_result()
        except Exception as e:
            _DarkMessageBox(self, kind='danger', title=t('common.dialog.error_title'), body=str(e)).exec()
            return
        if result is None:
            return

        self._split_result = result if isinstance(result, SplitResult) else None

        dlg = _PreviewDialog(result, self)
        dlg.segment_selected.connect(self._on_preview_segment_selected)
        dlg.exec()


    def _on_preview_segment_selected(self, idx: int) -> None:
        self._map.highlight_segment(idx)


    # ────────────────────────────────────────────────────────
    # 저장
    # ────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        if not self._gpx_files:
            return
        try:
            result = self._compute_result()
        except Exception as e:
            _DarkMessageBox(self, kind='danger', title=t('common.dialog.error_title'), body=str(e)).exec()
            return
        if result is None:
            return

        if isinstance(result, GpxFile):
            fragments = [result]
            stem      = result.path.stem
        else:
            fragments = result.fragments
            stem      = (self._gpx_files[0].path.stem
                         if self._gpx_files else 'output')

        out_dir   = self._panel.get_output_dir() or self._gpx_files[0].path.parent
        overwrite = self._panel.get_overwrite()
        names     = [make_output_filename(stem, i + 1, f) for i, f in enumerate(fragments)]
        conflicts = [n for n in names if (out_dir / n).exists() and not overwrite]
        if conflicts:
            _DarkMessageBox(
                self, kind='warning',
                title=t('gpx_merger.dialog.file_conflict_title'),
                body=(
                    t('gpx_merger.dialog.file_conflict_msg') + '\n\n' +
                    '\n'.join(conflicts[:10]) +
                    (f'\n{t("gpx_merger.dialog.file_conflict_more", n=len(conflicts)-10)}'
                     if len(conflicts) > 10 else '')
                ),
            ).exec()
            return

        saved, failed = [], []
        for frag, name in zip(fragments, names):
            try:
                save_gpx_file(frag, out_dir / name, overwrite=overwrite)
                saved.append(name)
            except Exception as e:
                failed.append(f'{name}: {e}')

        msg = t('gpx_merger.status.saved_msg', count=len(saved))
        if failed:
            msg += '\n\n' + t('gpx_merger.dialog.save_fail_prefix') + '\n' + '\n'.join(failed)
            _DarkMessageBox(self, kind='warning', title=t('gpx_merger.dialog.save_done_partial'), body=msg).exec()
        else:
            self._status.showMessage(msg)
            _DarkMessageBox(self, kind='info', title=t('gpx_merger.dialog.save_done_title'), body=msg).exec()


    def _do_capture(self, mode: str) -> None:
        _fallback = self._chart.parent()
        target = (
            self._right_splitter if hasattr(self, '_right_splitter')
            else (_fallback if isinstance(_fallback, QWidget) else self)
        )
        pixmap = target.grab()

        if mode == 'clipboard':
            QApplication.clipboard().setPixmap(pixmap)
            return

        ba  = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buf, 'JPEG', 95)
        buf.close()
        if ba.isEmpty():
            error_print('[GpxMerger] 캡처 JPEG 인코딩 실패')
            return

        img_bytes = self._add_capture_exif(bytes(ba.data()))
        ts        = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename  = f'{ts}_dodoRynx_gpx.jpg'

        path_str, _ = QFileDialog.getSaveFileName(
            self, t('gpx_merger.dialog.jpg_export_title'),
            filename, 'JPEG (*.jpg *.jpeg)')
        if not path_str:
            return
        try:
            Path(path_str).write_bytes(img_bytes)
        except Exception as exc:
            _DarkMessageBox(self, kind='warning', title=t('gpx_merger.dialog.save_fail_title'), body=str(exc)).exec()


    def _add_capture_exif(self, jpeg_bytes: bytes) -> bytes:
        try:
            import piexif
            from PIL import Image
            exif_dict = {
                '0th': {
                    piexif.ImageIFD.Software:  b'dodoRynx',
                    piexif.ImageIFD.Artist:    b'dodoRynx',
                    piexif.ImageIFD.Copyright: b'dodoRynx',
                },
                'Exif': {}, 'GPS': {}, '1st': {},
            }
            img = Image.open(io.BytesIO(jpeg_bytes))
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=93, exif=piexif.dump(exif_dict))
            return out.getvalue()
        except Exception as e:
            error_print(f'[GpxMerger] EXIF 삽입 실패: {e}')
            return jpeg_bytes


    # ────────────────────────────────────────────────────────
    # 결과 계산
    # ────────────────────────────────────────────────────────

    def _compute_result(self):
        if not self._gpx_files:
            return None

        files = self._apply_filters(self._gpx_files)
        op    = self._panel.get_operation()

        if op == 'merge':
            if len(files) < 2:
                raise ValueError(t('gpx_merger.dialog.need_2_files'))
            opts = self._panel.get_merge_options()
            return merge_gpx_files(files, opts)

        if len(files) > 1:
            _DarkMessageBox(
                self, kind='info',
                title=t('gpx_merger.dialog.split_only_first_title'),
                body=t('gpx_merger.dialog.split_only_first'),
            ).exec()
        f    = files[0]
        mode = self._panel.get_split_mode()

        if mode == 'gap':
            return split_by_time_gap(f, self._panel.get_gap_minutes())
        elif mode == 'date':
            return split_by_date(f)
        elif mode == 'dist':
            return split_by_distance(f, self._panel.get_dist_km())
        elif mode == 'points':
            return split_by_point_count(f, self._panel.get_point_count())
        elif mode == 'manual':
            if not self._split_indices:
                raise ValueError(t('gpx_merger.dialog.no_manual_splits'))
            return split_manual(f, self._split_indices)

        raise ValueError(t('gpx_merger.dialog.unknown_mode', mode=mode))


    def _apply_filters(self, files: List[GpxFile]) -> List[GpxFile]:
        result = copy.deepcopy(files)

        if self._panel.get_remove_anomalies():
            max_spd = self._panel.get_max_speed()
            for f in result:
                for trk in f.tracks:
                    for s in trk.segments:
                        cleaned, n = remove_anomalies(s.points, max_spd)
                        if n:
                            self._status.showMessage(
                                t('gpx_merger.status.anomaly_removed', name=f.path.name, n=n))
                        s.points = cleaned

        if self._panel.get_smooth_elevation():
            win = self._panel.get_smooth_window()
            for f in result:
                for trk in f.tracks:
                    for s in trk.segments:
                        s.points = smooth_elevation(s.points, win)
        return result


# ────────────────────────────────────────────────────────────
# 미리보기 다이얼로그
# ────────────────────────────────────────────────────────────


class _PreviewDialog(QDialog):
    segment_selected = Signal(int)

    def __init__(self, result, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t('gpx_merger.preview.title'))
        self.resize(480, 360)
        self.setStyleSheet("""
            QDialog   { background: #1e1e1e; color: #d4d4d4; }
            QLabel    { color: #9cdcfe; background: transparent;
                        font-size: 11px; font-weight: 600; }
            QListWidget {
                background: #252525; color: #d4d4d4;
                border: 1px solid #3a3a3a; border-radius: 3px;
                font-size: 12px;
            }
            QListWidget::item           { padding: 4px 6px; }
            QListWidget::item:selected  { background: #0e4a7a; color: #fff; }
            QListWidget::item:hover     { background: #2a2a2a; }
            QPushButton {
                background: #2d2d2d; border: 1px solid #3a3a3a;
                border-radius: 4px; padding: 5px 18px;
                color: #d4d4d4; font-size: 12px;
            }
            QPushButton:hover   { background: #3a3a3a; border-color: #555; }
            QPushButton:pressed { background: #1a1a1a; }
        """)
        lay = QVBoxLayout(self)

        self._list = QListWidget()

        if isinstance(result, GpxFile):
            pts   = result.point_count
            stats = compute_file_stats(result)
            dist  = stats.total_distance_m / 1000.0
            self._list.addItem(QListWidgetItem(
                t('gpx_merger.preview.merge_result', pts=f'{pts:,}', dist=f'{dist:.2f}')))
        else:
            for i, frag in enumerate(result.fragments):
                pts   = frag.point_count
                stats = compute_file_stats(frag)
                dist  = stats.total_distance_m / 1000.0
                t_info = ''
                if frag.has_timestamps:
                    s = stats.segments[0] if stats.segments else None
                    if s and s.duration_sec:
                        mins = int(s.duration_sec // 60)
                        t_info = t('gpx_merger.preview.fragment_time', mins=mins)
                self._list.addItem(QListWidgetItem(
                    t('gpx_merger.preview.fragment', idx=f'{i+1:02d}',
                      pts=f'{pts:,}', dist=f'{dist:.2f}') + t_info))

        self._list.currentRowChanged.connect(self.segment_selected)
        lay.addWidget(QLabel(
            t('gpx_merger.preview.header', count=self._list.count())))
        lay.addWidget(self._list)

        close_btn = QPushButton(t('gpx_merger.preview.close'))
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)
