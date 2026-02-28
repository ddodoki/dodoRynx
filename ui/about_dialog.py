# -*- coding: utf-8 -*-
# ui/about_dialog.py

"""
프로그램 정보 다이얼로그
"""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from utils.app_meta import APP_AUTHOR, APP_NAME, APP_VERSION, APP_WEBSITE
from utils.lang_manager import t
from utils.paths import (
    ensure_licenses_in_exe_dir,
    get_icon_path,
    get_licenses_dir,
)


class AboutDialog(QDialog):
    """프로그램 정보 다이얼로그"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t('about_dialog.title', name=APP_NAME))
        
        # ===== 창 크기 고정 =====
        self.setFixedSize(700, 850)
        # =======================
        
        # 심플한 다크 테마
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a1a;
                color: #e6edf3;
            }
            
            QLabel {
                color: #e6edf3;
            }
            
            QTextEdit {
                background-color: #2b2b2b;
                color: #e6edf3;
                border: 1px solid #3d3d3d;
                border-radius: 6px;
                padding: 12px;
                font-size: 11px;
            }
            
            QPushButton {
                background-color: #2b2b2b;
                color: #e6edf3;
                border: 1px solid #3d3d3d;
                border-radius: 6px;
                padding: 8px 16px;
                min-width: 100px;
            }
            
            QPushButton:hover {
                background-color: #3d3d3d;
                border-color: #555;
            }
            
            QPushButton:pressed {
                background-color: #1a1a1a;
            }
            
            QPushButton:default {
                background-color: #0d7dd9;
                border-color: #0d7dd9;
            }
            
            QPushButton:default:hover {
                background-color: #1088eb;
            }
            
            QScrollBar:vertical {
                background-color: #1a1a1a;
                width: 10px;
                border-radius: 5px;
            }
            
            QScrollBar::handle:vertical {
                background-color: #3d3d3d;
                border-radius: 5px;
                min-height: 30px;
            }
            
            QScrollBar::handle:vertical:hover {
                background-color: #555;
            }
            
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        
        self._init_ui()
    

    def _init_ui(self):
        """UI 초기화"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # ===== 헤더 =====
        header = QWidget()
        header.setStyleSheet("""
            QWidget {
                background-color: #0d7dd9;
            }
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(25, 20, 25, 20)
        
        # 로고
        logo_label = QLabel()
        logo_path = get_icon_path("logo.png")
        
        if logo_path.exists():
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                pixmap = pixmap.scaled(
                    64, 64,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                logo_label.setPixmap(pixmap)
        else:
            logo_label.setText("🖼️")
            logo_label.setStyleSheet("font-size: 48px;")
        
        header_layout.addWidget(logo_label)
        
        # 프로그램 정보
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)
        
        name_label = QLabel(f"{APP_NAME}")
        name_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #ffffff;")
        info_layout.addWidget(name_label)
        
        version_label = QLabel(f"Version {APP_VERSION}")
        version_label.setStyleSheet("font-size: 13px; color: rgba(255, 255, 255, 200);")
        info_layout.addWidget(version_label)
        
        header_layout.addLayout(info_layout)
        header_layout.addStretch()
        
        layout.addWidget(header)
        
        # ===== 스크롤 영역 =====
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(25, 20, 25, 20)
        content_layout.setSpacing(20)
        
        # ===== 소개 (스크롤바 없이 fit) =====
        intro_title = QLabel(t('about_dialog.intro_title'))
        intro_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #0d7dd9;")
        content_layout.addWidget(intro_title)
        
        intro_text = QLabel(t('about_dialog.intro_text', name=APP_NAME))
        intro_text.setWordWrap(True)
        intro_text.setStyleSheet("""
            padding: 12px;
            background-color: #2b2b2b;
            border: 1px solid #3d3d3d;
            border-radius: 6px;
            line-height: 1.5;
        """)
        content_layout.addWidget(intro_text)
        
        # ===== 라이선스 (스크롤바 없이 fit) =====
        license_title = QLabel(t('about_dialog.license_title'))
        license_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #0d7dd9;")
        content_layout.addWidget(license_title)
        
        license_text = QLabel(t('about_dialog.license_text', name=APP_NAME))
        license_text.setWordWrap(True)
        license_text.setStyleSheet("""
            padding: 12px;
            background-color: #2b2b2b;
            border: 1px solid #3d3d3d;
            border-radius: 6px;
            line-height: 1.5;
        """)
        content_layout.addWidget(license_text)
        
        # ===== 사용법 (스크롤 가능) =====
        usage_title = QLabel(t('about_dialog.usage_title'))
        usage_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #0d7dd9;")
        content_layout.addWidget(usage_title)
        
        usage_text = QLabel()
        usage_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        usage_text.setWordWrap(True)
        usage_text.setText(t('about_dialog.usage_text'))
        content_layout.addWidget(usage_text)
        
        # ===== 웹사이트 =====
        website_label = QLabel(
            f'🌐 <a href="{APP_WEBSITE}" style="color: #0d7dd9; text-decoration: none;">{APP_WEBSITE}</a>'
        )

        website_label.setOpenExternalLinks(False)
        website_label.linkActivated.connect(
            lambda url: QDesktopServices.openUrl(QUrl(url))
        )

        website_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        website_label.setStyleSheet("padding: 10px;")
        content_layout.addWidget(website_label)
        
        scroll.setWidget(content)
        layout.addWidget(scroll)
        
        # ===== 하단 버튼 =====
        button_widget = QWidget()
        button_widget.setStyleSheet("""
            QPushButton {
                background-color: #757575;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #595959;
            }
        """)
        button_layout = QHBoxLayout(button_widget)
        button_layout.setContentsMargins(20, 12, 20, 12)
        button_layout.addStretch()
        
        licenses_btn = QPushButton(t('about_dialog.licenses_btn'))
        licenses_btn.setToolTip(t('about_dialog.licenses_tooltip'))
        licenses_btn.clicked.connect(self._open_licenses_folder)
        button_layout.addWidget(licenses_btn)
        
        close_btn = QPushButton(t('about_dialog.close_btn'))
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        button_layout.addWidget(close_btn)
        
        layout.addWidget(button_widget)
    
    
    def _open_licenses_folder(self):
        """라이선스 폴더 열기"""
        ensure_licenses_in_exe_dir()
        
        licenses_dir = get_licenses_dir()
        
        if not licenses_dir.exists():
            QMessageBox.warning(
                self,
                t('about_dialog.no_folder_title'),
                t('about_dialog.no_folder_msg', path=licenses_dir),
            )
            return
        
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(licenses_dir)))
        if not ok:
            QMessageBox.warning(
                self,
                t('about_dialog.open_error_title'),
                t('about_dialog.open_error_msg'),
            )