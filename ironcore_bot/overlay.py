"""
Transparent overlay with two draggable panes (status + actions).
Only the title bars/buttons are hit-testable; rest is click-through.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Tuple

import ctypes
import json
import win32api
import win32con
import win32gui

from .client_window import WindowInfo
from pathlib import Path

# Pane size limits
MIN_PANE_W = 50
MAX_PANE_W = 300
MIN_PANE_H = 50
MAX_PANE_H = 500


@dataclass
class Panel:
    x: int
    y: int
    width: int
    height: int
    color: tuple[int, int, int] = (0, 200, 255)
    outline_only: bool = True
    thickness: int = 2

    def rect(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.width, self.y + self.height


@dataclass
class TransparentOverlay:
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
    on_apply_options: Optional[Callable[[Optional[int], dict], None]] = None
    on_panes_changed: Optional[Callable[[dict], None]] = None
    options_modal_visible: bool = False
    _colorkey = win32api.RGB(255, 0, 255)
    custom_actions_rect: Optional[Tuple[int, int, int, int]] = None
    custom_rows: List[dict] = field(default_factory=list)
    _custom_active_field: Optional[Tuple[str, int]] = None  # ("name"/"count", idx)
    _custom_capture_action: Optional[Tuple[str, int]] = None  # ("action1"/"action2", idx)
    options_rect: Optional[Tuple[int, int, int, int]] = None
    available_windows: List[WindowInfo] = field(default_factory=list)
    selected_window_hwnd: Optional[int] = None
    _pane_sizes_backup: dict = field(default_factory=dict)
    _selected_window_backup: Optional[int] = None
    _relative_positions: dict = field(default_factory=dict)
    _was_iconic: bool = False
    _hidden_due_iconic: bool = False

    # draggable panes
    status_pane: Tuple[int, int, int, int] = (10, 100, 240, 140)  # x,y,w,h
    actions_pane: Tuple[int, int, int, int] = (260, 100, 240, 140)
    controls_pane: Tuple[int, int, int, int] = (10, 260, 240, 80)
    _dragging: Optional[Tuple[str, int, int]] = None  # ("status"/"actions"/"controls", dx, dy)
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

    def show(self) -> None:
        self._register_class()
        self._create_window()
        win32gui.UpdateWindow(self._hwnd)
        win32gui.PumpMessages()

    def close(self) -> None:
        if self._hwnd:
            win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)

    def _register_class(self) -> None:
        wndclass = win32gui.WNDCLASS()
        wndclass.lpfnWndProc = self._wnd_proc
        wndclass.hInstance = self._hinstance
        wndclass.lpszClassName = self._class_name
        wndclass.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        wndclass.hbrBackground = win32con.COLOR_WINDOW + 1
        try:
            win32gui.RegisterClass(wndclass)
        except win32gui.error:
            pass

    def _create_window(self) -> None:
        ex_style = win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW
        style = win32con.WS_POPUP
        left, top, right, bottom = self.window.rect
        width, height = right - left, bottom - top
        hwnd = win32gui.CreateWindowEx(
            ex_style,
            self._class_name,
            "Ironcore Overlay",
            style,
            left,
            top,
            width,
            height,
            0,
            0,
            self._hinstance,
            None,
        )
        self._hwnd = hwnd
        win32gui.SetLayeredWindowAttributes(hwnd, self._colorkey, 255, win32con.LWA_COLORKEY)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, left, top, width, height, win32con.SWP_SHOWWINDOW)
        ctypes.windll.user32.SetTimer(hwnd, 1, 500, None)
        # periodic sync timer (ms)

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int):
        if msg == win32con.WM_NCHITTEST:
            sx = win32api.LOWORD(lparam)
            sy = win32api.HIWORD(lparam)
            cx = sx - self.window.rect[0]
            cy = sy - self.window.rect[1]
            # Modale mają priorytet nad panelami
            if self._hit_custom_modal(cx, cy) or self._hit_options_modal(cx, cy):
                return win32con.HTCLIENT
            if self._hit_button(cx, cy) or self._hit_custom_btn(cx, cy) or self._hit_options_btn(cx, cy):
                return win32con.HTCLIENT
            if self._hit_titlebar(cx, cy):
                return win32con.HTCLIENT
            return win32con.HTTRANSPARENT
        if msg == win32con.WM_LBUTTONDOWN:
            x = win32api.LOWORD(lparam)
            y = win32api.HIWORD(lparam)
            if self.custom_modal_visible and self._handle_custom_click(lparam):
                return 0
            if self.options_modal_visible and self._handle_options_click(lparam):
                return 0
            if self._hit_button(x, y):
                if self.on_button_click:
                    self.on_button_click()
                return 0
            if self._hit_custom_btn(x, y):
                if self.on_custom_click:
                    self.on_custom_click()
                return 0
            if self._hit_options_btn(x, y):
                if self.on_options_click:
                    self.on_options_click()
                return 0
            pane = self._which_titlebar(x, y)
            if pane:
                if pane == "status":
                    px, py, w, h = self.status_pane
                elif pane == "actions":
                    px, py, w, h = self.actions_pane
                else:
                    px, py, w, h = self.controls_pane
                self._dragging = (pane, x - px, y - py)
                return 0
        if msg == win32con.WM_WINDOWPOSCHANGED or msg == win32con.WM_MOVE or msg == win32con.WM_SIZE:
            self._sync_to_window()
        if msg == win32con.WM_LBUTTONUP:
            if self._dragging:
                self._save_positions()
            self._dragging = None
        if msg == win32con.WM_MOUSEMOVE and self._dragging:
            pane, dx, dy = self._dragging
            x = win32api.LOWORD(lparam)
            y = win32api.HIWORD(lparam)
            if pane == "status":
                _, _, w, h = self.status_pane
                self.status_pane = (x - dx, y - dy, w, h)
            elif pane == "actions":
                _, _, w, h = self.actions_pane
                self.actions_pane = (x - dx, y - dy, w, h)
            elif pane == "controls":
                _, _, w, h = self.controls_pane
                self.controls_pane = (x - dx, y - dy, w, h)
            win32gui.InvalidateRect(hwnd, None, True)
            return 0
        if msg == win32con.WM_PAINT:
            self._on_paint(hwnd)
            return 0
        if msg == win32con.WM_TIMER:
            self._sync_to_window()
            return 0
        if msg == win32con.WM_DESTROY:
            try:
                ctypes.windll.user32.KillTimer(hwnd, 1)
            except Exception:
                pass
            if self.on_close_custom:
                self.on_close_custom()
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _on_paint(self, hwnd: int) -> None:
        hdc, paint_struct = win32gui.BeginPaint(hwnd)
        width, height = self.window.width, self.window.height
        try:
            background_brush = win32gui.CreateSolidBrush(self._colorkey)
            win32gui.FillRect(hdc, (0, 0, width, height), background_brush)
            win32gui.DeleteObject(background_brush)
            for panel in self.panels:
                self._draw_panel_outline(hdc, panel)
            self._draw_panes(hdc)
            self._draw_custom_modal(hdc)
            self._draw_options_modal(hdc)
            self._draw_active_indicator(hdc)
        finally:
            win32gui.EndPaint(hwnd, paint_struct)

    def set_status(self, lines: Iterable[str]) -> None:
        self.status_lines = [line for line in lines]
        if self._hwnd:
            rect = self._pane_rect_abs(self.status_pane)
            win32gui.InvalidateRect(self._hwnd, rect, True)

    def set_actions_status(self, lines: Iterable[str]) -> None:
        self.actions_lines = [line for line in lines]
        if self._hwnd:
            rect = self._pane_rect_abs(self.actions_pane)
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

    # Drawing helpers
    def _draw_panel_outline(self, hdc: int, panel: Panel) -> None:
        left, top, right, bottom = panel.rect()
        thickness = max(1, panel.thickness)
        brush = win32gui.CreateSolidBrush(win32api.RGB(*panel.color))
        try:
            win32gui.FillRect(hdc, (left, top, right, top + thickness), brush)
            win32gui.FillRect(hdc, (left, bottom - thickness, right, bottom), brush)
            win32gui.FillRect(hdc, (left, top, left + thickness, bottom), brush)
            win32gui.FillRect(hdc, (right - thickness, top, right, bottom), brush)
        finally:
            win32gui.DeleteObject(brush)

    def _draw_panes(self, hdc: int) -> None:
        self._draw_pane(hdc, self.status_pane, self.status_lines, include_buttons=False)
        self._draw_pane(hdc, self.actions_pane, self.actions_lines, include_buttons=False)
        self._draw_pane(hdc, self.controls_pane, [], include_buttons=True)

    def _draw_pane(self, hdc: int, pane: Tuple[int, int, int, int], lines: List[str], include_buttons: bool) -> None:
        x, y, w, h = pane
        px, py = x, y
        title_h = 20
        # border
        # title bar only (no border/background)
        bar_brush = win32gui.CreateSolidBrush(win32api.RGB(50, 50, 50))
        bar_pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(120, 120, 120))
        old_pen = win32gui.SelectObject(hdc, bar_pen)
        old_brush = win32gui.SelectObject(hdc, bar_brush)
        try:
            win32gui.Rectangle(hdc, px, py, px + w, py + title_h)
        finally:
            win32gui.SelectObject(hdc, old_pen)
            win32gui.SelectObject(hdc, old_brush)
            win32gui.DeleteObject(bar_pen)
            win32gui.DeleteObject(bar_brush)

        # content text on transparent background
        if lines:
            text = "\n".join(lines)
            rect = (px + 6, py + title_h + 4, px + w - 6, py + h - 6)
            old_bk = win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
            old_color = win32gui.SetTextColor(hdc, win32api.RGB(255, 255, 255))
            old_font = win32gui.SelectObject(hdc, self._font)
            win32gui.DrawText(hdc, text, -1, rect, win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_WORDBREAK)
            win32gui.SelectObject(hdc, old_font)
            win32gui.SetTextColor(hdc, old_color)
            win32gui.SetBkMode(hdc, old_bk)

        if include_buttons:
            pane_offset_x = x
            pane_offset_y = y
            if self.button_rect:
                bx, by, bw, bh = self.button_rect
                self._draw_button_rect(hdc, pane_offset_x + bx, pane_offset_y + by, bw, bh, self.button_label)
            if self.custom_btn_rect:
                cx, cy, cw, ch = self.custom_btn_rect
                self._draw_button_rect(hdc, pane_offset_x + cx, pane_offset_y + cy, cw, ch, "Custom")
            if self.options_btn_rect:
                ox, oy, ow, oh = self.options_btn_rect
                self._draw_button_rect(hdc, pane_offset_x + ox, pane_offset_y + oy, ow, oh, "Options")

    def _draw_button_rect(self, hdc: int, x: int, y: int, w: int, h: int, label: str) -> None:
        brush = win32gui.CreateSolidBrush(win32api.RGB(60, 60, 60))
        pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(180, 180, 180))
        old_brush = win32gui.SelectObject(hdc, brush)
        old_pen = win32gui.SelectObject(hdc, pen)
        try:
            win32gui.Rectangle(hdc, x, y, x + w, y + h)
            win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
            win32gui.SetTextColor(hdc, win32api.RGB(230, 230, 230))
            rect = (x + 4, y + 2, x + w - 4, y + h - 2)
            win32gui.DrawText(hdc, label, -1, rect, win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
        finally:
            win32gui.SelectObject(hdc, old_brush)
            win32gui.SelectObject(hdc, old_pen)
            win32gui.DeleteObject(brush)
            win32gui.DeleteObject(pen)

    def _draw_input(self, hdc: int, x: int, y: int, w: int, h: int, text: str, active: bool = False) -> None:
        brush = win32gui.CreateSolidBrush(win32api.RGB(40, 40, 40))
        pen_color = win32api.RGB(200, 200, 120) if active else win32api.RGB(180, 180, 180)
        pen = win32gui.CreatePen(win32con.PS_SOLID, 1, pen_color)
        old_brush = win32gui.SelectObject(hdc, brush)
        old_pen = win32gui.SelectObject(hdc, pen)
        try:
            win32gui.Rectangle(hdc, x, y, x + w, y + h)
            win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
            win32gui.SetTextColor(hdc, win32api.RGB(230, 230, 230))
            rect = (x + 4, y + 2, x + w - 4, y + h - 2)
            win32gui.DrawText(hdc, text, -1, rect, win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE)
        finally:
            win32gui.SelectObject(hdc, old_brush)
            win32gui.SelectObject(hdc, old_pen)
            win32gui.DeleteObject(brush)
            win32gui.DeleteObject(pen)

    def _draw_custom_modal(self, hdc: int) -> None:
        if not self.custom_modal_visible or not self.custom_actions_rect:
            return
        x, y, w, h = self.custom_actions_rect
        px, py = x, y
        title_h = 24
        brush = win32gui.CreateSolidBrush(win32api.RGB(30, 30, 30))
        pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(160, 160, 160))
        old_brush = win32gui.SelectObject(hdc, brush)
        old_pen = win32gui.SelectObject(hdc, pen)
        old_bk = win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
        old_color = win32gui.SetTextColor(hdc, win32api.RGB(230, 230, 230))
        try:
            win32gui.Rectangle(hdc, px, py, px + w, py + h)
            bar_brush = win32gui.CreateSolidBrush(win32api.RGB(50, 50, 50))
            win32gui.FillRect(hdc, (px, py, px + w, py + title_h), bar_brush)
            win32gui.DeleteObject(bar_brush)
            win32gui.DrawText(
                hdc,
                "Custom actions",
                -1,
                (px + 6, py + 2, px + w - 6, py + title_h),
                win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
            )
            # Editable list
            content_x = px + 8
            content_y = py + title_h + 8
            row_h = 26
            for idx, row in enumerate(self.custom_rows):
                ry = content_y + idx * row_h
                self._draw_input(hdc, content_x, ry, 140, row_h - 4, str(row.get("name", "")), active=self._custom_active_field == ("name", idx))
                lbl1 = "press key..." if self._custom_capture_action == ("action1", idx) else str(row.get("action1", "select"))
                self._draw_button_rect(hdc, content_x + 150, ry, 80, row_h - 4, lbl1)
                win32gui.DrawText(
                    hdc, "and", -1, (content_x + 235, ry, content_x + 255, ry + row_h), win32con.DT_LEFT | win32con.DT_VCENTER
                )
                lbl2 = "press key..." if self._custom_capture_action == ("action2", idx) else str(row.get("action2", "select"))
                self._draw_button_rect(hdc, content_x + 260, ry, 80, row_h - 4, lbl2)
                self._draw_input(
                    hdc,
                    content_x + 345,
                    ry,
                    40,
                    row_h - 4,
                    str(row.get("count", "1")),
                    active=self._custom_active_field == ("count", idx),
                )
                self._draw_button_rect(hdc, content_x + 390, ry, 20, row_h - 4, "x")
            plus_y = content_y + len(self.custom_rows) * row_h
            self._draw_button_rect(hdc, content_x, plus_y, 24, row_h - 4, "+")
            self._draw_button_rect(hdc, px + w - 70, py + h - 32, 60, 24, "Save")
        finally:
            win32gui.SelectObject(hdc, old_brush)
            win32gui.SelectObject(hdc, old_pen)
            win32gui.SetBkMode(hdc, old_bk)
            win32gui.SetTextColor(hdc, old_color)
            win32gui.DeleteObject(brush)
            win32gui.DeleteObject(pen)

    def _draw_options_modal(self, hdc: int) -> None:
        if not self.options_modal_visible or not self.options_rect:
            return
        x, y, w, h = self.options_rect
        px, py = x, y
        title_h = 24
        brush = win32gui.CreateSolidBrush(win32api.RGB(30, 30, 30))
        pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(160, 160, 160))
        old_brush = win32gui.SelectObject(hdc, brush)
        old_pen = win32gui.SelectObject(hdc, pen)
        old_bk = win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
        old_color = win32gui.SetTextColor(hdc, win32api.RGB(230, 230, 230))
        try:
            win32gui.Rectangle(hdc, px, py, px + w, py + h)
            bar_brush = win32gui.CreateSolidBrush(win32api.RGB(50, 50, 50))
            win32gui.FillRect(hdc, (px, py, px + w, py + title_h), bar_brush)
            win32gui.DeleteObject(bar_brush)
            win32gui.DrawText(
                hdc,
                "Options",
                -1,
                (px + 6, py + 2, px + w - 6, py + title_h),
                win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
            )
            content_x = px + 8
            content_y = py + title_h + 8
            # Windows list
            win32gui.DrawText(
                hdc,
                "Game window (ironcore.exe):",
                -1,
                (content_x, content_y, px + w - 16, content_y + 18),
                win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
            )
            list_y = content_y + 18
            row_h = 24
            if not self.available_windows:
                win32gui.DrawText(
                    hdc,
                    "Brak dostepnych okien procesu.",
                    -1,
                    (content_x, list_y, px + w - 16, list_y + row_h),
                    win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
                )
                list_height = row_h
            else:
                for idx, winfo in enumerate(self.available_windows):
                    ry = list_y + idx * row_h
                    label = f"{winfo.hwnd} | pid {winfo.process_id} | {winfo.width}x{winfo.height}"
                    prefix = "[x] " if self.selected_window_hwnd == winfo.hwnd else "[ ] "
                    self._draw_button_rect(hdc, content_x, ry, min(360, w - 16), row_h - 2, prefix + label)
                list_height = max(row_h, len(self.available_windows) * row_h)

            panes_y = list_y + list_height + 10
            win32gui.DrawText(
                hdc,
                "Pane sizes (w/h):",
                -1,
                (content_x, panes_y, px + w - 16, panes_y + 18),
                win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
            )
            row_y = panes_y + 20
            pane_rows = [
                ("status", "Status", self.status_pane),
                ("actions", "Actions", self.actions_pane),
                ("controls", "Controls", self.controls_pane),
            ]
            for idx, (name, label, pane) in enumerate(pane_rows):
                py_row = row_y + idx * 28
                win32gui.DrawText(
                    hdc,
                    label,
                    -1,
                    (content_x, py_row, content_x + 60, py_row + 22),
                    win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
                )
                w_val = str(pane[2])
                h_val = str(pane[3])
                # width controls
                self._draw_button_rect(hdc, content_x + 70, py_row, 20, 22, "-")
                self._draw_input(hdc, content_x + 92, py_row, 46, 22, w_val, active=False)
                self._draw_button_rect(hdc, content_x + 140, py_row, 20, 22, "+")
                # height controls
                self._draw_button_rect(hdc, content_x + 180, py_row, 20, 22, "-")
                self._draw_input(hdc, content_x + 202, py_row, 46, 22, h_val, active=False)
                self._draw_button_rect(hdc, content_x + 250, py_row, 20, 22, "+")

            apply_y = row_y + len(pane_rows) * 28 + 10
            self._draw_button_rect(hdc, px + w - 80, apply_y, 70, 26, "Apply")
            self._draw_button_rect(hdc, px + w - 160, apply_y, 70, 26, "Cancel")
        finally:
            win32gui.SelectObject(hdc, old_brush)
            win32gui.SelectObject(hdc, old_pen)
            win32gui.SetBkMode(hdc, old_bk)
            win32gui.SetTextColor(hdc, old_color)
            win32gui.DeleteObject(brush)
            win32gui.DeleteObject(pen)

    def _draw_active_indicator(self, hdc: int) -> None:
        if not self.options_modal_visible:
            return
        badge_w, badge_h = 70, 22
        x = 8
        y = 8
        brush = win32gui.CreateSolidBrush(win32api.RGB(20, 120, 20))
        pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(200, 255, 200))
        old_brush = win32gui.SelectObject(hdc, brush)
        old_pen = win32gui.SelectObject(hdc, pen)
        old_bk = win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
        old_color = win32gui.SetTextColor(hdc, win32api.RGB(230, 255, 230))
        try:
            win32gui.Rectangle(hdc, x, y, x + badge_w, y + badge_h)
            win32gui.DrawText(
                hdc,
                "Active",
                -1,
                (x, y, x + badge_w, y + badge_h),
                win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
            )
        finally:
            win32gui.SelectObject(hdc, old_brush)
            win32gui.SelectObject(hdc, old_pen)
            win32gui.SetBkMode(hdc, old_bk)
            win32gui.SetTextColor(hdc, old_color)
            win32gui.DeleteObject(brush)
            win32gui.DeleteObject(pen)

    # Hit testing helpers
    def _hit_titlebar(self, sx: int, sy: int) -> bool:
        return self._which_titlebar(sx, sy) is not None

    def _which_titlebar(self, sx: int, sy: int) -> Optional[str]:
        for name, pane in (("status", self.status_pane), ("actions", self.actions_pane), ("controls", self.controls_pane)):
            x, y, w, h = pane
            px, py = x, y
            title_h = 20
            if px <= sx <= px + w and py <= sy <= py + title_h:
                return name
        return None

    def _hit_button(self, sx: int, sy: int) -> bool:
        if not self.button_rect:
            return False
        px, py = self.controls_pane[0], self.controls_pane[1]
        x, y, w, h = self.button_rect
        return px + x <= sx <= px + x + w and py + y <= sy <= py + y + h

    def _hit_custom_btn(self, sx: int, sy: int) -> bool:
        if not self.custom_btn_rect:
            return False
        px, py = self.controls_pane[0], self.controls_pane[1]
        x, y, w, h = self.custom_btn_rect
        return px + x <= sx <= px + x + w and py + y <= sy <= py + y + h

    def _hit_options_btn(self, sx: int, sy: int) -> bool:
        if not self.options_btn_rect:
            return False
        px, py = self.controls_pane[0], self.controls_pane[1]
        x, y, w, h = self.options_btn_rect
        return px + x <= sx <= px + x + w and py + y <= sy <= py + y + h

    def _hit_custom_modal(self, sx: int, sy: int) -> bool:
        if not self.custom_modal_visible or not self.custom_actions_rect:
            return False
        x, y, w, h = self.custom_actions_rect
        return x <= sx <= x + w and y <= sy <= y + h

    def _hit_options_modal(self, sx: int, sy: int) -> bool:
        if not self.options_modal_visible or not self.options_rect:
            return False
        x, y, w, h = self.options_rect
        return x <= sx <= x + w and y <= sy <= y + h

    def _pane_rect_abs(self, pane: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        x, y, w, h = pane
        return (x, y, x + w, y + h)

    def _handle_custom_click(self, lparam: int) -> bool:
        if not self.custom_modal_visible or not self.custom_actions_rect:
            return False
        sx = win32api.LOWORD(lparam)
        sy = win32api.HIWORD(lparam)
        x, y, w, h = self.custom_actions_rect
        px, py = x, y
        title_h = 24
        content_x = px + 8
        content_y = py + title_h + 8
        row_h = 26
        # save
        if self._point_in_rect(sx, sy, (px + w - 70, py + h - 32, 60, 24)):
            self._save_custom_actions()
            self.custom_modal_visible = False
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
            return True
        # plus
        plus_rect = (content_x, content_y + len(self.custom_rows) * row_h, 24, row_h - 4)
        if self._point_in_rect(sx, sy, plus_rect):
            self.custom_rows.append({"name": "", "action1": "select", "action2": "select", "count": "1"})
            win32gui.InvalidateRect(self._hwnd, None, True)
            return True
        # rows
        for idx, row in enumerate(self.custom_rows):
            ry = content_y + idx * row_h
            # name
            if self._point_in_rect(sx, sy, (content_x, ry, 140, row_h - 4)):
                self._custom_active_field = ("name", idx)
                self._custom_capture_action = None
                win32gui.SetForegroundWindow(self._hwnd)
                win32gui.SetFocus(self._hwnd)
                return True
            # action1
            if self._point_in_rect(sx, sy, (content_x + 150, ry, 80, row_h - 4)):
                self._custom_capture_action = ("action1", idx)
                self._custom_active_field = None
                win32gui.SetForegroundWindow(self._hwnd)
                win32gui.SetFocus(self._hwnd)
                return True
            # action2
            if self._point_in_rect(sx, sy, (content_x + 260, ry, 80, row_h - 4)):
                self._custom_capture_action = ("action2", idx)
                self._custom_active_field = None
                win32gui.SetForegroundWindow(self._hwnd)
                win32gui.SetFocus(self._hwnd)
                return True
            # count
            if self._point_in_rect(sx, sy, (content_x + 345, ry, 40, row_h - 4)):
                self._custom_active_field = ("count", idx)
                self._custom_capture_action = None
                win32gui.SetForegroundWindow(self._hwnd)
                win32gui.SetFocus(self._hwnd)
                return True
            # delete
            if self._point_in_rect(sx, sy, (content_x + 390, ry, 20, row_h - 4)):
                if 0 <= idx < len(self.custom_rows):
                    self.custom_rows.pop(idx)
                    self._custom_active_field = None
                    self._custom_capture_action = None
                    win32gui.InvalidateRect(self._hwnd, None, True)
                return True
        return False

    def _handle_options_click(self, lparam: int) -> bool:
        if not self.options_modal_visible or not self.options_rect:
            return False
        sx = win32api.LOWORD(lparam)
        sy = win32api.HIWORD(lparam)
        x, y, w, h = self.options_rect
        px, py = x, y
        title_h = 24
        content_x = px + 8
        content_y = py + title_h + 8
        list_y = content_y + 18
        row_h = 24

        # window select
        for idx, winfo in enumerate(self.available_windows):
            ry = list_y + idx * row_h
            rect = (content_x, ry, min(360, w - 16), row_h - 2)
            if self._point_in_rect(sx, sy, rect):
                self.selected_window_hwnd = winfo.hwnd
                if self._hwnd:
                    win32gui.InvalidateRect(self._hwnd, None, True)
                return True

        list_height = max(row_h, len(self.available_windows) * row_h if self.available_windows else row_h)
        panes_y = list_y + list_height + 10
        row_y = panes_y + 20
        pane_rows = [
            ("status", self.status_pane),
            ("actions", self.actions_pane),
            ("controls", self.controls_pane),
        ]
        for idx, (name, pane) in enumerate(pane_rows):
            py_row = row_y + idx * 28
            width_minus = (content_x + 70, py_row, 20, 22)
            width_plus = (content_x + 140, py_row, 20, 22)
            height_minus = (content_x + 180, py_row, 20, 22)
            height_plus = (content_x + 250, py_row, 20, 22)
            if self._point_in_rect(sx, sy, width_minus):
                self._change_pane_size(name, dw=-1, dh=0)
                return True
            if self._point_in_rect(sx, sy, width_plus):
                self._change_pane_size(name, dw=1, dh=0)
                return True
            if self._point_in_rect(sx, sy, height_minus):
                self._change_pane_size(name, dw=0, dh=-1)
                return True
            if self._point_in_rect(sx, sy, height_plus):
                self._change_pane_size(name, dw=0, dh=1)
                return True

        apply_y = row_y + len(pane_rows) * 28 + 10
        apply_rect = (px + w - 80, apply_y, 70, 26)
        cancel_rect = (px + w - 160, apply_y, 70, 26)
        if self._point_in_rect(sx, sy, apply_rect):
            pane_sizes = self._pane_sizes_snapshot()
            self.options_modal_visible = False
            if self.on_apply_options:
                self.on_apply_options(self.selected_window_hwnd, pane_sizes)
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
            return True
        if self._point_in_rect(sx, sy, cancel_rect):
            self._restore_options_backup()
            self.options_modal_visible = False
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
            return True

        return False

    # Helpers
    def _point_in_rect(self, px: int, py: int, rect: Tuple[int, int, int, int]) -> bool:
        x, y, w, h = rect
        return x <= px <= x + w and y <= py <= y + h

    def _pane_sizes_snapshot(self) -> dict:
        return {
            "status": (self.status_pane[2], self.status_pane[3]),
            "actions": (self.actions_pane[2], self.actions_pane[3]),
            "controls": (self.controls_pane[2], self.controls_pane[3]),
        }

    def _change_pane_size(self, pane_name: str, dw: int, dh: int) -> None:
        if pane_name == "status":
            x, y, w, h = self.status_pane
            self.status_pane = self._clamp_pane((x, y, w + dw, h + dh))
        elif pane_name == "actions":
            x, y, w, h = self.actions_pane
            self.actions_pane = self._clamp_pane((x, y, w + dw, h + dh))
        elif pane_name == "controls":
            x, y, w, h = self.controls_pane
            self.controls_pane = self._clamp_pane((x, y, w + dw, h + dh))
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, None, True)
        if self.on_panes_changed:
            self.on_panes_changed(self._pane_sizes_snapshot())

    def _clamp_pane(self, pane: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        x, y, w, h = pane
        w = min(MAX_PANE_W, max(MIN_PANE_W, w))
        h = min(MAX_PANE_H, max(MIN_PANE_H, h))
        max_x = max(0, self.window.width - w)
        max_y = max(0, self.window.height - h)
        x = max(0, min(x, max_x))
        y = max(0, min(y, max_y))
        return (x, y, w, h)

    def _clamp_panes_to_window(self, invalidate: bool = True) -> None:
        self.status_pane = self._clamp_pane(self.status_pane)
        self.actions_pane = self._clamp_pane(self.actions_pane)
        self.controls_pane = self._clamp_pane(self.controls_pane)
        if invalidate and self._hwnd:
            win32gui.InvalidateRect(self._hwnd, None, True)
        self._capture_relative_positions()
        if self.on_panes_changed:
            self.on_panes_changed(self._pane_sizes_snapshot())

    def _sync_to_window(self) -> None:
        """
        Keep overlay aligned to the game window (position + size).
        """
        try:
            rect = win32gui.GetWindowRect(self.window.hwnd)
        except Exception:
            return
        if win32gui.IsIconic(self.window.hwnd):
            self._was_iconic = True
            if self._hwnd and not self._hidden_due_iconic:
                win32gui.ShowWindow(self._hwnd, win32con.SW_HIDE)
                self._hidden_due_iconic = True
            return

        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return

        rect_changed = rect != self.window.rect
        self.window = WindowInfo(hwnd=self.window.hwnd, process_id=self.window.process_id, rect=rect)

        if rect_changed or self._was_iconic or self._hidden_due_iconic:
            if self._hwnd:
                if self._hidden_due_iconic:
                    win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWNOACTIVATE)
                    self._hidden_due_iconic = False
                win32gui.SetWindowPos(
                    self._hwnd,
                    win32con.HWND_TOPMOST,
                    left,
                    top,
                    width,
                    height,
                    win32con.SWP_SHOWWINDOW,
                )
            self._apply_relative_positions()
            self._ensure_custom_rect()
            self._ensure_options_rect()
            self._clamp_panes_to_window(invalidate=False)
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
        self._was_iconic = False

    def _restore_options_backup(self) -> None:
        if self._pane_sizes_backup:
            for name, size in self._pane_sizes_backup.items():
                if not isinstance(size, (tuple, list)) or len(size) != 2:
                    continue
                w, h = size
                if name == "status":
                    x, y, _, _ = self.status_pane
                    self.status_pane = self._clamp_pane((x, y, int(w), int(h)))
                elif name == "actions":
                    x, y, _, _ = self.actions_pane
                    self.actions_pane = self._clamp_pane((x, y, int(w), int(h)))
                elif name == "controls":
                    x, y, _, _ = self.controls_pane
                    self.controls_pane = self._clamp_pane((x, y, int(w), int(h)))
            self._clamp_panes_to_window()
        if self._selected_window_backup is not None:
            self.selected_window_hwnd = self._selected_window_backup
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, None, True)
        self._capture_relative_positions()

    # Positions persistence
    def _load_positions(self) -> None:
        if self._positions_path.exists():
            try:
                data = json.loads(self._positions_path.read_text(encoding="utf-8"))
                sp = data.get("status")
                ap = data.get("actions")
                cp = data.get("controls")
                self._relative_positions = {}
                if sp and len(sp) == 4:
                    self.status_pane = self._pane_from_saved(sp, "status")
                if ap and len(ap) == 4:
                    self.actions_pane = self._pane_from_saved(ap, "actions")
                if cp and len(cp) == 4:
                    self.controls_pane = self._pane_from_saved(cp, "controls")
            except Exception:
                self._relative_positions = {}

    def _save_positions(self) -> None:
        self._capture_relative_positions()
        data = {k: v for k, v in self._relative_positions.items()}
        try:
            self._positions_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_custom_actions(self) -> None:
        path = Path("custom_actions.json")
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.custom_rows = data
            except Exception:
                self.custom_rows = []
        else:
            self.custom_rows = []

    def _save_custom_actions(self) -> None:
        path = Path("custom_actions.json")
        try:
            path.write_text(json.dumps(self.custom_rows, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.on_save_custom:
                self.on_save_custom()
        except Exception:
            pass

    def _ensure_custom_rect(self) -> None:
        if self.custom_actions_rect is None:
            w, h = 480, 220
            x = (self.window.width - w) // 2
            y = (self.window.height - h) // 2
            self.custom_actions_rect = (x, y, w, h)
            return
        x, y, w, h = self.custom_actions_rect
        max_w = min(self.window.width, 600)
        max_h = min(self.window.height, 400)
        w = min(max_w, max(200, w))
        h = min(max_h, max(150, h))
        x = max(0, min(x, self.window.width - w))
        y = max(0, min(y, self.window.height - h))
        self.custom_actions_rect = (x, y, w, h)

    def _ensure_options_rect(self) -> None:
        if self.options_rect is None:
            w, h = 520, 240
            x = (self.window.width - w) // 2
            y = (self.window.height - h) // 2
            self.options_rect = (x, y, w, h)
            return
        x, y, w, h = self.options_rect
        max_w = min(self.window.width, 640)
        max_h = min(self.window.height, 420)
        w = min(max_w, max(300, w))
        h = min(max_h, max(180, h))
        x = max(0, min(x, self.window.width - w))
        y = max(0, min(y, self.window.height - h))
        self.options_rect = (x, y, w, h)

    def _pane_from_saved(self, saved: list, name: str) -> tuple[int, int, int, int]:
        # If values look like relative (<=1), scale to current window
        try:
            if all(isinstance(v, (int, float)) and v <= 1.0 for v in saved):
                rel = tuple(float(v) for v in saved)
                self._relative_positions[name] = rel
                return self._pane_from_relative(rel)
        except Exception:
            pass
        # Fallback: treat as absolute, clamp, and store relative snapshot
        if len(saved) == 4:
            pane = self._clamp_pane(tuple(int(v) for v in saved))
            self._relative_positions[name] = self._pane_to_relative(pane)
            return pane
        return getattr(self, f"{name}_pane")

    def _pane_to_relative(self, pane: Tuple[int, int, int, int]) -> tuple[float, float, float, float]:
        w = max(1, self.window.width)
        h = max(1, self.window.height)
        x, y, pw, ph = pane
        return (x / w, y / h, pw / w, ph / h)

    def _pane_from_relative(self, rel: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        w = max(1, self.window.width)
        h = max(1, self.window.height)
        rx, ry, rw, rh = rel
        return self._clamp_pane((int(rx * w), int(ry * h), int(rw * w), int(rh * h)))

    def _apply_relative_positions(self) -> None:
        for name, rel in self._relative_positions.items():
            if not isinstance(rel, (tuple, list)) or len(rel) != 4:
                continue
            abs_pane = self._pane_from_relative(tuple(float(v) for v in rel))
            if name == "status":
                self.status_pane = abs_pane
            elif name == "actions":
                self.actions_pane = abs_pane
            elif name == "controls":
                self.controls_pane = abs_pane

    def _capture_relative_positions(self) -> None:
        self._relative_positions["status"] = self._pane_to_relative(self.status_pane)
        self._relative_positions["actions"] = self._pane_to_relative(self.actions_pane)
        self._relative_positions["controls"] = self._pane_to_relative(self.controls_pane)

    def set_available_windows(self, windows: List[WindowInfo], current_hwnd: Optional[int] = None) -> None:
        self.available_windows = list(windows)
        if current_hwnd:
            self.selected_window_hwnd = current_hwnd
        elif self.available_windows:
            self.selected_window_hwnd = self.available_windows[0].hwnd
        else:
            self.selected_window_hwnd = None
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, None, True)

    def start_options(self, windows: List[WindowInfo], current_hwnd: Optional[int]) -> None:
        self._pane_sizes_backup = self._pane_sizes_snapshot()
        self._selected_window_backup = current_hwnd
        self.set_available_windows(windows, current_hwnd=current_hwnd)
        self.options_modal_visible = True
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, None, True)

    def apply_pane_sizes(self, pane_sizes: dict) -> None:
        for name, size in pane_sizes.items():
            if not isinstance(size, (tuple, list)) or len(size) != 2:
                continue
            w, h = size
            if name == "status":
                x, y, _, _ = self.status_pane
                self.status_pane = self._clamp_pane((x, y, int(w), int(h)))
            elif name == "actions":
                x, y, _, _ = self.actions_pane
                self.actions_pane = self._clamp_pane((x, y, int(w), int(h)))
            elif name == "controls":
                x, y, _, _ = self.controls_pane
                self.controls_pane = self._clamp_pane((x, y, int(w), int(h)))
        self._clamp_panes_to_window()
        self._save_positions()
        if self.on_panes_changed:
            self.on_panes_changed(self._pane_sizes_snapshot())

    def update_window(self, window: WindowInfo) -> None:
        self.window = window
        if self._hwnd:
            left, top, right, bottom = window.rect
            width, height = right - left, bottom - top
            win32gui.SetWindowPos(self._hwnd, win32con.HWND_TOPMOST, left, top, width, height, win32con.SWP_SHOWWINDOW)
        self._apply_relative_positions()
        self._ensure_custom_rect()
        self._ensure_options_rect()
        self._clamp_panes_to_window()
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, None, True)
        if self.on_panes_changed:
            self.on_panes_changed(self._pane_sizes_snapshot())
