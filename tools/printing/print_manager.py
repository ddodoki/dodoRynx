# -*- coding: utf-8 -*-
# tools\printing\print_manager.py

"""
인쇄 관리자 - 프린터 목록, 설정 관리
"""

import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt
from PySide6.QtPrintSupport import QPrinterInfo

from utils.config_manager import ConfigManager
from utils.debug import debug_print, error_print, info_print

from .print_utils import FitMode, ImageLayout, PaperSize, PrintQuality


class PrintManager(QObject):
    """인쇄 관리자 (싱글톤)"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    

    def __init__(self):
        if self._initialized:
            return
        
        super().__init__()
        self._initialized = True
        
        info_print(f"✅ PrintManager 초기화 시작...")
        
        # 프린터 목록
        self.available_printers: List[str] = []
        self.default_printer: Optional[str] = None
        
        # 설정 관리자
        self.config = ConfigManager()
        
        # 시스템 프린터 로드
        self._load_system_printers()
        
        # 인쇄 설정 로드
        self._load_settings()
        
        info_print(f"✅ PrintManager 초기화 완료: {len(self.available_printers)}개 프린터")
    

    def _load_system_printers(self):
        """시스템 프린터 목록 로드"""
        try:
            # Qt의 QPrinterInfo 사용 (크로스 플랫폼)
            printers = QPrinterInfo.availablePrinters()
            self.available_printers = [p.printerName() for p in printers]
            
            # 기본 프린터
            default = QPrinterInfo.defaultPrinter()
            if not default.isNull():
                self.default_printer = default.printerName()
            
            info_print(f"프린터 목록: {self.available_printers}")
            info_print(f"기본 프린터: {self.default_printer}")
            
            #  Windows 전용: win32print로 추가 정보 확인 (선택사항)
            # try-except로 감싸서 없어도 에러 안 남
            try:
                import win32print  # type: ignore
                win32_default = win32print.GetDefaultPrinter()
                if win32_default and not self.default_printer:
                    self.default_printer = win32_default
                    debug_print(f"Win32 기본 프린터: {win32_default}")
            except ImportError:
                debug_print(f"win32print 없음 (선택사항, Windows 전용)")
            except Exception as e:
                debug_print(f"Win32 프린터 조회 실패 (무시): {e}")
        
        except Exception as e:
            error_print(f"프린터 목록 로드 실패: {e}")
            #  프린터 없어도 프로그램은 계속 실행
            self.available_printers = []
            self.default_printer = None


    def _load_settings(self):
        """저장된 인쇄 설정 로드"""
        try:
            self.last_printer = self.config.get("print.last_printer", self.default_printer)
            self.last_paper_size = self.config.get("print.paper_size", "A4")
            self.last_quality = self.config.get("print.quality", "STANDARD")
            self.last_fit_mode = self.config.get("print.fit_mode", "fit")
            self.last_layout = self.config.get("print.layout", "one")
            
            #  배치 간격 (기본값 5mm)
            self.last_spacing = self.config.get("print.spacing", 5.0)
            
            # 여백
            self.last_margins = {
                'left': self.config.get("print.margin_left", 10.0),
                'top': self.config.get("print.margin_top", 10.0),
                'right': self.config.get("print.margin_right", 10.0),
                'bottom': self.config.get("print.margin_bottom", 10.0),
            }
            
            self.last_copies = self.config.get("print.copies", 1)
            self.last_rotation = 0  # 항상 0
            self.last_overlay = self.config.get("print.overlay", False)
            self.last_color_mode = self.config.get("print.color_mode", "color")
            
            debug_print(f"인쇄 설정 로드: 프린터={self.last_printer}, 용지={self.last_paper_size}, 간격={self.last_spacing}mm")
        
        except Exception as e:
            error_print(f"인쇄 설정 로드 실패 (기본값 사용): {e}")
            # 기본값
            self.last_printer = self.default_printer
            self.last_paper_size = "A4"
            self.last_quality = "STANDARD"
            self.last_fit_mode = "fit"
            self.last_layout = "one"
            self.last_spacing = 5.0 
            self.last_margins = {'left': 10.0, 'top': 10.0, 'right': 10.0, 'bottom': 10.0}
            self.last_copies = 1
            self.last_rotation = 0
            self.last_overlay = False
            self.last_color_mode = "color"


    def save_settings(self, settings: Dict[str, Any]):
        """인쇄 설정 저장 (개선)"""
        try:
            # 프린터명
            printer = settings.get('printer', self.default_printer)
            if printer:
                self.config.set("print.last_printer", printer)
            
            # 용지 크기
            paper_size = settings.get('paper_size')
            if isinstance(paper_size, PaperSize):
                self.config.set("print.paper_size", paper_size.value)
            elif isinstance(paper_size, str):
                self.config.set("print.paper_size", paper_size)
            
            # 품질
            quality = settings.get('quality')
            if isinstance(quality, PrintQuality):
                self.config.set("print.quality", quality.name)
            elif isinstance(quality, str):
                self.config.set("print.quality", quality)
            
            # 배치
            layout = settings.get('layout')
            if isinstance(layout, ImageLayout):
                layout_map = {
                    ImageLayout.ONE_PER_PAGE: 'one',
                    ImageLayout.TWO_PER_PAGE: 'two',
                    ImageLayout.FOUR_PER_PAGE: 'four',
                }
                self.config.set("print.layout", layout_map.get(layout, 'one'))
            elif isinstance(layout, str):
                self.config.set("print.layout", layout)
            
            #  배치 간격
            spacing = settings.get('spacing', 5.0)
            self.config.set("print.spacing", float(spacing))
            
            # 맞추기
            fit_mode = settings.get('fit_mode')
            if isinstance(fit_mode, FitMode):
                self.config.set("print.fit_mode", fit_mode.value)
            elif isinstance(fit_mode, str):
                self.config.set("print.fit_mode", fit_mode)
            
            # 여백
            margins = settings.get('margins', {})
            self.config.set("print.margin_left", float(margins.get('left', 10.0)))
            self.config.set("print.margin_top", float(margins.get('top', 10.0)))
            self.config.set("print.margin_right", float(margins.get('right', 10.0)))
            self.config.set("print.margin_bottom", float(margins.get('bottom', 10.0)))
            
            # 매수
            self.config.set("print.copies", int(settings.get('copies', 1)))
            
            # 회전값은 저장하지 않음 (재실행 시 항상 0도로 리셋)
            # rotation은 저장하지 않음
            
            # 오버레이
            self.config.set("print.overlay", bool(settings.get('overlay', False)))
            
            # 방향
            orientation = settings.get('orientation', Qt.Orientation.Vertical)
            if orientation == Qt.Orientation.Horizontal:
                self.config.set("print.orientation", "horizontal")
            else:
                self.config.set("print.orientation", "vertical")
            
            # 컬러 모드 저장
            color_mode = settings.get('color_mode', 'color')
            self.config.set("print.color_mode", color_mode)
            
            # 저장
            self.config.save()
            
            # 내부 상태 업데이트
            self.last_printer = printer
            self.last_paper_size = paper_size.value if isinstance(paper_size, PaperSize) else paper_size
            self.last_quality = quality.name if isinstance(quality, PrintQuality) else quality
            self.last_margins = margins
            self.last_copies = settings.get('copies', 1)
            # last_rotation 업데이트 제거 (항상 0)
            
            info_print(f"인쇄 설정 저장 완료")
        
        except Exception as e:
            error_print(f"인쇄 설정 저장 실패: {e}")
            error_print(traceback.format_exc())


    def get_default_settings(self) -> Dict[str, Any]:
        """기본 설정 반환 (개선)"""
        # PaperSize enum 변환
        paper_size = PaperSize.A4
        for ps in PaperSize:
            if ps.value == self.last_paper_size:
                paper_size = ps
                break
        
        # PrintQuality enum 변환
        quality = PrintQuality.STANDARD
        for pq in PrintQuality:
            if pq.name == self.last_quality:
                quality = pq
                break
        
        # ImageLayout enum 변환
        layout_map = {
            'one': ImageLayout.ONE_PER_PAGE,
            'two': ImageLayout.TWO_PER_PAGE,
            'four': ImageLayout.FOUR_PER_PAGE,
        }
        layout = layout_map.get(self.last_layout, ImageLayout.ONE_PER_PAGE)
        
        # 방향
        orientation_str = self.config.get("print.orientation", "vertical")
        orientation = Qt.Orientation.Horizontal if orientation_str == "horizontal" else Qt.Orientation.Vertical
        
        return {
            'printer': self.last_printer or self.default_printer,
            'paper_size': paper_size,
            'quality': quality,
            'fit_mode': self.last_fit_mode,
            'layout': layout,
            'spacing': getattr(self, 'last_spacing', 5.0),  
            'margins': self.last_margins.copy(),
            'copies': self.last_copies,
            'rotation': 0,
            'overlay': self.last_overlay,
            'orientation': orientation,
            'color_mode': getattr(self, 'last_color_mode', 'color'),
        }


    def refresh_printers(self):
        """프린터 목록 갱신"""
        info_print(f"🔄 프린터 목록 갱신...")
        self._load_system_printers()

