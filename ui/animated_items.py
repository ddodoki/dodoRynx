# -*- coding: utf-8 -*-
# ui/animated_items.py
"""
애니메이션 관련 그래픽 아이템, 백그라운드 디코딩 워커, 로딩 오버레이.

image_viewer.py 의존 없음 → 순환 참조 발생하지 않음.

exported:
    WebPDecodeWorker   - WebP Pillow 백그라운드 디코딩 QThread
    ApngDecodeWorker   - APNG Pillow 백그라운드 디코딩 QThread
    LoadingOverlay     - 스피너 오버레이 QWidget
    AnimatedGraphicsItem - QMovie 기반 그래픽 아이템
    WebPAnimatedItem   - Pillow 프레임 기반 그래픽 아이템 (정확한 딜레이)
"""

import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QRectF
from PySide6.QtGui import QColor, QFont, QMovie, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QWidget

from utils.debug import error_print
from utils.lang_manager import t

__all__ = [
    "WebPDecodeWorker",
    "ApngDecodeWorker",
    "LoadingOverlay",
    "AnimatedGraphicsItem",
    "WebPAnimatedItem",
]


# ================================================================
# 백그라운드 디코딩 워커
# ================================================================

class WebPDecodeWorker(QThread):
    """WebP 프레임 백그라운드 디코딩 워커.

    decode_finished(frames, delays) 또는 decode_failed() emit.
    Signal 이름을 'finished' 대신 'decode_finished'로 지정하여
    QThread.finished 시그널과 충돌 방지.
    """
    decode_finished = Signal(list, list)  # frames: list[QPixmap], delays: list[int]
    decode_failed   = Signal()


    def __init__(self, file_path: Path) -> None:
        super().__init__()
        self._file_path = file_path


    def run(self) -> None:
        try:
            from core.image_loader import ImageLoader  # 지연 import (워커 스레드에서 실행)
            result = ImageLoader().load_webp_frames(self._file_path)
            if result and result[0]:
                self.decode_finished.emit(result[0], result[1])
            else:
                self.decode_failed.emit()
        except Exception as e:
            error_print(f"WebP 워커 오류: {e}")
            self.decode_failed.emit()


class ApngDecodeWorker(QThread):
    """APNG 프레임 백그라운드 디코딩 워커.

    image_viewer.py의 _ApngDecodeWorker(내부용 명명) 에서 이름 변경.
    이 파일로 이동하면서 모듈-공개 이름으로 확정.
    """
    decode_finished = Signal(list, list)  # frames: list[QPixmap], delays: list[int]
    decode_failed   = Signal()


    def __init__(self, file_path: Path) -> None:
        super().__init__()
        self._file_path = file_path


    def run(self) -> None:
        try:
            from core.image_loader import ImageLoader
            frames, delays = ImageLoader().load_apng_frames(self._file_path)  # type: ignore[misc]
            if frames:
                self.decode_finished.emit(frames, delays)
            else:
                self.decode_failed.emit()
        except Exception as e:
            error_print(f"APNG 워커 오류: {e}")
            self.decode_failed.emit()


# ================================================================
# 로딩 오버레이
# ================================================================

class LoadingOverlay(QWidget):
    """백그라운드 디코딩 중 표시되는 스피너 오버레이.

    공개 API:
        start(message?)  - 오버레이 표시 + 스피너 시작
        stop()           - 오버레이 숨김 + 텍스트 초기화
        set_message(txt) - 실행 중 텍스트 변경
    """

    _R_OUTER: int = 28   # 스포크 외곽 반경(px)
    _R_INNER: int = 18   # 스포크 내측 반경(px)
    _SPOKES:  int = 12   # 스포크 개수


    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setVisible(False)

        self._angle:   int = 0
        self._message: str = t('loading_overlay.anim_loading')

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(40)          # 25 fps
        self._spin_timer.timeout.connect(self._tick)

    # ── 공개 API ──────────────────────────────────────────────────

    def set_message(self, text: str) -> None:
        """표시 텍스트 변경 (start() 전후 모두 가능)."""
        self._message = text
        if self.isVisible():
            self.update()


    def start(self, message: str | None = None) -> None:
        """오버레이 표시 + 스피너 시작."""
        if message is not None:
            self._message = message
        self._angle = 0
        parent = self.parentWidget()
        if parent:
            self.setGeometry(parent.rect())
        self.setVisible(True)
        self.raise_()
        self._spin_timer.start()


    def stop(self) -> None:
        """오버레이 숨김 + 스피너 정지 + 텍스트 기본값 복원."""
        self._spin_timer.stop()
        self.setVisible(False)
        self._message = t('loading_overlay.anim_loading')

    # ── 내부 ──────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._angle = (self._angle + 15) % 360
        self.update()


    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        cx = self.width()  / 2.0
        cy = self.height() / 2.0
        step = 360 // self._SPOKES   # 스포크 간격 (도)

        for i in range(self._SPOKES):
            angle_deg = (self._angle + i * step) % 360
            alpha     = int(255 * (i + 1) / self._SPOKES)
            pen = QPen(QColor(74, 158, 255, alpha), 3.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            rad = math.radians(angle_deg)
            painter.drawLine(
                int(cx + self._R_INNER * math.cos(rad)),
                int(cy + self._R_INNER * math.sin(rad)),
                int(cx + self._R_OUTER * math.cos(rad)),
                int(cy + self._R_OUTER * math.sin(rad)),
            )

        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 210))
        painter.drawText(
            QRectF(cx - 150, cy + self._R_OUTER + 14, 300, 26),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self._message,
        )
        painter.end()


# ================================================================
# 그래픽 아이템
# ================================================================

class AnimatedGraphicsItem(QGraphicsPixmapItem):
    """QMovie 기반 GIF / WebP(고속) 애니메이션 그래픽 아이템.

    frameChanged 시그널을 구독해 QMovie가 디코딩한 프레임을
    씬에 반영한다. cleanup() 호출 후 씬에서 제거해야 한다.
    """

    def __init__(self, movie: QMovie) -> None:
        super().__init__()
        self.movie: Optional[QMovie] = movie
        if self.movie:
            self.movie.frameChanged.connect(self._update_frame)
            self.movie.start()


    def _update_frame(self) -> None:
        """cacheKey 비교 없이 직접 픽스맵 갱신 (불필요한 오버헤드 제거)."""
        if self.movie:
            self.setPixmap(self.movie.currentPixmap())


    def cleanup(self) -> None:
        """QMovie 정지 + signal 해제 + deleteLater.

        씬에서 removeItem() 하기 전에 반드시 호출해야 한다.
        """
        if self.movie:
            self.movie.stop()
            try:
                self.movie.frameChanged.disconnect(self._update_frame)
            except (RuntimeError, TypeError):
                pass
            self.movie.deleteLater()
            self.movie = None


class WebPAnimatedItem(QGraphicsPixmapItem):
    """Pillow 사전 디코딩 기반 애니메이션 아이템.

    QMovie WebP 타이밍 버그를 완전히 우회.
    프레임별 정확한 delay를 QTimer(singleShot)로 직접 제어하여 끊김 제거.
    pause() / resume() API를 제공해 포커스 아웃 시 절전 가능.
    """

    def __init__(self, frames: list, delays: list) -> None:
        super().__init__()
        self._frames: list = frames
        self._delays: list = delays
        self._idx:    int   = 0

        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next_frame)

        if self._frames:
            self.setPixmap(self._frames[0])
            if len(self._frames) > 1:
                self._timer.start(self._delays[0])


    def _next_frame(self) -> None:
        self._idx = (self._idx + 1) % len(self._frames)
        self.setPixmap(self._frames[self._idx])
        self._timer.start(self._delays[self._idx])


    def pause(self) -> None:
        """재생 일시정지."""
        self._timer.stop()


    def resume(self) -> None:
        """재생 재개 (pause() 이후)."""
        if self._frames and not self._timer.isActive():
            self._timer.start(self._delays[self._idx])


    def cleanup(self) -> None:
        """타이머 + 프레임 리스트 완전 해제.

        수정(B2): 구버전에서 누락된 _timer.deleteLater() 추가.
        씬에서 removeItem() 하기 전에 반드시 호출해야 한다.
        """
        self._timer.stop()
        self._timer.deleteLater() 
        self._frames.clear()
        self._delays.clear()
