# -*- coding: utf-8 -*-
# utils/dark_dialog.py
"""
다크 테마 다이얼로그 유틸리티.
QMessageBox / QInputDialog 의 OS 기본 스타일을 대체한다.

사용법::
    from utils.dark_dialog import DarkInputDialog, DarkMessageBox

    # 텍스트 입력
    dlg = DarkInputDialog(self, title="새 폴더", label="폴더 이름")
    if dlg.exec() == QDialog.DialogCode.Accepted:
        name = dlg.value()

    # 메시지
    DarkMessageBox(self, kind="warning", title="오류", body=msg).exec()

    # 확인 (question)
    dlg = DarkMessageBox(self, kind="question", title="삭제", body="삭제할까요?")
    if dlg.exec() == QDialog.DialogCode.Accepted:
        ...  # 예 클릭
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QFontComboBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QColorDialog
)
from PySide6.QtGui import QColor, QFont
from utils.lang_manager import t as _t


_DLG_STYLE = """
QDialog { background-color: #1e1e1e; }
QLabel#dlg_title  {
    color: #ffffff;
    font-size: 13px;
    font-weight: 600;
    background: transparent;    
}
QLabel#dlg_body   {
    color: #bbbbbb;
    font-size: 11px;
    background: transparent;    
}
QLineEdit {
    background-color: #2a2a2a;
    color: #ffffff;
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 5px;
    padding: 6px 10px;
    font-size: 12px;
    selection-background-color: rgba(74,158,255,0.45);
}
QLineEdit:focus { border: 1px solid #4a9eff; }
QPushButton {
    border-radius: 5px;
    font-size: 11px;
    font-weight: 500;
    padding: 6px 18px;
    min-width: 64px;
}
QPushButton#btn_primary { background-color: #4a9eff; color: #fff; border: none; }
QPushButton#btn_primary:hover   { background-color: #5aaeff; }
QPushButton#btn_primary:pressed { background-color: #2a7ed3; }
QPushButton#btn_danger  { background-color: #e05555; color: #fff; border: none; }
QPushButton#btn_danger:hover   { background-color: #f06060; }
QPushButton#btn_danger:pressed { background-color: #c03030; }
QPushButton#btn_cancel {
    background-color: transparent;
    color: #aaaaaa;
    border: 1px solid rgba(255,255,255,0.14);
}
QPushButton#btn_cancel:hover {
    background-color: rgba(255,255,255,0.07);
    color: #ffffff;
}
"""

_FRAME_STYLE = """
QFrame#dlg_frame {
    background-color: #1e1e1e;
    border: 1px solid rgba(255,255,255,0.13);
    border-radius: 8px;
}
"""

_SEP_STYLE = "background: rgba(255,255,255,0.07); max-height:1px; border:none;"

_ICON_MAP = {
    "info":     ("\u2139",  "#4a9eff"),
    "warning":  ("\u26a0",  "#f0a830"),
    "danger":   ("\u26a0",  "#e05555"),
    "question": ("?",        "#4a9eff"),
}


class DarkDialog(QDialog):
    """프레임리스 다크 다이얼로그 베이스."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(_DLG_STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._frame = QFrame()
        self._frame.setObjectName("dlg_frame")
        self._frame.setStyleSheet(_FRAME_STYLE)
        outer.addWidget(self._frame)

        self._inner = QVBoxLayout(self._frame)
        self._inner.setContentsMargins(24, 20, 24, 20)
        self._inner.setSpacing(10)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


class DarkInputDialog(DarkDialog):
    """QInputDialog.getText 대체."""

    def __init__(
        self, parent=None, title: str = "", label: str = "", text: str = "", required: bool = True
    ) -> None:
        super().__init__(parent)
        self.setMinimumWidth(360)

        if title:
            lbl_t = QLabel(title)
            lbl_t.setObjectName("dlg_title")
            self._inner.addWidget(lbl_t)

        if label:
            lbl_l = QLabel(label)
            lbl_l.setObjectName("dlg_body")
            self._inner.addWidget(lbl_l)

        self._edit = QLineEdit(text)
        self._edit.selectAll()
        self._inner.addWidget(self._edit)
        self._inner.addSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()

        btn_cancel = QPushButton("취소")
        btn_cancel.setObjectName("btn_cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_ok = QPushButton("확인")
        btn_ok.setObjectName("btn_primary")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_ok)

        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        self._inner.addLayout(row)
        self._edit.returnPressed.connect(self._on_ok)
        self._required = required


    def _on_ok(self) -> None:
        if self._required and not self._edit.text().strip():
            return        
        self.accept()


    def value(self) -> str:
        return self._edit.text().strip()


    @staticmethod
    def getText(
        parent,
        title: str,
        label: str,
        echo: QLineEdit.EchoMode = QLineEdit.EchoMode.Normal,
        text: str = "",
        flags: Qt.WindowType = Qt.WindowType.Dialog,  
    ) -> tuple[str, bool]:
        """QInputDialog.getText 드롭인 대체 — 다크 프레임리스 테마 적용."""
        dlg = DarkInputDialog(parent, title=title, label=label, text=text)
        if echo != QLineEdit.EchoMode.Normal:
            dlg._edit.setEchoMode(echo)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        return (dlg._edit.text() if accepted else text), accepted
    

class DarkMessageBox(DarkDialog):
    """QMessageBox 대체.

    kind: ``"info"`` | ``"warning"`` | ``"danger"`` | ``"question"``

    - ``"question"``  → 아니오 / 예(빨간색) 버튼. Accepted = Yes.
    - 나머지           → 확인 버튼 1개.
    """

    def __init__(
        self, parent=None, kind: str = "info", title: str = "", body: str = ""
    ) -> None:
        super().__init__(parent)
        self.setMinimumWidth(340)

        icon_char, icon_color = _ICON_MAP.get(kind, ("\u2139", "#4a9eff"))

        hdr = QHBoxLayout()
        hdr.setSpacing(10)
        lbl_icon = QLabel(icon_char)
        lbl_icon.setStyleSheet(
            f"color: {icon_color}; font-size: 17px; font-weight: 700;"
        )
        lbl_icon.setFixedWidth(22)
        lbl_title = QLabel(title)
        lbl_title.setObjectName("dlg_title")
        hdr.addWidget(lbl_icon)
        hdr.addWidget(lbl_title, 1)
        self._inner.addLayout(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(_SEP_STYLE)
        self._inner.addWidget(sep)

        if body:
            lbl_body = QLabel(body)
            lbl_body.setObjectName("dlg_body")
            lbl_body.setWordWrap(True)
            lbl_body.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._inner.addWidget(lbl_body)

        self._inner.addSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()

        if kind == "question":
            btn_no = QPushButton("아니오")
            btn_no.setObjectName("btn_cancel")
            btn_no.clicked.connect(self.reject)
            btn_yes = QPushButton("예")
            btn_yes.setObjectName("btn_danger")
            btn_yes.setDefault(True)
            btn_yes.clicked.connect(self.accept)
            row.addWidget(btn_no)
            row.addWidget(btn_yes)
        else:
            btn_ok = QPushButton("확인")
            btn_ok.setObjectName("btn_primary")
            btn_ok.setDefault(True)
            btn_ok.clicked.connect(self.accept)
            row.addWidget(btn_ok)

        self._inner.addLayout(row)


_DarkDialog      = DarkDialog
_DarkInputDialog = DarkInputDialog
_DarkMessageBox  = DarkMessageBox


# ─────────────────────────────────────────────────────────────────────────────
# 편집 저장 방식 선택 다이얼로그 (3-버튼: 같은폴더 / 다른이름 / 취소)
# ─────────────────────────────────────────────────────────────────────────────

class DarkSaveDialog(DarkDialog):
    """편집 완료 후 저장 방식을 선택하는 전용 다이얼로그.

    결과값::
        DarkSaveDialog.SAME_FOLDER  — 같은 폴더에 저장
        DarkSaveDialog.SAVE_AS      — 다른 이름으로 저장
        DarkSaveDialog.DISCARD      — 취소 (버리기)
    """

    SAME_FOLDER = 1
    SAVE_AS     = 2
    DISCARD     = 0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(360)
        self._result = self.DISCARD

        # 제목
        lbl_title = QLabel(_t('edit_dialog.save_title'))
        lbl_title.setObjectName("dlg_title")
        self._inner.addWidget(lbl_title)

        # 본문
        lbl_body = QLabel(_t('edit_dialog.save_text'))
        lbl_body.setObjectName("dlg_body")
        lbl_body.setWordWrap(True)
        self._inner.addWidget(lbl_body)

        # 부가 설명
        lbl_info = QLabel(_t('edit_dialog.save_info'))
        lbl_info.setObjectName("dlg_body")
        lbl_info.setWordWrap(True)
        lbl_info.setStyleSheet("color: #888888; font-size: 10px;")
        self._inner.addWidget(lbl_info)

        self._inner.addSpacing(4)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(_SEP_STYLE)
        self._inner.addWidget(sep)
        self._inner.addSpacing(4)

        # 버튼 행
        row = QHBoxLayout()
        row.setSpacing(8)

        btn_discard = QPushButton(_t('edit_dialog.btn_discard'))
        btn_discard.setObjectName("btn_cancel")
        btn_discard.clicked.connect(self._on_discard)

        btn_save_as = QPushButton(_t('edit_dialog.btn_save_as'))
        btn_save_as.setObjectName("btn_primary")
        btn_save_as.clicked.connect(self._on_save_as)

        btn_same = QPushButton(_t('edit_dialog.btn_same_folder'))
        btn_same.setObjectName("btn_primary")
        btn_same.setDefault(True)
        btn_same.clicked.connect(self._on_same_folder)

        row.addWidget(btn_discard)
        row.addStretch()
        row.addWidget(btn_save_as)
        row.addWidget(btn_same)
        self._inner.addLayout(row)


    def _on_same_folder(self) -> None:
        self._result = self.SAME_FOLDER
        self.accept()


    def _on_save_as(self) -> None:
        self._result = self.SAVE_AS
        self.accept()


    def _on_discard(self) -> None:
        self._result = self.DISCARD
        self.reject()


    def exec(self) -> int:  # type: ignore[override]
        super().exec()
        return self._result


# 모듈 내부 호환용 alias
_DarkSaveDialog = DarkSaveDialog



_TEXT_EXTRA_SS = """
QTextEdit {
    background: #1a1a1a;
    color: #e8e8e8;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 5px;
    padding: 8px 10px;
    font-size: 12px;
    selection-background-color: rgba(74,158,255,0.40);
}
QTextEdit:focus { border-color: #4a9eff; }
QFontComboBox {
    background: #2a2a2a; color: #e8e8e8;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 4px; padding: 0 6px;
    min-height: 28px; font-size: 11px;
}
QFontComboBox QAbstractItemView {
    background: #1e1e1e; color: #eee;
    selection-background-color: #1a3f6b;
}
QSpinBox {
    background: #2a2a2a; color: #e8e8e8;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 4px; padding: 0 4px;
    min-height: 28px; min-width: 64px; font-size: 11px;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 14px; background: #333; border: none;
}

"""


class DarkTextEditDialog(DarkDialog):
    """텍스트 입력/편집 다이얼로그.

    dlg = DarkTextEditDialog(parent, title="제목", text=item.text, ...)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        props = dlg.result_props()
    """

    def __init__(
        self,
        parent=None,
        *,
        title: str = "",
        text: str = "",
        font_family: str = "",
        font_size: int = 24,
        bold: bool = False,
        italic: bool = False,
        color: "QColor | None" = None,
        text_height: int = 100,
    ) -> None:
        super().__init__(parent)
        self.setMinimumWidth(460)
        self.setStyleSheet(self.styleSheet() + _TEXT_EXTRA_SS)

        # ── 타이틀 ──────────────────────────────────────────────
        if title:
            lbl = QLabel(title)
            lbl.setObjectName("dlg_title")
            self._inner.addWidget(lbl)

            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet(_SEP_STYLE)
            self._inner.addWidget(sep)

        # ── 폰트 컨트롤 행 ──────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(6)

        self._fontcb = QFontComboBox()
        if font_family:
            self._fontcb.setCurrentFont(QFont(font_family))

        self._sizespin = QSpinBox()
        self._sizespin.setRange(8, 500)
        self._sizespin.setValue(font_size)
        self._sizespin.setSuffix(" px")

        self._btnbold = QPushButton("B")
        self._btnbold.setObjectName("btnbold")
        self._btnbold.setCheckable(True)
        self._btnbold.setChecked(bold)
        self._btnbold.setStyleSheet("""
            QPushButton          { font-weight: 700; }
            QPushButton:checked  { background: #1a3f6b; color: #6ab4ff; border-color: #2a68b0; }
            QPushButton:checked:hover { background: #1e4d82; }
        """)

        self._btnitalic = QPushButton("I")
        self._btnitalic.setObjectName("btnitalic")
        self._btnitalic.setCheckable(True)
        self._btnitalic.setChecked(italic)
        self._btnitalic.setStyleSheet("""
            QPushButton          { font-style: italic; }
            QPushButton:checked  { background: #1a3f6b; color: #6ab4ff; border-color: #2a68b0; }
            QPushButton:checked:hover { background: #1e4d82; }
        """)

        self._color: list[QColor] = [color if color is not None else QColor(255, 255, 255)]
        self._btncolor = QPushButton()
        self._btncolor.setFixedSize(28, 28)
        self._btncolor.setToolTip(_t('dialog.pick_color'))
        self._refresh_color_btn()
        self._btncolor.clicked.connect(self._pick_color)

        row.addWidget(self._fontcb, 1)
        row.addWidget(self._sizespin)
        row.addWidget(self._btnbold)
        row.addWidget(self._btnitalic)
        row.addWidget(self._btncolor)
        self._inner.addLayout(row)

        # ── 텍스트 에디터 ────────────────────────────────────────
        self._textedit = QTextEdit()
        self._textedit.setPlainText(text)
        self._textedit.setMinimumHeight(text_height)
        self._inner.addWidget(self._textedit)

        # ── 버튼 행 ─────────────────────────────────────────────
        self._inner.addSpacing(6)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        btn_cancel = QPushButton(_t('dialog.Cancel'))
        btn_cancel.setObjectName("btncancel")
        btn_cancel.clicked.connect(self.reject)

        btn_ok = QPushButton(_t('dialog.OK'))
        btn_ok.setObjectName("btnprimary")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)

        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        self._inner.addLayout(btn_row)

    # ── 내부 헬퍼 ───────────────────────────────────────────────

    def _refresh_color_btn(self) -> None:
        c = self._color[0]
        fg = "#000" if c.lightness() > 128 else "#fff"
        self._btncolor.setStyleSheet(
            f"background:{c.name()}; color:{fg};"
            "border:1px solid rgba(255,255,255,0.20); border-radius:4px;"
        )

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(self._color[0], self)
        if c.isValid():
            self._color[0] = c
            self._refresh_color_btn()

    # ── 결과 접근자 ──────────────────────────────────────────────

    @property
    def text(self) -> str:
        return self._textedit.toPlainText()

    @property
    def font_family(self) -> str:
        return self._fontcb.currentFont().family()

    @property
    def font_size(self) -> int:
        return self._sizespin.value()

    @property
    def bold(self) -> bool:
        return self._btnbold.isChecked()

    @property
    def italic(self) -> bool:
        return self._btnitalic.isChecked()

    @property
    def color(self) -> QColor:
        return self._color[0]

    def result_props(self) -> dict:
        """item.update_properties(**dlg.result_props()) 에 바로 사용."""
        return dict(
            text=self.text or _t('shape_text_mixin.default_text'),
            font_family=self.font_family,
            font_size=self.font_size,
            color=self.color,
            bold=self.bold,
            italic=self.italic,
        )


# 모듈 외부 노출
DarkTextEditDialog = DarkTextEditDialog