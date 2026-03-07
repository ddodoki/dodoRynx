# -*- coding: utf-8 -*-
# ui/system_info_dialog.py

"""
시스템 정보 다이얼로그
GPU 가속 상태 및 시스템 정보 표시
"""

import platform
import subprocess

import psutil
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from utils.app_meta import APP_NAME, APP_VERSION
from utils.config_manager import ConfigManager
from utils.debug import debug_print, info_print
from utils.lang_manager import t


class SystemInfoDialog(QDialog):
    """시스템 정보 다이얼로그"""
    
    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        
        self.config = config
        
        self.setWindowTitle(t('system_info_dialog.title'))
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        
        self._init_ui()
    
    
    def _init_ui(self):
        """UI 초기화"""
        layout = QVBoxLayout(self)
        
        title = QLabel(t('system_info_dialog.heading', name=APP_NAME))
        title.setStyleSheet("""
            font-size: 16px;
            font-weight: bold;
            padding: 15px;
            color: #4a9eff;
        """)
        layout.addWidget(title)
        
        gpu_status = self._create_gpu_status_widget()
        layout.addWidget(gpu_status)
        
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setFont(QFont("Consolas", 9))
        self.info_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 10px;
            }
        """)
        
        info = self._gather_system_info()
        self.info_text.setPlainText(info)
        
        layout.addWidget(self.info_text)
        
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        close_btn = QPushButton(t('system_info_dialog.close_btn'))
        close_btn.setMinimumWidth(100)
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
    
    
    def _create_gpu_status_widget(self) -> QGroupBox:
        """GPU 가속 상태 위젯 생성"""
        group = QGroupBox(t('system_info_dialog.gpu_group'))
        layout = QVBoxLayout(group)
        
        use_opengl = self.config.get_rendering_setting('use_opengl', True)
        opengl_available = self._check_opengl_available()
        
        if use_opengl and opengl_available:
            # GPU 가속 활성화
            vsync = self.config.get_rendering_setting('vsync', True)
            msaa = self.config.get_rendering_setting('msaa_samples', 4)
            opengl_version = self._get_opengl_version()
            
            vsync_str = t('system_info_dialog.vsync_on') if vsync else t('system_info_dialog.vsync_off')
            status_label = QLabel(
                t('system_info_dialog.gpu_active',
                  version=opengl_version, msaa=msaa, vsync=vsync_str)
            )
            status_label.setStyleSheet("""
                QLabel {
                    background-color: rgba(76, 175, 80, 30);
                    color: #4caf50;
                    padding: 15px;
                    border-radius: 5px;
                    border: 2px solid rgba(76, 175, 80, 100);
                    font-size: 12px;
                }
            """)
        
        elif use_opengl and not opengl_available:
            # OpenGL 사용 불가
            status_label = QLabel(t('system_info_dialog.gpu_unavailable'))
            status_label.setStyleSheet("""
                QLabel {
                    background-color: rgba(255, 152, 0, 30);
                    color: #ff9800;
                    padding: 15px;
                    border-radius: 5px;
                    border: 2px solid rgba(255, 152, 0, 100);
                    font-size: 12px;
                }
            """)
        
        else:
            # 소프트웨어 렌더링
            status_label = QLabel(t('system_info_dialog.gpu_software'))
            status_label.setStyleSheet("""
                QLabel {
                    background-color: rgba(100, 100, 100, 30);
                    color: #aaa;
                    padding: 15px;
                    border-radius: 5px;
                    border: 2px solid rgba(100, 100, 100, 100);
                    font-size: 12px;
                }
            """)
        
        status_label.setWordWrap(True)
        layout.addWidget(status_label)
        
        tip_label = QLabel(t('system_info_dialog.rendering_tip'))
        tip_label.setStyleSheet("color: #888; font-size: 10px; padding: 5px;")
        layout.addWidget(tip_label)
        
        return group
    
    
    def _gather_system_info(self) -> str:
        """시스템 정보 수집"""
        lines = []
        
        # 애플리케이션 정보
        lines.append("=" * 60)
        lines.append(t('system_info_dialog.section_app'))
        lines.append("=" * 60)
        lines.append(t('system_info_dialog.app_name',    name=APP_NAME))
        lines.append(t('system_info_dialog.app_version', version=APP_VERSION))
        lines.append("")
        
        # 시스템 정보
        lines.append("=" * 60)
        lines.append(t('system_info_dialog.section_system'))
        lines.append("=" * 60)
        lines.append(t('system_info_dialog.sys_os',        system=platform.system(), release=platform.release()))
        lines.append(t('system_info_dialog.sys_version',   version=platform.version()))
        lines.append(t('system_info_dialog.sys_arch',      machine=platform.machine()))
        lines.append(t('system_info_dialog.sys_processor', processor=platform.processor()))
        lines.append("")
        
        # 메모리 정보
        lines.append("=" * 60)
        lines.append(t('system_info_dialog.section_memory'))
        lines.append("=" * 60)
        try:
            mem = psutil.virtual_memory()
            lines.append(t('system_info_dialog.mem_total',     value=mem.total / (1024**3)))
            lines.append(t('system_info_dialog.mem_available', value=mem.available / (1024**3)))
            lines.append(t('system_info_dialog.mem_usage',     value=mem.percent))
        except Exception as e:
            lines.append(t('system_info_dialog.mem_error', error=e))
        lines.append("")
        
        # 렌더링 정보
        lines.append("=" * 60)
        lines.append(t('system_info_dialog.section_rendering'))
        lines.append("=" * 60)
        
        use_opengl = self.config.get_rendering_setting('use_opengl', True)
        status = t('system_info_dialog.render_opengl_on') if use_opengl else t('system_info_dialog.render_opengl_off')
        lines.append(t('system_info_dialog.render_opengl', status=status))
        
        if use_opengl:
            opengl_info = self._get_opengl_details()
            lines.extend(opengl_info)
        else:
            lines.append(t('system_info_dialog.render_software'))
        lines.append("")
        
        # 캐시 설정
        lines.append("=" * 60)
        lines.append(t('system_info_dialog.section_cache'))
        lines.append("=" * 60)
        lines.append(t('system_info_dialog.cache_ahead',   count=self.config.get('cache.ahead_count', 25)))
        lines.append(t('system_info_dialog.cache_behind',  count=self.config.get('cache.behind_count', 5)))
        lines.append(t('system_info_dialog.cache_max_mem', value=self.config.get('cache.max_memory_mb', 500)))
        
        return "\n".join(lines)
    
    
    def _check_opengl_available(self) -> bool:
        """OpenGL 사용 가능 여부 확인"""
        try:
            from PySide6.QtOpenGLWidgets import QOpenGLWidget
            return True
        except Exception as e:
            debug_print(f"OpenGL 사용 불가: {e}")
            return False
    
    
    def _get_opengl_version(self) -> str:
        """OpenGL 버전 가져오기 (간단)"""
        try:
            return "3.3+"
        except:
            return t('system_info_dialog.opengl_unknown')
    
    
    def _get_opengl_details(self) -> list:
        """OpenGL 상세 정보"""
        lines = []
        
        try:
            vsync = self.config.get_rendering_setting('vsync', True)
            msaa = self.config.get_rendering_setting('msaa_samples', 4)
            
            lines.append(t('system_info_dialog.opengl_version'))
            vsync_str = t('system_info_dialog.vsync_on') if vsync else t('system_info_dialog.vsync_off')
            lines.append(t('system_info_dialog.opengl_vsync', status=vsync_str))
            lines.append(t('system_info_dialog.opengl_msaa',  value=msaa))
            
            # GPU 정보 시도
            try:
                if platform.system() == 'Windows':
                    # wmic로 GPU 정보 가져오기
                    result = subprocess.run(
                        ['wmic', 'path', 'win32_VideoController', 'get', 'name'],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    gpu_name = result.stdout.strip().split('\n')[1].strip()
                    if gpu_name:
                        lines.append(t('system_info_dialog.opengl_gpu', name=gpu_name))
            except:
                pass
        
        except Exception as e:
            lines.append(t('system_info_dialog.opengl_error', error=e))
        
        return lines
