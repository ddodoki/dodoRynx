# -*- coding: utf-8 -*-
# ui/folder_explorer.py

import os
import shutil
import subprocess
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union, cast

import send2trash

from PySide6.QtCore import (
    QDir,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QSortFilterProxyModel,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QPainter,
    QPalette,
    QStandardItem,
    QStandardItemModel, 
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget, 
    QStyle
)

from utils.lang_manager import t

if TYPE_CHECKING:
    from main_window import MainWindow
    from utils.config_manager import ConfigManager


# ─────────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────────

def _get_supported_image_exts() -> frozenset[str]:
    """
    X 표시 기준(=이미지 존재 여부) 확장자 집합.
    우선순위: FolderNavigator.SUPPORTED_EXTENSIONS → (fallback) 하드코딩.
    """
    try:
        # 앱이 실제로 폴더 스캔/감시에 쓰는 확장자 기준을 우선 사용
        from core.folder_navigator import FolderNavigator  # type: ignore
        exts = getattr(FolderNavigator, "SUPPORTED_EXTENSIONS", None)
        if isinstance(exts, (list, tuple, set)) and exts:
            return frozenset(str(e).lower() for e in exts)
    except Exception:
        pass

    # fallback: 썸네일바가 RAW를 이미지 취급하는 확장자 포함
    return frozenset({
        ".jpg", ".jpeg", ".png", ".apng", ".gif", ".webp", ".bmp", ".tiff", ".tif",
        ".heic", ".heif", ".avif", ".jxl", ".svg",
        ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".pef", ".srw", ".raf",
    })


def _norm_path(p: Path | str) -> str:
    s = str(p).replace("\\", "/").rstrip("/")
    return s


# ─────────────────────────────────────────────────────────────
# 디자인 토큰 (상태바와 공유)
# ─────────────────────────────────────────────────────────────

_TOOLBAR_STYLE = """
    QWidget#fe_toolbar {
        background: #252525;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }
    QToolButton {
        background: rgba(255, 255, 255, 0.05);
        color: #cccccc;
        border: 1px solid rgba(255, 255, 255, 0.10);
        border-radius: 4px;
        padding: 0px 6px;
        font-size: 13px;
    }
    QToolButton:hover {
        background: rgba(74, 158, 255, 0.18);
        border-color: rgba(74, 158, 255, 0.60);
        color: #ffffff;
    }
    QToolButton:pressed {
        background: rgba(74, 158, 255, 0.32);
        border-color: rgba(74, 158, 255, 0.80);
    }
    QToolButton::menu-indicator { image: none; }
"""

_TREE_STYLE = """
    QTreeView {
        background: #1e1e1e;
        color: #cccccc;
        border: none;
        font-size: 11px;
        show-decoration-selected: 1;
        outline: none;
    }
    QTreeView::item {
        padding: 2px 2px;
        min-height: 22px;
    }
    QTreeView::item:selected {
        background: rgba(74, 158, 255, 0.22);
        color: #ffffff;
    }
    QTreeView::item:selected:active {
        background: rgba(74, 158, 255, 0.28);
        color: #ffffff;
    }
    QTreeView::item:hover:!selected {
        background: rgba(255, 255, 255, 0.05);
    }

    QTreeView::branch {
        background: #1e1e1e;
        border-image: none;
        image: none;
    }
    QTreeView::branch:selected {
        background: rgba(74, 158, 255, 0.22);
    }
    QTreeView::branch:hover:!selected {
        background: rgba(255, 255, 255, 0.05);
    }

    QTreeView::branch:has-siblings:!adjoins-item {
        border-image: none; image: none;
        background: #1e1e1e;
    }
    QTreeView::branch:has-siblings:adjoins-item {
        border-image: none; image: none;
        background: #1e1e1e;
    }
    QTreeView::branch:!has-children:!has-siblings:adjoins-item {
        border-image: none; image: none;
        background: #1e1e1e;
    }
    QTreeView::branch:has-children:!has-siblings:closed,
    QTreeView::branch:closed:has-children:has-siblings {
        border-image: none; image: none;
    }
    QTreeView::branch:open:has-children:!has-siblings,
    QTreeView::branch:open:has-children:has-siblings {
        border-image: none; image: none;
    }

    /* ── 스크롤바 (이하 동일) ── */
    QScrollBar:vertical {
        width: 6px; background: transparent;
        border-radius: 3px; margin: 2px 0px;
    }
    QScrollBar::handle:vertical {
        background: rgba(255,255,255,0.15);
        border-radius: 3px; min-height: 30px;
    }
    QScrollBar::handle:vertical:hover   { background: rgba(255,255,255,0.25); }
    QScrollBar::handle:vertical:pressed { background: rgba(74,158,255,0.60); }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical
        { height: 0px; border: none; background: none; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical
        { background: none; }

    QScrollBar:horizontal {
        height: 6px; background: transparent;
        border-radius: 3px; margin: 0px 2px;
    }
    QScrollBar::handle:horizontal {
        background: rgba(255,255,255,0.15);
        border-radius: 3px; min-width: 30px;
    }
    QScrollBar::handle:horizontal:hover   { background: rgba(255,255,255,0.25); }
    QScrollBar::handle:horizontal:pressed { background: rgba(74,158,255,0.60); }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal
        { width: 0px; border: none; background: none; }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal
        { background: none; }
"""

_MENU_STYLE = """
    QMenu {
        background-color: #1e1e1e;
        color: #cccccc;
        border: 1px solid rgba(255, 255, 255, 0.10);
        border-radius: 6px;
        padding: 4px 0;
        font-size: 11px;
    }
    QMenu::item {
        padding: 6px 22px 6px 14px;
        background-color: transparent;
    }
    QMenu::item:selected {
        background: rgba(74, 158, 255, 0.22);
        color: #ffffff;
        border-radius: 3px;
    }
    QMenu::item:disabled { color: rgba(255,255,255,0.25); }
    QMenu::separator {
        height: 1px;
        background: rgba(255, 255, 255, 0.07);
        margin: 3px 8px;
    }
"""

_QUICK_MENU_STYLE = """
    QMenu {
        background-color: #1e1e1e;
        color: #cccccc;
        border: 1px solid rgba(255, 255, 255, 0.10);
        border-radius: 6px;
        padding: 4px 0;
        font-size: 11px;
    }
    QMenu::item {
        padding: 6px 16px;
        background-color: transparent;
    }
    QMenu::item:selected {
        background: rgba(74, 158, 255, 0.22);
        color: #ffffff;
        border-radius: 3px;
    }
"""

def _has_images(folder: Path) -> bool:
    exts = _get_supported_image_exts()
    try:
        return any(
            p.suffix.lower() in exts
            for p in folder.iterdir()
            if p.is_file()
        )
    except PermissionError:
        return True


# ─────────────────────────────────────────────────────────────────────────────
# DirsOnly 프록시
# ─────────────────────────────────────────────────────────────────────────────

class _DirsOnlyProxy(QSortFilterProxyModel):
    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: Union[QModelIndex, QPersistentModelIndex],
    ) -> bool:
        src = self.sourceModel()
        assert isinstance(src, QFileSystemModel)
        parent_idx = cast(QModelIndex, source_parent)
        idx = src.index(source_row, 0, parent_idx)
        return src.isDir(idx)


# ─────────────────────────────────────────────────────────────────────────────
# Branch 화살표를 직접 그리는 델리게이트
# ─────────────────────────────────────────────────────────────────────────────

class _BranchDelegate(QStyledItemDelegate):
    BRANCH_W = 16

    def __init__(self, empty_set: set, parent=None) -> None:
        super().__init__(parent)
        self._empty: set = empty_set


    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: Union[QModelIndex, QPersistentModelIndex],
    ) -> None:
        super().paint(painter, option, index)

        idx = cast(QModelIndex, index)
        tree = self.parent()

        # ── branch 화살표 ──────────────────────────────────────
        if isinstance(tree, QTreeView):
            has_children = tree.model().hasChildren(idx)
            if has_children:
                expanded = tree.isExpanded(idx)
                arrow = "▼" if expanded else "▶"
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                r = option.rect
                branch_rect = QRect(r.x() - self.BRANCH_W, r.y(), self.BRANCH_W, r.height())

                from PySide6.QtGui import QFont as _QF
                f = _QF(painter.font())
                f.setPointSize(7)
                painter.setFont(f)

                is_selected = option.state & QStyle.StateFlag.State_Selected
                arrow_color = QColor("#ffffff") if is_selected else QColor("#888888")
                painter.setPen(arrow_color)

                painter.drawText(
                    branch_rect,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                    arrow,
                )
                painter.restore()

        raw_model = idx.model()
        path_str = ""
        if isinstance(raw_model, QFileSystemModel):
            path_str = raw_model.filePath(idx)
        elif isinstance(raw_model, QSortFilterProxyModel):
            src_idx   = raw_model.mapToSource(idx)
            src_model = raw_model.sourceModel()
            if isinstance(src_model, QFileSystemModel):
                path_str = src_model.filePath(src_idx)

        if not path_str:
            return

        normalized = _norm_path(path_str)

        if normalized not in self._empty:
            return

        painter.save()
        r = option.rect
        fm = option.fontMetrics
        label = idx.data() or ""
        text_w = fm.horizontalAdvance(label)
        icon_w = r.height()   
        badge_x = r.x() + icon_w + text_w + 6
        badge_rect = QRect(badge_x, r.y(), 18, r.height())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        from PySide6.QtGui import QFont as _QF2
        f2 = _QF2(painter.font())
        f2.setPointSize(max(7, f2.pointSize() - 1))
        f2.setBold(True)
        painter.setFont(f2)
        painter.setPen(QColor(210, 70, 70))
        painter.drawText(
            badge_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "✕",
        )
        painter.restore()


# ─────────────────────────────────────────────────────────────────────────────
# 일반 탭 트리
# ─────────────────────────────────────────────────────────────────────────────

class _FolderTree(QTreeView):
    path_activated = Signal(Path)

    def __init__(self, empty_set: set, parent=None) -> None:
        super().__init__(parent)
        self._empty_set = empty_set
        self._fe_pending_scroll = None
        
        self._scroll_timer = QTimer(self)  
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(80)  
        self._scroll_timer.timeout.connect(self._do_scroll)

        self._fs_model = QFileSystemModel(self)
        self._fs_model.setRootPath("")
        self._fs_model.setFilter(
            QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden
        )

        self._proxy = _DirsOnlyProxy(self)
        self._proxy.setSourceModel(self._fs_model)
        self.setModel(self._proxy)

        self._delegate = _BranchDelegate(self._empty_set, self)
        self.setItemDelegate(self._delegate)

        for col in range(1, self._fs_model.columnCount()):
            self.hideColumn(col)

        self.setHeaderHidden(True)
        self.setAnimated(True)
        self.setIndentation(self._delegate.BRANCH_W)
        self.setUniformRowHeights(True)
        self.setStyleSheet(_TREE_STYLE)

        p = self.palette()
        p.setColor(QPalette.ColorRole.Text,            QColor("#cccccc"))
        p.setColor(QPalette.ColorRole.Base,            QColor("#1e1e1e"))
        p.setColor(QPalette.ColorRole.AlternateBase,   QColor("#222222"))
        p.setColor(QPalette.ColorRole.Highlight,       QColor(52, 84, 122))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        p.setColor(QPalette.ColorRole.BrightText,      QColor("#888888"))
        p.setColor(QPalette.ColorRole.Mid,             QColor("#1e1e1e")) 
        p.setColor(QPalette.ColorRole.Midlight,        QColor("#1e1e1e"))  
        p.setColor(QPalette.ColorRole.Dark,            QColor("#1e1e1e"))  
        self.setPalette(p)

        self.activated.connect(self._on_activated)
        self.clicked.connect(self._on_activated)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.header().setStretchLastSection(True)  
        self.header().setSectionResizeMode(       
            0, self.header().ResizeMode.ResizeToContents
        )
        self._proxy.rowsInserted.connect(self._on_rows_inserted)
        self.setRootIsDecorated(False)


    def drawBranches(self, painter, rect, index) -> None:
        painter.fillRect(rect, QColor("#1e1e1e"))

        if self.selectionModel().isSelected(index):
            painter.fillRect(rect, QColor(74, 158, 255, int(255 * 0.28)))


    def _on_activated(self, proxy_idx: QModelIndex) -> None:
        src_idx = self._proxy.mapToSource(proxy_idx)
        p = self._fs_model.filePath(src_idx)
        if p:
            self.path_activated.emit(Path(p))


    def _on_rows_inserted(self, parent: QModelIndex, first: int, last: int) -> None:
        """새 행이 삽입될 때마다 호출 — 대기 경로가 있으면 타이머 리셋."""
        if self._fe_pending_scroll is None:
            return

        self._scroll_timer.start()  


    def navigate_to(self, path: Path) -> None:
        self._fe_pending_scroll = path
        self._scroll_retry = 0

        src_idx = self._fs_model.index(str(path))
        if src_idx.isValid():
            proxy_idx = self._proxy.mapFromSource(src_idx)
            if proxy_idx.isValid():
                self.expand(proxy_idx)
                self.setCurrentIndex(proxy_idx)
        else:
            # directoryLoaded 연결 (중복 방지)
            try:
                self._fs_model.directoryLoaded.disconnect(self._on_directory_loaded)
            except (RuntimeError, TypeError):
                pass
            self._fs_model.directoryLoaded.connect(self._on_directory_loaded)

            self._expand_path_chain(path)

        self._scroll_timer.start()


    def _expand_path_chain(self, target: Path) -> None:
        """루트 → target 방향으로 알려진 조상까지 순서대로 expand."""
        current = Path(target.parts[0])  # C:\ 또는 /
        for part in target.parts[1:]:
            src_idx = self._fs_model.index(str(current))
            if not src_idx.isValid():
                break  # 이 레벨부터 미로드 — directoryLoaded가 이어받음
            proxy_idx = self._proxy.mapFromSource(src_idx)
            if proxy_idx.isValid():
                self.expand(proxy_idx)  # expand → Qt 내부에서 directoryLoaded 트리거
            current = current / part
            

    def _on_directory_loaded(self, loaded_dir: str) -> None:
        """directoryLoaded: 로드된 경로가 target의 조상이면 다음 레벨 cascade."""
        if self._fe_pending_scroll is None:
            return

        target = self._fe_pending_scroll
        loaded = Path(loaded_dir)

        try:
            rel = target.relative_to(loaded)
        except ValueError:
            return

        src_idx = self._fs_model.index(str(target))
        if src_idx.isValid():
            try:
                self._fs_model.directoryLoaded.disconnect(self._on_directory_loaded)
            except RuntimeError:
                pass
            proxy_idx = self._proxy.mapFromSource(src_idx)
            if proxy_idx.isValid():
                self.expand(proxy_idx)
                self.setCurrentIndex(proxy_idx)
            self._scroll_timer.start()
            return

        if rel.parts:
            next_path = loaded / rel.parts[0]
            next_src  = self._fs_model.index(str(next_path))
            if next_src.isValid():
                next_proxy = self._proxy.mapFromSource(next_src)
                if next_proxy.isValid():
                    self.expand(next_proxy)
                    

    def _do_scroll(self) -> None:
        if self._fe_pending_scroll is None:
            return

        src_idx = self._fs_model.index(str(self._fe_pending_scroll))
        if not src_idx.isValid():
            self._scroll_retry = getattr(self, '_scroll_retry', 0) + 1
            if self._scroll_retry > 5:
                # 5회 초과 시 포기 — pending + 연결 모두 정리
                self._fe_pending_scroll = None
                self._scroll_retry = 0
                try:
                    self._fs_model.directoryLoaded.disconnect(self._on_directory_loaded)
                except (RuntimeError, TypeError):
                    pass
                return

            try:
                self._fs_model.directoryLoaded.disconnect(self._on_directory_loaded)
            except (RuntimeError, TypeError):
                pass
            try:
                self._fs_model.directoryLoaded.connect(self._on_directory_loaded)
            except Exception:
                pass
            return

        # 성공 시 카운터 + pending 초기화
        self._scroll_retry = 0
        self._fe_pending_scroll = None

        proxy_idx = self._proxy.mapFromSource(src_idx)
        if not proxy_idx.isValid():
            return

        self.expand(proxy_idx)
        self.setCurrentIndex(proxy_idx)
        self.scrollTo(proxy_idx, QAbstractItemView.ScrollHint.PositionAtCenter)


    def current_path(self) -> Optional[Path]:
        idx = self.currentIndex()
        if not idx.isValid():
            return None
        src_idx = self._proxy.mapToSource(idx)
        p = self._fs_model.filePath(src_idx)
        return Path(p) if p else None


    def path_at(self, proxy_idx: QModelIndex) -> Optional[Path]:
        if not proxy_idx.isValid():
            return None
        src_idx = self._proxy.mapToSource(proxy_idx)
        p = self._fs_model.filePath(src_idx)
        return Path(p) if p else None


    def navigate_to_root(self) -> None:
        """내 컴퓨터 — 모든 펼침 닫기 → 드라이브 목록만 표시."""
        self._fe_pending_scroll = None
        self._scroll_timer.stop()
        self.collapseAll()
        self.clearSelection()
        self.setCurrentIndex(QModelIndex())
        self.scrollToTop()

# ─────────────────────────────────────────────────────────────────────────────
# 즐겨찾기 탭 트리
# ─────────────────────────────────────────────────────────────────────────────

def _fav_display_name(path: Path, max_path_chars: int = 20) -> str:

    name = path.name or str(path)
    parts = path.parts
     
    if len(parts) <= 2:
        short_path = str(parts[0]) if parts else str(path)
    else:
        drive = parts[0]    # "C:\\"
        parent = parts[-2]  # 부모 폴더명
        
        # 부모 폴더명 축약
        if len(parent) > max_path_chars:
            half = (max_path_chars - 3) // 2
            parent = parent[:half] + "..." + parent[-half:]
        
        short_path = f"{drive}...\\{parent}"
    
    return f"{name} ({short_path})"


class _FavTree(QTreeView):
    path_activated = Signal(Path)

    def __init__(self, empty_set: set, parent=None) -> None:
        super().__init__(parent)
        self._empty_set = empty_set
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self.setItemDelegate(_BranchDelegate(self._empty_set, self))
        self.setHeaderHidden(True)
        self.setAnimated(False)
        self.setIndentation(6)
        self.setUniformRowHeights(True)
        self.setRootIsDecorated(False)
        self.setStyleSheet(_TREE_STYLE)

        p = self.palette()
        p.setColor(QPalette.ColorRole.Text,            QColor("#cccccc"))
        p.setColor(QPalette.ColorRole.Base,            QColor("#1e1e1e"))
        p.setColor(QPalette.ColorRole.AlternateBase,   QColor("#222222"))
        p.setColor(QPalette.ColorRole.Highlight,       QColor(52, 84, 122))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        p.setColor(QPalette.ColorRole.BrightText,      QColor("#888888"))
        p.setColor(QPalette.ColorRole.Mid,             QColor("#1e1e1e")) 
        p.setColor(QPalette.ColorRole.Midlight,        QColor("#1e1e1e")) 
        p.setColor(QPalette.ColorRole.Dark,            QColor("#1e1e1e")) 
        self.setPalette(p)

        self.activated.connect(self._on_activated)
        self.clicked.connect(self._on_activated)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.header().setStretchLastSection(True)
        self.header().setSectionResizeMode(
            0, self.header().ResizeMode.ResizeToContents
        )


    def drawBranches(self, painter, rect, index) -> None:
        painter.fillRect(rect, QColor("#1e1e1e"))

        if self.selectionModel().isSelected(index):
            painter.fillRect(rect, QColor(74, 158, 255, int(255 * 0.28)))

    def _on_activated(self, idx: QModelIndex) -> None:
        item = self._model.itemFromIndex(idx)
        if item:
            p = Path(item.data(Qt.ItemDataRole.UserRole) or "")
            if p.is_dir():
                self.path_activated.emit(p)


    def load_favorites(self, paths: list) -> None:
        self._model.clear()
        for p_str in paths:
            p = Path(p_str)
            if p.is_dir():
                display_text = _fav_display_name(p)
                item = QStandardItem(display_text)
                item.setData(p_str, Qt.ItemDataRole.UserRole)
                item.setToolTip(p_str)
                item.setEditable(False)
                self._model.appendRow(item)


    def add_favorite(self, path: Path) -> None:
        for row in range(self._model.rowCount()):
            item = self._model.item(row)
            if item and item.data(Qt.ItemDataRole.UserRole) == str(path):
                return
        self._add_item(path)


    def remove_favorite(self, path: Path) -> None:
        for row in range(self._model.rowCount()):
            item = self._model.item(row)
            if item and item.data(Qt.ItemDataRole.UserRole) == str(path):
                self._model.removeRow(row)
                return


    def _add_item(self, path: Path) -> None:
        display_text = _fav_display_name(path)
        item = QStandardItem(display_text)
        item.setData(str(path), Qt.ItemDataRole.UserRole)
        item.setToolTip(str(path))
        item.setEditable(False)
        self._model.appendRow(item)


    def all_paths(self) -> list:
        result = []
        for row in range(self._model.rowCount()):
            item = self._model.item(row)
            if item:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result


    def path_at(self, idx: QModelIndex) -> Optional[Path]:
        item = self._model.itemFromIndex(idx)
        if item:
            p = Path(item.data(Qt.ItemDataRole.UserRole) or "")
            return p if p.is_dir() else None
        return None


# ─────────────────────────────────────────────────────────────────────────────
# FolderExplorer 메인 위젯
# ─────────────────────────────────────────────────────────────────────────────

class FolderExplorer(QWidget):
    folder_selected = Signal(Path)

    _QUICK_KEYS = ["computer", "desktop", "documents", "pictures", "downloads"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._main_window: Optional["MainWindow"] = None
        self._config: Optional["ConfigManager"] = None
        self._active: bool = False
        self._clipboard_paths: list = []
        self._clipboard_is_cut: bool = False
        self._last_activated_path: Optional[Path] = None
        self._build_ui()

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def set_main_window(self, mw: "MainWindow") -> None:
        self._main_window = mw
        self._config = mw.config
        self._load_settings()


    def activate(self, initial_path: Optional[Path] = None) -> None:
        self._active = True
        target: Optional[Path] = None
        if initial_path and initial_path.is_dir():
            target = initial_path
        elif self._config:
            last = self._config.get("folder_explorer", {}).get("last_path")
            if last and isinstance(last, str) and last.strip() and Path(last).is_dir():
                target = Path(last)
        if target:
            self._normal_tree.navigate_to(target)


    def deactivate(self) -> None:
        self._active = False
        self._normal_tree._fs_model.setRootPath("")

        
    def navigate_to_folder(self, path: Path) -> None:
        if not path.is_dir():
            return
        self._normal_tree.navigate_to(path)


    def refresh_empty_state(self, folder: Path) -> None:
        """폴더의 X 상태를 재계산해서 empty_set + config 캐시를 갱신."""
        if not folder or not folder.is_dir():
            return
        norm = _norm_path(folder)
        is_empty = (not _has_images(folder))

        if is_empty:
            self._empty_set.add(norm)
        else:
            self._empty_set.discard(norm)

        # 트리 뷰 즉시 갱신 (숨김 위젯인 _fav_tree는 갱신 불필요)
        self._normal_tree.viewport().update()

        if self._config:
            fe = dict(self._config.get("folder_explorer", {}))
            lst = [_norm_path(x) for x in fe.get("empty_folders", [])]
            if is_empty:
                if norm not in lst:
                    lst.append(norm)
            else:
                lst = [x for x in lst if x != norm]
            fe["empty_folders"] = lst
            self._config.set("folder_explorer", fe)
            self._config.schedule_save()


    def mark_empty_folder(self, path: Path) -> None:
        self.refresh_empty_state(path)


    def on_files_changed(self, file_path: Path) -> None:
        """외부(FolderWatcher 등)에서 파일 변경 통지 시 호출."""
        try:
            parent = file_path.parent if file_path else None
            if parent and parent.is_dir():
                self.refresh_empty_state(parent)
        except Exception:
            pass


    # ── UI 빌드 ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 상단 툴바 ──────────────────────────────────────────────
        toolbar_widget = QWidget()
        toolbar_widget.setObjectName("fe_toolbar")
        toolbar_widget.setStyleSheet(_TOOLBAR_STYLE)
        toolbar_widget.setFixedHeight(36)
        toolbar_layout = QHBoxLayout(toolbar_widget)
        toolbar_layout.setContentsMargins(6, 0, 6, 0)
        toolbar_layout.setSpacing(4)

        _TOOL_BTN_H = 26

        # 바로가기 드롭다운
        self._quick_btn = QToolButton()
        self._quick_btn.setText("📂")
        self._quick_btn.setFixedHeight(_TOOL_BTN_H)
        self._quick_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._quick_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        _quick_menu = QMenu(self._quick_btn)
        _quick_menu.setStyleSheet(_QUICK_MENU_STYLE)
        for key in self._QUICK_KEYS:
            act = QAction(t(f"folder_explorer.quick.{key}"), self)
            act.setData(key)
            act.triggered.connect(self._on_quick_access)
            _quick_menu.addAction(act)
        self._quick_btn.setMenu(_quick_menu)

        # 즐겨찾기 드롭다운 버튼 (탭 대체)
        self._fav_btn = QToolButton()
        self._fav_btn.setText("⭐")
        self._fav_btn.setFixedHeight(_TOOL_BTN_H)
        self._fav_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._fav_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._fav_menu = QMenu(self._fav_btn)
        self._fav_menu.setStyleSheet(_QUICK_MENU_STYLE)
        self._fav_btn.setMenu(self._fav_menu)

        # 위로 버튼
        self._up_btn = QToolButton()
        self._up_btn.setText("↑")
        self._up_btn.setFixedHeight(_TOOL_BTN_H)
        self._up_btn.setToolTip(t("folder_explorer.toolbar.go_up_tooltip"))
        self._up_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._up_btn.clicked.connect(self._go_up)

        # 새 폴더 버튼
        self._new_btn = QToolButton()
        self._new_btn.setText("+")
        self._new_btn.setFixedHeight(_TOOL_BTN_H)
        self._new_btn.setToolTip(t("folder_explorer.toolbar.new_folder_tooltip"))
        self._new_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._new_btn.clicked.connect(self._create_folder)

        toolbar_layout.addWidget(self._quick_btn, 1)
        toolbar_layout.addWidget(self._fav_btn,   1)
        toolbar_layout.addWidget(self._up_btn,    1)
        toolbar_layout.addWidget(self._new_btn,   1)
        root.addWidget(toolbar_widget)

        # ── 폴더 트리 (탭 없이 직접 배치) ─────────────────────────
        self._empty_set: set = set()

        self._normal_tree = _FolderTree(self._empty_set, self)
        self._normal_tree.path_activated.connect(self._on_path_activated)
        self._normal_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._normal_tree.customContextMenuRequested.connect(
            lambda pos: self._show_context_menu(pos, self._normal_tree)
        )
        root.addWidget(self._normal_tree, 1)

        # _FavTree는 UI 없이 데이터/델리게이트 전용으로 유지
        self._fav_tree = _FavTree(self._empty_set, self)
        self._fav_tree.setVisible(False)


    # ── 컨텍스트 메뉴 ─────────────────────────────────────────────────────────

    def _rebuild_fav_menu(self) -> None:
        """즐겨찾기 드롭다운 메뉴를 현재 목록으로 재구성."""
        self._fav_menu.clear()
        favs = self._get_favorites()  # 이미 str 필터링된 목록

        # 실제로 존재하는 폴더만 표시
        valid_favs = [f for f in favs if Path(f).is_dir()]

        if not valid_favs:
            empty_act = QAction(t("folder_explorer.fav.empty"), self)
            empty_act.setEnabled(False)
            self._fav_menu.addAction(empty_act)
            return

        for i, fav_str in enumerate(valid_favs, start=1):
            p = Path(fav_str)
            folder_name = p.name or str(p)

            # 경로 요약: 루트/드라이브 + "..." + 부모폴더명
            parent = p.parent
            try:
                parts = parent.parts
                if len(parts) > 2:
                    summary = f"{parts[0]}...{parts[-1]}"
                elif len(parts) >= 1:
                    summary = str(parent)
                else:
                    summary = str(parent)
            except Exception:
                summary = str(parent)

            label = f"{i}.  {folder_name}  ({summary})"
            act = QAction(label, self)
            act.setData(fav_str)
            act.setToolTip(fav_str)
            act.triggered.connect(self._on_fav_menu_activated)
            self._fav_menu.addAction(act)


    def _on_fav_menu_activated(self) -> None:
        """즐겨찾기 드롭다운 항목 클릭 → 해당 폴더로 이동."""
        action = self.sender()
        if not isinstance(action, QAction):
            return
        raw = action.data()
        if not raw:
            return
        path = Path(str(raw))
        if path.is_dir():
            self._normal_tree.navigate_to(path)
            self._on_path_activated(path)
            

    def _show_context_menu(
        self,
        pos,
        tree: "_FolderTree",
    ) -> None:
        idx = tree.indexAt(pos)
        path = tree.path_at(idx) if idx.isValid() else tree.current_path()

        menu = QMenu(self)
        menu.setStyleSheet(_MENU_STYLE)

        if path and path.is_dir():
            a = QAction(t("folder_explorer.menu.open"), self)
            a.triggered.connect(lambda checked=False, p=path: self._on_path_activated(p))
            menu.addAction(a)

            a2 = QAction(t("folder_explorer.menu.open_in_explorer"), self)
            a2.triggered.connect(lambda checked=False, p=path: self._open_in_explorer(p))
            menu.addAction(a2)
            menu.addSeparator()

            favs = self._get_favorites()
            if str(path) in favs:
                af = QAction(t("folder_explorer.menu.fav_remove"), self)
                af.triggered.connect(lambda checked=False, p=path: self._remove_favorite(p))
            else:
                af = QAction(t("folder_explorer.menu.fav_add"), self)
                af.triggered.connect(lambda checked=False, p=path: self._add_favorite(p))
            menu.addAction(af)
            menu.addSeparator()

            an = QAction(t("folder_explorer.menu.new_folder"), self)
            an.triggered.connect(lambda checked=False, p=path: self._create_folder(parent_path=p))
            menu.addAction(an)

            ar = QAction(t("folder_explorer.menu.rename"), self)
            ar.triggered.connect(lambda checked=False, p=path: self._rename_folder(p))
            menu.addAction(ar)
            menu.addSeparator()

            ac = QAction(t("folder_explorer.menu.copy"), self)
            ac.triggered.connect(lambda checked=False, p=path: self._copy_folder(p, cut=False))
            menu.addAction(ac)

            ax = QAction(t("folder_explorer.menu.cut"), self)
            ax.triggered.connect(lambda checked=False, p=path: self._copy_folder(p, cut=True))
            menu.addAction(ax)

            acp = QAction(t("folder_explorer.menu.copy_path"), self)
            acp.triggered.connect(lambda checked=False, p=path: self._copy_path(p))
            menu.addAction(acp)

        _has_clip = bool(self._clipboard_paths) or \
                    QApplication.clipboard().mimeData().hasUrls()
        if _has_clip:
            ap = QAction(t("folder_explorer.menu.paste"), self)
            _dst = path if (path and path.is_dir()) else self._normal_tree.current_path()
            ap.triggered.connect(lambda checked=False, d=_dst: self._paste_folder(d))
            menu.addAction(ap)

        if path and path.is_dir():
            menu.addSeparator()

            aprop = QAction(t("folder_explorer.menu.properties"), self)
            aprop.triggered.connect(lambda checked=False, p=path: self._show_properties(p))
            menu.addAction(aprop)

            ad = QAction(t("folder_explorer.menu.delete"), self)
            ad.triggered.connect(lambda checked=False, p=path: self._delete_folder(p))
            menu.addAction(ad)

        if not menu.isEmpty():
            menu.exec(tree.viewport().mapToGlobal(pos))


    # ── 빠른 접근 ─────────────────────────────────────────────────────────────

    def _on_quick_access(self) -> None:
        action = self.sender()
        if not isinstance(action, QAction):
            return
        key = action.data()

        # 내 컴퓨터 — 경로 없이 루트(드라이브 목록)로 이동
        if key == "computer":
            self._normal_tree.navigate_to_root()
            return  # folder_selected 시그널 발생 없음 (선택된 폴더 없으므로)

        path = self._resolve_quick_path(key)
        if path and path.is_dir():
            self._normal_tree.navigate_to(path)
            self._on_path_activated(path)


    @staticmethod
    def _resolve_quick_path(key: str) -> Optional[Path]:
        if key == "computer":
            return None  

        home = Path.home()
        mapping = {
            "desktop":   home / "Desktop",
            "documents": home / "Documents",
            "pictures":  home / "Pictures",
            "downloads": home / "Downloads",
        }
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import windll, wintypes
                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                csidl_map = {
                    "desktop":   0x0000,
                    "documents": 0x0005,
                    "pictures":  0x0027,
                }
                if key in csidl_map:
                    windll.shell32.SHGetFolderPathW(0, csidl_map[key], 0, 0, buf)
                    p = Path(buf.value)
                    if p.is_dir():
                        return p
                elif key == "downloads":
                    import winreg
                    with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\Shell Folders",
                    ) as k:
                        val, _ = winreg.QueryValueEx(
                            k, "{374DE290-123F-4565-9164-39C4925E467B}"
                        )
                        p = Path(val)
                        if p.is_dir():
                            return p
            except Exception:
                pass
        return mapping.get(key, home)

    # ── 경로 활성화 ───────────────────────────────────────────────────────────

    def _on_path_activated(self, path: Path) -> None:
        if not self._active:
            return
        # 선택 시점에 X 상태를 재계산(추가/해제 모두 반영)
        self._last_activated_path = path
        self.refresh_empty_state(path)
        self._save_last_path(path)
        self.folder_selected.emit(path)

    # ── 위로 이동 ─────────────────────────────────────────────────────────────

    def _go_up(self) -> None:
        cur = self._normal_tree.current_path()
        if not cur:
            return
        parent = cur.parent
        if parent == cur:
            return
        self._normal_tree.navigate_to(parent)
        self._on_path_activated(parent)

    # ── 새 폴더 ───────────────────────────────────────────────────────────────

    def _create_folder(self, checked: bool = False, parent_path: Optional[Path] = None) -> None:
        base = parent_path or self._normal_tree.current_path()
        if not base or not base.is_dir():
            return
        name, ok = QInputDialog.getText(
            self,
            t("folder_explorer.dialog.new_folder_title"),
            t("folder_explorer.dialog.new_folder_label"),
        )
        if not ok or not name.strip():
            return
        new_path = base / name.strip()
        try:
            new_path.mkdir(parents=False, exist_ok=False)
            QTimer.singleShot(300, lambda p=new_path: self._normal_tree.navigate_to(p))
        except Exception as e:
            QMessageBox.warning(
                self,
                t("folder_explorer.dialog.error_title"),
                t("folder_explorer.dialog.new_folder_fail") + str(e),
            )

    # ── 이름 바꾸기 ───────────────────────────────────────────────────────────

    def _rename_folder(self, path: Path) -> None:
        new_name, ok = QInputDialog.getText(
            self,
            t("folder_explorer.dialog.rename_title"),
            t("folder_explorer.dialog.rename_label"),
            text=path.name,
        )
        if not ok or not new_name.strip():
            return
        new_path = path.parent / new_name.strip()
        try:
            path.rename(new_path)
            QTimer.singleShot(300, lambda p=new_path: self._normal_tree.navigate_to(p))
        except Exception as e:
            QMessageBox.warning(
                self,
                t("folder_explorer.dialog.error_title"),
                t("folder_explorer.dialog.rename_fail") + str(e),
            )

    # ── 복사 / 잘라내기 ───────────────────────────────────────────────────────

    def _copy_folder(self, path: Path, cut: bool) -> None:
        self._clipboard_paths = [path]
        self._clipboard_is_cut = cut

    def _copy_path(self, path: Path) -> None:
        QApplication.clipboard().setText(str(path))

    # ── 붙여넣기 ──────────────────────────────────────────────────────────────

    def _paste_folder(self, dst: Optional[Path]) -> None:
        target_dir = dst or self._normal_tree.current_path()
        if not target_dir or not target_dir.is_dir():
            QMessageBox.warning(
                self,
                t("folder_explorer.dialog.paste_title"),
                t("folder_explorer.dialog.paste_no_target"),
            )
            return

        if self._clipboard_paths:
            sources      = list(self._clipboard_paths)
            is_cut       = self._clipboard_is_cut
            use_internal = True
        else:
            mime = QApplication.clipboard().mimeData()
            if not mime.hasUrls():
                QMessageBox.information(
                    self,
                    t("folder_explorer.dialog.paste_title"),
                    t("folder_explorer.dialog.paste_no_clip"),
                )
                return
            sources = [
                Path(u.toLocalFile()) for u in mime.urls()
                if Path(u.toLocalFile()).exists()
            ]
            if not sources:
                return
            is_cut       = self._detect_system_cut(mime)
            use_internal = False

        failed: list[tuple[str, str]] = []
        for src in sources:
            dest = target_dir / src.name
            counter = 1
            while dest.exists():
                dest = target_dir / f"{src.stem}_copy{counter}{src.suffix}"
                counter += 1
            try:
                if is_cut:
                    shutil.move(str(src), str(dest))
                else:
                    if src.is_dir():
                        shutil.copytree(str(src), str(dest))
                    else:
                        shutil.copy2(str(src), str(dest))
            except Exception as e:
                failed.append((src.name, str(e)))
                continue 

        # 내부 클립보드 잘라내기: 전체 성공 시에만 초기화
        if use_internal and is_cut and not failed:
            self._clipboard_paths.clear()
            self._clipboard_is_cut = False

        # 실패 항목 일괄 보고
        if failed:
            msg = "\n".join(f"• {name}: {err}" for name, err in failed)
            QMessageBox.warning(
                self,
                t("folder_explorer.dialog.error_title"),
                t("folder_explorer.dialog.paste_fail") + "\n" + msg,
            )

        self.refresh_empty_state(target_dir)
        QTimer.singleShot(0, lambda p=target_dir: self._normal_tree.navigate_to(p))


    @staticmethod
    def _detect_system_cut(mime) -> bool:
        """시스템 클립보드의 Cut/Copy 판별. Preferred DropEffect=2 이면 Cut."""
        try:
            effect_data = bytes(mime.data("Preferred DropEffect").data())
            if len(effect_data) >= 4:
                import struct
                effect = struct.unpack_from("<I", effect_data)[0]
                return effect == 2  # DROPEFFECT_MOVE = Cut
        except Exception:
            pass
        return False  # 기본값: Copy


    # ── 삭제 ──────────────────────────────────────────────────────────────────

    def _delete_folder(self, path: Path) -> None:
        reply = QMessageBox.question(
            self,
            t("folder_explorer.dialog.delete_title"),
            t("folder_explorer.dialog.delete_confirm", name=path.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        parent = path.parent
        cur    = self._normal_tree.current_path()

        try:
            send2trash.send2trash(str(path))

            # 현재 선택이 삭제된 경로(또는 그 하위)이면 부모로 이동
            if cur and (cur == path or cur.is_relative_to(path)):
                self._normal_tree.navigate_to(parent)
                self._on_path_activated(parent)

            self.refresh_empty_state(parent)

        except Exception as e:
            # 삭제 실패 — 트리 상태는 변경하지 않음
            QMessageBox.warning(
                self,
                t("folder_explorer.dialog.error_title"),
                t("folder_explorer.dialog.delete_fail") + str(e),
            )


    # ── 탐색기로 열기 ─────────────────────────────────────────────────────────

    @staticmethod
    def _open_in_explorer(path: Path) -> None:
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", str(path)])
            elif os.uname().sysname == "Darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    # ── 속성 ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _show_properties(path: Path) -> None:
        if os.name != "nt":
            return
        try:
            if not path.exists():
                return

            import ctypes
            import ctypes.wintypes as wintypes

            SEE_MASK_INVOKEIDLIST = 0x0000000C

            class SHELLEXECUTEINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("fMask", wintypes.ULONG),
                    ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR),
                    ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD),
                    ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE),
                ]

            sei = SHELLEXECUTEINFO()
            sei.cbSize = ctypes.sizeof(sei)
            sei.fMask = SEE_MASK_INVOKEIDLIST
            sei.hwnd = None
            sei.lpVerb = "properties"
            sei.lpFile = str(path.resolve())
            sei.lpParameters = None
            sei.lpDirectory = None
            sei.nShow = 1
            sei.hInstApp = None

            ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))  # type: ignore[attr-defined]
        except Exception:
            # 여기서 pass만 하면 디버깅이 불가능하니, 최소한 stderr라도 남기는 걸 권장
            traceback.print_exc()


    # ── 즐겨찾기 ──────────────────────────────────────────────────────────────

    def _get_favorites(self) -> list:
        if not self._config:
            return []
        raw = self._config.get("folder_explorer", {}).get("favorites", [])
        # bool/None/비문자열이 섞여 있을 경우 방어적으로 필터링
        if not isinstance(raw, list):
            return []
        return [f for f in raw if isinstance(f, str) and f.strip()]


    def _save_favorites(self, favs: list) -> None:
        if not self._config:
            return
        # 저장 전에 문자열만, 공백 제거, 중복 제거
        clean = list(dict.fromkeys(
            f for f in favs if isinstance(f, str) and f.strip()
        ))
        fe = dict(self._config.get("folder_explorer", {}))
        fe["favorites"] = clean
        self._config.set("folder_explorer", fe)
        self._config.schedule_save()


    def _add_favorite(self, path: Path) -> None:
        favs = self._get_favorites()
        if str(path) not in favs:
            favs.append(str(path))
            self._save_favorites(favs)
        self._fav_tree.add_favorite(path)
        self._rebuild_fav_menu()


    def _remove_favorite(self, path: Path) -> None:
        favs = [f for f in self._get_favorites() if f != str(path)]
        self._save_favorites(favs)
        self._fav_tree.remove_favorite(path)
        self._rebuild_fav_menu()


    # ── 설정 저장/로드 ────────────────────────────────────────────────────────

    def _load_settings(self) -> None:
        if not self._config:
            return
        fe = self._config.get("folder_explorer", {})

        # favorites 오염값 자동 정리 (bool/None/빈 문자열 제거)
        raw_favs = fe.get("favorites", [])
        if not isinstance(raw_favs, list):
            raw_favs = []
        clean_favs = [f for f in raw_favs if isinstance(f, str) and f.strip()]

        # 오염값이 있었다면 즉시 정리해서 저장 (다음 실행에도 깨끗하게)
        if len(clean_favs) != len(raw_favs if isinstance(raw_favs, list) else []):
            self._save_favorites(clean_favs)

        self._fav_tree.load_favorites(clean_favs)

        for p_str in fe.get("empty_folders", []):
            if not isinstance(p_str, str):
                continue
            normalized = p_str.replace("\\", "/").rstrip("/")
            if normalized and Path(normalized).is_dir():
                self._empty_set.add(normalized)

        self._rebuild_fav_menu()


    def _save_last_path(self, path: Path) -> None:
        if not self._config:
            return
        # 유효한 디렉터리일 때만 저장
        if not path or not path.is_dir():
            return
        fe = dict(self._config.get("folder_explorer", {}))
        fe["last_path"] = str(path)
        self._config.set("folder_explorer", fe)
        self._config.schedule_save()


    def get_current_folder(self) -> Optional[Path]:
        """현재 선택된 폴더 반환. 패널 가시성과 무관하게 마지막 선택값 반환."""
        # 1) 마지막으로 명시적으로 활성화된 경로 우선
        if self._last_activated_path and self._last_activated_path.is_dir():
            return self._last_activated_path

        # 2) 트리 위젯의 현재 선택 fallback
        return self._normal_tree.current_path()