"""
Transparent overlay with two draggable panes (status + actions).
Only the title bars/buttons are hit-testable; rest is click-through.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Tuple

import json
import win32api
import win32con
import win32gui

from .client_window import WindowInfo
from pathlib import Path


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
    _colorkey = win32api.RGB(255, 0, 255)
    custom_actions_rect: Optional[Tuple[int, int, int, int]] = None
    custom_rows: List[dict] = field(default_factory=list)
    _custom_active_field: Optional[Tuple[str, int]] = None  # ("name"/"count", idx)
    _custom_capture_action: Optional[Tuple[str, int]] = None  # ("action1"/"action2", idx)

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
        self._ensure_custom_rect()
        self._load_custom_actions()

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

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int):
        if msg == win32con.WM_NCHITTEST:
            x = win32api.LOWORD(lparam)
            y = win32api.HIWORD(lparam)
            if self._hit_titlebar(x, y) or self._hit_custom_modal(x, y):
                return win32con.HTCLIENT
            if self._hit_button(x, y) or self._hit_custom_btn(x, y):
                return win32con.HTCLIENT
            return win32con.HTTRANSPARENT
        if msg == win32con.WM_LBUTTONDOWN:
            x = win32api.LOWORD(lparam) + self.window.rect[0]
            y = win32api.HIWORD(lparam) + self.window.rect[1]
            pane = self._which_titlebar(x, y)
            if pane:
                if pane == "status":
                    px, py, w, h = self.status_pane
                elif pane == "actions":
                    px, py, w, h = self.actions_pane
                else:
                    px, py, w, h = self.controls_pane
                self._dragging = (pane, x - px - self.window.rect[0], y - py - self.window.rect[1])
                return 0
            if self._hit_button(x, y):
                if self.on_button_click:
                    self.on_button_click()
                return 0
            if self._hit_custom_btn(x, y):
                if self.on_custom_click:
                    self.on_custom_click()
                return 0
            if self.custom_modal_visible and self._handle_custom_click(lparam):
                return 0
        if msg == win32con.WM_LBUTTONUP:
            if self._dragging:
                self._save_positions()
            self._dragging = None
        if msg == win32con.WM_MOUSEMOVE and self._dragging:
            pane, dx, dy = self._dragging
            x = win32api.LOWORD(lparam) + self.window.rect[0]
            y = win32api.HIWORD(lparam) + self.window.rect[1]
            if pane == "status":
                _, _, w, h = self.status_pane
                self.status_pane = (x - dx - self.window.rect[0], y - dy - self.window.rect[1], w, h)
            elif pane == "actions":
                _, _, w, h = self.actions_pane
                self.actions_pane = (x - dx - self.window.rect[0], y - dy - self.window.rect[1], w, h)
            elif pane == "controls":
                _, _, w, h = self.controls_pane
                self.controls_pane = (x - dx - self.window.rect[0], y - dy - self.window.rect[1], w, h)
            win32gui.InvalidateRect(hwnd, None, True)
            return 0
        if msg == win32con.WM_PAINT:
            self._on_paint(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
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
        left, top = self.window.rect[0], self.window.rect[1]
        px, py = x + left, y + top
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
            pane_offset_x = x + left
            pane_offset_y = y + top
            if self.button_rect:
                bx, by, bw, bh = self.button_rect
                self._draw_button_rect(hdc, pane_offset_x + bx, pane_offset_y + by, bw, bh, self.button_label)
            if self.custom_btn_rect:
                cx, cy, cw, ch = self.custom_btn_rect
                self._draw_button_rect(hdc, pane_offset_x + cx, pane_offset_y + cy, cw, ch, "Custom")

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
        left, top = self.window.rect[0], self.window.rect[1]
        px, py = x + left, y + top
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

    # Hit testing helpers
    def _hit_titlebar(self, sx: int, sy: int) -> bool:
        return self._which_titlebar(sx, sy) is not None

    def _which_titlebar(self, sx: int, sy: int) -> Optional[str]:
        left, top = self.window.rect[0], self.window.rect[1]
        for name, pane in (("status", self.status_pane), ("actions", self.actions_pane), ("controls", self.controls_pane)):
            x, y, w, h = pane
            px, py = x + left, y + top
            title_h = 20
            if px <= sx <= px + w and py <= sy <= py + title_h:
                return name
        return None

    def _hit_button(self, sx: int, sy: int) -> bool:
        if not self.button_rect:
            return False
        left, top = self.window.rect[0], self.window.rect[1]
        px, py = self.controls_pane[0] + left, self.controls_pane[1] + top
        x, y, w, h = self.button_rect
        return px + x <= sx <= px + x + w and py + y <= sy <= py + y + h

    def _hit_custom_btn(self, sx: int, sy: int) -> bool:
        if not self.custom_btn_rect:
            return False
        left, top = self.window.rect[0], self.window.rect[1]
        px, py = self.controls_pane[0] + left, self.controls_pane[1] + top
        x, y, w, h = self.custom_btn_rect
        return px + x <= sx <= px + x + w and py + y <= sy <= py + y + h

    def _hit_custom_modal(self, sx: int, sy: int) -> bool:
        if not self.custom_modal_visible or not self.custom_actions_rect:
            return False
        left, top = self.window.rect[0], self.window.rect[1]
        x, y, w, h = self.custom_actions_rect
        return left + x <= sx <= left + x + w and top + y <= sy <= top + y + h

    def _pane_rect_abs(self, pane: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        x, y, w, h = pane
        left, top = self.window.rect[0], self.window.rect[1]
        return (left + x, top + y, left + x + w, top + y + h)

    def _handle_custom_click(self, lparam: int) -> bool:
        if not self.custom_modal_visible or not self.custom_actions_rect:
            return False
        left, top = self.window.rect[0], self.window.rect[1]
        sx = win32api.LOWORD(lparam) + left
        sy = win32api.HIWORD(lparam) + top
        x, y, w, h = self.custom_actions_rect
        px, py = x + left, y + top
        title_h = 24
        content_x = px + 8
        content_y = py + title_h + 8
        row_h = 26
        # save
        if self._point_in_rect(sx, sy, (px + w - 70, py + h - 32, 60, 24)):
            self._save_custom_actions()
            self.custom_modal_visible = False
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

    # Helpers
    def _point_in_rect(self, px: int, py: int, rect: Tuple[int, int, int, int]) -> bool:
        x, y, w, h = rect
        return x <= px <= x + w and y <= py <= y + h

    # Positions persistence
    def _load_positions(self) -> None:
        if self._positions_path.exists():
            try:
                data = json.loads(self._positions_path.read_text(encoding="utf-8"))
                sp = data.get("status")
                ap = data.get("actions")
                cp = data.get("controls")
                if sp and len(sp) == 4:
                    self.status_pane = tuple(sp)  # type: ignore
                if ap and len(ap) == 4:
                    self.actions_pane = tuple(ap)  # type: ignore
                if cp and len(cp) == 4:
                    self.controls_pane = tuple(cp)  # type: ignore
            except Exception:
                pass

    def _save_positions(self) -> None:
        data = {
            "status": list(self.status_pane),
            "actions": list(self.actions_pane),
            "controls": list(self.controls_pane),
        }
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
