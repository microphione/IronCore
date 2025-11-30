from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

import win32api
import win32con
import win32gui

from ..client_window import WindowInfo
from .drawing import OverlayDrawingMixin
from .hittest import OverlayHitTestMixin
from .layout import OverlayLayoutMixin
from .panel import Panel
from .persistence import OverlayPersistenceMixin
from .windowing import OverlayWindowMixin


@dataclass
class TransparentOverlay(
    OverlayWindowMixin,
    OverlayDrawingMixin,
    OverlayHitTestMixin,
    OverlayLayoutMixin,
    OverlayPersistenceMixin,
):
    window: WindowInfo
    panels: list[Panel] = field(default_factory=list)
    status_lines: list[str] = field(default_factory=list)
    actions_lines: list[str] = field(default_factory=list)
    button_rect: Optional[Tuple[int, int, int, int]] = None
    button_label: str = "Reset"
    on_button_click: Optional[Callable[[], None]] = None
    custom_btn_rect: Optional[Tuple[int, int, int, int]] = None
    on_custom_click: Optional[Callable[[], None]] = None
    on_save_custom: Optional[Callable[[], None]] = None
    on_close_custom: Optional[Callable[[], None]] = None
    custom_modal_visible: bool = False
    options_btn_rect: Optional[Tuple[int, int, int, int]] = None
    on_options_click: Optional[Callable[[], None]] = None
    on_apply_options: Optional[Callable[[Optional[int], dict, Optional[str], Optional[int]], None]] = None
    on_panes_changed: Optional[Callable[[dict], None]] = None
    options_modal_visible: bool = False
    _colorkey = win32api.RGB(255, 0, 255)
    custom_actions_rect: Optional[Tuple[int, int, int, int]] = None
    custom_rows: List[dict] = field(default_factory=list)
    _custom_active_field: Optional[Tuple[str, int]] = None
    _custom_capture_action: Optional[Tuple[str, int]] = None
    options_rect: Optional[Tuple[int, int, int, int]] = None
    available_windows: List[WindowInfo] = field(default_factory=list)
    selected_window_hwnd: Optional[int] = None
    _pane_sizes_backup: dict = field(default_factory=dict)
    _selected_window_backup: Optional[int] = None
    _relative_positions: dict = field(default_factory=dict)
    _was_iconic: bool = False
    _hidden_due_iconic: bool = False
    selected_melee: str = "Fist"
    selected_shield_mode: int = 1  # 1 mob or 2 mobs
    afk_alert_enabled: bool = False
    afk_alert_volume: int = 50  # 0-100
    _selected_melee_backup: Optional[str] = None
    _selected_shield_mode_backup: Optional[int] = None
    _afk_alert_backup: Optional[bool] = None
    _afk_volume_backup: Optional[int] = None
    status_reset_rect: Optional[Tuple[int, int, int, int]] = None
    status_reset_label: str = "Reset"
    on_status_reset_click: Optional[Callable[[], None]] = None
    skills_pane: Tuple[int, int, int, int] = (260, 260, 240, 100)
    skills_lines: list[str] = field(default_factory=list)
    show_exp: bool = True
    show_timers: bool = True
    show_skills: bool = True
    _show_backup: dict = field(default_factory=dict)
    status_pane: Tuple[int, int, int, int] = (10, 100, 240, 140)
    actions_pane: Tuple[int, int, int, int] = (260, 100, 240, 140)
    controls_pane: Tuple[int, int, int, int] = (10, 260, 240, 80)
    _dragging: Optional[Tuple[str, int, int]] = None
    _modal_dragging: Optional[Tuple[str, int, int]] = None
    _positions_path: Path = Path("overlay_positions.json")

    def __post_init__(self) -> None:
        self._hwnd: Optional[int] = None
        self._class_name = f"IroncoreOverlay_{os.getpid()}"
        self._hinstance = win32api.GetModuleHandle(None)
        font_id = getattr(win32con, "DEFAULT_GUI_FONT", getattr(win32con, "SYSTEM_FONT", 17))
        self._font = win32gui.GetStockObject(font_id)
        self._load_positions()
        self._clamp_panes_to_window()
        self._ensure_custom_rect()
        self._ensure_options_rect()
        self._load_custom_actions()
        self._pane_sizes_backup = self._pane_sizes_snapshot()
        self._selected_window_backup = None
        self._show_backup = {"status": self.show_exp, "actions": self.show_timers, "skills": self.show_skills}
        self._layout_status_reset_button()

    def show(self) -> None:
        self._register_class()
        self._create_window()
        win32gui.UpdateWindow(self._hwnd)
        win32gui.PumpMessages()

    def close(self) -> None:
        if self._hwnd:
            win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)

    def set_status(self, lines: Iterable[str]) -> None:
        self.status_lines = [line for line in lines]
        self._layout_status_reset_button()
        if self._hwnd and self.show_exp:
            rect = self._pane_rect_abs(self.status_pane)
            win32gui.InvalidateRect(self._hwnd, rect, True)

    def set_actions_status(self, lines: Iterable[str]) -> None:
        self.actions_lines = [line for line in lines]
        if self._hwnd and self.show_timers:
            rect = self._pane_rect_abs(self.actions_pane)
            win32gui.InvalidateRect(self._hwnd, rect, True)

    def set_skills_status(self, lines: Iterable[str]) -> None:
        self.skills_lines = [line for line in lines]
        if self._hwnd and self.show_skills:
            rect = self._pane_rect_abs(self.skills_pane)
            win32gui.InvalidateRect(self._hwnd, rect, True)

    def set_button(self, rect: Tuple[int, int, int, int], label: str, on_click: Callable[[], None]) -> None:
        self.button_rect = rect
        self.button_label = label
        self.on_button_click = on_click
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, self._pane_rect_abs(self.controls_pane), True)

    def set_custom_button(self, rect: Tuple[int, int, int, int], on_click: Callable[[], None]) -> None:
        self.custom_btn_rect = rect
        self.on_custom_click = on_click
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, self._pane_rect_abs(self.controls_pane), True)

    def set_options_button(self, rect: Tuple[int, int, int, int], on_click: Callable[[], None]) -> None:
        self.options_btn_rect = rect
        self.on_options_click = on_click
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, self._pane_rect_abs(self.controls_pane), True)

    def set_status_reset_button(self, label: str, on_click: Callable[[], None]) -> None:
        self.status_reset_label = label
        self.on_status_reset_click = on_click
        self._layout_status_reset_button()
        if self._hwnd and self.show_exp:
            win32gui.InvalidateRect(self._hwnd, self._pane_rect_abs(self.status_pane), True)

    def open_custom_modal(self) -> None:
        self.custom_actions_rect = None
        self._ensure_custom_rect()
        self.custom_modal_visible = True
        self._modal_dragging = None
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, None, True)
