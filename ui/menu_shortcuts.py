# -*- coding: utf-8 -*-
# ui/menu_shortcuts.py
"""
메뉴 & 단축키 모듈

담당 범위:
  - MenuBuilder            : 우클릭 컨텍스트 메뉴 생성 팩토리
  - ShortcutManager        : QShortcut 전역 단축키 등록·관리
  - MenuShortcutController : MainWindow 브릿지

"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QMenu, QWidget

from utils.debug import debug_print, error_print
from utils.lang_manager import t

if TYPE_CHECKING:
    pass


# ══════════════════════════════════════════════════════════════
# 공통 스타일시트
# ══════════════════════════════════════════════════════════════

_MENU_STYLE = """
QMenu {
    background-color: #1e1e1e;
    color: #e0e0e0;
    border: 1px solid #3c3c3c;
    padding: 8px;
}
QMenu::item {
    padding: 4px 20px 4px 13px;
    border-radius: 4px;
    margin: 1px 5px;
}
QMenu::item:selected  { background-color: #0d7dd9; color: #ffffff; }
QMenu::item:pressed   { background-color: #0a5fa5; }
QMenu::item:disabled  { color: #707070; background-color: transparent; }
QMenu::separator      { height: 1px; background: #444444; margin: 8px 15px; }
"""


def _menu(title: str = "", parent: Optional[QWidget] = None) -> QMenu:
    m = QMenu(title, parent)
    m.setStyleSheet(_MENU_STYLE)
    return m

# ══════════════════════════════════════════════════════════════
# MenuBuilder
# ══════════════════════════════════════════════════════════════

class MenuBuilder:
    """MainWindow 상태를 반영한 컨텍스트 메뉴를 빌드한다."""

    def __init__(self, main_window) -> None:
        self._mw = main_window

    def build(self, parent: Optional[QWidget] = None) -> QMenu:
        mw   = self._mw
        menu = _menu(parent=parent)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        self._section_file(menu, parent)

        menu.addSeparator()

        self._section_highlight(menu, parent)

        menu.addSeparator()

        self._section_rotate(menu, parent)
        self._section_capture(menu, parent)
        self._section_print(menu, parent)

        menu.addSeparator()

        self._section_view(menu, parent)
        self._section_info(menu, parent)

        menu.addSeparator()

        exit_act = menu.addAction(t('menu.exit'))
        exit_act.setShortcut(QKeySequence("Alt+F4"))
        exit_act.triggered.connect(mw.close)

        for action in menu.actions():
            if not action.isSeparator() and not action.menu():
                action.setShortcutVisibleInContextMenu(True)

        return menu

    # ── 파일 작업 ─────────────────────────────────────────────

    def _section_file(self, menu: QMenu, parent: Optional[QWidget]) -> None:
        mw       = self._mw
        has_file = bool(mw._current_file)

        a = menu.addAction(t('menu.file.title_open'))
        a.setShortcut(QKeySequence("Ctrl+O"))
        a.triggered.connect(mw._open_file_dialog)

        a = menu.addAction(t('menu.file.rename'))
        a.setShortcut(QKeySequence("F2"))
        a.setEnabled(has_file)
        a.triggered.connect(mw._rename_file)

        a = menu.addAction(t('menu.file.cut'))
        a.setShortcut(QKeySequence.StandardKey.Cut)
        a.setEnabled(has_file)
        a.triggered.connect(mw._cut_file)

        a = menu.addAction(t('menu.file.copy'))
        a.setShortcut(QKeySequence.StandardKey.Copy)
        a.setEnabled(has_file)
        a.triggered.connect(mw._copy_file)

        a = menu.addAction(t('menu.file.paste'))
        a.setShortcut(QKeySequence.StandardKey.Paste)
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        can_paste = mime is not None and (mime.hasUrls() or mime.hasText())
        a.setEnabled(can_paste)
        a.triggered.connect(mw._paste_file)

        a = menu.addAction(t('menu.file.delete'))
        a.setShortcut(QKeySequence.StandardKey.Delete)
        a.setEnabled(has_file)
        a.triggered.connect(mw._delete_file)

        menu.addSeparator()

        a = menu.addAction(t('menu.file.open_location'))
        a.setShortcut(QKeySequence("Ctrl+Shift+E"))
        a.setEnabled(has_file)
        a.triggered.connect(mw._open_file_location)

        a = menu.addAction(t('menu.file.properties'))
        a.setShortcut(QKeySequence("Alt+Return"))
        a.setEnabled(has_file)
        a.triggered.connect(mw._show_file_properties)

        a = menu.addAction(t('menu.file.copy_path'))
        a.setShortcut(QKeySequence("Ctrl+Shift+A"))
        a.setEnabled(has_file)
        a.triggered.connect(mw._copy_file_path)

        a = menu.addAction(t('menu.view.fullscreen'))
        a.setShortcut(QKeySequence("F11"))
        a.setCheckable(True)
        a.setChecked(getattr(mw, "is_fullscreen", False))
        a.triggered.connect(mw._toggle_fullscreen)


    # ── 하이라이트 ────────────────────────────────────────────

    def _section_highlight(self, menu: QMenu, parent: Optional[QWidget]) -> None:
        mw              = self._mw
        has_file        = bool(mw._current_file)
        highlight_count = mw.navigator.get_highlight_count()
        is_highlighted  = mw.navigator.is_current_highlighted()

        # GPS (파일 있을 때만)
        if has_file:
            gps_act = menu.addAction(t('menu.highlight.gps_view'))
            gps_act.setShortcut(QKeySequence("Ctrl+G"))

            # 수정: bool() 로 명시적 변환 → 반드시 True/False 보장
            gps_val = getattr(mw.image_viewer, "current_gps", None)
            has_gps = bool(
                gps_val is not None
                and isinstance(gps_val, tuple)
                and len(gps_val) == 2
                and gps_val[0] is not None
                and gps_val[1] is not None
            )

            gps_act.setEnabled(has_gps)   # ← 반드시 bool
            if has_gps:
                gps_act.triggered.connect(mw._view_gps)
            menu.addSeparator()

        hl_act = menu.addAction(t('menu.highlight.toggle'))
        hl_act.setShortcut(QKeySequence("H"))
        hl_act.setCheckable(True)
        hl_act.setChecked(is_highlighted)
        hl_act.setEnabled(has_file)
        hl_act.triggered.connect(mw._toggle_highlight)

        if highlight_count > 0:
            clear_act = menu.addAction(
                t('menu.highlight.clear_all', count=highlight_count)
            )
            clear_act.setShortcut(QKeySequence("Ctrl+Shift+H"))
            clear_act.triggered.connect(mw._clear_all_highlights)

            hl_sub = _menu(t('menu.highlight.task', count=highlight_count), parent)

            a = hl_sub.addAction(t('menu.highlight.delete'))
            a.setShortcut(QKeySequence("Ctrl+Shift+Delete"))
            a.triggered.connect(mw._delete_highlighted_files)

            a = hl_sub.addAction(t('menu.highlight.copy'))
            a.setShortcut(QKeySequence("Ctrl+Shift+C"))
            a.triggered.connect(mw._copy_highlighted_files)

            a = hl_sub.addAction(t('menu.highlight.cut'))
            a.setShortcut(QKeySequence("Ctrl+Shift+X"))
            a.triggered.connect(mw._cut_highlighted_files)

            menu.addMenu(hl_sub)


    # ── 뷰 토글 ───────────────────────────────────────────────

    def _section_view(self, menu: QMenu, parent: Optional[QWidget]) -> None:
        mw = self._mw
        toggle_sub = _menu(t('menu.view.title'), menu)

        fe_act = toggle_sub.addAction(t("menu.view.folder_explorer"))
        fe_act.setShortcut(QKeySequence("N"))
        fe_act.setCheckable(True)
        fe_act.setChecked(hasattr(mw, "folder_explorer") and mw.folder_explorer.isVisible())
        fe_act.triggered.connect(mw.toggle_folder_explorer)

        a = toggle_sub.addAction(t('menu.view.overlay_info'))
        a.setShortcut(QKeySequence("I"))
        a.setCheckable(True)
        a.setChecked(mw.overlay_widget.isVisible())
        a.triggered.connect(mw._toggle_overlay)

        a = toggle_sub.addAction(t('menu.view.metadata_panel'))
        a.setShortcut(QKeySequence("M"))
        a.setCheckable(True)
        a.setChecked(mw.metadata_panel.isVisible())
        a.triggered.connect(mw._toggle_metadata)

        a = toggle_sub.addAction(t('menu.view.thumbnail_bar'))
        a.setShortcut(QKeySequence("T"))
        a.setCheckable(True)
        a.setChecked(mw.thumbnail_bar.isVisible())
        a.triggered.connect(mw._toggle_thumbnail_bar)

        a = toggle_sub.addAction(t('menu.view.status_bar'))
        a.setShortcut(QKeySequence("S"))
        a.setCheckable(True)
        a.setChecked(mw.statusbar.isVisible())
        a.triggered.connect(mw._toggle_status_bar)

        a = toggle_sub.addAction(t('menu.view.perf_overlay'))
        a.setShortcut(QKeySequence("F12"))
        a.setCheckable(True)
        a.setChecked(mw.status_ctrl._perf_overlay.isVisible())
        a.triggered.connect(mw._toggle_performance_overlay)
       
        menu.addMenu(toggle_sub)


    # ── 회전 ──────────────────────────────────────────────────

    def _section_rotate(self, menu: QMenu, parent: Optional[QWidget]) -> None:
        mw       = self._mw
        has_file = bool(mw._current_file)
        
        rot_sub = _menu(t('menu.rotate.title'), menu)

        specs = [
            (t('menu.rotate.left'),  "Ctrl+Shift+Left",  mw._on_rotate_left),
            (t('menu.rotate.right'), "Ctrl+Shift+Right", mw._on_rotate_right),
            (t('menu.rotate.reset'), "Ctrl+Shift+Up",    mw._on_rotate_reset),
            (t('menu.rotate.apply'), "Ctrl+Shift+Down",  mw._on_rotate_apply),
        ]
        for label, key, slot in specs:
            a = rot_sub.addAction(label)
            a.setShortcut(QKeySequence(key))
            a.setEnabled(has_file)
            a.triggered.connect(slot)

        menu.addMenu(rot_sub)

    # ── 화면 캡처 ─────────────────────────────────────────────

    def _section_capture(self, menu: QMenu, parent: Optional[QWidget]) -> None:
        mw      = self._mw
        cap_sub = _menu(t('menu.capture.title'), menu)

        a = cap_sub.addAction(t('menu.capture.clipboard'))
        a.setShortcut(QKeySequence("Ctrl+Alt+C"))
        a.triggered.connect(mw._capture_to_clipboard)

        a = cap_sub.addAction(t('menu.capture.save_file'))
        a.setShortcut(QKeySequence("Ctrl+Alt+S"))
        a.triggered.connect(mw._capture_and_save)

        menu.addMenu(cap_sub)

    # ── 인쇄 ──────────────────────────────────────────────────

    def _section_print(self, menu: QMenu, parent: Optional[QWidget]) -> None:
        mw        = self._mw
        has_file  = bool(mw._current_file)
        prt_sub = _menu(t('menu.print.title'), menu)

        a = prt_sub.addAction(t('menu.print.current'))
        a.setShortcut(QKeySequence("Ctrl+P"))
        a.setEnabled(has_file)
        a.triggered.connect(mw._on_print_current)

        a = prt_sub.addAction(t('menu.print.all'))
        a.setShortcut(QKeySequence("Ctrl+Alt+P"))
        a.triggered.connect(mw._on_print_all)

        a = prt_sub.addAction(t('menu.print.highlighted'))
        a.setShortcut(QKeySequence("Ctrl+Shift+P"))
        a.triggered.connect(mw._on_print_highlighted)

        menu.addMenu(prt_sub)

    # ── 정보/설정 ─────────────────────────────────────────────

    def _section_info(self, menu: QMenu, parent: Optional[QWidget]) -> None:
        mw       = self._mw
        info_sub = _menu(t('menu.info.title'), menu)

        a = info_sub.addAction(t('menu.info.settings'))
        a.setShortcut(QKeySequence("Ctrl+,"))
        a.triggered.connect(mw._open_settings)

        a = info_sub.addAction(t('menu.info.system_info'))
        a.setShortcut(QKeySequence("F4"))
        a.triggered.connect(mw._show_system_info)

        a = info_sub.addAction(t('menu.info.about'))
        a.setShortcut(QKeySequence("F1"))
        a.triggered.connect(mw._show_about_dialog)

        menu.addMenu(info_sub)


# ══════════════════════════════════════════════════════════════
# ShortcutManager
# ══════════════════════════════════════════════════════════════

class ShortcutManager:
    """
    QShortcut 전역 단축키 등록·관리.
    원본 setup_shortcuts() 의 메서드명·단축키를 그대로 유지.
    """

    def __init__(self, main_window) -> None:
        self._mw = main_window
        self._shortcuts: dict[str, QShortcut] = {}

    def setup(self) -> None:
        mw  = self._mw
        reg = self._reg

        # ── 탐색 ──────────────────────────────────────────────
        reg("next_right",      "Right",           mw._next_image)
        reg("next_space",      "Space",           mw._next_image)
        reg("prev_left",       "Left",            mw._previous_image)
        reg("prev_backspace",  "Backspace",        mw._previous_image)
        reg("first",           "Home",            mw._first_image)
        reg("last",            "End",             mw._last_image)

        # ── 줌 ────────────────────────────────────────────────
        reg("zoom_fit",        "F",               lambda: mw.image_viewer.set_zoom_mode("fit"))
        reg("zoom_actual",     "1",               lambda: mw.image_viewer.set_zoom_mode("actual"))
        reg("zoom_width",      "W",               lambda: mw.image_viewer.set_zoom_mode("width"))
        reg("zoom_in",         "Ctrl+=",          lambda: mw.image_viewer.zoom_in())
        reg("zoom_in2",        "Ctrl++",          lambda: mw.image_viewer.zoom_in())
        reg("zoom_out",        "Ctrl+-",          lambda: mw.image_viewer.zoom_out())

        # ── 뷰 토글 ───────────────────────────────────────────
        reg("fullscreen",      "F11",             mw._toggle_fullscreen)
        reg("folder_explorer", "N",               mw.toggle_folder_explorer)
        reg("overlay",         "I",               mw._toggle_overlay)
        reg("metadata",        "M",               mw._toggle_metadata)
        reg("thumbnail",       "T",               mw._toggle_thumbnail_bar)
        reg("status_bar",       "S",              mw._toggle_status_bar)
        reg("perf",            "F12",             mw._toggle_performance_overlay)

        # ── 파일 작업 ─────────────────────────────────────────
        reg("open_file",       "Ctrl+O",          mw._open_file_dialog)
        reg("open_folder",     "Ctrl+Shift+O",    mw._open_folder_dialog)
        reg("rename",          "F2",              mw._rename_file)
        reg("cut",             "Ctrl+X",          mw._cut_file)
        reg("copy",            "Ctrl+C",          mw._copy_file)
        reg("paste",           "Ctrl+V",          mw._paste_file)
        reg("delete",          "Delete",          mw._delete_file)
        reg("location",        "Ctrl+Shift+E",    mw._open_file_location)
        reg("properties",      "Alt+Return",      mw._show_file_properties)
        reg("copy_path",       "Ctrl+Shift+A",    mw._copy_file_path)

        # ── 하이라이트 ────────────────────────────────────────
        reg("highlight",       "H",               mw._toggle_highlight)
        reg("clear_hl",        "Ctrl+Shift+H",    mw._clear_all_highlights)
        reg("del_hl",          "Ctrl+Shift+Delete", mw._delete_highlighted_files)
        reg("copy_hl",         "Ctrl+Shift+C",    mw._copy_highlighted_files)
        reg("cut_hl",          "Ctrl+Shift+X",    mw._cut_highlighted_files)

        # ── 회전 ──────────────────────────────────────────────
        reg("rot_left",        "Ctrl+Shift+Left",  mw._on_rotate_left)
        reg("rot_right",       "Ctrl+Shift+Right", mw._on_rotate_right)
        reg("rot_reset",       "Ctrl+Shift+Up",    mw._on_rotate_reset)
        reg("rot_apply",       "Ctrl+Shift+Down",  mw._on_rotate_apply)

        # ── 캡처 ──────────────────────────────────────────────
        reg("cap_clip",        "Ctrl+Alt+C",      mw._capture_to_clipboard)
        reg("cap_save",        "Ctrl+Alt+S",      mw._capture_and_save)

        # ── 인쇄 ──────────────────────────────────────────────
        reg("print_cur",       "Ctrl+P",          mw._on_print_current)
        reg("print_all",       "Ctrl+Alt+P",      mw._on_print_all)
        reg("print_hl",        "Ctrl+Shift+P",    mw._on_print_highlighted)

        # ── 기타 ──────────────────────────────────────────────
        reg("gps",             "Ctrl+G",          mw._view_gps)
        reg("settings",        "Ctrl+,",          mw._open_settings)
        reg("about",           "F1",              mw._show_about_dialog)
        reg("sysinfo",         "F4",              mw._show_system_info)
        reg("reload",          "F5",              mw._reload_current_image)
        #reg("escape",          "Escape",          self._on_escape)
        reg("E",               "E",               mw.enter_edit_mode)


        debug_print(f"ShortcutManager: {len(self._shortcuts)}개 단축키 등록 완료")


    def _reg(self, name, key, slot, context=Qt.ShortcutContext.WindowShortcut) -> None:
        sc = None
        try:
            sc = QShortcut(QKeySequence(key), self._mw, context=context)
            sc.activated.connect(slot)
            self._shortcuts[name] = sc
        except Exception as e:
            error_print(f"ShortcutManager: '{name}' ({key}) 등록 실패: {e}")

            if sc is not None and name not in self._shortcuts:
                sc.setParent(None)
                sc.deleteLater()


    def get(self, name: str) -> Optional[QShortcut]:
        return self._shortcuts.get(name)

    def set_enabled(self, name: str, enabled: bool) -> None:
        sc = self._shortcuts.get(name)
        if sc:
            sc.setEnabled(enabled)


    def unregister_all(self) -> None:
        for sc in self._shortcuts.values():
            sc.deleteLater()  
        self._shortcuts.clear()
        

# ══════════════════════════════════════════════════════════════
# MenuShortcutController
# ══════════════════════════════════════════════════════════════

class MenuShortcutController:
    """
    MainWindow ↔ MenuBuilder / ShortcutManager 브릿지.

    - 반드시 UI 생성이 모두 끝난 뒤 setup() 을 호출할 것.
      (상태바, 썸네일바 등 모든 self.* 속성이 준비된 이후)
    """

    def __init__(self, main_window) -> None:
        self._mw       = main_window
        self._builder  = MenuBuilder(main_window)
        self._shortcut = ShortcutManager(main_window)


    def setup(self) -> None:
        """단축키 등록. _init_ui() 맨 마지막 줄에서 호출."""
        self._shortcut.setup()
        debug_print("MenuShortcutController: 설정 완료")


    def build_context_menu(self, parent: Optional[QWidget] = None) -> QMenu:
        """현재 상태를 반영한 컨텍스트 메뉴 반환."""
        return self._builder.build(parent)


    def set_shortcut_enabled(self, name: str, enabled: bool) -> None:
        self._shortcut.set_enabled(name, enabled)


    def get_shortcut(self, name: str) -> Optional[QShortcut]:
        return self._shortcut.get(name)