# -*- coding: utf-8 -*-
# ui/thumbnail_bar.py

"""
썸네일 바 - 폴더의 이미지를 가로 스크롤 썸네일로 표시
파일명 포함 + 고정 높이 + EXIF 회전 + 하이라이트
"""

import os
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageOps

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QRunnable,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QContextMenuEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.cache_manager import CacheManager
from core.hybrid_cache import HybridCache

from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t


# ============================================
# 브릿지 (워커 → 메인 스레드 안전 전달)
# ============================================

class _ThumbBridge(QObject):
    """QRunnable → 메인 스레드 안전 전달
    Signal은 자동으로 QueuedConnection 처리됨"""
    loaded = Signal(int, QImage, int)  # (index, q_image, generation_id)


class ThumbnailLoader(QRunnable):
    """HybridCache 기반 썸네일 로더 (메모리 + 디스크 자동 관리)"""

    def __init__(
        self,
        index: int,
        file_path: Path,
        size: int,
        cache: HybridCache,        # ← L1/L2 파라미터 4개 → 1개로 통합
        bridge: '_ThumbBridge',
        generation_id: int,
    ) -> None:
        super().__init__()
        self.index          = index
        self.file_path      = file_path
        self.thumbnail_size = size
        self.cache          = cache
        self.bridge         = bridge
        self.generation_id  = generation_id
        self.cancelled      = False
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            if self.cancelled:
                # 취소도 카운터에 포함시켜야 total에 도달 가능
                self.bridge.loaded.emit(self.index, QImage(), self.generation_id)
                return

            try:
                stat = self.file_path.stat()
            except OSError:
                # 파일 접근 불가 → null 이미지로 emit
                self.bridge.loaded.emit(self.index, QImage(), self.generation_id)
                return

            xmp_mtime   = self._get_xmp_mtime()
            cache_key   = f"{str(self.file_path.resolve())}|{int(stat.st_mtime)}|{int(xmp_mtime)}|{self.thumbnail_size}"
            source_mtime = stat.st_mtime

            # L1/L2 캐시 히트
            try:
                pixmap = self.cache.get(cache_key)
            except Exception as e:
                warning_print(f"캐시 읽기 실패 ({self.file_path.name}): {e}")
                pixmap = None 

            if pixmap and not pixmap.isNull():
                self.bridge.loaded.emit(
                    self.index,
                    pixmap.toImage() if not self.cancelled else QImage(),
                    self.generation_id,
                )
                return

            if self.cancelled:
                self.bridge.loaded.emit(self.index, QImage(), self.generation_id)
                return

            # 실제 썸네일 생성
            qimage = self._generate_thumbnail()

            if self.cancelled:
                self.bridge.loaded.emit(self.index, QImage(), self.generation_id)
                return

            if qimage is None or qimage.isNull():
                # 생성 실패 (PIL 파싱 오류, 지원 안 되는 포맷 등)
                #    null QImage로 emit → 카운터는 올리고 픽스맵은 스킵
                self.bridge.loaded.emit(self.index, QImage(), self.generation_id)
                return

            # ── 성공 경로 ─────────────────────────────────────────
            self.bridge.loaded.emit(self.index, qimage, self.generation_id)

            # 디스크 캐시 저장 (emit 이후 → UI 먼저 업데이트)
            try:
                raw_data = HybridCache.qimage_to_bytes(qimage, fmt="JPEG", quality=60)
                if raw_data:
                    #self.cache._db_save(cache_key, raw_data, None, None, source_mtime)
                    self.cache.db_save(cache_key, raw_data, None, None, source_mtime)
            except Exception as e:
                error_print(f"{self.file_path.name} DB 저장 실패: {e}")

        except Exception as e:
            error_print(f"{self.file_path.name}: {e}")
            # 예외 상황에서도 emit → 카운터 보전
            try:
                self.bridge.loaded.emit(self.index, QImage(), self.generation_id)
            except Exception:
                pass


    def cancel(self) -> None:
        self.cancelled = True

    # ──────────────────────────────────────────────────────────
    # 썸네일 생성
    # ──────────────────────────────────────────────────────────

    def _generate_thumbnail(self) -> Optional[QImage]:
        """L3 생성 — RAW / HEIF / 일반 포맷 분기"""
        ext = self.file_path.suffix.lower()

        raw_exts  = ('.cr2', '.cr3', '.nef', '.arw', '.dng',
                    '.orf', '.rw2', '.pef', '.srw', '.raf')
        heif_exts = ('.heic', '.heif', '.avif') 

        if ext in raw_exts or ext in heif_exts: 
            return self._generate_raw_thumbnail()

        return self._generate_normal_thumbnail()


    def _generate_raw_thumbnail(self) -> Optional[QImage]:
        """RAW 포맷 썸네일 생성"""
        from core.image_loader import ImageLoader
        loader = ImageLoader()
        pixmap = loader.load(
            self.file_path,
            (self.thumbnail_size * 2, self.thumbnail_size * 2)
        )
        if not pixmap or pixmap.isNull():
            return None
        
        pixmap = loader.apply_exif_rotation(self.file_path, pixmap)

        full_img = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
        w, h = full_img.width(), full_img.height()
        min_side = min(w, h)
        x = (w - min_side) // 2
        y = (h - min_side) // 2
        cropped = full_img.copy(x, y, min_side, min_side)
        return cropped.scaled(
            self.thumbnail_size, self.thumbnail_size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )


    def _generate_normal_thumbnail(self) -> Optional[QImage]:
        with Image.open(self.file_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGB')
            w, h = img.size
            min_side = min(w, h)
            img = img.crop(((w-min_side)//2, (h-min_side)//2,
                            (w+min_side)//2, (h+min_side)//2))
            img = img.resize((self.thumbnail_size, self.thumbnail_size),
                            Image.Resampling.BILINEAR).convert('RGB')

            data = img.tobytes()
            qimg = QImage(data, img.width, img.height,
                        img.width * 3, QImage.Format.Format_RGB888)
            return qimg.copy()  
        
    # ──────────────────────────────────────────────────────────
    # XMP mtime 빠른 읽기
    # ──────────────────────────────────────────────────────────

    def _get_xmp_mtime(self) -> float:
        """XMP 사이드카 mtime (RAW 회전 반영용)"""
        raw_exts = ('.cr2', '.cr3', '.nef', '.arw', '.dng',
                    '.orf', '.rw2', '.pef', '.srw', '.raf')
        if self.file_path.suffix.lower() not in raw_exts:
            return 0.0
        xmp_path = self.file_path.with_suffix(self.file_path.suffix + '.xmp')
        try:
            return xmp_path.stat().st_mtime
        except OSError:
            return 0.0

# ============================================
# 썸네일 아이템 (개별 썸네일)
# ============================================

class ThumbnailItem(QFrame):
    """썸네일 아이템 (이미지 + 파일명 + 하이라이트)"""
    
    clicked = Signal(int)
    ctrl_clicked = Signal(int)
    shift_clicked = Signal(int, bool) 

    def __init__(self, index: int, file_name: str, size: int) -> None:
        super().__init__()
        self.index = index
        self.thumbnail_size = size
        self.file_name = file_name
        self.is_selected = False
        self.is_highlighted = False
        self.is_temp_highlighted = False
        self._is_secondary: bool = False

        self.setToolTip(file_name)

        # ── 레이아웃: 여백 최소화 ──────────────────────────────
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(2, 2, 2, 2)
        self.main_layout.setSpacing(0)          # image + label 간격 0

        # ── 이미지 레이블 ──────────────────────────────────────
        self.image_label = QLabel()
        self.image_label.setFixedSize(size, size)
        self.image_label.setScaledContents(False)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 배경 투명 → QFrame 자체가 카드 배경 역할
        self.image_label.setStyleSheet("background: transparent; border: none;")
        # 마우스 이벤트를 QFrame으로 통과시킴 (hover 작동)
        self.image_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.main_layout.addWidget(self.image_label)

        # ── 파일명 레이블: 이미지 바로 아래 세련된 caption ──────
        self.name_label = QLabel()
        self.name_label.setFixedWidth(size)
        self.name_label.setFixedHeight(16)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("""
            QLabel {
                color: rgba(204, 204, 204, 0.85);
                font-size: 9px;
                background: transparent;
                border: none;
                padding: 0px 2px;
            }
        """)
        self.name_label.setWordWrap(False)
        self.name_label.setText(self._truncate_filename(file_name, size))
        self.name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.main_layout.addWidget(self.name_label)

        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(0)
        self._update_border()
        # 전체 높이: image + label + 상하 margin(2+2) = size + 20
        self.setFixedSize(size + 6, size + 22)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)


    def _truncate_filename(self, filename: str, max_width: int) -> str:
        """파일명 길이 제한"""
        max_chars = (max_width // 6) + 4
        if len(filename) > max_chars:
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            
            available = max_chars - len(suffix) - 3
            if available > 0:
                half = available // 2
                return f"{stem[:half]}...{stem[-half:]}{suffix}"
            else:
                return f"{stem[:max_chars-3]}..."
        return filename


    def set_pixmap(self, pixmap: QPixmap) -> None:
        """썸네일 이미지 설정"""
        self.image_label.setPixmap(pixmap)
    

    def set_selected(self, selected: bool) -> None:
        """선택 상태 설정"""
        if self.is_selected == selected:
            return
        
        self.is_selected = selected
        self._update_border()


    def set_temp_highlighted(self, highlighted: bool) -> None:
        """
        임시 하이라이트 설정 (붙여넣기 등)
        """
        if self.is_temp_highlighted == highlighted:
            return
        
        self.is_temp_highlighted = highlighted
        self._update_border()
    

    def set_secondary(self, value: bool) -> None:
        """보조 뷰어 표시 중 표시 (주황색 테두리)"""
        if self._is_secondary == value:
            return
        self._is_secondary = value
        self._update_border()


    def _update_border(self) -> None:
        """테두리 + 배경 tint 업데이트 (선택/하이라이트 동시 표현)"""
        _R = "border-radius: 3px;"

        # ── 테두리 (선택 계열)
        if self.is_selected:
            border = "border: 2px solid #4a9eff;"    
        elif self._is_secondary:
            border = "border: 2px solid #6fcf5a;" 
        else:
            border = "border: 1px solid #404040;"   

        # ── 배경 tint (하이라이트 계열)
        if self.is_selected and self.is_highlighted:
            bg = "background-color: rgba(255, 255, 0, 0.40);"      # 선택+하이라이트: 노랑 40%
        elif self.is_selected and self.is_temp_highlighted:
            bg = "background-color: rgba(255, 100, 100, 0.40);"    # 선택+임시: 빨강 40%
        elif self.is_selected and self._is_secondary:
            bg = "background-color: rgba(111, 207, 90, 0.22);"     # 선택+보조
        elif self.is_selected:
            bg = "background-color: rgba(74, 158, 255, 0.18);"     # 선택만
        elif self.is_highlighted:
            bg = "background-color: rgba(255, 255, 0, 0.30);"      # 하이라이트만: 노랑 30%
        elif self.is_temp_highlighted:
            bg = "background-color: rgba(255, 100, 100, 0.30);"    # 임시만: 빨강 30%
        else:
            bg = "background-color: #252525;"                      # 기본

        if self.is_selected:
            hover = ""
        elif self.is_highlighted:
            hover = """
                QFrame:hover {
                    border: 1px solid #aaaaaa;
                }
            """
        elif self.is_temp_highlighted:
            hover = """
                QFrame:hover {
                    border: 1px solid #ff8888;
                }
            """
        else:
            hover = """
                QFrame:hover {
                    border: 1px solid #5a5a5a;
                    background-color: #2e2e2e;
                }
            """

        # ── 최종 스타일 적용
        self.setStyleSheet(f"""
            QFrame {{
                {border}
                {_R}
                {bg}
            }}
            {hover}
        """)


    def set_highlighted(self, highlighted: bool) -> None:
        """영구 하이라이트 설정 (기존)"""
        if self.is_highlighted == highlighted:
            return
        
        self.is_highlighted = highlighted
        self._update_border()


    def mousePressEvent(self, event):
        """클릭 이벤트"""
        if event.button() == Qt.MouseButton.LeftButton:
            modifiers = QApplication.keyboardModifiers()
            
            # Ctrl만
            if modifiers == Qt.KeyboardModifier.ControlModifier:
                self.ctrl_clicked.emit(self.index)
            # Shift만
            elif modifiers == Qt.KeyboardModifier.ShiftModifier:
                self.shift_clicked.emit(self.index, False)
            # Ctrl+Shift
            elif modifiers == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                self.shift_clicked.emit(self.index, True)
            # 일반 클릭
            else:
                self.clicked.emit(self.index)


cpu_count = os.cpu_count() or 4
thumb_threads = max(1, min(cpu_count - 2, 4))

# ============================================
# 썸네일 바 (메인 위젯)
# ============================================

class ThumbnailBar(QWidget):
    """썸네일 바 위젯 (고정 높이 + 하이라이트)"""
    
    thumbnail_clicked = Signal(int)
    sort_requested = Signal(str, bool)  # (정렬 타입, 역순 여부)

    thumbnail_load_started   = Signal(int)          # 총 썸네일 수
    thumbnail_load_progress  = Signal(int, int)     # (완료된 수, 전체 수)
    thumbnail_load_finished  = Signal(int)          # 완료된 총 수

    # Ctrl+클릭 / Shift+클릭 이벤트를 시그널로 전달
    # main_window가 수신하여 navigator를 직접 조작
    highlight_toggle_requested      = Signal(Path)          # Ctrl+클릭
    highlight_range_requested       = Signal(int, int, bool, object)
    temp_highlights_clear_requested = Signal()              # 임시 해제 요청
    status_message_requested        = Signal(str, int)      # 상태바 메시지 요청 (msg, ms)
    context_menu_requested          = Signal(QPoint)        # 우클릭 컨텍스트 메뉴 위치

    THUMBNAIL_SIZE = 72 # 80 -> 72

    # ============================================
    # 초기화
    # ============================================

    def __init__(
        self,
        cache_manager: CacheManager,
        thumb_memory_mb: int = 100, 
        thumb_disk_mb:   int = 500, 
    ) -> None:
        super().__init__()
        self.cache_manager = cache_manager
        self.image_list: List[Path] = []
        self.current_index = -1
        self._secondary_index: int = -1
        self.thumbnail_items: List[ThumbnailItem] = []
        self.highlighted_files: set = set()
        self.temp_highlighted_files: set = set()
        self.last_clicked_index = -1
        self._prev_shift_range: Optional[tuple[int, int]] = None

        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(thumb_threads)
        info_print(f"썸네일 스레드 풀: {thumb_threads}개 (CPU: {cpu_count}코어)")

        self._generation_id: int = 0

        self._thumb_bridge = _ThumbBridge()
        self._thumb_bridge.loaded.connect(self._on_thumbnail_loaded)

        self._thumb_total = 0
        self._thumb_done = 0
        self._thumb_active = False

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._pending_scroll_index: Optional[int] = None

        self._thumb_cache: HybridCache = HybridCache(
            namespace="thumbnails",
            max_memory_mb=thumb_memory_mb,
            max_disk_mb=thumb_disk_mb,
            expiry_days=0,
        )

        self._init_ui() 


    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        # ── 화살표 버튼 공통 스타일
        _ARROW_STYLE = """
            QPushButton {
                background: transparent;
                border: none;
                color: rgba(136, 136, 136, 0.8);
                font-size: 18px;
            }
            QPushButton:hover {
                color: #4a9eff;
                background: rgba(74, 158, 255, 0.10);
                border-radius: 4px;
            }
            QPushButton:pressed {
                color: #2a7ed3;
                background: rgba(74, 158, 255, 0.20);
            }
        """

        self.left_btn = QPushButton("‹") 
        self.left_btn.setFixedSize(24, 48)
        self.left_btn.setStyleSheet(_ARROW_STYLE)
        self.left_btn.clicked.connect(self._scroll_left)
        layout.addWidget(self.left_btn)

        # ── 스크롤 영역
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setFixedHeight(self.THUMBNAIL_SIZE + 40)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #202020;
                border: none;
                border-top: 1px solid rgba(255, 255, 255, 0.06);
            }
            QScrollBar:horizontal {
                height: 6px;
                background: transparent;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(255, 255, 255, 0.18);
                border-radius: 3px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover {
                background: rgba(255, 255, 255, 0.30);
            }
            QScrollBar::handle:horizontal:pressed {
                background: rgba(74, 158, 255, 0.60);
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal { width: 0px; }
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal { background: none; }
        """)

        self.scroll_area.installEventFilter(self)
        self.scroll_area.viewport().installEventFilter(self)

        # ── 썸네일 컨테이너
        self.thumbnail_container = QWidget()
        self.thumbnail_container.setStyleSheet("background: transparent;")
        self.thumbnail_layout = QHBoxLayout(self.thumbnail_container)
        self.thumbnail_layout.setContentsMargins(4, 4, 4, 4)
        self.thumbnail_layout.setSpacing(4)
        self.thumbnail_layout.addStretch()

        self.scroll_area.setWidget(self.thumbnail_container)
        layout.addWidget(self.scroll_area)

        self.right_btn = QPushButton("›")  # GUILLEMET
        self.right_btn.setFixedSize(24, 48)
        self.right_btn.setStyleSheet(_ARROW_STYLE)
        self.right_btn.clicked.connect(self._scroll_right)
        layout.addWidget(self.right_btn)

        self.setFixedHeight(self.THUMBNAIL_SIZE + 48)
        # 바 자체 배경 (이미지 뷰어와 구분)
        self.setStyleSheet("""
            ThumbnailBar {
                background-color: #1c1c1c;
                border-top: 1px solid rgba(255, 255, 255, 0.07);
            }
        """)

    # ============================================
    # 썸네일 목록 관리
    # ============================================

    def _get_thumb_cache(self) -> 'HybridCache':
        """첫 실제 사용 시에만 DB 열기"""
        if self._thumb_cache is None:
            self._thumb_cache = HybridCache(
                namespace="thumbnails",
                max_memory_mb=50,
                max_disk_mb=300,
                expiry_days=0,
            )
        return self._thumb_cache


    def set_image_list(self, image_list: List[Path], current_index: int = 0) -> None:
        """위젯 생성 → 로딩 분리: 풀 포화 방지"""
        old_temp = self.temp_highlighted_files.copy()
        debug_print(f"[set_image_list] START: {len(image_list)}개")

        # 추가 — 이전 폴더 썸네일 메모리 즉시 해제 (디스크 캐시는 유지)
        self._thumb_cache.clear_memory()
        debug_print("[thumbnails] 메모리 캐시 삭제")
        
        # 세대 증가 (이전 워커 무시)
        self._generation_id += 1
        current_gen = self._generation_id
        
        # 풀 초기화 (대기 큐만)
        self.thread_pool.clear()
        
        # 기존 위젯 제거 (레이아웃 완전 클리어)
        while self.thumbnail_layout.count() > 1:
            item = self.thumbnail_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.hide()
                widget.deleteLater()
        self.thumbnail_items.clear()

        # ── 빈 목록 완전 초기화
        if not image_list:
            self.image_list               = []
            self.current_index            = -1
            self.last_clicked_index       = -1
            self.highlighted_files.clear()
            self.temp_highlighted_files.clear()
            self._thumb_total  = 0
            self._thumb_done   = 0
            self._thumb_active = False
            debug_print("[set_image_list] 빈 목록 - 초기화 완료")
            return

        # 상태 업데이트
        self.image_list    = image_list
        self.current_index = current_index
        
        # 진행 시작
        self._thumb_total  = len(image_list)
        self._thumb_done   = 0
        self._thumb_active = self._thumb_total > 0
        if self._thumb_active:
            self.thumbnail_load_started.emit(self._thumb_total)
        
        debug_print(f"[set_image_list] 위젯 생성 시작: {len(image_list)}개")
        
        # ── 1단계: 위젯 생성 (동기) ─────────────────────────
        for i, file_path in enumerate(image_list):
            item = ThumbnailItem(i, file_path.name, self.THUMBNAIL_SIZE)
            item.clicked.connect(self._on_thumbnail_click)
            item.ctrl_clicked.connect(self._on_thumbnail_ctrl_click)
            item.shift_clicked.connect(self._on_thumbnail_shift_click)
            if file_path in self.highlighted_files:
                item.set_highlighted(True)

            if file_path in old_temp: 
                item.set_temp_highlighted(True)
                self.temp_highlighted_files.add(file_path)

            item.set_selected(i == current_index)
            self.thumbnail_layout.insertWidget(i, item)
            self.thumbnail_items.append(item)
        
        debug_print(f"[set_image_list] 위젯 생성 완료")
        
        # ── 2단계: 스크롤 (레이아웃 먼저) ───────────────────
        QTimer.singleShot(0, lambda: self._ensure_layout_and_scroll(current_index))
        
        # ── 3단계: 로딩 청크 (1프레임 후 지연 시작) ─────────
        QTimer.singleShot(16, lambda: self._start_thumbnail_loading(image_list, current_gen))
        
        debug_print(f"[set_image_list] END")


    def set_secondary_index(self, index: int) -> None:
        """
        보조 뷰어 인덱스 갱신.
        이전 항목 주황 테두리 해제 → 새 항목 주황 테두리 설정.
        primary(파란 테두리)와 겹치면 primary 우선 (is_selected가 덮어씀).
        """
        # 이전 항목 해제
        if 0 <= self._secondary_index < len(self.thumbnail_items):
            self.thumbnail_items[self._secondary_index].set_secondary(False)

        self._secondary_index = index

        # 신규 항목 설정
        if 0 <= index < len(self.thumbnail_items):
            self.thumbnail_items[index].set_secondary(True)


    def clear_secondary_index(self) -> None:
        """보조 뷰어 인덱스 초기화."""
        if 0 <= self._secondary_index < len(self.thumbnail_items):
            self.thumbnail_items[self._secondary_index].set_secondary(False)
        self._secondary_index = -1
        

    def _start_thumbnail_loading(
        self, image_list: List[Path], current_gen: int
    ) -> None:
        """청크 단위 로딩 — 풀 포화 방지"""
        CHUNK_SIZE = 24  # 8스레드 × 3개씩
        
        def load_chunk(start: int):
            end = min(start + CHUNK_SIZE, len(image_list))
            #debug_print(f"[load_chunk] {start}-{end}/{len(image_list)}")
            
            for i in range(start, end):
                try:
                    self._load_thumbnail_async(i, image_list[i], current_gen)
                except Exception as e:
                    error_print(f"[load_chunk {i}] 에러: {e}")
            
            if end < len(image_list):
                QTimer.singleShot(16, lambda: load_chunk(end))  # 60fps
        
        load_chunk(0)


    def add_thumbnail(self, file_path: Path, insert_index: int) -> None:
        # generation_id는 건드리지 않음 (set_image_list 전용)
        current_gen = self._generation_id   # ← 현재 세대 그대로 사용

        item = ThumbnailItem(insert_index, file_path.name, self.THUMBNAIL_SIZE)
        item.clicked.connect(self._on_thumbnail_click)
        item.ctrl_clicked.connect(self._on_thumbnail_ctrl_click)
        item.shift_clicked.connect(self._on_thumbnail_shift_click)

        if file_path in self.highlighted_files:
            item.set_highlighted(True)

        self.thumbnail_layout.insertWidget(insert_index, item)
        self.thumbnail_items.insert(insert_index, item)

        for i in range(insert_index + 1, len(self.thumbnail_items)):
            self.thumbnail_items[i].index = i

        # Bug B 수정: done은 건드리지 않고 total만 증가
        self._thumb_total  += 1
        self._thumb_active  = True
        self.thumbnail_load_progress.emit(self._thumb_done, self._thumb_total)

        self._load_thumbnail_async(insert_index, file_path, current_gen)
        self.image_list.insert(insert_index, file_path)
        info_print(f"썸네일 추가: 인덱스 {insert_index}, {file_path.name}")


    def remove_thumbnail(self, filepath: Path) -> int:
        try:
            remove_index = self.image_list.index(filepath)
        except ValueError:
            return -1

        # ── 상태 클리어 ───────────────────────────────────────
        self.highlighted_files.discard(filepath)
        self.temp_highlighted_files.discard(filepath)  

        # ── 위젯 제거 ─────────────────────────────────────────
        if 0 <= remove_index < len(self.thumbnail_items):
            item = self.thumbnail_items.pop(remove_index)
            item.set_selected(False)  
            item.set_highlighted(False)
            item.set_temp_highlighted(False)
            self.thumbnail_layout.removeWidget(item)
            item.deleteLater()

        self.image_list.pop(remove_index)

        # ── 인덱스 재정렬 ─────────────────────────────────────
        for i in range(remove_index, len(self.thumbnail_items)):
            self.thumbnail_items[i].index = i

        # ── 빈 목록 완전 초기화 ───────────────────────────────
        if not self.image_list:
            self.current_index      = -1
            self.last_clicked_index = -1
            self.highlighted_files.clear()
            self.temp_highlighted_files.clear()
            self._thumb_total  = 0
            self._thumb_done   = 0
            self._thumb_active = False
            debug_print("모든 파일 제거됨 - 썸네일바 상태 초기화")
            info_print(f"썸네일 제거됨: 인덱스 {remove_index}, {filepath.name}")
            return remove_index

        # ── current_index 조정 ───────────────────────────────
        if remove_index < self.current_index:
            # 현재 선택보다 앞 아이템 삭제 → 인덱스 -1 (선택 대상 유지)
            self.current_index -= 1

        elif remove_index == self.current_index:
            # 현재 선택 아이템 삭제 → 같은 위치 또는 마지막으로 클램프
            self.current_index = min(self.current_index, len(self.image_list) - 1)
            if 0 <= self.current_index < len(self.thumbnail_items):
                self.thumbnail_items[self.current_index].set_selected(True)

        # remove_index > current_index: 변화 없음

        info_print(f"썸네일 제거됨: 인덱스 {remove_index}, {filepath.name}")
        return remove_index


    def update_file_name(self, old_path: Path, new_path: Path) -> bool:
        """
        개별 파일명 변경 반영 (썸네일 재생성 없이)
        """
        try:
            # 파일 목록에서 찾기
            if old_path not in self.image_list:
                return False
            
            index = self.image_list.index(old_path)
            
            # 파일 목록 업데이트
            self.image_list[index] = new_path
            
            # 썸네일 아이템 파일명 업데이트
            if 0 <= index < len(self.thumbnail_items):
                item = self.thumbnail_items[index]
                item.file_name = new_path.name
                item.name_label.setText(item._truncate_filename(new_path.name, self.THUMBNAIL_SIZE))
                item.setToolTip(new_path.name)
            
            # 하이라이트도 업데이트
            if old_path in self.highlighted_files:
                self.highlighted_files.remove(old_path)
                self.highlighted_files.add(new_path)
            
            info_print(f"썸네일바 파일명 업데이트: {old_path.name} → {new_path.name}")
            return True
        
        except Exception as e:
            error_print(f"썸네일바 파일명 업데이트 실패: {e}")
            return False


    def refresh_thumbnails(self, file_list: List[Path], current_index: int) -> None:
        """ set_image_list()가 highlighted_files를 그대로 유지하므로 중복 처리 제거"""
        # highlighted_files는 이미 set_image_list() 내에서 참조됨
        self.set_image_list(file_list, current_index)
        debug_print(f"썸네일 새로고침: {len(file_list)}개, 하이라이트 {len(self.highlighted_files)}개 유지")


    # ============================================
    # 썸네일 로딩
    # ============================================

    def _load_thumbnail_async(
        self, index: int, file_path: Path, generation_id: int
    ) -> None:
        current  = self.current_index
        distance = abs(index - current)
        delay    = 0 if distance <= 3 else 20 if distance <= 15 else 50 if distance <= 40 else 100

        def _do_start():
            loader = ThumbnailLoader(
                index         = index,
                file_path     = file_path,
                size          = self.THUMBNAIL_SIZE,
                cache         = self._get_thumb_cache(), 
                bridge        = self._thumb_bridge,
                generation_id = generation_id,
            )
            self.thread_pool.start(loader)

        if delay == 0:
            _do_start()
        else:
            QTimer.singleShot(delay, _do_start)


    @Slot(int, QImage, int)
    def _on_thumbnail_loaded(self, index: int, qimage: QImage, genid: int) -> None:
        if genid != self._generation_id:
            return  

        # null 이미지(실패/취소)일 때는 픽스맵 설정 스킵, 카운터만 증가
        if not qimage.isNull():
            if 0 <= index < len(self.thumbnail_items):
                pixmap = QPixmap.fromImage(qimage)
                self.thumbnail_items[index].set_pixmap(pixmap)

        # 항상 카운터 증가
        self._thumb_done += 1
        self.thumbnail_load_progress.emit(self._thumb_done, self._thumb_total)

        if self._thumb_done >= self._thumb_total:
            self._thumb_active = False
            self.thumbnail_load_finished.emit(self._thumb_total)


    # ============================================
    # 선택 및 하이라이트
    # ============================================

    def set_current_index(self, index: int) -> None:
        """선택 강조 + 중앙 스크롤"""
        # 이전 선택 해제 (전체 순회 대신 이전 인덱스만)
        if 0 <= self.current_index < len(self.thumbnail_items):
            self.thumbnail_items[self.current_index].set_selected(False)

        self.current_index = index

        if 0 <= index < len(self.thumbnail_items):
            self.thumbnail_items[index].set_selected(True)
            self._request_scroll(index)


    def update_current_index_only(self, index: int) -> None:
        """set_current_index와 동일 (호환성 유지)"""
        self.set_current_index(index)


    def toggle_highlight(self, file_path: Path) -> None:
        """
        하이라이트 토글 (호환성 유지용)
        실제로는 Navigator에서 관리하고 sync_from_navigator() 호출 권장
        """
        if file_path in self.highlighted_files:
            self.highlighted_files.remove(file_path)
        else:
            self.highlighted_files.add(file_path)
        
        # 해당 썸네일 업데이트
        try:
            index = self.image_list.index(file_path)
            if 0 <= index < len(self.thumbnail_items):
                is_highlighted = file_path in self.highlighted_files
                self.thumbnail_items[index].set_highlighted(is_highlighted)
        except ValueError:
            pass


    @Slot()
    def on_highlights_cleared(self) -> None:
        """전체 해제 수신"""
        self.highlighted_files.clear()
        for item in self.thumbnail_items:
            item.set_highlighted(False)


    # ============================================
    # 임시 하이라이트 관리
    # ============================================

    def set_temp_highlights(self, files: List[Path]) -> None:
        """
        임시 하이라이트 설정
        """
        self.temp_highlighted_files = set(files)
        
        # UI 업데이트
        for i, item in enumerate(self.thumbnail_items):
            if i < len(self.image_list):
                is_temp = self.image_list[i] in self.temp_highlighted_files
                item.set_temp_highlighted(is_temp)
        
        info_print(f"썸네일바 임시 하이라이트: {len(files)}개")
    
    
    def clear_temp_highlights(self) -> None:
        """임시 하이라이트 모두 해제"""
        if not self.temp_highlighted_files:
            return
        
        self.temp_highlighted_files.clear()
        
        # UI 업데이트
        for item in self.thumbnail_items:
            item.set_temp_highlighted(False)
        
        info_print(f"썸네일바 임시 하이라이트 해제")


    # ============================================
    # 스크롤 로직 — 타이머 1개 재사용
    # ============================================

    def _request_scroll(self, index: int) -> None:
        # 동일 인덱스 재요청 방지
        if self._pending_scroll_index == index and self._scroll_timer.isActive():
            return

        self._scroll_timer.stop()
        try:
            self._scroll_timer.timeout.disconnect()
        except Exception:
            pass

        self._pending_scroll_index = index
        self._scroll_timer.timeout.connect(lambda: self._do_scroll(index))
        self._scroll_timer.start(16)
        

    def _do_scroll(self, index: int, retry: int = 0) -> None:
        MAX_RETRY = 15   # 기존 10 → 15 (max 확정까지 여유 확보)

        if not (0 <= index < len(self.thumbnail_items)):
            self._pending_scroll_index = None
            return

        item      = self.thumbnail_items[index]
        item_x    = item.x()
        item_w    = item.width()
        vp_width  = self.scroll_area.viewport().width()
        scrollbar = self.scroll_area.horizontalScrollBar()

        # 레이아웃 미완성 판정:
        #   조건 1) index > 0 인데 item.x() == 0  → 위젯 위치 미계산
        #   조건 2) scrollbar.maximum() == 0       → QScrollArea 크기 미확정 ← 기존 누락
        layout_not_ready = (item_x == 0 and index > 0) or \
                        (scrollbar.maximum() == 0 and index > 0)

        if layout_not_ready:
            if retry >= MAX_RETRY:
                # 추정 위치로 강제 이동
                estimated_x = index * (item_w if item_w > 0 else self.THUMBNAIL_SIZE + 15)
                target = max(0, estimated_x - vp_width // 2)
                scrollbar.setValue(min(target, scrollbar.maximum()))
                self._pending_scroll_index = None
                debug_print(f"_do_scroll: 타임아웃 → 추정 강제 스크롤 (idx={index})")
                return
            QTimer.singleShot(50, lambda: self._do_scroll(index, retry + 1))
            return

        # ── 정상 스크롤 ──────────────────────────────────────────
        target = max(0, min(
            item_x - vp_width // 2 + item_w // 2,
            scrollbar.maximum()
        ))
        if abs(scrollbar.value() - target) >= 2:
            scrollbar.setValue(target)

        self._pending_scroll_index = None
        debug_print(f"_do_scroll: idx={index}, x={item_x}, target={target}, max={scrollbar.maximum()}")


    def _ensure_layout_and_scroll(self, target_index: int, retry_count: int = 0) -> None:
        """
        초기 로딩 후 레이아웃 완성을 기다려 스크롤 (set_image_list / reorder_for_sort용).
        """
        if not (0 <= target_index < len(self.thumbnail_items)):
            return

        # 컨테이너 너비 체크는 유지 (레이아웃이 완전히 0인 경우 대기)
        container_w = self.thumbnail_container.width()
        item_x = self.thumbnail_items[target_index].x()
        layout_ready = container_w > 100 and (item_x > 0 or target_index == 0)

        if layout_ready:
            self._request_scroll(target_index)
        elif retry_count < 10:
            QTimer.singleShot(
                50,
                lambda: self._ensure_layout_and_scroll(target_index, retry_count + 1)
            )
        else:
            self._request_scroll(target_index) 


    # ============================================
    # 클릭 이벤트
    # ============================================

    @Slot(int)
    def _on_thumbnail_click(self, index: int) -> None:
        """일반 클릭 — 임시 하이라이트 해제 요청 후 이동"""
        self.clear_temp_highlights()
        self.temp_highlights_clear_requested.emit()
        self.last_clicked_index = index
        self._prev_shift_range = None
        self.thumbnail_clicked.emit(index)


    @Slot(int)
    def _on_thumbnail_ctrl_click(self, index: int) -> None:
        """Ctrl+클릭 — 하이라이트 토글 요청만 emit"""
        if 0 <= index < len(self.image_list):
            self.highlight_toggle_requested.emit(self.image_list[index])
            self.last_clicked_index = index
            self._prev_shift_range = None


    @Slot(Path, bool)
    def on_highlight_changed(self, file_path: Path, is_highlighted: bool) -> None:
        """Navigator 상태 변경을 수신하여 UI만 업데이트"""
        if is_highlighted:
            self.highlighted_files.add(file_path)
        else:
            self.highlighted_files.discard(file_path)
        try:
            idx = self.image_list.index(file_path)
            if 0 <= idx < len(self.thumbnail_items):
                self.thumbnail_items[idx].set_highlighted(is_highlighted)
        except ValueError:
            pass


    @Slot(set)
    def on_highlights_set(self, highlighted: set) -> None:
        """일괄 하이라이트 교체 (Shift+클릭 결과 수신)"""
        self.highlighted_files = set(highlighted)
        for i, path in enumerate(self.image_list):
            if i < len(self.thumbnail_items):
                self.thumbnail_items[i].set_highlighted(path in highlighted)


    @Slot(int, bool)
    def _on_thumbnail_shift_click(self, index: int, is_ctrl_held: bool) -> None:
        if self.last_clicked_index == -1:
            self._on_thumbnail_ctrl_click(index)
            return

        start = min(self.last_clicked_index, index)
        end   = max(self.last_clicked_index, index)

        self.highlight_range_requested.emit(start, end, is_ctrl_held, self._prev_shift_range)

        # Ctrl+Shift(해제)가 아닐 때만 범위 저장
        if not is_ctrl_held:
            self._prev_shift_range = (start, end)
        else:
            self._prev_shift_range = None

        
    # ============================================
    # 스크롤
    # ============================================

    def _scroll_left(self) -> None:
        """왼쪽으로 스크롤"""
        scrollbar = self.scroll_area.horizontalScrollBar()
        scrollbar.setValue(scrollbar.value() - self.THUMBNAIL_SIZE)
    

    def _scroll_right(self) -> None:
        """오른쪽으로 스크롤"""
        scrollbar = self.scroll_area.horizontalScrollBar()
        scrollbar.setValue(scrollbar.value() + self.THUMBNAIL_SIZE)


    def eventFilter(self, obj, event) -> bool:
        """이벤트 필터 - 휠 이벤트를 가로 스크롤로 변환"""
        if event.type() == QEvent.Type.Wheel:
            # 휠 델타값 가져오기
            delta = event.angleDelta().y()
            
            # 가로 스크롤바 조작
            scrollbar = self.scroll_area.horizontalScrollBar()
            
            # 스크롤 속도 조절 (픽셀 단위)
            scroll_amount = self.THUMBNAIL_SIZE // 2  # 썸네일 절반 크기만큼 스크롤
            
            if delta > 0:
                # 휠 위로 → 왼쪽 스크롤
                scrollbar.setValue(scrollbar.value() - scroll_amount)
            else:
                # 휠 아래로 → 오른쪽 스크롤
                scrollbar.setValue(scrollbar.value() + scroll_amount)
            
            # 이벤트 소비 (기본 동작 막기)
            return True
        
        # 다른 이벤트는 기본 처리
        return super().eventFilter(obj, event)


    # ============================================
    # UI 관련
    # ============================================

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        # 위치 정보만 시그널로 전달, 메뉴 생성은 MainWindow 책임
        self.context_menu_requested.emit(event.globalPos())
        

    def reorder_for_sort(self, new_image_list: List[Path], current_index: int) -> None:
        """
        정렬 전용 재배치. set_image_list() 와 달리:
        - 위젯 파괴/재생성 없음 (기존 ThumbnailItem 재사용)
        - 메모리 캐시 유지 (clear_memory 미호출)
        - generation_id 유지 (진행 중 로딩 유지)
        """
        # 파일 수가 달라진 경우 → 안전하게 전체 갱신
        if len(new_image_list) != len(self.image_list):
            warning_print(
                f"reorder_for_sort: 파일 수 불일치 "
                f"({len(self.image_list)} → {len(new_image_list)}) — set_image_list 호출"
            )
            self.set_image_list(new_image_list, current_index)
            return

        # path → ThumbnailItem 매핑 (O(n) 딕셔너리)
        path_to_item: dict[Path, ThumbnailItem] = {}
        for i, path in enumerate(self.image_list):
            if i < len(self.thumbnail_items):
                path_to_item[path] = self.thumbnail_items[i]

        # 새 순서에 없는 path가 있으면 전체 갱신
        if not all(p in path_to_item for p in new_image_list):
            warning_print("reorder_for_sort: 알 수 없는 파일 포함 — set_image_list 호출")
            self.set_image_list(new_image_list, current_index)
            return

        # ── 레이아웃에서 모든 아이템 위젯 분리 (stretch 제외) ────
        # stretch는 마지막 1개이므로 count-1 개만 takeAt(0)
        widgets_in_layout = []
        while self.thumbnail_layout.count() > 1:
            item = self.thumbnail_layout.takeAt(0)
            w = item.widget() if item else None
            if w:
                widgets_in_layout.append(w)

        # ── 새 순서로 재삽입 ──────────────────────────────────────
        new_items: List[ThumbnailItem] = []
        for i, path in enumerate(new_image_list):
            widget = path_to_item[path]
            widget.index = i  # 인덱스 갱신
            self.thumbnail_layout.insertWidget(i, widget)
            new_items.append(widget)

        self.thumbnail_items = new_items
        self.image_list      = list(new_image_list)
        self.current_index   = current_index

        # ── 선택·하이라이트 상태 일괄 갱신 ─────────────────────
        for i, (path, item) in enumerate(zip(new_image_list, new_items)):
            item.set_selected(i == current_index)
            item.set_highlighted(path in self.highlighted_files)
            item.set_temp_highlighted(path in self.temp_highlighted_files)

        # ── 스크롤 위치 갱신 (레이아웃 확정 후) ─────────────────
        QTimer.singleShot(0, lambda: self._ensure_layout_and_scroll(current_index))

        debug_print(f"[reorder_for_sort] 완료: {len(new_image_list)}개 재배치")


    def clear_memory_cache(self) -> None:
        """메모리 캐시만 삭제 (설정 다이얼로그 버튼용)"""
        self._thumb_cache.clear_memory()


    def update_cache_limits(self, memory_mb: int, disk_mb: int) -> None:
        """캐시 한도 런타임 갱신 (설정 변경 시)"""
        self._thumb_cache.max_memory_bytes = memory_mb * 1024 * 1024
        self._thumb_cache.max_disk_bytes   = disk_mb   * 1024 * 1024
        debug_print(f"썸네일 캐시 한도 갱신: {memory_mb}MB / {disk_mb}MB")


    def get_cache_stats(self) -> dict:
        """캐시 통계 반환 (설정 화면 표시용)"""
        return self._thumb_cache.stats()


    def clear_disk_cache(self) -> None:
        """캐시 전체 삭제 (설정 화면 버튼용)"""
        self._thumb_cache.clear()
        info_print("썸네일 캐시 전체 삭제 완료")


    def reset_loading_state(self) -> None:
        """로딩 상태 강제 초기화 (외부 호출용)."""
        self._thumb_active = False
        self.thumbnail_load_finished.emit(0)        