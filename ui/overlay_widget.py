# -*- coding: utf-8 -*-
# ui/overlay_widget.py

"""
오버레이 위젯 - 이미지 정보 표시
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFontMetrics, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QSizePolicy,
    QWidget,
)

from core.map_loader import RasterTileMapLoader, get_raster_zoom_range
from utils.debug import debug_print, error_print, info_print, warning_print
from utils.lang_manager import t


_ZOOM_DEBOUNCE_MS = 600 


class OverlayWidget(QWidget):
    """이미지 정보 오버레이"""
    
    # ============================================
    # 초기화
    # ============================================

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        
        # 설정
        self.enabled = True  
        self.show_file_info = True
        self.show_camera_info = True
        self.show_exif_info = True
        self.show_lens_info = False
        self.show_gps_info = False
        self.show_map = False
        self.opacity_value = 0.8
        self.position = "top_left"
        
        # 데이터
        self.file_path: Optional[Path] = None
        self.metadata: Dict[str, Any] = {}
        
        # 지도 관련
        self.map_loader:  Optional[RasterTileMapLoader] = None  
        self.current_map: Optional[QPixmap] = None
        self.current_gps: Optional[Tuple[float, float]] = None
        self._map_loader_id: Optional[int] = None

        # 동적 줌 범위
        _mn, _mx           = get_raster_zoom_range()
        self._min_zoom: int = _mn
        self._max_zoom: int = _mx
        self.current_zoom:  int = _mx 

        self._zoom_debounce_timer = QTimer(self)
        self._zoom_debounce_timer.setSingleShot(True)
        self._zoom_debounce_timer.setInterval(_ZOOM_DEBOUNCE_MS)
        self._zoom_debounce_timer.timeout.connect(self._on_zoom_debounced)        

        # 스케일 팩터
        self.scale_factor = 1.0
        
        # 기본 폰트 크기 저장
        self.base_font_size = 11
        self.base_title_font_size = 12

        self._fail_generation: int = 0  
        self._externally_hidden: bool = False 

        # UI
        self._init_ui()
    

    def _init_ui(self):
        """UI 초기화"""

        self.setWindowFlags(Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.info_label = QLabel(self) 
        self.info_label.setWordWrap(True)
        self.info_label.setTextFormat(Qt.TextFormat.PlainText)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.info_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        
        self.info_label.setStyleSheet(f"""
            QLabel {{
                color: white;
                background-color: rgba(0, 0, 0, {int(0.8 * 180)});
                padding: 12px 15px;
                border-radius: 6px;
                font-size: 13px;
                font-family: 'Consolas', 'Courier New', 'Malgun Gothic', monospace;
                line-height: 1.5;
            }}
        """)
        
        self.info_label.hide()
        
        self.map_label = QLabel(self)
        self.map_label.setScaledContents(False)
        self.map_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        
        # 스타일 설정
        self.map_label.setStyleSheet(f"""
            QLabel {{
                background-color: transparent;
                border-radius: 6px;
                border: 2px solid rgba(255, 255, 255, 100);
            }}
        """)
        
        self.map_opacity_effect = QGraphicsOpacityEffect()
        self.map_opacity_effect.setOpacity(0.8)
        self.map_label.setGraphicsEffect(self.map_opacity_effect)

        self.map_label.hide()
        self.setVisible(False)

    # ============================================
    # 설정 관리
    # ============================================

    def update_settings(self, enabled: bool, show_file: bool, show_camera: bool, 
                        show_exif: bool, show_lens: bool, show_gps: bool, show_map: bool,
                        opacity: float, position: str):
        """설정 업데이트 (검증 포함)"""
        
        if not (0.0 <= opacity <= 1.0):
            warning_print(f"opacity 범위 초과: {opacity}, 기본값 0.8 사용")
            opacity = 0.8
        
        # position 검증
        valid_positions = ["top_left", "top_right", "bottom_left", "bottom_right"]
        if position not in valid_positions:
            warning_print(f"잘못된 position: {position}, 기본값 'top_left' 사용")
            position = "top_left"

        # 지도 비활성화 또는 오버레이 비활성화 시 로더 즉시 정리
        if not enabled or not show_map:
            self.stop_map_loader()

        self.enabled = enabled
        self.show_file_info = show_file
        self.show_camera_info = show_camera
        self.show_exif_info = show_exif
        self.show_lens_info = show_lens
        self.show_gps_info = show_gps
        self.show_map = show_map
        self.opacity_value = opacity
        self.position = position
        
        # 스타일 업데이트
        opacity_int = int(opacity * 180)
        
        self.info_label.setStyleSheet(f"""
            QLabel {{
                color: white;
                background-color: rgba(0, 0, 0, {opacity_int});
                padding: 12px 15px;
                border-radius: 6px;
                font-size: 13px;
                font-family: 'Consolas', 'Courier New', 'Malgun Gothic', monospace;
                line-height: 1.5;
            }}
        """)
        
        self.map_label.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0, 0, 0, {opacity_int});
                border-radius: 6px;
                border: 2px solid rgba(255, 255, 255, 100);
            }}
        """)
        
        self.map_opacity_effect.setOpacity(opacity)
        self._refresh_display()

        # show_map이 켜졌고 GPS가 있으면 지도도 갱신
        if enabled and show_map:
            self._refresh_map()        
 

    def set_scale(self, scale: float) -> None:
        """오버레이 전체 크기 조절"""
        new_scale = max(0.5, min(2.0, scale))
        if new_scale == self.scale_factor: 
            return
        self.scale_factor = new_scale
        
        scaled_font_size = int(13 * self.scale_factor)
        scaled_padding_h = int(15 * self.scale_factor)
        scaled_padding_v = int(12 * self.scale_factor)
        opacity_int = int(self.opacity_value * 180)
        
        self.info_label.setStyleSheet(f"""
            QLabel {{
                color: white;
                background-color: rgba(0, 0, 0, {opacity_int});
                padding: {scaled_padding_v}px {scaled_padding_h}px;
                border-radius: {int(6 * self.scale_factor)}px;
                font-size: {scaled_font_size}px;
                font-family: 'Consolas', 'Courier New', 'Malgun Gothic', monospace;
                line-height: 1.5;
            }}
        """)
        
        self.map_label.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0, 0, 0, {opacity_int});
                border-radius: {int(6 * self.scale_factor)}px;
                border: {int(2 * self.scale_factor)}px solid rgba(255, 255, 255, 100);
            }}
        """)

        _MAP_BASE_W, _MAP_BASE_H = 400, 300
        if self.current_map and not self.current_map.isNull():
            scaled_map_width  = int(_MAP_BASE_W * self.scale_factor)
            scaled_map_height = int(_MAP_BASE_H * self.scale_factor)

            # ── 항상 self.current_map(원본)을 소스로 사용 ──
            # map_label.pixmap()은 이미 스케일된 버전이므로 사용 금지
            scaled_pixmap = self.current_map.scaled(
                scaled_map_width, scaled_map_height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.map_label.setPixmap(scaled_pixmap)

        if self.file_path and self.metadata:
            self._refresh_display()


    # ============================================
    # 데이터 설정 및 표시
    # ============================================

    def set_data(
        self,
        file_path:    Optional[Path],
        metadata:     Dict[str, Any],
        initial_zoom: Optional[int] = None,  
    ) -> None:
        """데이터 설정"""
        debug_print(f"[overlay] camera keys: {list(metadata.get('camera', {}).keys())}")

        self.stop_map_loader()

        self.file_path = file_path
        self.metadata  = metadata or {}

        # initial_zoom 즉시 반영 (debounce 없음 — 이미지 전환 시 호출)
        if initial_zoom is not None:
            self.current_zoom = max(self._min_zoom, min(initial_zoom, self._max_zoom))
            debug_print(f"[overlay] initial_zoom 적용: {self.current_zoom}")

        # GPS 정보 저장
        if 'gps' in metadata and metadata['gps']:
            gps = metadata['gps']
            self.current_gps = (gps['latitude'], gps['longitude'])
            debug_print(f"GPS 정보 저장: {self.current_gps}")
        else:
            self.current_gps = None
            debug_print("GPS 정보 없음")

        self._refresh_map()
        self._refresh_display()
        debug_print(f"OverlayWidget.isVisible(): {self.isVisible()}")


    def _refresh_display(self):
        """화면 갱신 (렌더링 최적화)"""
        debug_print(f"OverlayWidget._refresh_display() 시작")
        debug_print(f"  enabled={self.enabled}, file_path={self.file_path}")

        if self._externally_hidden:  
            return
        
        if not self.enabled or not self.file_path:
            debug_print(f"  조건 미충족 - 숨김")
            self.setVisible(False)
            self.info_label.hide()
            self.map_label.hide()
            return

        # ── self를 부모 크기에 맞춤 ──────────────────
        parent = self.parent()
        if parent and isinstance(parent, QWidget):
            parent_width  = parent.width()
            parent_height = parent.height()
            # OverlayWidget 자신이 부모를 완전히 덮어야 레이블이 클립 안 됨
            if self.size() != parent.size():
                self.resize(parent_width, parent_height)
        else:
            screen = QGuiApplication.primaryScreen()
            if screen:
                geo = screen.geometry()
                parent_width, parent_height = geo.width(), geo.height()
            else:
                parent_width, parent_height = 1920, 1080

        lines = []
        
        # 파일 정보
        if self.show_file_info:
            filename = self.file_path.name
            if len(filename) > 40:
                lines.append(f"📄 {filename[:40]}")
                rest = filename[40:]
                if len(rest) > 37:
                    rest = rest[:34] + "..."
                lines.append(f"   {rest}")
            else:
                lines.append(f"📄 {filename}")
            
            if 'file_size' in self.metadata:
                lines.append(f"   {self.metadata['file_size']}")
            
            if 'dimensions' in self.metadata:
                w, h = self.metadata['dimensions']
                lines.append(f"   {w} × {h}")
                
                mp = (w * h) / 1_000_000
                if mp >= 1:
                    lines.append(f"   {mp:.1f} MP")
        
        # 카메라 정보
        if self.show_camera_info and 'camera' in self.metadata:
            camera = self.metadata['camera']
            if camera:
                lines.append("")
                
                # 제조사 + 모델
                camera_parts = []
                if 'make' in camera:
                    camera_parts.append(camera['make'])
                if 'model' in camera:
                    camera_parts.append(camera['model'])
                
                if camera_parts:
                    camera_line = ' '.join(camera_parts)
                    # 너무 길면 줄바꿈
                    if len(camera_line) > 35:
                        lines.append(f"📷 {camera['make']}")
                        if 'model' in camera:
                            lines.append(f"   {camera['model']}")
                    else:
                        lines.append(f"📷 {camera_line}")
                
                # 촬영일시
                if 'date_taken' in camera:
                    date_str = camera['date_taken']
                    try:
                        # 초 제거 (19자 → 16자)
                        if len(date_str) == 19:
                            date_str = date_str[:16]
                    except:
                        pass
                    lines.append(f"   📅 {date_str}")
                
                # 회전 정보 (정상이 아닌 경우만)
                if 'orientation' in camera and camera['orientation'] != t('metadata.orient_normal'):
                    lines.append(f"   🔄 {camera['orientation']}")
        
        # ===== EXIF 촬영 정보 =====
        # 노출 파라미터는 camera 섹션에 있음 (패치 후 구조 변경)
        #   값은 이미 포맷된 문자열: focal_length="24mm", f_stop="f/2.8",
        #   exposure_time="1/250s", iso="ISO 1600" → 접두사 중복 추가 금지
        if self.show_exif_info and 'camera' in self.metadata:
            cam = self.metadata['camera']
            if cam:
                exif_parts = []
                
                # 초점거리 (e.g. "24mm" 또는 "24mm (35mm 환산)")
                if 'focal_length' in cam:
                    exif_parts.append(cam['focal_length'])
                
                # 조리개 (e.g. "f/2.8") — f/ 접두사 이미 포함
                if 'f_stop' in cam:
                    exif_parts.append(cam['f_stop'])
                
                # 셔터 스피드 (e.g. "1/250s")
                if 'exposure_time' in cam:
                    exif_parts.append(cam['exposure_time'])
                
                # ISO (e.g. "ISO 1600") — "ISO " 접두사 이미 포함
                if 'iso' in cam:
                    exif_parts.append(cam['iso'])
                
                if exif_parts:
                    exif_line = ' · '.join(exif_parts)
                    if len(exif_line) > 35 and len(exif_parts) >= 2: 
                        half = max(1, len(exif_parts) // 2)    
                        lines.append(f"🔧 {' · '.join(exif_parts[:half])}")
                        lines.append(f"   {' · '.join(exif_parts[half:])}")
                    else:
                        lines.append(f"🔧 {exif_line}")
        
        # 렌즈 정보 간소화
        if self.show_lens_info and 'camera' in self.metadata:
            camera = self.metadata['camera']
            
            lens_parts = []
            if 'lens_make' in camera and camera['lens_make']:
                lens_parts.append(camera['lens_make'])
            if 'lens_model' in camera and camera['lens_model']:
                lens_parts.append(camera['lens_model'])
            
            if lens_parts:
                if lines and lines[-1] != "":
                    lines.append("")
                
                lens_line = ' '.join(lens_parts)
                
                # "Nikon NIKKOR 24-70mm" → "NIKKOR 24-70mm"
                if len(lens_parts) == 2:
                    # 모델명이 제조사로 시작하면 제조사 생략
                    if lens_parts[1].upper().startswith(lens_parts[0].upper()):
                        lens_line = lens_parts[1]

                # 길이 제한
                if len(lens_line) > 38:
                    lens_line = lens_line[:35] + "..."
                
                lines.append(f"🔍 {lens_line}")

        # GPS 고도 표시 수정
        if self.show_gps_info and 'gps' in self.metadata:
            gps = self.metadata['gps']
            if gps:
                if lines and lines[-1] != "":
                    lines.append("")
                
                gps_line = f"📍 {gps['display']}"
                
                if 'altitude' in gps:
                    gps_line += f" | ⛰️ {gps['altitude']}"
                
                lines.append(gps_line)

        scaled_margin = int(12 * self.scale_factor)
        scaled_width = int(400 * self.scale_factor)

        parent = self.parent()
        if parent and isinstance(parent, QWidget):
            parent_width = parent.width()
            parent_height = parent.height()        
        else:
            # 부모가 없으면 화면 크기 사용
            screen = QGuiApplication.primaryScreen()
            if screen:
                geometry = screen.geometry()
                parent_width = geometry.width()
                parent_height = geometry.height()
                debug_print(f"화면 크기 사용: {parent_width}x{parent_height}")
            else:
                # 최후의 기본값
                parent_width = 1920
                parent_height = 1080
                warning_print(f"화면 크기를 가져올 수 없음 - 기본값 사용")
        
        has_map = bool(self.show_map and self.current_gps is not None)
        scaled_map_width = int(400 * self.scale_factor)
        scaled_map_height = int(300 * self.scale_factor)
        spacing = int(10 * self.scale_factor)
        
        total_height = 0
        if lines:
            text = "\n".join(lines)
            self.info_label.setText(text)
            
            font = self.info_label.font()
            metrics = QFontMetrics(font)
            
            scaled_padding_h = int(15 * self.scale_factor) * 2
            text_width = scaled_width - scaled_padding_h
            
            text_rect = metrics.boundingRect(
                0, 0, text_width, 100000,
                Qt.TextFlag.TextWordWrap,
                text
            )
            
            text_height = text_rect.height()
            margin = max(int(text_height * 0.05), int(5 * self.scale_factor))
            text_height += margin
            
            scaled_padding_v = int(12 * self.scale_factor) * 2
            total_height = text_height + scaled_padding_v
            
            min_height = int(50 * self.scale_factor)
            max_height = int(600 * self.scale_factor)
            total_height = max(min_height, min(max_height, total_height))
            
            debug_print(f"  텍스트높이={total_height}px")
        
        if self.position in ["top_right", "bottom_right"]:
            content_x = parent_width - scaled_width - scaled_margin
        else:
            content_x = scaled_margin

        if lines:
            # Y 좌표 계산
            if self.position in ["bottom_left", "bottom_right"]:
                # 하단: 텍스트가 위, 지도가 아래
                if has_map:
                    total_content_height = total_height + spacing + scaled_map_height
                    info_y = parent_height - total_content_height - scaled_margin
                else:
                    info_y = parent_height - total_height - scaled_margin
            else:
                info_y = scaled_margin
            
            debug_print(f"  position={self.position}, info_x={content_x}, info_y={info_y}")

            self.info_label.setGeometry(content_x, info_y, scaled_width, total_height)
            self.info_label.raise_() 
            
            self.info_label.show()
        else:
            self.info_label.setText("")
            self.info_label.hide()
        
        if has_map:
            # Y 좌표 계산
            if lines:
                # 텍스트가 있으면 텍스트 아래에 배치
                info_geom = self.info_label.geometry()
                map_y = info_geom.y() + info_geom.height() + spacing
            else:
                # 텍스트가 없으면 위치에 따라 직접 계산
                if self.position in ["bottom_left", "bottom_right"]:
                    map_y = parent_height - scaled_map_height - scaled_margin
                else:
                    map_y = scaled_margin
            
            # ── 하단 클립 방어 ──────────────────────
            max_map_bottom = parent_height - scaled_margin
            if map_y + scaled_map_height > max_map_bottom:
                map_y = max(scaled_margin, max_map_bottom - scaled_map_height)


            debug_print(f"  map_x={content_x}, map_y={map_y}")
            
            self.map_label.setGeometry(content_x, map_y, scaled_map_width, scaled_map_height)
            self.map_label.raise_() 
            
            debug_print(f"  map_label 위치 설정: geometry={self.map_label.geometry()}")
            
            if not self.map_label.pixmap() or self.map_label.pixmap().isNull():
                self.map_label.hide()
            else:
                self.map_label.show()
        else:
            self.map_label.hide()
        
        # 오버레이 가시성
        has_content = bool(lines)
        should_show = has_content or has_map
        
        debug_print(f"  has_content={has_content}, has_map={has_map}, should_show={should_show}")
        
        self.setVisible(should_show)
        
        if should_show:
            self.raise_()
            debug_print(f"  오버레이 표시됨!")


    # ============================================
    # 지도 관련
    # ============================================

    def _refresh_map(self) -> None:
        # 앱 초기화 순서와 무관하게 항상 최신 범위 유지
        _mn, _mx = get_raster_zoom_range()
        self._min_zoom = _mn
        self._max_zoom = _mx
        self.current_zoom = max(_mn, min(self.current_zoom, _mx))  # clamp

        if not self.enabled:
            self.current_map = None
            self.map_label.hide()
            return

        if self.show_map and self.current_gps:
            lat, lon = self.current_gps
            self._load_map(lat, lon)
        else:
            self.current_map = None
            self.map_label.hide()


    def update_map_zoom(self, zoom: int) -> None:
        # 하드코딩 1~18 → 동적 범위
        if not (self._min_zoom <= zoom <= self._max_zoom):
            warning_print(f"잘못된 줌 레벨: {zoom} (유효 범위: {self._min_zoom}~{self._max_zoom})")
            return
        self.current_zoom = zoom
        self._zoom_debounce_timer.start()


    def _on_zoom_debounced(self) -> None:
        """디바운스 완료 후 실제 지도 재로드"""
        if self.enabled and self.show_map and self.current_gps:
            lat, lon = self.current_gps
            self._load_map(lat, lon)


    def _load_map(self, latitude: float, longitude: float) -> None:
        debug_print(f"지도 로드 시작: ({latitude:.6f}, {longitude:.6f}), 줌={self.current_zoom}")

        # PMTilesMapLoader → RasterTileMapLoader
        pix = RasterTileMapLoader.get_cached_pixmap(
            latitude, longitude, self.current_zoom, 400, 300
        )
        if pix is not None:
            self.stop_map_loader()
            self._show_map_pixmap(pix)
            debug_print("[RasterTiles] 오버레이 캐시 HIT — 즉시 표시")
            return

        self.stop_map_loader()
        self.map_label.clear()
        self.map_label.setText(t("overlay.map_loading"))
        self.map_label.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 144);
                border-radius: 6px;
                border: 2px solid rgba(100, 150, 255, 150);
                color: rgba(255, 255, 255, 200);
                font-size: 12px;
                font-weight: bold;
            }
        """)
        self.map_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_label.show()

        # ← PMTilesMapLoader → RasterTileMapLoader
        self.map_loader = RasterTileMapLoader(
            latitude, longitude,
            zoom   = self.current_zoom,
            width  = 400,
            height = 300,
        )
        self._map_loader_id = id(self.map_loader)
        self.map_loader.map_loaded.connect(
            self._on_map_loaded, Qt.ConnectionType.QueuedConnection
        )
        self.map_loader.load_failed.connect(
            self._on_map_failed, Qt.ConnectionType.QueuedConnection
        )
        self.map_loader.start()
        debug_print("[RasterTiles] 오버레이 지도 로더 시작됨")


    def _show_map_pixmap(self, pix: "QPixmap") -> None:
        """QPixmap을 scale_factor에 맞춰 map_label에 표시"""
        self.current_map = pix
        scaled_w = int(400 * self.scale_factor)
        scaled_h = int(300 * self.scale_factor)
        scaled_pix = pix.scaled(
            scaled_w, scaled_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        opacity_int = int(self.opacity_value * 180)
        self.map_label.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(0, 0, 0, {opacity_int});
                border-radius: {int(6 * self.scale_factor)}px;
                border: {int(2 * self.scale_factor)}px solid rgba(255, 255, 255, 100);
            }}
        """)
        self.map_label.setPixmap(scaled_pix)
        self.map_label.show()


    def _on_map_loaded(self, q_image: QImage) -> None:
        sender = self.sender()
        # isinstance로 타입 좁히기 → Pylance가 map_loaded/load_failed 인식
        if not isinstance(sender, RasterTileMapLoader) \
                or id(sender) != getattr(self, "_map_loader_id", None):
            if isinstance(sender, RasterTileMapLoader):
                try:
                    sender.map_loaded.disconnect(self._on_map_loaded)
                    sender.load_failed.disconnect(self._on_map_failed)
                except RuntimeError:
                    pass
                sender.deleteLater()
            return

        loader = self.map_loader
        self.map_loader = None
        self._map_loader_id = None
        if loader is not None:                         
            try:
                loader.map_loaded.disconnect(self._on_map_loaded)
                loader.load_failed.disconnect(self._on_map_failed)
            except RuntimeError:
                pass
            loader.deleteLater()

        if q_image is None or q_image.isNull():
            return
        pix = QPixmap.fromImage(q_image)
        if pix.isNull():
            return
        self._show_map_pixmap(pix)
        self._refresh_display()    
        

    def _on_map_failed(self, error) -> None:
        warning_print(f"지도 로드 실패: {error}")

        sender = self.sender()
        if not isinstance(sender, RasterTileMapLoader) \
                or id(sender) != getattr(self, "_map_loader_id", None):
            if isinstance(sender, RasterTileMapLoader):
                try:
                    sender.map_loaded.disconnect(self._on_map_loaded)
                    sender.load_failed.disconnect(self._on_map_failed)
                except RuntimeError:
                    pass
                sender.deleteLater()
            return

        self.current_map    = None
        loader              = self.map_loader
        self.map_loader     = None
        self._map_loader_id = None
        if loader:
            try:
                loader.map_loaded.disconnect(self._on_map_loaded)
                loader.load_failed.disconnect(self._on_map_failed)
            except RuntimeError:
                pass
            loader.deleteLater()

        if self.show_map and self.enabled:
            self._fail_generation += 1
            current_gen = self._fail_generation
            self.map_label.setText(t("overlay.map_load_fail", error=str(error)[:30]))
            self.map_label.show()
            QTimer.singleShot(
                3000,
                lambda g=current_gen: self._hide_fail_label(g)
            )
        else:
            self.map_label.hide()

    def _hide_fail_label(self, generation: int) -> None:
        """실패 레이블 지연 숨김 — 세대가 일치할 때만 실행."""
        if getattr(self, '_fail_generation', 0) == generation:
            self.map_label.hide()

    # ============================================
    # 유틸리티
    # ============================================

    def hide_overlay(self) -> None:
        """오버레이 숨김"""
        self._externally_hidden = True  
        self.stop_map_loader()
        self.setVisible(False)
        self.info_label.hide()
        self.map_label.hide()


    def show_overlay(self) -> None:
        self._externally_hidden = False 
        self._refresh_display()
        

    def clear(self):
        """오버레이 초기화 (메모리 정리)"""
        debug_print(f"OverlayWidget.clear() 호출")

        # 안전 종료 헬퍼 사용
        self.stop_map_loader()

        self.file_path = None
        self.metadata = {}
        self.current_map = None
        self.current_gps = None

        self.info_label.clear()
        self.info_label.hide()
        self.map_label.clear()
        self.map_label.hide()
        self.setVisible(False)
        debug_print(f"OverlayWidget 초기화 완료")
        

    def stop_map_loader(self) -> None:
        """
        현재 RasterTileMapLoader를 안전하게 취소하고 참조를 해제한다.
        """
        if self.map_loader is None:
            return

        loader          = self.map_loader
        self.map_loader = None

        try:
            loader.map_loaded.disconnect(self._on_map_loaded)
        except RuntimeError:
            pass
        try:
            loader.load_failed.disconnect(self._on_map_failed)
        except RuntimeError:
            pass

        loader.cancel()
        loader.deleteLater()
        debug_print("[RasterTiles] 오버레이 맵 로더 취소 완료")


    def hideEvent(self, event) -> None:
        """부모 윈도우 닫힘 or 숨김 시 WebView 정리"""
        self.stop_map_loader()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None: 
        super().resizeEvent(event)
        # 자신이 리사이즈될 때 내부 레이블 재배치
        if self.file_path and self.metadata:
            self._refresh_display()