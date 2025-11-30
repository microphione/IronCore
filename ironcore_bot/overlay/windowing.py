from __future__ import annotations

import ctypes

import win32api
import win32con
import win32gui


class OverlayWindowMixin:
    def _hit_modal_title(self, rect: tuple[int, int, int, int] | None, sx: int, sy: int, title_h: int = 24) -> bool:
        if not rect:
            return False
        x, y, w, h = rect
        return x <= sx <= x + w and y <= sy <= y + title_h

    def _start_modal_drag(self, kind: str, sx: int, sy: int) -> bool:
        rect = self.custom_actions_rect if kind == "custom" else self.options_rect
        if not rect:
            return False
        x, y, w, h = rect
        self._modal_dragging = (kind, sx - x, sy - y)
        return True

    def _update_modal_drag(self, sx: int, sy: int) -> None:
        if not self._modal_dragging:
            return
        kind, dx, dy = self._modal_dragging
        rect = self.custom_actions_rect if kind == "custom" else self.options_rect
        if not rect:
            self._modal_dragging = None
            return
        x, y, w, h = rect
        new_x = max(0, min(self.window.width - w, sx - dx))
        new_y = max(0, min(self.window.height - h, sy - dy))
        if kind == "custom":
            self.custom_actions_rect = (new_x, new_y, w, h)
        else:
            self.options_rect = (new_x, new_y, w, h)
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, None, True)

    def _end_modal_drag(self) -> None:
        self._modal_dragging = None

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

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int):
        if msg == win32con.WM_NCHITTEST:
            sx = win32api.LOWORD(lparam)
            sy = win32api.HIWORD(lparam)
            cx = sx - self.window.rect[0]
            cy = sy - self.window.rect[1]
            if self._hit_custom_modal(cx, cy) or self._hit_options_modal(cx, cy):
                return win32con.HTCLIENT
            if self._hit_skills_ui(cx, cy):
                return win32con.HTCLIENT
            if self._hit_button(cx, cy) or self._hit_custom_btn(cx, cy) or self._hit_options_btn(cx, cy):
                return win32con.HTCLIENT
            if self._hit_titlebar(cx, cy):
                return win32con.HTCLIENT
            return win32con.HTTRANSPARENT
        if msg == win32con.WM_LBUTTONDOWN:
            x = win32api.LOWORD(lparam)
            y = win32api.HIWORD(lparam)
            if self.custom_modal_visible and self._hit_modal_title(self.custom_actions_rect, x, y):
                if self._start_modal_drag("custom", x, y):
                    return 0
            if self.options_modal_visible and self._hit_modal_title(self.options_rect, x, y):
                if self._start_modal_drag("options", x, y):
                    return 0
            if self.custom_modal_visible and self._handle_custom_click(lparam):
                return 0
            if self.options_modal_visible and self._handle_options_click(lparam):
                return 0
            if self._handle_skills_panel_click(x, y):
                return 0
            if self._hit_status_reset(x, y):
                if self.on_status_reset_click:
                    self.on_status_reset_click()
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
                elif pane == "skills":
                    px, py, w, h = self.skills_pane
                else:
                    px, py, w, h = self.controls_pane
                self._dragging = (pane, x - px, y - py)
                return 0
        if msg == win32con.WM_WINDOWPOSCHANGED or msg == win32con.WM_MOVE or msg == win32con.WM_SIZE:
            self._sync_to_window()
        if msg == win32con.WM_LBUTTONUP:
            if self._modal_dragging:
                self._end_modal_drag()
            if self._dragging:
                self._save_positions()
            self._dragging = None
        if msg == win32con.WM_MOUSEMOVE and self._dragging:
            pane, dx, dy = self._dragging
            x = win32api.LOWORD(lparam)
            y = win32api.HIWORD(lparam)
            if self._modal_dragging:
                self._update_modal_drag(x, y)
                return 0
            if pane == "status":
                _, _, w, h = self.status_pane
                self.status_pane = (x - dx, y - dy, w, h)
            elif pane == "actions":
                _, _, w, h = self.actions_pane
                self.actions_pane = (x - dx, y - dy, w, h)
            elif pane == "skills":
                _, _, w, h = self.skills_pane
                self.skills_pane = (x - dx, y - dy, w, h)
            elif pane == "controls":
                _, _, w, h = self.controls_pane
                self.controls_pane = (x - dx, y - dy, w, h)
            win32gui.InvalidateRect(hwnd, None, True)
            return 0
        if msg == win32con.WM_MOUSEMOVE and self._modal_dragging:
            x = win32api.LOWORD(lparam)
            y = win32api.HIWORD(lparam)
            self._update_modal_drag(x, y)
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
