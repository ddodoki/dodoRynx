# -*- coding: utf-8 -*-
# tools\gps_map\gps_map_cluster_dialog.py

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QGridLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from tools.gps_map.gps_map_thumbs import DISPLAY_THUMBBAR_SIZE, GpsThumbProvider


class _RepButton(QPushButton):
    def __init__(self, filepath: str, filename: str) -> None:
        super().__init__()
        self.filepath = filepath
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(DISPLAY_THUMBBAR_SIZE + 16, DISPLAY_THUMBBAR_SIZE + 26)
        self.setText(filename)
        self.setStyleSheet(
            "QPushButton{background:#242424;color:#cfcfcf;border:1px solid #444;border-radius:6px;padding:4px;}"
            "QPushButton:hover{border-color:#6a6a6a;background:#2d2d2d;}"
            "QPushButton:checked{border:2px solid #4a9eff;background:#253245;color:#fff;}"
        )


class ClusterRepresentativeDialog(QDialog):
    def __init__(self, provider: GpsThumbProvider, filepaths: list[str], current_rep: str = "", parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._filepaths = filepaths
        self._buttons: dict[str, _RepButton] = {}
        self._selected = current_rep or (filepaths[0] if filepaths else "")
        self.setWindowTitle("대표 썸네일 선택")
        self.resize(420, 320)
        self._provider.thumb_ready.connect(self._on_thumb_ready)
        self._build_ui()
        self._provider.bump_generation()
        self._provider.request_many(filepaths)


    def selected_filepath(self) -> str:
        return self._selected


    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        title = QLabel("줌 아웃 시 겹친 핀 묶음에서 사용할 대표 이미지를 선택하세요.")
        title.setWordWrap(True)
        title.setStyleSheet("color:#d0d0d0;font-size:12px;")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        wrap = QWidget()
        grid = QGridLayout(wrap)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(8)

        for i, fp in enumerate(self._filepaths):
            btn = _RepButton(fp, fp.split('/')[-1])
            btn.clicked.connect(lambda checked=False, path=fp: self._select(path))
            btn.setChecked(fp == self._selected)
            self._buttons[fp] = btn
            grid.addWidget(btn, i // 4, i % 4)

        scroll.setWidget(wrap)
        root.addWidget(scroll, 1)

        ok = QPushButton("확인")
        ok.clicked.connect(self.accept)
        root.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)


    def _select(self, filepath: str) -> None:
        self._selected = filepath
        for fp, btn in self._buttons.items():
            btn.setChecked(fp == filepath)


    def _on_thumb_ready(self, filepath: str, qimg: QImage, _generation: int) -> None:
        btn = self._buttons.get(filepath)
        if btn is None or qimg.isNull():
            return
        from PySide6.QtGui import QIcon, QPixmap

        pix = QPixmap.fromImage(qimg).scaled(
            DISPLAY_THUMBBAR_SIZE, DISPLAY_THUMBBAR_SIZE,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding, 
            Qt.TransformationMode.SmoothTransformation)
        # center crop
        if pix.width() > DISPLAY_THUMBBAR_SIZE or pix.height() > DISPLAY_THUMBBAR_SIZE:
            x = (pix.width() - DISPLAY_THUMBBAR_SIZE) // 2
            y = (pix.height() - DISPLAY_THUMBBAR_SIZE) // 2
            pix = pix.copy(x, y, DISPLAY_THUMBBAR_SIZE, DISPLAY_THUMBBAR_SIZE)
        btn.setIcon(QIcon(pix))
        btn.setIconSize(pix.size())
