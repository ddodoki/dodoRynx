# -*- coding: utf-8 -*-
# tools\gps_map\gps_map_gpx_dialog.py

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QFrame, QFileDialog,
    QSizePolicy,
)
from utils.debug import error_print
from utils.lang_manager import t
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox


class GpsMapGpxManagerDialog(QDialog):
    """
    GPX 파일 관리 다이얼로그.
    - 저장 폴더의 GPX 목록: 열기 / 삭제
    - 하단 찾아보기: 외부 파일 → 폴더 복사 → 열기
    """

    file_selected = Signal(Path)

    _QSS = """
    QDialog { background: #1a1a1a; color: #ccc; }
    QLabel  { color: #ccc; font-size: 11px; }
    QLabel#lb_title { color: #eee; font-size: 13px; font-weight: bold; }
    QLabel#lb_empty { color: #555; font-size: 12px; }
    QScrollArea, QWidget#list_body { background: #1a1a1a; border: none; }
    QFrame#row  { background: #222; border-radius: 4px; }
    QFrame#div  { background: #2e2e2e; }
    QPushButton {
        background: #2e2e2e; color: #bbb;
        border: 1px solid #444; border-radius: 4px;
        padding: 4px 10px; font-size: 11px; min-width: 52px;
    }
    QPushButton:hover  { background: #3a3a3a; color: #fff; }
    QPushButton#btn_open {
        background: #1a3a5a; color: #7ec8ff; border-color: #2a5a8a;
    }
    QPushButton#btn_open:hover  { background: #1e4a72; }
    QPushButton#btn_del  {
        background: #3a1a1a; color: #ff8888; border-color: #5a2a2a;
    }
    QPushButton#btn_del:hover  { background: #4e1e1e; }
    QPushButton#btn_browse {
        background: #1e2e1e; color: #7ec87e;
        border-color: #3a5a3a; padding: 6px 18px; font-size: 12px;
        min-width: 160px;
    }
    QPushButton#btn_browse:hover { background: #2a4a2a; color: #aaffaa; }
    """

    def __init__(self, gpx_dir: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Dialog)
        self._dir = gpx_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self.setWindowTitle(t('gps_map.gpxdialog.title'))
        self.setMinimumWidth(500)
        self.setMaximumWidth(620)
        self.setStyleSheet(self._QSS)
        self._build_ui()
        self._refresh()

    # ── UI 구성 ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 제목
        lb = QLabel(t('gps_map.gpxdialog.heading'))
        lb.setObjectName('lb_title')
        root.addWidget(lb)

        self._make_divider(root)

        # 스크롤 목록
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(300)
        self._list_body = QWidget()
        self._list_body.setObjectName('list_body')
        self._list_vbox = QVBoxLayout(self._list_body)
        self._list_vbox.setContentsMargins(0, 0, 4, 0)
        self._list_vbox.setSpacing(3)
        self._list_vbox.addStretch()
        scroll.setWidget(self._list_body)
        root.addWidget(scroll)

        self._make_divider(root)

        # 하단 찾아보기
        bottom = QHBoxLayout()
        bottom.addStretch()
        btn = QPushButton(t('gps_map.gpxdialog.btn_browse'))
        btn.setObjectName('btn_browse')
        btn.clicked.connect(self._on_browse)
        bottom.addWidget(btn)
        root.addLayout(bottom)


    @staticmethod
    def _make_divider(layout: QVBoxLayout) -> None:
        line = QFrame()
        line.setObjectName('div')
        line.setFixedHeight(1)
        layout.addWidget(line)

    # ── 목록 갱신 ────────────────────────────────────────────────────

    def _refresh(self) -> None:
        while self._list_vbox.count() > 1:
            item = self._list_vbox.takeAt(0)
            if item is None: 
                break
            w = item.widget()
            if w:
                w.deleteLater()

        files = sorted(
            self._dir.glob('*.gpx'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not files:
            lb = QLabel(t('gps_map.gpxdialog.empty_state'))
            lb.setObjectName('lb_empty')
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setFixedHeight(80)
            self._list_vbox.insertWidget(0, lb)
            return

        for fp in files:
            self._list_vbox.insertWidget(
                self._list_vbox.count() - 1,
                self._make_row(fp)
            )


    def _make_row(self, fp: Path) -> QFrame:
        row = QFrame()
        row.setObjectName('row')
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 5, 8, 5)
        lay.setSpacing(8)

        # 파일명
        lb_name = QLabel(fp.name)
        lb_name.setStyleSheet('color:#ddd; font-size:12px;')
        lb_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lb_name.setToolTip(str(fp))

        # 크기
        size_kb = fp.stat().st_size / 1024
        lb_size = QLabel(f'{size_kb:.1f} KB')
        lb_size.setStyleSheet('color:#555; font-size:10px; min-width:52px;')
        lb_size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 열기 버튼
        btn_open = QPushButton(t('gps_map.gpxdialog.btn_open'))
        btn_open.setObjectName('btn_open')
        btn_open.setFixedWidth(56)
        btn_open.clicked.connect(lambda _, p=fp: self._on_open(p))

        # 삭제 버튼
        btn_del = QPushButton(t('gps_map.gpxdialog.btn_del'))
        btn_del.setObjectName('btn_del')
        btn_del.setFixedWidth(56)
        btn_del.clicked.connect(lambda _, p=fp: self._on_delete(p))

        lay.addWidget(lb_name)
        lay.addWidget(lb_size)
        lay.addWidget(btn_open)
        lay.addWidget(btn_del)
        return row

    # ── 이벤트 핸들러 ────────────────────────────────────────────────

    def _on_open(self, path: Path) -> None:
        self.file_selected.emit(path)
        self.accept()


    def _on_delete(self, path: Path) -> None:
        _del_dlg = _DarkMessageBox(
            self, kind='question',
            title=t('gps_map.gpxdialog.del_confirm_title'),
            body=t('gps_map.gpxdialog.del_confirm_msg', filename=path.name),
        )
        if _del_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            path.unlink()
            self._refresh()
        except Exception as e:
            _DarkMessageBox(
                self, kind='danger',
                title=t('gps_map.gpxdialog.delete_error_title'),
                body=t('gps_map.gpxdialog.delete_error_msg', err=str(e)),
            ).exec()


    def _on_browse(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, t('gps_map.gpxdialog.browse_title'), '', t('gps_map.gpxdialog.browse_filter')
        )
        if not path_str:
            return

        src  = Path(path_str)
        dest = self._dir / src.name

        # 같은 파일이면 복사 불필요
        if src.resolve() == dest.resolve():
            self.file_selected.emit(dest)
            self.accept()
            return

        # 동일 이름 파일 존재 시 덮어쓰기 확인
        if dest.exists():
            _ow_dlg = _DarkMessageBox(
                self, kind='question',
                title=t('gps_map.gpxdialog.overwrite_title'),
                body=t('gps_map.gpxdialog.overwrite_msg', filename=dest.name),
            )
            if _ow_dlg.exec() != QDialog.DialogCode.Accepted:
                self.file_selected.emit(src)
                self.accept()
                return

        try:
            shutil.copy2(src, dest)
        except Exception as e:
            error_print(f'[GpsMap] GPX 복사 실패: {e}')
            _DarkMessageBox(
                self, kind='warning',
                title=t('gps_map.gpxdialog.copy_error_title'),
                body=t('gps_map.gpxdialog.copy_error_msg', err=str(e)),
            ).exec()
            dest = src 

        self.file_selected.emit(dest)
        self.accept()

