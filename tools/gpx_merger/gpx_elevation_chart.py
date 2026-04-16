# -*- coding: utf-8 -*-
# tools\gpx_merger\gpx_elevation_chart.py

"""GPX Merger — QPainter 고도/시간 차트 + 분할 지점 토글"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone 
from typing import List, Optional, Tuple

from PySide6.QtCore  import QPoint, QRect, Qt, Signal
from PySide6.QtGui   import (
    QColor, QFont, QLinearGradient, QMouseEvent,
    QPainter, QPainterPath, QPen, QWheelEvent,
)
from PySide6.QtWidgets import QMenu, QWidget

try:
    import piexif
    _HAS_PIEXIF = True
except ImportError:
    piexif = None        # type: ignore[assignment]
    _HAS_PIEXIF = False

from utils.debug      import debug_print, error_print  
from utils.lang_manager import t


# ── 내부 상수 ──────────────────────────────────────────────────
_PAD_L  = 52
_PAD_R  = 12
_PAD_T  = 20
_PAD_B  = 42
_SNAP_PX = 8     

_C_START    = QColor( 80, 200, 120)  
_C_END      = QColor(255, 100, 100)    
_C_FILE_SEP = QColor(100, 160, 255, 120) 

_C_BG       = QColor(18,  22,  30)
_C_GRID     = QColor(50,  55,  65)
_C_FILL_TOP = QColor(255, 154,  47, 80)
_C_FILL_BOT = QColor(255, 154,  47,  8)
_C_LINE     = QColor(255, 154,  47)
_C_SPLIT    = QColor(255, 100, 100, 200)
_C_HOVER    = QColor(120, 200, 255, 180)
_C_TEXT     = QColor(140, 155, 170)
_C_TIME     = QColor(120, 200, 255)


def _fmt_time_tz(s, utc_offset: float = 0.0) -> str:
    """UTC 오프셋 적용 후 'HH:MM:SS' 반환."""

    dt = _parse_time(s)
    if dt is None:
        return ''
    return (dt + timedelta(hours=utc_offset)).strftime('%H:%M:%S')


def _parse_time(s) -> Optional[datetime]:
    """str 또는 datetime → datetime (UTC). 실패 시 None."""
    if s is None:
        return None

    if isinstance(s, datetime):
        if s.tzinfo is None:
            return s.replace(tzinfo=timezone.utc)
        return s
    if not isinstance(s, str) or not s.strip():
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S',  '%Y-%m-%dT%H:%M:%S.%fZ',
                '%Y-%m-%dT%H:%M:%S.%f'):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _fmt_time(s) -> str:
    """ISO 문자열 또는 datetime → 'HH:MM:SS' (없으면 '')"""
    dt = _parse_time(s)
    if dt is None:
        return ''
    return dt.strftime('%H:%M:%S')


def _fmt_dist(m: float) -> str:
    if m >= 1000:
        return f'{m/1000:.1f} km'
    return f'{m:.0f} m'


def _fmt_ele(e: Optional[float]) -> str:
    if e is None or not math.isfinite(e):
        return ''
    return f'{e:.0f} m'


class GpxElevationChart(QWidget):
    """
    고도 프로파일 차트.

    Signals
    -------
    split_point_added(int)    orig_idx — 분할 지점 추가 요청
    split_point_removed(int)  orig_idx — 분할 지점 제거 요청
    point_hovered(int)        orig_idx — 마우스 호버
    """

    split_point_added   = Signal(int)
    split_point_removed = Signal(int)
    point_hovered       = Signal(int)
    capture_requested   = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(80)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._profile:       List  = [] 
        self._split_indices: List[int] = []
        self._hover_idx:     int   = -1
        self._has_time:      bool  = False

        self._file_boundaries:  List[int] = [] 
        self._track_segments:   List[Tuple[int,int,str]] = []
        self._zoom_x0:          float     = 0.0 
        self._zoom_x1:          float     = 1.0  
        self._ignore_next_press: bool = False   
        self._file_ranges: List[Tuple[int, int, str]] = []

        self._show_time_labels: bool = True
        self._utc_offset: float = 0.0

    # ────────────────────────────────────────────────────────
    # 공개 API
    # ────────────────────────────────────────────────────────

    def load_profile(self, profile: list, split_indices: List[int],
                    track_segments: Optional[List[Tuple[int, int, str]]] = None,
                    file_boundaries: Optional[List[int]] = None,
                    file_ranges: Optional[List[Tuple[int, int, str]]] = None) -> None:  
        self._profile         = self._normalize(profile or [])
        self._split_indices   = list(split_indices)
        self._track_segments  = list(track_segments or [])
        self._file_boundaries = list(file_boundaries or [])
        self._file_ranges     = list(file_ranges or [])
        self._has_time        = self._detect_time()

        self._orig_idx_to_dist: dict[int, float] = {
            int(self._pt_attr(pt, 'orig_idx', i) or i): self._dist_m(pt)
            for i, pt in enumerate(self._profile)
        }

        self.update()                          


    def _normalize(self, pts: list) -> list:
        result = []

        def _get(*keys, default=None): 
            for k in keys:
                v = d.get(k)         
                if v is None:
                    v = getattr(p, k, None) if not isinstance(p, dict) else None
                if v is not None:
                    return v
            return default

        for i, p in enumerate(pts):
            if isinstance(p, dict):
                d = p
            else:
                d = p.__dict__ if hasattr(p, '__dict__') else {}
                for slot in getattr(type(p), '__slots__', []):
                    d[slot] = getattr(p, slot, None)

            result.append({
                'dist_m':   _get('dist_m', 'distance_m', 'distm', 'distM',
                                default=0.0),
                'ele':      _get('ele', 'ele_m', 'elevation', 'altitude', 'elev'),
                'time':     _get('time', 'timestamp', 'datetime'),
                'orig_idx': _get('orig_idx', 'original_index', 'idx', 'index',
                                default=i),
            })
        return result


    def set_split_indices(self, indices: List[int]) -> None:
        self._split_indices = list(indices)
        self.update()


    def set_utc_offset(self, offset: float) -> None:
        self._utc_offset = offset
        self.update()
        
    # ────────────────────────────────────────────────────────
    # 내부 헬퍼
    # ────────────────────────────────────────────────────────

    def _detect_time(self) -> bool:
        for p in self._profile[:20]:
            tv = p['time'] if isinstance(p, dict) else getattr(p, 'time', None)
            if tv:
                return True
        return False


    def _pt_attr(self, p, attr: str, default=None):
        if isinstance(p, dict):
            return p.get(attr, default)
        return getattr(p, attr, default)


    def _dist_m(self, p) -> float:
        """dist_m 값을 float 로 보장 (None 또는 미존재 → 0.0)"""
        v = self._pt_attr(p, 'dist_m', 0.0)
        return float(v) if v is not None else 0.0

    # ── 좌표 변환 ─────────────────────────────────────────────

    def _layout(self) -> dict:
        w, h    = self.width(), self.height()
        pad_b   = 62 if self._has_time else 28
        plot_w  = max(1, w - _PAD_L - _PAD_R)
        plot_h  = max(1, h - _PAD_T - pad_b)
        eles    = [self._pt_attr(p, 'ele') for p in self._profile]
        eles    = [e for e in eles if e is not None and math.isfinite(e)]
        min_e   = min(eles, default=0.0)
        max_e   = max(eles, default=100.0)
        rng_e   = max(1.0, max_e - min_e)
        dists   = [self._dist_m(p) for p in self._profile]
        full_max_d  = max(dists, default=1.0) or 1.0
        view_min_d  = full_max_d * self._zoom_x0   
        view_max_d  = full_max_d * self._zoom_x1
        view_rng_d  = max(1.0, view_max_d - view_min_d)
        return dict(w=w, h=h, pw=plot_w, ph=plot_h, pad_b=pad_b,
                    min_e=min_e, max_e=max_e, rng_e=rng_e,
                    max_d=full_max_d,
                    view_min_d=view_min_d, view_max_d=view_max_d,
                    view_rng_d=view_rng_d)


    def _x_of(self, dist_m: float, lay: dict) -> float:
        ratio = (dist_m - lay['view_min_d']) / lay['view_rng_d']
        return _PAD_L + ratio * lay['pw']


    def _y_of(self, ele: float, lay: dict) -> float:
        norm = (ele - lay['min_e']) / lay['rng_e']
        return _PAD_T + (1.0 - norm) * lay['ph']


    def _idx_at_x(self, px: int) -> int:
        if not self._profile:
            return -1
        lay = self._layout()
        ratio    = max(0.0, min(1.0, (px - _PAD_L) / lay['pw']))
        target_d = lay['view_min_d'] + ratio * lay['view_rng_d']
        best_i, best_gap = 0, float('inf')
        for i, p in enumerate(self._profile):
            gap = abs(self._dist_m(p) - target_d)
            if gap < best_gap:
                best_gap, best_i = gap, i
        return best_i


    def _nearest_split_at_x(self, px: int) -> Optional[int]:
        """px 근처(_SNAP_PX 이내) 기존 분할선의 orig_idx 반환, 없으면 None"""
        if not self._profile or not self._split_indices:
            return None
        lay = self._layout()
        for orig_idx in self._split_indices:
            p_idx = None
            for i, p in enumerate(self._profile):
                if self._pt_attr(p, 'orig_idx', i) == orig_idx:
                    p_idx = i
                    break
            if p_idx is None:
                continue
            dist_m = self._dist_m(self._profile[p_idx]) 
            sx = self._x_of(dist_m, lay)
            if abs(px - sx) <= _SNAP_PX:
                return orig_idx
        return None

    # ────────────────────────────────────────────────────────
    # 이벤트
    # ────────────────────────────────────────────────────────

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if not self._profile:
            return
        idx = self._idx_at_x(int(e.position().x())) 
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.point_hovered.emit(
                self._pt_attr(self._profile[idx], 'orig_idx', idx))
            self.update()


    def leaveEvent(self, _) -> None:
        self._hover_idx = -1
        self.point_hovered.emit(-1)  
        self.update()


    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return

        if self._ignore_next_press:
            self._ignore_next_press = False
            return
        if not self._profile:
            return

        px = int(e.position().x())

        existing = self._nearest_split_at_x(px)
        if existing is not None:
            self.split_point_removed.emit(existing)
            return

        idx  = self._idx_at_x(px)
        if idx < 0:
            return
        orig = self._pt_attr(self._profile[idx], 'orig_idx', idx)
        self.split_point_added.emit(orig)

    # ────────────────────────────────────────────────────────
    # 그리기
    # ────────────────────────────────────────────────────────

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _C_BG)

        if not self._profile:
            p.setPen(_C_TEXT)
            p.drawText(QRect(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                    t('gpx_merger.chart.empty_hint'))
            return

        lay = self._layout()
        self._draw_grid(p, lay)
        self._draw_profile(p, lay)
        self._draw_file_boundaries(p, lay)
        self._draw_file_markers(p, lay)
        self._draw_split_lines(p, lay)
        self._draw_start_end(p, lay)
        self._draw_hover(p, lay)
        self._draw_axes(p, lay)
        self._draw_info_box(p, lay)     
        self._draw_zoom_hint(p, lay) 


    def _find_profile_pt_last_before(self, orig_limit: int):
        """orig_idx < orig_limit 인 마지막 profile 포인트 (파일 끝점용)."""
        result = None
        for i, pt in enumerate(self._profile):
            if int(self._pt_attr(pt, 'orig_idx') or 0) < orig_limit:
                result = pt
        return result


    def _find_profile_pt_first_from(self, orig_start: int):
        """orig_idx >= orig_start 인 첫 profile 포인트 (파일 시작점용)."""
        for i, pt in enumerate(self._profile):
            if int(self._pt_attr(pt, 'orig_idx') or 0) >= orig_start:
                return pt
        return None


    def _draw_file_markers(self, p: QPainter, lay: dict) -> None:
        if len(self._file_ranges) <= 1:
            return
        if not self._show_time_labels:
            return

        bot_y = int(_PAD_T + lay['ph'])
        font  = QFont(); font.setPixelSize(9)
        p.setFont(font)

        for fi, (seg_start, seg_end, color_hex) in enumerate(self._file_ranges):
            color    = QColor(color_hex)
            is_first = (fi == 0)
            is_last  = (fi == len(self._file_ranges) - 1)
            ty_off = 20 if fi % 2 == 0 else 36

            # ── 파일 끝 마커 (마지막 파일 제외) ────────────────
            if not is_last:
                end_pt = self._find_profile_pt_last_before(seg_end)
                if end_pt is not None:
                    ex  = int(self._x_of(self._dist_m(end_pt), lay))
                    ele = self._pt_attr(end_pt, 'ele')
                    if ele is not None and math.isfinite(float(ele)):
                        ey = int(self._y_of(float(ele), lay))
                        p.setPen(QPen(QColor(255, 255, 255, 160), 1))
                        p.setBrush(color)
                        p.drawEllipse(QPoint(ex, ey), 3, 3)
                    t_str = _fmt_time_tz(self._pt_attr(end_pt, 'time'), self._utc_offset)
                    if t_str:
                        p.setPen(color)    
                        p.drawText(QRect(ex - 58, bot_y + ty_off, 54, 12),
                                Qt.AlignmentFlag.AlignRight, t_str)

            # ── 파일 시작 마커 (첫 파일 제외) ──────────────────
            if not is_first:
                start_pt = self._find_profile_pt_first_from(seg_start)
                if start_pt is not None:
                    sx  = int(self._x_of(self._dist_m(start_pt), lay))
                    ele = self._pt_attr(start_pt, 'ele')
                    if ele is not None and math.isfinite(float(ele)):
                        sy = int(self._y_of(float(ele), lay))
                        p.setPen(QPen(QColor(255, 255, 255, 160), 1))
                        p.setBrush(color)
                        p.drawEllipse(QPoint(sx, sy), 3, 3)
                    t_str = _fmt_time_tz(self._pt_attr(start_pt, 'time'), self._utc_offset)
                    if t_str:
                        p.setPen(color)      
                        p.drawText(QRect(sx + 4, bot_y + ty_off, 54, 12),
                                Qt.AlignmentFlag.AlignLeft, t_str)
                        

    def _draw_start_end(self, p: QPainter, lay: dict) -> None:
        if not self._profile:
            return
        start_pt = self._profile[0]
        end_pt   = self._profile[-1] if len(self._profile) > 1 else None
        bot_y    = _PAD_T + lay['ph']

        for is_start, pt in ((True, start_pt), (False, end_pt)):
            if pt is None:
                continue
            dist_m = self._dist_m(pt)
            ele    = self._pt_attr(pt, 'ele')
            time_v = self._pt_attr(pt, 'time')
            t_str  = _fmt_time_tz(time_v, self._utc_offset)
            color  = _C_START if is_start else _C_END
            cx     = int(self._x_of(dist_m, lay))

            # ── 고도 마커 ──────────────────────────────────────
            if ele is not None and math.isfinite(ele):
                cy = int(self._y_of(ele, lay))
                p.setPen(QPen(QColor(255, 255, 255), 1.5))
                p.setBrush(color)
                p.drawEllipse(QPoint(cx, cy), 5, 5)
                font = QFont(); font.setPixelSize(9); font.setBold(True)
                p.setFont(font)
                p.setPen(color)
                lbl_x = cx + 7 if is_start else cx - 15
                p.drawText(lbl_x, cy - 4, 'S' if is_start else 'E')

            # ── 시간 레이블 (배경 박스) ─────────────────────────
            if t_str and self._show_time_labels:
                font2 = QFont(); font2.setPixelSize(10); font2.setBold(True)
                p.setFont(font2)
                fm  = p.fontMetrics()
                bw  = fm.horizontalAdvance(t_str) + 12
                bh  = 17
                bx  = cx if is_start else cx - bw
                by  = int(bot_y) + 32

                # 반투명 배경
                bg = QColor(color); bg.setAlpha(45)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(bx, by, bw, bh, 4, 4)

                # 테두리
                border = QColor(color); border.setAlpha(180)
                p.setPen(QPen(border, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(bx, by, bw, bh, 4, 4)

                # 텍스트
                p.setPen(color)
                p.drawText(QRect(bx, by, bw, bh),
                        Qt.AlignmentFlag.AlignCenter, t_str)


    def _draw_file_boundaries(self, p: QPainter, lay: dict) -> None:
        if not self._file_boundaries:
            return
        font = QFont(); font.setPixelSize(9)
        p.setFont(font)
        for boundary_no, orig_idx in enumerate(self._file_boundaries):  
            for i, pt in enumerate(self._profile):
                if self._pt_attr(pt, 'orig_idx', i) == orig_idx:
                    dist_m = self._dist_m(pt)
                    bx = int(self._x_of(dist_m, lay))
                    p.setPen(QPen(_C_FILE_SEP, 1, Qt.PenStyle.DashDotLine))
                    p.drawLine(bx, _PAD_T, bx, _PAD_T + lay['ph'])
                    p.setPen(_C_FILE_SEP.lighter(150))
                    p.drawText(bx + 3, _PAD_T + 10,
                            f'▶{boundary_no + 2}')          
                    break


    def _draw_grid(self, p: QPainter, lay: dict) -> None:
        pen = QPen(_C_GRID, 1, Qt.PenStyle.SolidLine)
        p.setPen(pen)
        for i in range(1, 4):
            y = _PAD_T + i / 4 * lay['ph']
            p.drawLine(int(_PAD_L), int(y),
                       int(_PAD_L + lay['pw']), int(y))


    def _draw_profile(self, p: QPainter, lay: dict) -> None:
        if len(self._profile) < 2:
            return
        if not self._track_segments:
            self._draw_profile_segment(p, lay, self._profile, _C_LINE)
            return
        for seg_start, seg_end, color_hex in self._track_segments:
            seg_pts = [pt for pt in self._profile
                    if seg_start <= int(self._pt_attr(pt, 'orig_idx') or 0) < seg_end]
            if seg_pts:
                self._draw_profile_segment(p, lay, seg_pts, QColor(color_hex))


    def _draw_profile_segment(self, p: QPainter, lay: dict,
                            pts: list, color: QColor) -> None:
        if len(pts) < 2:
            return
        bot_y   = _PAD_T + lay['ph']
        top_c   = QColor(color); top_c.setAlpha(80)
        bot_c   = QColor(color); bot_c.setAlpha(8)
        grad    = QLinearGradient(0, _PAD_T, 0, bot_y)
        grad.setColorAt(0.0, top_c)
        grad.setColorAt(1.0, bot_c)

        path    = QPainterPath()
        first   = True
        last_x  = 0.0
        first_x = float(_PAD_L)

        for pt in pts:
            e = self._pt_attr(pt, 'ele')
            if e is None or not math.isfinite(e):
                first = True
                continue
            x = self._x_of(self._dist_m(pt), lay)
            y = self._y_of(e, lay)
            if first:
                first_x = max(float(_PAD_L), x)
                path.moveTo(first_x, y)
                first = False
            else:
                path.lineTo(x, y)
            last_x = x

        if first:
            return

        clamped_last_x = min(last_x, float(_PAD_L + lay['pw']))
        fill = QPainterPath(path)
        fill.lineTo(clamped_last_x, bot_y)
        fill.lineTo(first_x, bot_y)
        fill.closeSubpath()
        p.fillPath(fill, grad)

        p.setPen(QPen(color, 2.0))
        p.drawPath(path)


    def _draw_split_lines(self, p: QPainter, lay: dict) -> None:
        if not self._split_indices:
            return
        for orig_idx in self._split_indices:
            dist_m = None
            for i, pt in enumerate(self._profile):
                if self._pt_attr(pt, 'orig_idx', i) == orig_idx:
                    dist_m = self._dist_m(pt) 
                    break
            if dist_m is None:
                continue
            sx = int(self._x_of(dist_m, lay))
            p.setPen(QPen(_C_SPLIT, 1.5, Qt.PenStyle.DashLine))
            p.drawLine(sx, _PAD_T, sx, _PAD_T + lay['ph'])
            p.setPen(QPen(_C_SPLIT, 1))
            font = QFont()
            font.setPixelSize(9)
            p.setFont(font)
            p.drawText(sx - 4, _PAD_T - 2, '✕')


    def _draw_hover(self, p: QPainter, lay: dict) -> None:
        if self._hover_idx < 0 or self._hover_idx >= len(self._profile):
            return
        pt     = self._profile[self._hover_idx]
        dist_m = self._dist_m(pt)
        ele    = self._pt_attr(pt, 'ele')
        time   = self._pt_attr(pt, 'time')

        hx = int(self._x_of(dist_m, lay))

        # 수직 호버선
        p.setPen(QPen(_C_HOVER, 1, Qt.PenStyle.DashLine))
        p.drawLine(hx, _PAD_T, hx, _PAD_T + lay['ph'])

        # 고도 점
        if ele is not None and math.isfinite(ele):
            hy = int(self._y_of(ele, lay))
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.setBrush(_C_HOVER)
            p.drawEllipse(QPoint(hx, hy), 4, 4)

        # 툴팁 라벨
        parts = [_fmt_dist(dist_m)]
        if ele is not None and math.isfinite(ele):
            parts.append(_fmt_ele(ele))
        time_str = _fmt_time_tz(time, self._utc_offset)
        if time_str:
            parts.append(time_str)

        label = '  '.join(p2 for p2 in parts if p2)
        if label:
            self._draw_label(p, hx, _PAD_T - 4, label)


    def _draw_label(self, p: QPainter, cx: int, bot_y: int,
                    text: str) -> None:
        font = QFont()
        font.setPixelSize(11)
        p.setFont(font)
        fm    = p.fontMetrics()
        tw    = fm.horizontalAdvance(text)
        bw    = tw + 14
        bh    = 18
        bx    = max(2, min(cx - bw // 2, self.width() - bw - 2))
        by = max(2, bot_y - bh)  

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 28, 42, 220))
        p.drawRoundedRect(bx, by, bw, bh, 5, 5)

        p.setPen(_C_TIME)
        p.drawText(QRect(bx, by, bw, bh),
                   Qt.AlignmentFlag.AlignCenter, text)


    def _draw_axes(self, p: QPainter, lay: dict) -> None:
        font = QFont()
        font.setPixelSize(10)
        p.setFont(font)
        p.setPen(_C_TEXT)

        # Y축 (고도) — 변경 없음
        for i in range(5):
            ratio = i / 4
            y     = _PAD_T + ratio * lay['ph']
            val   = lay['max_e'] - ratio * lay['rng_e']
            p.drawText(QRect(0, int(y) - 8, _PAD_L - 6, 16),
                    Qt.AlignmentFlag.AlignRight |
                    Qt.AlignmentFlag.AlignVCenter,
                    f'{val:.0f}m')

        # X축 — 줌 범위 기준으로 step 계산
        bot  = _PAD_T + lay['ph']
        step = _axis_step(lay['view_rng_d'])  

        d_start = (math.floor(lay['view_min_d'] / step) + 1) * step   
        d = d_start
        while d < lay['view_max_d']:                                  
            x = int(self._x_of(d, lay))
            if _PAD_L <= x <= _PAD_L + lay['pw']:    
                p.drawLine(x, int(bot), x, int(bot) + 3)
                p.drawText(QRect(x - 24, int(bot) + 4, 48, 14),
                        Qt.AlignmentFlag.AlignCenter,
                        _fmt_dist(d))
            d += step

        # 힌트 텍스트
        p.setPen(_C_SPLIT.lighter(130))
        p.drawText(QRect(_PAD_L, _PAD_T + lay['ph'] + 20, lay['pw'], 12),
                Qt.AlignmentFlag.AlignRight,
                t('gpx_merger.chart.hint_split'))


    def _draw_info_box(self, p: QPainter, lay: dict) -> None:
        """총 거리 · 총 시간 · 평균 속도 인포박스 (우상단)."""
        if not self._profile:
            return

        total_dist_m = self._dist_m(self._profile[-1])
        start_t = end_t = None
        for pt in self._profile:
            dt = _parse_time(self._pt_attr(pt, 'time'))
            if dt:
                if start_t is None: start_t = dt
                end_t = dt

        parts: List[str] = [_fmt_dist(total_dist_m)]
        if start_t and end_t and end_t > start_t:
            sec = (end_t - start_t).total_seconds()
            h, m = int(sec // 3600), int((sec % 3600) // 60)
            parts.append(f'{h}h {m:02d}m' if h else f'{m}m')
            dist_km = total_dist_m / 1000.0
            if sec > 0 and dist_km > 0:
                parts.append(f'{dist_km / (sec / 3600):.1f} km/h')

        text = '  ·  '.join(parts)
        font = QFont(); font.setPixelSize(11)
        p.setFont(font)
        fm = p.fontMetrics()
        bw, bh = fm.horizontalAdvance(text) + 16, 20
        bx = _PAD_L + lay['pw'] - bw - 4
        by = _PAD_T + 4

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 28, 42, 210))
        p.drawRoundedRect(int(bx), int(by), bw, bh, 4, 4)
        p.setPen(_C_TEXT)
        p.drawText(QRect(int(bx), int(by), bw, bh),
                Qt.AlignmentFlag.AlignCenter, text)


    def _draw_zoom_hint(self, p: QPainter, lay: dict) -> None:
        """줌 상태 표시 — 전체 보기가 아닐 때만 좌상단에 표시."""
        if self._zoom_x0 == 0.0 and self._zoom_x1 == 1.0:
            return
        pct  = int((self._zoom_x1 - self._zoom_x0) * 100)
        text = t('gpx_merger.chart.zoom_hint', pct=pct)
        font = QFont(); font.setPixelSize(10)
        p.setFont(font)
        p.setPen(QColor(120, 200, 255, 180))
        p.drawText(_PAD_L + 4, _PAD_T + 14, text)


    def wheelEvent(self, e: QWheelEvent) -> None:
        """스크롤 줌 — 마우스 위치 고정 줌인/아웃, 더블클릭으로 초기화."""
        if not self._profile:
            e.ignore()
            return
        delta = e.angleDelta().y()
        if delta == 0:
            e.ignore()
            return

        lay   = self._layout()
        ratio = max(0.0, min(1.0, (e.position().x() - _PAD_L) / lay['pw']))
        span  = self._zoom_x1 - self._zoom_x0
        factor    = 0.75 if delta > 0 else 1.333     
        new_span  = max(0.05, min(1.0, span * factor))

        # 커서 위치를 앵커로 고정
        anchor = self._zoom_x0 + ratio * span
        new_x0 = anchor - ratio * new_span
        new_x1 = new_x0 + new_span

        if new_x0 < 0.0: new_x0, new_x1 = 0.0, new_span
        if new_x1 > 1.0: new_x0, new_x1 = 1.0 - new_span, 1.0

        self._zoom_x0, self._zoom_x1 = new_x0, new_x1
        e.accept()
        self.update()


    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        # NOTE: Qt 이벤트 순서상 첫 번째 press는 이미 처리됨.
        # _ignore_next_press는 더블클릭 직후 이어지는 단독 클릭을 방지하기 위한 것.
        self._ignore_next_press = True
        self._zoom_x0, self._zoom_x1 = 0.0, 1.0
        self.update()


    def contextMenuEvent(self, e) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #1e1e1e;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 4px 0;
                color: #d4d4d4;
            }
            QMenu::item {
                padding: 5px 28px 5px 16px;
                background: transparent;
            }
            QMenu::item:selected {
                background: #0e4a7a;
                color: #fff;
            }
            QMenu::item:disabled { color: #555; }
            QMenu::separator {
                height: 1px;
                background: #3a3a3a;
                margin: 3px 0;
            }
            QMenu::indicator {
                width: 14px; height: 14px;
                left: 6px;
            }
            QMenu::indicator:checked {
                image: none;
                background: #0e7fd4;
                border: 1px solid #4a9eff;
                border-radius: 2px;
            }
        """)

        act_copy   = menu.addAction(t('gpx_merger.chart.ctx_copy'))
        act_export = menu.addAction(t('gpx_merger.chart.ctx_export'))
        menu.addSeparator()
        act_time   = menu.addAction(t('gpx_merger.chart.ctx_time_labels'))
        act_time.setCheckable(True)
        act_time.setChecked(self._show_time_labels)
        if self._zoom_x0 != 0.0 or self._zoom_x1 != 1.0:
            menu.addSeparator()
            act_zoom   = menu.addAction(t('gpx_merger.chart.ctx_zoom_reset'))
            def _reset_zoom() -> None:
                self._zoom_x0 = 0.0
                self._zoom_x1 = 1.0
                self.update()
            act_zoom.triggered.connect(_reset_zoom)
        act = menu.exec(e.globalPos())
        if act == act_copy:
            self._copy_to_clipboard()
        elif act == act_export:
            self._export_jpg()
        elif act == act_time:                               
            self._show_time_labels = not self._show_time_labels
            self.update()


    def _copy_to_clipboard(self) -> None:
        self.capture_requested.emit('clipboard')


    def _export_jpg(self) -> None:
        self.capture_requested.emit('jpg')


    def _inject_exif(self, path: str) -> None:
        """EXIF Software / Artist / Copyright 에 dodoRynx 주입."""
        try:
            import piexif
            exif_dict = {
                '0th': {
                    piexif.ImageIFD.Software:  'dodoRynx'.encode(),
                    piexif.ImageIFD.Artist:    'dodoRynx'.encode(),
                    piexif.ImageIFD.Copyright: 'dodoRynx'.encode(),
                },
                'Exif': {}, 'GPS': {},
            }
            piexif.insert(piexif.dump(exif_dict), path)
        except ImportError:
            debug_print('[Chart] EXIF 주입 실패 — pip install piexif')
        except Exception as e:
            error_print(f'[Chart] EXIF 오류: {e}')


def _axis_step(max_d: float) -> float:
    for step in (500, 1000, 2000, 5000, 10000, 20000, 50000):
        if max_d / step <= 6:
            return float(step)
    return 100000.0
