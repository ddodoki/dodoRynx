# -*- coding: utf-8 -*-
# ui\dialogs\model_download_dialog.py

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from utils.lang_manager import t
from utils.dark_dialog import DarkMessageBox as _DarkMessageBox

_SS = """
QDialog {
    background : #1a1a1a; color : #d0d0d0;
    border : 1px solid #3a3a3a; border-radius: 8px;
}
QLabel              { color: #c0c0c0; font-size: 12px; background: transparent; }
QLabel#title        { color: #e8e8e8; font-size: 13px; font-weight: 700; }
QLabel#sub          { color: #7a7a7a; font-size: 11px; }
QProgressBar {
    background: #2a2a2a; border: 1px solid #404040;
    border-radius: 4px; height: 18px;
    text-align: center; color: #c8c8c8;
    font-size: 11px; font-weight: 600;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #1a5296, stop:1 #4a9eff);
    border-radius: 3px;
}
QPushButton#cancel {
    background: #3a1e1e; color: #cc8888;
    border: 1px solid #5a2e2e; border-radius: 4px;
    font-size: 11px; padding: 4px 18px; min-height: 26px;
}
QPushButton#cancel:hover    { background: #4a2020; color: #ffaaaa; }
QPushButton#cancel:disabled { color: #555; border-color: #333; background: #222; }
"""

@runtime_checkable
class _DownloadWorkerProtocol(Protocol):
    progress: Any
    finished: Any
    failed:   Any

    def start(self)            -> None: ...
    def isRunning(self)        -> bool: ...
    def wait(self, *args: Any, **kwargs: Any) -> bool: ...
    def cancel(self)           -> None: ...



class ModelDownloadDialog(QDialog):

    def __init__(
        self,
        worker:   _DownloadWorkerProtocol,
        title:    str = "",
        desc:     str = "",
        filename: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._title    = title    or t('bg_remove.dl_title')
        self._desc     = desc     or t('bg_remove.dl_desc')
        self._filename = filename

        self.setStyleSheet(_SS)
        self.setWindowTitle(self._title)
        self.setFixedWidth(460)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        self._build_ui()

        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)


    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        lbl_title = QLabel(self._title)
        lbl_title.setObjectName("title")
        root.addWidget(lbl_title)

        lbl_desc = QLabel(self._desc)
        lbl_desc.setWordWrap(True)
        root.addWidget(lbl_desc)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFormat("%p%")
        root.addWidget(self._bar)

        status_row = QHBoxLayout()
        self._lbl_size = QLabel("0 MB / — MB")
        self._lbl_size.setObjectName("sub")
        self._lbl_file = QLabel(self._filename)
        self._lbl_file.setObjectName("sub")
        self._lbl_file.setAlignment(Qt.AlignmentFlag.AlignRight)
        status_row.addWidget(self._lbl_size)
        status_row.addStretch(1)
        status_row.addWidget(self._lbl_file)
        root.addLayout(status_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_cancel = QPushButton(t('bg_remove.dl_cancel'))
        self._btn_cancel.setObjectName("cancel")
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)
        root.addLayout(btn_row)


    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._worker.isRunning():
            self._worker.start()


    def _start(self) -> None:
        self._worker.start()


    def _on_progress(self, downloaded: int, total: int) -> None:
        if total <= 0:
            self._bar.setRange(0, 0)
            return
        self._bar.setRange(0, 100)
        pct = int(downloaded / total * 100)
        self._bar.setValue(min(99, pct))
        dl_mb    = downloaded / 1024 / 1024
        total_mb = total      / 1024 / 1024
        self._lbl_size.setText(f"{dl_mb:.1f} MB / {total_mb:.1f} MB")


    def _on_finished(self) -> None:
        self._bar.setValue(100)
        self._bar.setFormat(t('bg_remove.dl_done'))
        self._btn_cancel.setEnabled(False)
        QTimer.singleShot(600, self.accept)


    def _on_failed(self, error: str) -> None:
        self._btn_cancel.setEnabled(True)

        if self._worker.isRunning():
            self._worker.wait(500)

        _DarkMessageBox(
            self, kind='danger',
            title=t('bg_remove.dl_fail_title'),
            body=t('bg_remove.dl_fail_msg', error=error[:400]),
        ).exec()
        self.reject()


    def _on_cancel(self) -> None:
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setText(t('dialog.Cancelling'))

        if self._worker.isRunning():
            if hasattr(self._worker, 'cancel'):
                self._worker.cancel()
            try:
                self._worker.finished.disconnect(self._on_finished)
                self._worker.failed.disconnect(self._on_failed)
            except RuntimeError:
                pass
            self._worker.finished.connect(self.reject)
            self._worker.failed.connect(lambda _: self.reject())
        else:
            self.reject()

