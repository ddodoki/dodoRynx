# -*- coding: utf-8 -*-
# utils/performance_monitor.py

"""
성능 모니터링 - 로딩 시간, 메모리 사용량 추적
"""

import time
from contextlib import contextmanager
from typing import Dict, List, Optional

import psutil

from utils.debug import debug_print, error_print, info_print, warning_print


# ============================================
# 성능 모니터
# ============================================

class PerformanceMonitor:

# ============================================
# 초기화
# ============================================

    def __init__(self):
        # 타이머
        self.load_start_time: Optional[float] = None
        self.load_end_time: Optional[float] = None
        self.last_load_time: float = 0.0
        
        self.load_times: List[float] = [] 
        self.max_load_count = 100 

        self._timer_stack: Dict[str, float] = {} 
        
        # psutil Process
        try:
            self.process = psutil.Process()
        except Exception as e:
            error_print(f"psutil Process 초기화 실패: {e}")
            self.process = None
    

# ============================================
# 로딩 시간 측정
# ============================================

    def start_load(self) -> None:
        """로딩 시작 시간 기록"""
        self.load_start_time = time.perf_counter()
        debug_print(f"로딩 시작")
    

    def end_load(self) -> float:
        """
        로딩 종료 시간 기록
        
        Returns:
            로딩 시간 (ms)
        """
        if self.load_start_time:
            self.load_end_time = time.perf_counter()
            self.last_load_time = (self.load_end_time - self.load_start_time) * 1000  # ms
            
            self.load_times.append(self.last_load_time)
            
            # 최대 개수 유지
            if len(self.load_times) > self.max_load_count:
                self.load_times.pop(0)
            
            debug_print(f"로딩 완료: {self.last_load_time:.2f}ms")
            
            return self.last_load_time
        
        return 0.0
    

    def get_last_load_time(self) -> float:
        """
        마지막 로딩 시간 (ms)
        
        Returns:
            로딩 시간 (ms)
        """
        return self.last_load_time
    

    def get_average_load_time(self) -> float:
        """
        평균 로딩 시간 (ms)
        
        Returns:
            평균 로딩 시간 (ms)
        """
        if not self.load_times:
            return 0.0
        
        return sum(self.load_times) / len(self.load_times)
    

    def get_load_stats(self) -> Dict[str, float]:
        """
        로딩 시간 통계
        
        Returns:
            {'last': float, 'avg': float, 'min': float, 'max': float, 'count': int}
        """
        if not self.load_times:
            return {
                'last': 0.0,
                'avg': 0.0,
                'min': 0.0,
                'max': 0.0,
                'count': 0
            }
        
        return {
            'last': self.last_load_time,
            'avg': sum(self.load_times) / len(self.load_times),
            'min': min(self.load_times),
            'max': max(self.load_times),
            'count': len(self.load_times)
        }


# ============================================
# 범용 타이머
# ============================================

    def start_timer(self, name: str = "default") -> None:
        """
        범용 타이머 시작
        
        Args:
            name: 타이머 이름 (중첩 타이머 지원)
        """
        self._timer_stack[name] = time.perf_counter()
        #debug_print(f"타이머 시작: {name}")
    

    def end_timer(self, name: str = "default") -> float:
        """
        범용 타이머 종료
        
        Args:
            name: 타이머 이름
        
        Returns:
            경과 시간 (ms)
        """
        if name not in self._timer_stack:
            warning_print(f"타이머가 시작되지 않음: {name}")
            return 0.0
        
        start_time = self._timer_stack.pop(name)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        
        #debug_print(f"타이머 종료: {name}, 경과={elapsed_ms:.2f}ms")
        
        return elapsed_ms
    

    @contextmanager
    def measure(self, name: str = "operation"):
        """
        컨텍스트 매니저로 시간 측정
        
        Args:
            name: 작업 이름
        
        Example:
            >>> with monitor.measure("image_load"):
            ...     load_image()
        """
        start_time = time.perf_counter()
        #debug_print(f"측정 시작: {name}")
        
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            info_print(f"[PERF] {name}: {elapsed_ms:.2f}ms")


# ============================================
# 메모리 측정
# ============================================

    def get_memory_usage(self) -> Dict[str, float]:
        """
        프로세스 메모리 사용량
        
        Returns:
            {'rss_mb': float, 'vms_mb': float}
        """
        if not self.process:
            return {'rss_mb': 0.0, 'vms_mb': 0.0}
        
        try:
            mem_info = self.process.memory_info()
            
            return {
                'rss_mb': mem_info.rss / (1024 * 1024),  # 실제 메모리
                'vms_mb': mem_info.vms / (1024 * 1024),  # 가상 메모리
            }
        except Exception as e:
            error_print(f"메모리 사용량 조회 실패: {e}")
            return {'rss_mb': 0.0, 'vms_mb': 0.0}
    

    def get_system_memory(self) -> Dict[str, float]:
        """
        시스템 메모리 정보
        
        Returns:
            {'total_gb': float, 'available_gb': float, 'used_gb': float, 'percent': float}
        """
        try:
            mem = psutil.virtual_memory()
            
            return {
                'total_gb': mem.total / (1024 ** 3),
                'available_gb': mem.available / (1024 ** 3),
                'used_gb': mem.used / (1024 ** 3),
                'percent': mem.percent,
            }
        except Exception as e:
            error_print(f"시스템 메모리 조회 실패: {e}")
            return {
                'total_gb': 0.0,
                'available_gb': 0.0,
                'used_gb': 0.0,
                'percent': 0.0,
            }


# ============================================
# CPU 측정 (추가)
# ============================================

    def get_cpu_usage(self, interval: float = 0.1) -> float:
        """
        프로세스 CPU 사용률
        
        Args:
            interval: 측정 간격 (초)
        
        Returns:
            CPU 사용률 (%)
        """
        if not self.process:
            return 0.0
        
        try:
            return self.process.cpu_percent(interval=interval)
        except Exception as e:
            error_print(f"CPU 사용률 조회 실패: {e}")
            return 0.0
    

    def get_system_cpu_usage(self) -> float:
        """
        시스템 전체 CPU 사용률
        
        Returns:
            CPU 사용률 (%)
        """
        try:
            return psutil.cpu_percent(interval=0.1)
        except Exception as e:
            error_print(f"시스템 CPU 사용률 조회 실패: {e}")
            return 0.0


# ============================================
# 통합 통계
# ============================================

    def get_stats(self) -> Dict[str, float]:
        """
        전체 통계
        
        Returns:
            {'load_time_ms': float, 'memory_mb': float, 'memory_percent': float}
        """
        mem_usage = self.get_memory_usage()
        sys_mem = self.get_system_memory()
        
        return {
            'load_time_ms': self.last_load_time,
            'memory_mb': mem_usage['rss_mb'],
            'memory_percent': sys_mem['percent'],
        }
    

    def get_detailed_stats(self) -> Dict:
        """
        상세 통계 (모든 정보 포함)
        
        Returns:
            상세 통계 딕셔너리
        """
        load_stats = self.get_load_stats()
        mem_usage = self.get_memory_usage()
        sys_mem = self.get_system_memory()
        
        return {
            'load': load_stats,
            'memory': {
                'process_mb': mem_usage['rss_mb'],
                'system_used_gb': sys_mem['used_gb'],
                'system_total_gb': sys_mem['total_gb'],
                'system_percent': sys_mem['percent'],
            },
        }
    

    def format_stats(self) -> str:
        """
        통계 문자열로 포맷팅
        
        Returns:
            포맷된 통계 문자열
        """
        stats = self.get_stats()
        
        return (
            f"로딩: {stats['load_time_ms']:.0f}ms | "
            f"메모리: {stats['memory_mb']:.1f}MB "
            f"({stats['memory_percent']:.1f}%)"
        )
    

    def format_detailed_stats(self) -> str:
        """
        상세 통계 문자열로 포맷팅
        
        Returns:
            포맷된 상세 통계 문자열
        """
        stats = self.get_detailed_stats()
        load = stats['load']
        mem = stats['memory']
        
        lines = [
            f"[성능 통계]",
            f"로딩 시간:",
            f"  - 최근: {load['last']:.2f}ms",
            f"  - 평균: {load['avg']:.2f}ms",
            f"  - 최소/최대: {load['min']:.2f}ms / {load['max']:.2f}ms",
            f"  - 측정 횟수: {load['count']}",
            f"메모리:",
            f"  - 프로세스: {mem['process_mb']:.1f}MB",
            f"  - 시스템: {mem['system_used_gb']:.2f}GB / {mem['system_total_gb']:.2f}GB ({mem['system_percent']:.1f}%)",
        ]
        
        return "\n".join(lines)


# ============================================
# 유틸리티
# ============================================

    def reset_load_stats(self) -> None:
        """로딩 시간 통계 초기화"""
        self.load_times.clear()
        self.last_load_time = 0.0
        info_print(f"로딩 시간 통계 초기화")
    

    def print_stats(self) -> None:
        """통계 출력 (디버깅용)"""
        info_print(self.format_detailed_stats())
