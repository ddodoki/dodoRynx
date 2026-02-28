# -*- coding: utf-8 -*-
# printing/print_dialog.py

"""
인쇄 미리보기 다이얼로그
"""

import os
import platform
import subprocess
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QMarginsF, QRectF, Qt, Signal, Slot, QThread
from PySide6.QtGui import QPainter, QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QProgressDialog,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t

from .print_manager import PrintManager
from .print_preview_widget import PrintPreviewWidget, get_image_cache
from .print_settings_widget import PrintSettingsWidget
from .print_utils import (
    FitMode,
    ImageLayout,
    PaperSize,
    PrintQuality,
    calculate_print_size,
    create_printer,
    get_page_size_mm,
    get_qpage_size,
    mm_to_pixels,
    render_image_with_overlay,
    render_page_to_painter,
)


class PrintThread(QThread):
    """실제 인쇄 스레드"""
    
    progress = Signal(int, int)  # (current, total)
    completed = Signal()
    failed = Signal(str)
    
    def __init__(
        self,
        printer: QPrinter,
        image_paths: List[Path],
        metadata_list: List[dict],
        settings: Dict[str, Any]
    ):
        super().__init__()
        
        self.printer = printer
        self.image_paths = image_paths
        self.metadata_list = metadata_list
        self.settings = settings
        self._is_cancelled = False


    def run(self):
        painter = QPainter() 
        try:
            if not painter.begin(self.printer):
                self.failed.emit(t('print.init_fail'))
                return

            layout          = self.settings['layout']
            images_per_page = layout.cols * layout.rows
            total_pages     = (len(self.image_paths) + images_per_page - 1) // images_per_page
            copies          = self.settings['copies']
            actual_dpi      = self.printer.resolution()
            image_cache     = get_image_cache()

            for copy_num in range(copies):
                for page_idx in range(total_pages):
                    if self._is_cancelled:
                        info_print("인쇄 취소됨")
                        return  

                    start_idx     = page_idx * images_per_page
                    end_idx       = min(start_idx + images_per_page, len(self.image_paths))
                    page_images   = self.image_paths[start_idx:end_idx]
                    page_metadata = self.metadata_list[start_idx:end_idx]
                    page_rect     = painter.viewport()

                    success = render_page_to_painter(
                        painter=painter,
                        image_paths=page_images,
                        metadata_list=page_metadata,
                        settings=self.settings,
                        page_size_px=(page_rect.width(), page_rect.height()),
                        dpi=actual_dpi,
                        image_cache=image_cache,
                        cancel_check=lambda: self._is_cancelled,
                    )
                    if not success:
                        raise Exception(t('print.page_render_fail'))

                    current = copy_num * total_pages + page_idx + 1
                    self.progress.emit(current, total_pages * copies)

                    if page_idx < total_pages - 1 or copy_num < copies - 1:
                        self.printer.newPage()

            info_print("✅ 인쇄 완료")
            self.completed.emit()

        except Exception as e:
            error_print(f"인쇄 실패: {e}")
            self.failed.emit(str(e))

        finally:
            if painter.isActive(): 
                painter.end()


    def cancel(self):
        """인쇄 취소"""
        self._is_cancelled = True


class PrintDialog(QDialog):
    """인쇄 미리보기 다이얼로그"""
    
    def __init__(
        self,
        image_paths: List[Path],
        metadata_list: List[dict],
        parent=None
    ):
        super().__init__(parent)
        
        self.image_paths = image_paths
        self.metadata_list = metadata_list
        
        self.print_manager = PrintManager()
        
        self.current_page = 0
        self.total_pages = 1
        
        self.print_thread: Optional[PrintThread] = None
        self._saved_pdf_path: str = ""
        
        self.setWindowTitle(t('print.window_title'))
        self.setMinimumSize(1200, 1000)
        
        self.init_ui()
        self.load_settings()
        self.update_preview()
    

    def init_ui(self):
        """UI 초기화"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # ===== 상단: 프린터 선택 =====
        top_layout = QHBoxLayout()
        
        # 프린터 선택
        top_layout.addWidget(QLabel(t('print.printer_label')))
        self.printer_combo = QComboBox()
        self.printer_combo.addItems(self.print_manager.available_printers)
        
        # 기본 프린터 선택
        if self.print_manager.default_printer:
            idx = self.printer_combo.findText(self.print_manager.default_printer)
            if idx >= 0:
                self.printer_combo.setCurrentIndex(idx)
        
        self.printer_combo.setMinimumWidth(200)
        top_layout.addWidget(self.printer_combo)
        
        # 프린터 속성 버튼
        self.properties_btn = QPushButton(t('print.properties_btn'))
        self.properties_btn.clicked.connect(self._on_printer_properties)
        top_layout.addWidget(self.properties_btn)
        
        # 새로고침 버튼
        refresh_btn = QPushButton("🔄")
        refresh_btn.setMaximumWidth(40)
        refresh_btn.setToolTip(t('print.refresh_tooltip'))
        refresh_btn.clicked.connect(self._on_refresh_printers)
        top_layout.addWidget(refresh_btn)
        
        top_layout.addStretch()
        
        layout.addLayout(top_layout)
        
        # ===== 중앙: 미리보기 + 설정 =====
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 좌측: 미리보기
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        
        self.preview_widget = PrintPreviewWidget()
        preview_layout.addWidget(self.preview_widget)

        # 줌 컨트롤 버튼
        zoom_control_layout = QHBoxLayout()
        
        zoom_out_btn = QPushButton("🔍➖")
        zoom_out_btn.setMaximumWidth(40)
        zoom_out_btn.setToolTip(t('print.zoom_out_tooltip'))
        zoom_out_btn.clicked.connect(self.preview_widget.zoom_out)
        zoom_control_layout.addWidget(zoom_out_btn)
        
        zoom_reset_btn = QPushButton("1:1")
        zoom_reset_btn.setMaximumWidth(50)
        zoom_reset_btn.setToolTip(t('print.zoom_reset_tooltip'))
        zoom_reset_btn.clicked.connect(self.preview_widget.reset_zoom)
        zoom_control_layout.addWidget(zoom_reset_btn)
        
        zoom_in_btn = QPushButton("🔍➕")
        zoom_in_btn.setMaximumWidth(40)
        zoom_in_btn.setToolTip(t('print.zoom_in_tooltip'))
        zoom_in_btn.clicked.connect(self.preview_widget.zoom_in)
        zoom_control_layout.addWidget(zoom_in_btn)
        
        zoom_fit_btn = QPushButton(t('print.zoom_fit_btn'))
        zoom_fit_btn.setMaximumWidth(60)
        zoom_fit_btn.setToolTip(t('print.zoom_fit_tooltip'))
        zoom_fit_btn.clicked.connect(self.preview_widget.zoom_fit)
        zoom_control_layout.addWidget(zoom_fit_btn)
        
        zoom_control_layout.addStretch()

        # 페이지 네비게이션
        nav_layout = QHBoxLayout()
        nav_layout.addLayout(zoom_control_layout)
        nav_layout.addStretch()
        
        self.prev_btn = QPushButton(t('print.prev_btn'))
        self.prev_btn.clicked.connect(self._on_prev_page)
        self.prev_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a9eff;
                color: white;
                font-weight: bold;
                padding: 8px 15px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #357abd;
            }
            QPushButton:disabled {
                background-color: #555;
                color: #888;
            }
        """)
        nav_layout.addWidget(self.prev_btn)
        
        self.page_label = QLabel("1 / 1")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumWidth(100)
        self.page_label.setStyleSheet("""
            QLabel {
                font-size: 16px;
                font-weight: bold;
                padding: 8px 15px;
                background-color: #2b2b2b;
                color: #4a9eff;
                border: 2px solid #4a9eff;
                border-radius: 4px;
            }
        """)
        nav_layout.addWidget(self.page_label)
        
        self.next_btn = QPushButton(t('print.next_btn'))
        self.next_btn.clicked.connect(self._on_next_page)
        self.next_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a9eff;
                color: white;
                font-weight: bold;
                padding: 8px 15px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #357abd;
            }
            QPushButton:disabled {
                background-color: #555;
                color: #888;
            }
        """)
        nav_layout.addWidget(self.next_btn)
        
        nav_layout.addStretch()
        preview_layout.addLayout(nav_layout)
        
        splitter.addWidget(preview_container)
        
        # 우측: 설정
        self.settings_widget = PrintSettingsWidget()
        self.settings_widget.settings_changed.connect(self._on_settings_changed)
        splitter.addWidget(self.settings_widget)
        
        # 분할 비율 (70:30)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        
        layout.addWidget(splitter)
        
        # ===== 하단: 버튼 =====
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton(t('print.cancel_btn'))
        cancel_btn.setMinimumWidth(100)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        self.pdf_btn   = QPushButton(t('print.pdf_btn'))
        self.pdf_btn.clicked.connect(self._on_save_pdf)
        button_layout.addWidget(self.pdf_btn)
        self.pdf_btn.setStyleSheet("""
            QPushButton {
                background-color: #16ab2a;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #108820;
            }
        """)


        self.print_btn = QPushButton(t('print.print_btn'))
        self.print_btn.setMinimumWidth(100)
        self.print_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a9eff;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #357abd;
            }
        """)
        self.print_btn.clicked.connect(self._on_print)
        button_layout.addWidget(self.print_btn)
        
        layout.addLayout(button_layout)


    def load_settings(self):
        """저장된 설정 로드"""
        settings = self.print_manager.get_default_settings()
        self.settings_widget.set_settings(settings)
    

    def _on_settings_changed(self, settings: dict):
        """설정 변경 시"""
        debug_print(f"설정 변경됨")
        self.update_preview()
    

    def update_preview(self):
        """미리보기 업데이트"""
        settings = self.settings_widget.get_settings()
        
        # 총 페이지 수 계산
        layout = settings['layout']
        images_per_page = layout.cols * layout.rows
        self.total_pages = (len(self.image_paths) + images_per_page - 1) // images_per_page
        
        # 현재 페이지 범위 확인
        if self.current_page >= self.total_pages:
            self.current_page = self.total_pages - 1
        
        # 페이지 라벨 업데이트
        self.page_label.setText(f"{self.current_page + 1} / {self.total_pages}")
        
        # 버튼 활성화 상태
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(self.current_page < self.total_pages - 1)
        
        # 현재 페이지 이미지 추출
        start_idx = self.current_page * images_per_page
        end_idx = min(start_idx + images_per_page, len(self.image_paths))
        
        page_images = self.image_paths[start_idx:end_idx]
        page_metadata = self.metadata_list[start_idx:end_idx]
        
        # 미리보기 렌더링
        self.preview_widget.set_preview(
            self.current_page,
            page_images,
            settings,
            page_metadata
        )
    

    def _on_prev_page(self):
        """이전 페이지"""
        if self.current_page > 0:
            self.current_page -= 1
            self.update_preview()
    

    def _on_next_page(self):
        """다음 페이지"""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.update_preview()
    

    def _on_refresh_printers(self):
        """프린터 목록 새로고침"""
        self.print_manager.refresh_printers()
        
        current = self.printer_combo.currentText()
        self.printer_combo.clear()
        self.printer_combo.addItems(self.print_manager.available_printers)
        
        # 이전 선택 복원
        idx = self.printer_combo.findText(current)
        if idx >= 0:
            self.printer_combo.setCurrentIndex(idx)
    

    def _on_printer_properties(self):
        """프린터 속성 대화상자"""
        try:
            import win32print  # type: ignore
            import win32ui  # type: ignore
            
            printer_name = self.printer_combo.currentText()
            if not printer_name:
                QMessageBox.warning(self, t('print.no_printer_title'), t('print.no_printer_msg'))
                return
            
            QMessageBox.information(
                self,
                t('print.props_title'),
                t('print.props_msg', name=printer_name),
            )
        
        except ImportError:
            QMessageBox.information(self, t('print.props_info_title'), t('print.props_info_msg'))


    def _on_save_pdf(self):
        """PDF로 저장"""
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            t('print.pdf_save_title'),
            str(Path.home() / "print_output.pdf"),
            t('print.pdf_filter'),
        )
        
        if not file_path:
            return
        
        self._saved_pdf_path = file_path
        
        try:
            settings = self.settings_widget.get_settings()
            
            # 컬러 모드 확인 로그
            color_mode = settings.get('color_mode', 'color')
            info_print(f"PDF 저장 - 컬러 모드: {color_mode}")
            
            # PDF 프린터 생성
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(file_path)
            
            # 용지 크기
            page_size = QPageSize(get_qpage_size(settings['paper_size']))
            printer.setPageSize(page_size)
            
            # 방향
            if settings['orientation'] == Qt.Orientation.Horizontal:
                printer.setPageOrientation(QPageLayout.Orientation.Landscape)
            else:
                printer.setPageOrientation(QPageLayout.Orientation.Portrait)
            
            # 여백
            margins_mm = settings['margins']
            margins = QMarginsF(
                margins_mm['left'],
                margins_mm['top'],
                margins_mm['right'],
                margins_mm['bottom']
            )
            printer.setPageMargins(margins, QPageLayout.Unit.Millimeter)
            
            # 해상도
            printer.setResolution(settings['quality'].dpi)
            
            info_print(f"PDF 프린터 설정 완료: {file_path}")
            
            # 설정 저장
            self._save_current_settings()
            
            # PDF 생성 스레드 실행
            self._execute_print_thread(printer, settings, is_pdf=True)
        
        except Exception as e:
            error_print(f"PDF 저장 실패: {e}")

            error_print(traceback.format_exc())
            QMessageBox.critical(self, t('print.error_title'), t('print.pdf_error_msg', error=e))


    def _on_print(self):
        """실제 프린터로 인쇄"""
        try:
            settings = self.settings_widget.get_settings()
            
            # 현재 선택된 프린터 사용
            printer_name = self.printer_combo.currentText()
            if not printer_name:
                QMessageBox.critical(self, t('print.error_title'), t('print.no_printer_msg'))
                return
            
            # Microsoft Print to PDF 또는 가상 PDF 프린터 감지
            is_pdf_printer = any(keyword in printer_name.lower() for keyword in [
                'pdf', 'xps', 'onenote'
            ])
            
            if is_pdf_printer:
                # PDF 프린터는 별도 처리 (파일 경로 필요)
                reply = QMessageBox.question(
                    self,
                    t('print.pdf_printer_title'),
                    t('print.pdf_printer_msg', name=printer_name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                
                if reply == QMessageBox.StandardButton.Yes:
                    self._on_save_pdf()
                    return
                else:
                    info_print(f"PDF 프린터 인쇄 취소")
                    return
            
            # 인쇄 확인
            reply = QMessageBox.question(
                self,
                t('print.confirm_title'),
                t('print.confirm_msg',
                printer=printer_name,
                copies=settings['copies'],
                paper=settings['paper_size'].value),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            
            if reply != QMessageBox.StandardButton.Yes:
                return
            
            # 선택된 프린터로 생성
            printer = create_printer(
                printer_name,
                settings['paper_size'],
                settings['orientation'],
                settings['quality']
            )
            
            if not printer:
                QMessageBox.critical(self, t('print.error_title'), t('print.create_fail_msg'))
                return
            
            # 여백 설정
            margins_mm = settings['margins']
            margins = QMarginsF(
                margins_mm['left'],
                margins_mm['top'],
                margins_mm['right'],
                margins_mm['bottom']
            )
            printer.setPageMargins(margins, QPageLayout.Unit.Millimeter)
            
            # 매수 설정
            printer.setCopyCount(settings['copies'])
            printer.setOutputFormat(QPrinter.OutputFormat.NativeFormat)
            
            info_print(f"인쇄 시작: {printer_name}")
            
            # 설정 저장
            self._save_current_settings()
            
            # 인쇄 스레드 실행
            self._execute_print_thread(printer, settings, is_pdf=False)
        
        except Exception as e:
            error_print(f"인쇄 실패: {e}")
            error_print(traceback.format_exc())
            QMessageBox.critical(self, t('print.error_title'), t('print.print_error_msg', error=e))


    def _execute_print_thread(self, printer: QPrinter, settings: Dict[str, Any], is_pdf: bool = False):
        """ 인쇄 스레드 실행 (PDF 완료 대화상자 개선)"""

        if self.print_thread is not None and self.print_thread.isRunning():
            QMessageBox.warning(
                self,
                t('print.warn_title'),
                t('print.already_printing'), 
            )
            return

        # 진행 다이얼로그
        progress = QProgressDialog(
            t('print.printing_init') if not is_pdf else t('print.pdf_generating_init'),
            t('print.cancel_progress'),
            0,
            self.total_pages * settings['copies'],
            self,
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        
        # 인쇄 스레드
        self.print_thread = PrintThread(
            printer,
            self.image_paths,
            self.metadata_list,
            settings
        )
        
        def on_progress(current, total):
            progress.setValue(current)
            key = 'print.printing_progress' if not is_pdf else 'print.pdf_progress'
            progress.setLabelText(t(key, current=current, total=total))
        
        def on_completed():
            progress.close()
            if is_pdf:
                self._show_pdf_complete_dialog()
            else:
                QMessageBox.information(self, t('print.done_title'), t('print.done_msg'))
                self.accept()
        
        def on_failed(error):
            progress.close()
            msg_key = 'print.print_fail_msg' if not is_pdf else 'print.pdf_fail_msg'
            QMessageBox.critical(self, t('print.error_title'), t(msg_key, error=error))
        
        def on_cancelled():
            if self.print_thread:
                self.print_thread.cancel()
                self.print_thread.wait()
            progress.close()    
        
        self.print_thread.progress.connect(on_progress)
        self.print_thread.completed.connect(on_completed)
        self.print_thread.failed.connect(on_failed)
        progress.canceled.connect(on_cancelled)
        
        self.print_thread.start()


    def _show_pdf_complete_dialog(self):
        """ PDF 완료 대화상자 (파일 열기 버튼 포함)"""
        
        dialog = QDialog(self)
        dialog.setWindowTitle("PDF 저장 완료")
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(dialog)
        
        # 메시지
        message = QLabel(t('print.pdf_done_msg', path=self._saved_pdf_path))
        message.setWordWrap(True)
        message.setStyleSheet("font-size: 12px; padding: 10px;")
        layout.addWidget(message)
        
        # 버튼 레이아웃
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        # 폴더 열기 버튼
        open_folder_btn = QPushButton(t('print.open_folder_btn'))
        open_folder_btn.setMinimumWidth(120)
        open_folder_btn.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #666;
            }
        """)
        

        def open_folder():
            """파일이 있는 폴더 열기"""
            try:
                folder_path = str(Path(self._saved_pdf_path).parent)
                
                system = platform.system()
                if system == "Windows":
                    os.startfile(folder_path)
                elif system == "Darwin":  # macOS
                    subprocess.Popen(["open", folder_path])
                else:  # Linux
                    subprocess.Popen(["xdg-open", folder_path])
                
                info_print(f"폴더 열기: {folder_path}")
            except Exception as e:
                error_print(f"폴더 열기 실패: {e}")
                QMessageBox.warning(dialog, t('print.warn_title'), t('print.folder_open_fail', error=e))
        
        open_folder_btn.clicked.connect(open_folder)
        button_layout.addWidget(open_folder_btn)
        
        # 파일 열기 버튼
        open_file_btn = QPushButton(t('print.open_pdf_btn'))
        open_file_btn.setMinimumWidth(120)
        open_file_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a9eff;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #357abd;
            }
        """)
        

        def open_file():
            """PDF 파일 열기"""
            try:
                file_path = self._saved_pdf_path
                
                system = platform.system()
                if system == "Windows":
                    os.startfile(file_path)
                elif system == "Darwin":  # macOS
                    subprocess.Popen(["open", file_path])
                else:  # Linux
                    subprocess.Popen(["xdg-open", file_path])
                
                info_print(f"PDF 열기: {file_path}")
                dialog.accept()
            except Exception as e:
                error_print(f"PDF 열기 실패: {e}")
                QMessageBox.warning(dialog, t('print.warn_title'), t('print.pdf_open_fail', error=e))
        
        open_file_btn.clicked.connect(open_file)
        button_layout.addWidget(open_file_btn)
        
        # 확인 버튼
        ok_btn = QPushButton(t('print.ok_btn'))
        ok_btn.setMinimumWidth(100)
        ok_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_btn)
        
        layout.addLayout(button_layout)
        
        dialog.exec()


    def wheelEvent(self, event):
        """마우스 휠로 페이지 이동"""
        if event.angleDelta().y() > 0:
            self._on_prev_page()
        else:
            self._on_next_page()
        
        event.accept()


    def _save_current_settings(self):
        """ 현재 설정 저장"""
        try:
            settings = self.settings_widget.get_settings()
            
            # 프린터명도 포함
            settings['printer'] = self.printer_combo.currentText()
            
            # PrintManager를 통해 저장
            self.print_manager.save_settings(settings)
            
            debug_print(f"현재 인쇄 설정 저장 완료")
        
        except Exception as e:
            error_print(f"설정 저장 실패: {e}")


    def closeEvent(self, event):
        """ 다이얼로그 닫힐 때 설정 저장"""
        try:
            # 인쇄 스레드가 실행 중이면 취소
            if self.print_thread and self.print_thread.isRunning():
                reply = QMessageBox.question(
                    self,
                    t('print.in_progress_title'),
                    t('print.in_progress_msg'),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                
                if reply == QMessageBox.StandardButton.Yes:
                    self.print_thread.cancel()
                    finished = self.print_thread.wait(3000) 
                    if not finished:
                        warning_print("인쇄 스레드가 3초 내 종료되지 않음 — 강제 종료 시도")
                        self.print_thread.terminate()  
                        still_running = not self.print_thread.wait(1000) 
                        if still_running:
                            error_print("스레드 강제 종료 실패 — 리소스 누수 가능성")
                else:
                    event.ignore()
                    return

            self._save_current_settings()

            if hasattr(self, 'preview_widget'):
                self.preview_widget.clear_preview()
            
            info_print(f"인쇄 다이얼로그 종료")
        
        except Exception as e:
            error_print(f"종료 처리 실패: {e}")
        
        finally:
            super().closeEvent(event)


    def reject(self):
        """ 취소 버튼 클릭 시"""
        # 설정 저장 후 닫기
        self._save_current_settings()
        super().reject()
    
    def accept(self):
        """ 인쇄 완료 시"""
        # 설정은 이미 _on_print에서 저장됨
        super().accept()

