# -*- coding: utf-8 -*-
# printing/print_settings_widget.py

"""
인쇄 설정 패널 위젯
"""

from typing import Any, Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t

from .print_utils import FitMode, ImageLayout, PaperSize, PrintQuality


class MarginPreset:
    """ 여백 프리셋 정의"""
    
    PRESETS = {
        "custom":   None,
        "none":     {"left": 0.0,  "top": 0.0,  "right": 0.0,  "bottom": 0.0},
        "narrow":   {"left": 5.0,  "top": 5.0,  "right": 5.0,  "bottom": 5.0},
        "normal":   {"left": 10.0, "top": 10.0, "right": 10.0, "bottom": 10.0},
        "wide":     {"left": 20.0, "top": 20.0, "right": 20.0, "bottom": 20.0},
        "document": {"left": 20.0, "top": 15.0, "right": 20.0, "bottom": 15.0},
        "photo":    {"left": 3.0,  "top": 3.0,  "right": 3.0,  "bottom": 3.0},
    }
    
    @classmethod
    def get_preset_names(cls):
        """프리셋 이름 목록 반환"""
        return list(cls.PRESETS.keys())
    
    @classmethod
    def get_preset_values(cls, name: str):
        """프리셋 값 반환"""
        return cls.PRESETS.get(name)
    

class PrintSettingsWidget(QWidget):
    """인쇄 설정 패널"""
    
    # 설정 변경 시그널
    settings_changed = Signal(dict)
    

    def __init__(self, parent=None):
        super().__init__(parent)

        # 현재 회전 각도 추적
        self.current_rotation = 0

        # 프로그래밍 방식 업데이트 플래그 (무한 루프 방지)
        self._updating_margins = False

        self.init_ui()
    

    def init_ui(self):
        """UI 초기화"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # ===== 용지 설정 =====
        paper_group    = QGroupBox(t('print_settings.paper_group'))
        paper_layout = QVBoxLayout()
        
        # 용지 크기
        paper_size_layout = QHBoxLayout()
        paper_size_layout.addWidget(QLabel(t('print_settings.paper_size_label')))
        self.paper_size_combo = QComboBox()
        for size in PaperSize:
            self.paper_size_combo.addItem(size.value, size)
        self.paper_size_combo.currentIndexChanged.connect(self._on_settings_changed)
        paper_size_layout.addWidget(self.paper_size_combo)
        paper_layout.addLayout(paper_size_layout)
        
        # 방향
        orientation_layout = QHBoxLayout()
        orientation_layout.addWidget(QLabel(t('print_settings.orientation_label')))
        self.orientation_combo = QComboBox()
        self.orientation_combo.addItem(t('print_settings.portrait'),  Qt.Orientation.Vertical)
        self.orientation_combo.addItem(t('print_settings.landscape'), Qt.Orientation.Horizontal)
        self.orientation_combo.currentIndexChanged.connect(self._on_settings_changed)
        orientation_layout.addWidget(self.orientation_combo)
        paper_layout.addLayout(orientation_layout)
        
        paper_group.setLayout(paper_layout)
        layout.addWidget(paper_group)
        
        # ===== 품질 설정 =====
        quality_group  = QGroupBox(t('print_settings.quality_group'))
        quality_layout = QVBoxLayout()
        
        self.quality_combo = QComboBox()
        for quality in PrintQuality:
            self.quality_combo.addItem(quality.get_label(), quality)
        self.quality_combo.setCurrentIndex(1)  # 기본: 표준
        self.quality_combo.currentIndexChanged.connect(self._on_settings_changed)
        quality_layout.addWidget(self.quality_combo)
        
        quality_group.setLayout(quality_layout)
        layout.addWidget(quality_group)

        # ===== 컬러 모드 설정 (새로 추가) =====
        color_group    = QGroupBox(t('print_settings.color_group'))
        color_layout = QVBoxLayout()
        
        self.color_combo = QComboBox()
        self.color_combo.addItem(t('print_settings.color_output'),     "color")
        self.color_combo.addItem(t('print_settings.grayscale_output'), "grayscale")
        self.color_combo.currentIndexChanged.connect(self._on_settings_changed)
        self.color_combo.setStyleSheet("""
            QComboBox {
                padding: 5px;
            }
        """)
        color_layout.addWidget(self.color_combo)
        
        color_group.setLayout(color_layout)
        layout.addWidget(color_group)


        # ===== 이미지 배치 =====
        layout_group   = QGroupBox(t('print_settings.layout_group'))
        layout_layout = QVBoxLayout()
        
        # 배치 모드
        self.layout_combo = QComboBox()
        for img_layout in ImageLayout:
            self.layout_combo.addItem(img_layout.get_label(), img_layout)
        self.layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        layout_layout.addWidget(self.layout_combo)
        
        # 배치 간격 설정
        spacing_layout = QHBoxLayout()
        spacing_layout.addWidget(QLabel(t('print_settings.spacing_label')))
        
        self.spacing_spinbox = QDoubleSpinBox()
        self.spacing_spinbox.setRange(0.0, 20.0)
        self.spacing_spinbox.setValue(5.0)
        self.spacing_spinbox.setSuffix(" mm")
        self.spacing_spinbox.setSingleStep(1.0)
        self.spacing_spinbox.setDecimals(1)
        self.spacing_spinbox.valueChanged.connect(self._on_settings_changed)
        self.spacing_spinbox.setToolTip(t('print_settings.spacing_tooltip'))
        
        spacing_layout.addWidget(self.spacing_spinbox)
        layout_layout.addLayout(spacing_layout)
        
        # 간격 안내 라벨
        self.spacing_info_label = QLabel(t('print_settings.spacing_info_one'))
        self.spacing_info_label.setStyleSheet("""
            QLabel {
                color: #888;
                font-size: 10px;
                padding: 2px;
            }
        """)
        layout_layout.addWidget(self.spacing_info_label)
        
        layout_group.setLayout(layout_layout)
        layout.addWidget(layout_group)
            
        # ===== 맞추기 옵션 =====
        fit_group      = QGroupBox(t('print_settings.fit_group'))
        fit_layout = QVBoxLayout()
        
        self.fit_combo = QComboBox()
        self.fit_combo.addItem(t('print_settings.fit_original'), FitMode.ORIGINAL.value)
        self.fit_combo.addItem(t('print_settings.fit_page'),     FitMode.FIT_PAGE.value)
        self.fit_combo.addItem(t('print_settings.fit_fill'),     FitMode.FILL_PAGE.value)
        self.fit_combo.setCurrentIndex(1)  # 기본: 용지에 맞춤
        self.fit_combo.currentIndexChanged.connect(self._on_settings_changed)
        fit_layout.addWidget(self.fit_combo)
        
        fit_group.setLayout(fit_layout)
        layout.addWidget(fit_group)
        
        # ===== 여백 설정 =====
        margin_group   = QGroupBox(t('print_settings.margin_group'))
        margin_layout = QVBoxLayout()
        
        # 여백 프리셋 콤보박스
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel(t('print_settings.preset_label')))
        
        self.margin_preset_combo = QComboBox()
        self.margin_preset_combo.addItems(MarginPreset.get_preset_names())

        target = 'normal'
        idx = MarginPreset.get_preset_names().index(target)
        self.margin_preset_combo.setCurrentIndex(idx)  # 인덱스 3 = "normal" (10mm)

        self.margin_preset_combo.currentIndexChanged.connect(self._on_margin_preset_changed)
        self.margin_preset_combo.setStyleSheet("""
            QComboBox {
                background-color: #3b3b3b;
                color: #4a9eff;
                font-weight: bold;
                padding: 5px;
                border: 2px solid #4a9eff;
                border-radius: 4px;
            }
            QComboBox:hover {
                background-color: #444;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #2b2b2b;
                color: white;
                selection-background-color: #4a9eff;
            }
        """)
        
        preset_layout.addWidget(self.margin_preset_combo)
        margin_layout.addLayout(preset_layout)
        
        # 여백 스핀박스
        self.margin_spinboxes = {}
        for margin_name, label in [
            ('left', 'left'),
            ('top', 'top'),
            ('right', 'right'),
            ('bottom', 'bottom')
        ]:
            
            h_layout = QHBoxLayout()
            h_layout.addWidget(QLabel(f"{label}:"))
            
            spinbox = QDoubleSpinBox()
            spinbox.setRange(0, 50)
            spinbox.setValue(10)
            spinbox.setSuffix(" mm")
            spinbox.setSingleStep(1.0)
            spinbox.setDecimals(1)
            spinbox.valueChanged.connect(self._on_margin_value_changed)
            
            self.margin_spinboxes[margin_name] = spinbox
            h_layout.addWidget(spinbox)
            margin_layout.addLayout(h_layout)
        
        margin_group.setLayout(margin_layout)
        layout.addWidget(margin_group)
        
        # ===== 회전 (버튼 방식으로 변경) =====
        rotation_group = QGroupBox(t('print_settings.rotation_group'))
        rotation_layout = QVBoxLayout()
        
        # 현재 각도 표시
        self.rotation_label = QLabel(t('print_settings.rotation_label', angle=0))
        self.rotation_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rotation_label.setStyleSheet("""
            QLabel {
                font-size: 14px;
                font-weight: bold;
                padding: 10px;
                background-color: #3b3b3b;
                color: #4a9eff;
                border-radius: 4px;
            }
        """)
        rotation_layout.addWidget(self.rotation_label)
        
        # 버튼 레이아웃
        button_layout = QHBoxLayout()
        
        # 왼쪽 회전 버튼 (반시계방향 90°)
        self.rotate_left_btn  = QPushButton(t('print_settings.rotate_left_btn'))
        self.rotate_left_btn.setMinimumHeight(50)
        self.rotate_left_btn.clicked.connect(self._on_rotate_left)
        self.rotate_left_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a9eff;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #357abd;
            }
        """)
        button_layout.addWidget(self.rotate_left_btn)
        
        # 리셋 버튼
        self.rotate_reset_btn = QPushButton(t('print_settings.rotate_reset_btn'))
        self.rotate_reset_btn.setMinimumHeight(50)
        self.rotate_reset_btn.clicked.connect(self._on_rotate_reset)
        self.rotate_reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #666;
            }
        """)
        button_layout.addWidget(self.rotate_reset_btn)
        
        # 오른쪽 회전 버튼 (시계방향 90°)
        self.rotate_right_btn = QPushButton(t('print_settings.rotate_right_btn'))
        self.rotate_right_btn.setMinimumHeight(50)
        self.rotate_right_btn.clicked.connect(self._on_rotate_right)
        self.rotate_right_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a9eff;
                color: white;
                font-weight: bold;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #357abd;
            }
        """)
        button_layout.addWidget(self.rotate_right_btn)
        
        rotation_layout.addLayout(button_layout)
        rotation_group.setLayout(rotation_layout)
        layout.addWidget(rotation_group)
        
        # ===== 매수 =====
        copies_group   = QGroupBox(t('print_settings.copies_group'))
        copies_layout = QHBoxLayout()
        
        self.copies_spinbox = QSpinBox()
        self.copies_spinbox.setRange(1, 99)
        self.copies_spinbox.setValue(1)
        self.copies_spinbox.valueChanged.connect(self._on_settings_changed)
        copies_layout.addWidget(self.copies_spinbox)
        
        copies_group.setLayout(copies_layout)
        layout.addWidget(copies_group)
        
        # 하단 여백
        layout.addStretch()


    def _on_layout_changed(self):
        """ 배치 모드 변경 시"""
        # 1장에 1이미지인지 확인
        current_layout = self.layout_combo.currentData()
        
        if current_layout == ImageLayout.ONE_PER_PAGE:
            # 1장에 1이미지: 간격 비활성화
            self.spacing_spinbox.setEnabled(False)
            self.spacing_info_label.setText(t('print_settings.spacing_info_one'))
            self.spacing_info_label.setStyleSheet("""
                QLabel {
                    color: #888;
                    font-size: 10px;
                    padding: 2px;
                }
            """)
        else:
            # 2개 이상: 간격 활성화
            self.spacing_spinbox.setEnabled(True)
            self.spacing_info_label.setText(t('print_settings.spacing_info_multi'))
            self.spacing_info_label.setStyleSheet("""
                QLabel {
                    color: #4a9eff;
                    font-size: 10px;
                    font-weight: bold;
                    padding: 2px;
                }
            """)
        
        self._on_settings_changed()


    def _on_margin_preset_changed(self, index: int):
        """ 여백 프리셋 변경 시"""
        preset_name = self.margin_preset_combo.currentText()
        preset_values = MarginPreset.get_preset_values(preset_name)
        
        if preset_values is None:
            # "사용자 정의" 선택 시 아무것도 안 함
            debug_print(f"여백 프리셋: 사용자 정의")
            return
        
        debug_print(f"여백 프리셋 적용: {preset_name}")
        
        # 무한 루프 방지 플래그
        self._updating_margins = True
        
        try:
            # 여백 스핀박스 값 업데이트
            for key, value in preset_values.items():
                if key in self.margin_spinboxes:
                    self.margin_spinboxes[key].setValue(value)
        finally:
            self._updating_margins = False
        
        # 설정 변경 이벤트 발생
        self._on_settings_changed()
    

    def _on_margin_value_changed(self):
        """ 여백 값 수동 변경 시"""
        # 프로그래밍 방식 업데이트 중이면 무시
        if self._updating_margins:
            return
        
        # 사용자가 수동으로 값을 변경하면 "사용자 정의"로 전환
        current_preset = self.margin_preset_combo.currentText()
        
        # 현재 여백 값
        current_margins = {
            'left': self.margin_spinboxes['left'].value(),
            'top': self.margin_spinboxes['top'].value(),
            'right': self.margin_spinboxes['right'].value(),
            'bottom': self.margin_spinboxes['bottom'].value(),
        }
        
        # 현재 선택된 프리셋 값과 비교
        preset_values = MarginPreset.get_preset_values(current_preset)
        
        if preset_values is not None:
            # 프리셋 값과 다르면 "사용자 정의"로 변경
            if current_margins != preset_values:
                self._updating_margins = True
                self.margin_preset_combo.setCurrentIndex(0)  # "사용자 정의"
                self._updating_margins = False
                debug_print(f"여백 프리셋: 사용자 정의로 전환")
        
        self._on_settings_changed()


    def set_settings(self, settings: Dict[str, Any]):
        """ 설정 적용 (회전 버그 수정)"""
        # 용지 크기
        paper_size = settings.get('paper_size', 'A4')
        if isinstance(paper_size, str):
            for i in range(self.paper_size_combo.count()):
                if self.paper_size_combo.itemData(i).value == paper_size:
                    self.paper_size_combo.setCurrentIndex(i)
                    break
        elif isinstance(paper_size, PaperSize):
            # PaperSize enum 직접 전달된 경우
            for i in range(self.paper_size_combo.count()):
                if self.paper_size_combo.itemData(i) == paper_size:
                    self.paper_size_combo.setCurrentIndex(i)
                    break
        
        # 방향
        orientation = settings.get('orientation', Qt.Orientation.Vertical)
        for i in range(self.orientation_combo.count()):
            if self.orientation_combo.itemData(i) == orientation:
                self.orientation_combo.setCurrentIndex(i)
                break
        
        # 품질
        quality = settings.get('quality', 'STANDARD')
        if isinstance(quality, str):
            for i in range(self.quality_combo.count()):
                q = self.quality_combo.itemData(i)
                if q.name == quality:
                    self.quality_combo.setCurrentIndex(i)
                    break
        elif isinstance(quality, PrintQuality):
            # PrintQuality enum 직접 전달된 경우
            for i in range(self.quality_combo.count()):
                if self.quality_combo.itemData(i) == quality:
                    self.quality_combo.setCurrentIndex(i)
                    break
        
        # 배치
        layout = settings.get('layout', 'one')
        if isinstance(layout, str):
            layout_map = {
                'one': ImageLayout.ONE_PER_PAGE,
                'two': ImageLayout.TWO_PER_PAGE,
                'four': ImageLayout.FOUR_PER_PAGE,
            }
            layout_enum = layout_map.get(layout, ImageLayout.ONE_PER_PAGE)
            for i in range(self.layout_combo.count()):
                if self.layout_combo.itemData(i) == layout_enum:
                    self.layout_combo.setCurrentIndex(i)
                    break
        elif isinstance(layout, ImageLayout):
            for i in range(self.layout_combo.count()):
                if self.layout_combo.itemData(i) == layout:
                    self.layout_combo.setCurrentIndex(i)
                    break
        
        # 배치 간격
        spacing = settings.get('spacing', 5.0)
        self.spacing_spinbox.setValue(spacing)
        
        # 배치 모드에 따라 간격 활성화/비활성화
        self._on_layout_changed()
        
        # 맞추기
        fit_mode = settings.get('fit_mode', 'fit')
        for i in range(self.fit_combo.count()):
            if self.fit_combo.itemData(i) == fit_mode:
                self.fit_combo.setCurrentIndex(i)
                break
        
        # 여백
        margins = settings.get('margins', {})
        for key, spinbox in self.margin_spinboxes.items():
            spinbox.setValue(margins.get(key, 10.0))
        
        # 컬러 모드
        color_mode = settings.get('color_mode', 'color')
        for i in range(self.color_combo.count()):
            if self.color_combo.itemData(i) == color_mode:
                self.color_combo.setCurrentIndex(i)
                break
        
        # 회전 (항상 0으로 시작)
        rotation = settings.get('rotation', 0)
        #self.current_rotation = 0  # 항상 0으로 리셋
        self.current_rotation = rotation
        self._update_rotation_label()
        
        # 매수
        self.copies_spinbox.setValue(settings.get('copies', 1))


    def _on_rotate_left(self):
        """왼쪽 회전 (반시계방향 90°)"""
        self.current_rotation = (self.current_rotation - 90) % 360
        self._update_rotation_label()
        self._on_settings_changed()
    

    def _on_rotate_right(self):
        """오른쪽 회전 (시계방향 90°)"""
        self.current_rotation = (self.current_rotation + 90) % 360
        self._update_rotation_label()
        self._on_settings_changed()
    

    def _on_rotate_reset(self):
        """회전 리셋"""
        self.current_rotation = 0
        self._update_rotation_label()
        self._on_settings_changed()
    

    def _update_rotation_label(self):
        """회전 각도 라벨 업데이트"""
        self.rotation_label.setText(t('print_settings.rotation_label', angle=self.current_rotation))
    

    def _on_settings_changed(self):
        """설정 변경 시"""
        settings = self.get_settings()
        self.settings_changed.emit(settings)


    def get_settings(self) -> Dict[str, Any]:
        """현재 설정 반환"""
        return {
            'paper_size': self.paper_size_combo.currentData(),
            'orientation': self.orientation_combo.currentData(),
            'quality': self.quality_combo.currentData(),
            'layout': self.layout_combo.currentData(),
            'spacing': self.spacing_spinbox.value(), 
            'fit_mode': self.fit_combo.currentData(),
            'margins': {
                'left': self.margin_spinboxes['left'].value(),
                'top': self.margin_spinboxes['top'].value(),
                'right': self.margin_spinboxes['right'].value(),
                'bottom': self.margin_spinboxes['bottom'].value(),
            },
            'rotation': self.current_rotation,
            'copies': self.copies_spinbox.value(),
            'overlay': False,
            'color_mode': self.color_combo.currentData(),
        }
    
    