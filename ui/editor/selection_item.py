# -*- coding: utf-8 -*-
# ui\editor\selection_item.py

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsRectItem


class SelectionItem(QGraphicsRectItem):
    """드래그로 그리는 점선 선택 영역"""

    def __init__(self) -> None:
        super().__init__()

        pen = QPen(QColor(74, 158, 255), 1.5, Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(QColor(74, 158, 255, 30))   # 반투명 파랑
        self.setZValue(100)    
        self.setVisible(False)


    def set_rect(self, rect: QRectF) -> None:
        self.setRect(rect)
        self.setVisible(not rect.isEmpty())
