# -*- coding: utf-8 -*-
# utils/gps_handler.py

"""
GPS 핸들러 - GPS 좌표를 지도 서비스로 연동
"""

import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional, Tuple

from utils.debug import debug_print, error_print, info_print, warning_print


# ============================================
# GPS 핸들러
# ============================================

class GPSHandler:
    """GPS → 지도 서비스 연동"""
    
    MAP_URLS = {
        # 네이버 지도 (place 방식 - 더 정확함)
        'naver': "https://map.naver.com/p/search/{lat},{lon}",
        
        # 카카오맵 (좌표로 지도 보기)
        'kakao': "https://map.kakao.com/link/map/{lat},{lon}",
        
        # 구글 지도
        'google': "https://www.google.com/maps/search/?api=1&query={lat},{lon}",
    }


# ============================================
# 초기화
# ============================================

    def __init__(self, browser_path: str = 'system_default', map_service: str = 'naver'):
        """
        Args:
            browser_path: 브라우저 실행 파일 경로 ('system_default'면 시스템 기본)
            map_service: 지도 서비스 ('naver', 'kakao', 'google')
        """
        self.browser_path = browser_path
        self.map_service = map_service
        
        if browser_path != 'system_default':
            if not Path(browser_path).exists():
                warning_print(f"브라우저 경로가 존재하지 않음: {browser_path}")
                warning_print(f"시스템 기본 브라우저로 대체됨")
                self.browser_path = 'system_default'
        
        debug_print(f"GPSHandler 초기화: browser={browser_path}, service={map_service}")


# ============================================
# 지도 열기
# ============================================

    def open_map(self, lat: float, lon: float) -> bool:
        """
        GPS 좌표로 지도 열기
        
        Args:
            lat: 위도 (latitude)
            lon: 경도 (longitude)
        
        Returns:
            성공 여부
        """
        if not self.is_valid_coordinates(lat, lon):
            error_print(f"잘못된 GPS 좌표: lat={lat}, lon={lon}")
            return False
        
        url = self._build_url(lat, lon)
        debug_print(f"지도 URL: {url}")
        
        success = False
        
        try:
            if self.browser_path == 'system_default':
                # 시스템 기본 브라우저
                success = webbrowser.open(url)
                info_print(f"시스템 기본 브라우저로 지도 열기")
            else:
                # 사용자 지정 브라우저
                success = self._open_with_custom_browser(url)
            
            if success:
                info_print(f"지도 열기 성공: {url}")
                return True
            else:
                warning_print(f"브라우저 열기 실패 - fallback 시도")
        
        except Exception as e:
            error_print(f"브라우저 열기 실패: {e}")
        
        if not success:
            try:
                #debug_print(f"Fallback: 시스템 기본 브라우저 시도")
                success = webbrowser.open(url)
                
                if success:
                    info_print(f"Fallback 성공: 시스템 기본 브라우저")
                    return True
                else:
                    error_print(f"Fallback 실패: 브라우저를 열 수 없음")
                    return False
            
            except Exception as e:
                error_print(f"Fallback 실패: {e}")
                return False
        
        return success


    def _open_with_custom_browser(self, url: str) -> bool:
        """
        사용자 지정 브라우저로 URL 열기
        
        Args:
            url: 열 URL
        
        Returns:
            성공 여부
        """
        try:
            browser_path = Path(self.browser_path)
            
            if not browser_path.exists():
                warning_print(f"브라우저 파일이 존재하지 않음: {browser_path}")
                return False
            
            if sys.platform == 'win32':
                # Windows: subprocess로 직접 실행 (더 안정적)
                subprocess.Popen([str(browser_path), url], shell=False)
                info_print(f"사용자 지정 브라우저로 열기: {browser_path.name}")
                return True
            else:
                # macOS/Linux: webbrowser.get() 사용
                try:
                    browser = webbrowser.get(f'"{str(browser_path)}" %s')
                    browser.open(url)
                    info_print(f"사용자 지정 브라우저로 열기: {browser_path.name}")
                    return True
                except Exception as e:
                    warning_print(f"webbrowser.get() 실패: {e}")
                    return False
        
        except Exception as e:
            error_print(f"사용자 지정 브라우저 실행 실패: {e}")
            return False


# ============================================
# 유틸리티 메소드
# ============================================

    def _build_url(self, lat: float, lon: float) -> str:
        """
        지도 URL 생성
        
        Args:
            lat: 위도
            lon: 경도
        
        Returns:
            지도 URL
        """
        template = self.MAP_URLS.get(self.map_service, self.MAP_URLS['google'])
        url = template.format(lat=lat, lon=lon)
        
        #debug_print(f"URL 생성: service={self.map_service}, url={url}")
        
        return url
    
    
    @staticmethod
    def is_valid_coordinates(lat: float, lon: float) -> bool:
        """
        GPS 좌표 유효성 검사
        
        Args:
            lat: 위도 (-90 ~ 90)
            lon: 경도 (-180 ~ 180)
        
        Returns:
            유효 여부
        """
        is_valid = -90 <= lat <= 90 and -180 <= lon <= 180
        
        if not is_valid:
            debug_print(f"잘못된 GPS 좌표: lat={lat} (범위: -90~90), lon={lon} (범위: -180~180)")
        
        return is_valid


    def get_map_service_name(self) -> str:
        """
        현재 지도 서비스 이름 반환
        
        Returns:
            지도 서비스 이름 (한글)
        """
        service_names = {
            'naver': '네이버 지도',
            'kakao': '카카오맵',
            'google': '구글 지도'
        }
        return service_names.get(self.map_service, self.map_service)


    def set_map_service(self, map_service: str) -> None:
        """
        지도 서비스 변경
        
        Args:
            map_service: 지도 서비스 ('naver', 'kakao', 'google')
        """
        if map_service in self.MAP_URLS:
            self.map_service = map_service
            info_print(f"지도 서비스 변경: {self.get_map_service_name()}")
        else:
            warning_print(f"지원하지 않는 지도 서비스: {map_service}")


    def set_browser_path(self, browser_path: str) -> None:
        """
        브라우저 경로 변경
        
        Args:
            browser_path: 브라우저 실행 파일 경로
        """
        if browser_path != 'system_default':
            if not Path(browser_path).exists():
                warning_print(f"브라우저 경로가 존재하지 않음: {browser_path}")
                warning_print(f"시스템 기본 브라우저로 대체됨")
                browser_path = 'system_default'
        
        self.browser_path = browser_path
        info_print(f"브라우저 경로 변경: {browser_path}")


# ============================================
# 정적 유틸리티 메소드
# ============================================

    @staticmethod
    def format_coordinates(lat: float, lon: float) -> str:
        """
        GPS 좌표를 문자열로 포맷팅
        
        Args:
            lat: 위도
            lon: 경도
        
        Returns:
            포맷된 문자열 (예: "37.5665° N, 126.9780° E")
        """
        # 위도 방향
        lat_dir = "N" if lat >= 0 else "S"
        lat_abs = abs(lat)
        
        # 경도 방향
        lon_dir = "E" if lon >= 0 else "W"
        lon_abs = abs(lon)
        
        return f"{lat_abs:.4f}° {lat_dir}, {lon_abs:.4f}° {lon_dir}"


    @staticmethod
    def parse_coordinates(coord_str: str) -> Optional[Tuple[float, float]]:
        """
        문자열에서 GPS 좌표 파싱
        
        Args:
            coord_str: 좌표 문자열 (예: "37.5665, 126.9780" 또는 "37.5665° N, 126.9780° E")
        
        Returns:
            (lat, lon) 튜플 또는 None (파싱 실패 시)
        """
        try:
            # 쉼표로 분리
            parts = coord_str.replace('°', '').split(',')
            if len(parts) != 2:
                return None
            
            # 공백 및 방향 기호 제거
            lat_str = parts[0].strip().split()[0]
            lon_str = parts[1].strip().split()[0]
            
            lat = float(lat_str)
            lon = float(lon_str)
            
            # 유효성 검사
            if GPSHandler.is_valid_coordinates(lat, lon):
                return (lat, lon)
            else:
                return None
        
        except Exception as e:
            debug_print(f"GPS 좌표 파싱 실패: {coord_str}, 에러: {e}")
            return None

