# -*- coding: utf-8 -*-
# ui\dialogs\dep_install_dialog.py

from __future__ import annotations

import subprocess
import sys
from typing import List

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QProgressBar, QPushButton, QTextEdit,
)

from utils.lang_manager import t 

_SS = """
QDialog {
    background: #1a1a1a; color: #d0d0d0;
    border: 1px solid #3a3a3a; border-radius: 8px;
}
QLabel          { color: #c0c0c0; font-size: 12px; background: transparent; }
QLabel#title    { color: #e8e8e8; font-size: 13px; font-weight: 700; }
QTextEdit       {
    background: #111; color: #888; font-family: Consolas, monospace;
    font-size: 10px; border: 1px solid #333; border-radius: 4px;
}
QProgressBar {
    background: #2a2a2a; border: 1px solid #404040;
    border-radius: 4px; height: 16px;
    text-align: center; color: #ccc; font-size: 10px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #1a5296, stop:1 #4a9eff);
    border-radius: 3px;
}
QPushButton#ok {
    background: #163354; color: #5aaaff;
    border: 1px solid #2563a8; border-radius: 4px;
    font-size: 11px; padding: 4px 18px; min-height: 26px;
}
QPushButton#ok:hover   { background: #1e4a7a; }
QPushButton#ok:disabled { background: #222; color: #555; border-color: #333; }
QPushButton#cancel {
    background: #2a2a2a; color: #888;
    border: 1px solid #444; border-radius: 4px;
    font-size: 11px; padding: 4px 18px; min-height: 26px;
}
QPushButton#cancel:hover { background: #3a1e1e; color: #cc8888; }
"""

_INSTALL_CMDS = {
    "onnxruntime": ["onnxruntime"],
}


class _InstallWorker(QThread):
    log_line = Signal(str)
    pkg_done = Signal(str, bool)
    all_done = Signal(bool)

    def __init__(self, packages: List[str], parent=None) -> None:
        super().__init__(parent)
        self._packages = packages

    def run(self) -> None:
        all_ok = True
        for pkg_key in self._packages:
            key  = pkg_key.split()[0]
            args = _INSTALL_CMDS.get(key, [key])

            cmd = [sys.executable, "-m", "pip", "install"] + args
            self.log_line.emit(f"$ {' '.join(cmd)}\n")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.log_line.emit(line)
                proc.wait()
                success = (proc.returncode == 0)
            except Exception as e:
                self.log_line.emit(t('dep_install_dialog.error', error=e))
                success = False

            self.pkg_done.emit(key, success)
            if not success:
                all_ok = False

        self.all_done.emit(all_ok)


class DepInstallDialog(QDialog):
    """
    누락 패키지 자동 설치 다이얼로그.
    설치 완료 → accept() / 실패·취소 → reject()
    """

    def __init__(self, missing: List[str], parent=None) -> None:
        super().__init__(parent)
        self._missing = missing
        self._worker: _InstallWorker | None = None
        self._success = False

        self.setStyleSheet(_SS)
        self.setWindowTitle(t('dep_install_dialog.window_title'))
        self.setFixedWidth(500)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowTitleHint
        )
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)

        lbl_title = QLabel(t('dep_install_dialog.title'))
        lbl_title.setObjectName("title")
        root.addWidget(lbl_title)

        pkg_text = "\n".join(f"  • {p.split()[0]}" for p in self._missing)
        root.addWidget(QLabel(t('dep_install_dialog.packages_label', packages=pkg_text)))

        warn = QLabel(t('dep_install_dialog.size_warning'))
        warn.setStyleSheet("color: #ffaa44; font-size: 11px;")
        root.addWidget(warn)

        self._bar = QProgressBar()
        self._bar.setRange(0, len(self._missing))
        self._bar.setValue(0)
        root.addWidget(self._bar)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(140)
        root.addWidget(self._log)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._btn_install = QPushButton(t('dep_install_dialog.btn_install'))
        self._btn_install.setObjectName("ok")
        self._btn_install.clicked.connect(self._start_install)

        self._btn_cancel = QPushButton(t('dep_install_dialog.btn_cancel'))
        self._btn_cancel.setObjectName("cancel")
        self._btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self._btn_install)
        btn_row.addWidget(self._btn_cancel)
        root.addLayout(btn_row)

    # ── 설치 ──────────────────────────────────────────────────────

    def _start_install(self) -> None:
        self._btn_install.setEnabled(False)
        self._btn_cancel.setEnabled(False)
        self._log.clear()

        self._worker = _InstallWorker(self._missing)
        self._worker.log_line.connect(self._append_log)
        self._worker.pkg_done.connect(self._on_pkg_done)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()


    def _append_log(self, line: str) -> None:
        self._log.insertPlainText(line)
        self._log.ensureCursorVisible()


    def _on_pkg_done(self, pkg: str, success: bool) -> None:
        self._bar.setValue(self._bar.value() + 1)
        key = 'dep_install_dialog.pkg_done_success' if success else 'dep_install_dialog.pkg_done_failed'
        self._append_log(t(key, pkg=pkg))


    def _on_all_done(self, success: bool) -> None:
        self._btn_cancel.setEnabled(True)
        if success:
            self._btn_install.setText(t('dep_install_dialog.btn_done'))
            self._append_log(t('dep_install_dialog.all_done_success'))
            QTimer.singleShot(800, self.accept)
        else:
            self._btn_install.setText(t('dep_install_dialog.btn_retry'))
            self._btn_install.setEnabled(True)
            self._append_log(t('dep_install_dialog.all_done_failed'))


    def reject(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(2000)
        super().reject()


        