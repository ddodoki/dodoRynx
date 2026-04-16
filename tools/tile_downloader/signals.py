# tools\tile_downloader\signals.py

from PySide6.QtCore import QObject, Signal


class CalcSignals(QObject):
    progress  = Signal(str)  
    finished  = Signal(object) 
    error     = Signal(str)
    cancelled = Signal()


class EngineSignals(QObject):

    tile_result      = Signal(int, int)       
    state_changed    = Signal(str)           

    # 로그
    log_emitted      = Signal(str, str, float) 

    # 디스크
    disk_checked     = Signal(float, float)
    disk_full        = Signal()
    disk_restored    = Signal()

    # 완료
    engine_finished  = Signal(dict)
    engine_cancelled = Signal(dict)

