from __future__ import annotations

from typing import List, Optional, Tuple

import win32con
import win32gui

from ..client_window import WindowInfo
from .constants import MAX_PANE_H, MAX_PANE_W, MIN_PANE_H, MIN_PANE_W


class OverlayLayoutMixin:
    def _layout_status_reset_button(self) -> None:
        if not getattr(self, "on_status_reset_click", None):
            self.status_reset_rect = None
            return
        _, _, pane_w, pane_h = self.status_pane
        title_h = 20
        padding_x = 8
        line_height = 16
        lines = getattr(self, "status_lines", [])
        text_lines = max(1, len(lines))
        content_h = text_lines * line_height
        desired_y = title_h + 4 + content_h + 24
        btn_w, btn_h = 80, 22
        btn_y = min(pane_h - btn_h - 6, desired_y)
        btn_y = max(title_h + 4, btn_y)
        self.status_reset_rect = (padding_x, btn_y, btn_w, btn_h)

    def _options_panel_defs(self) -> list[tuple[str, str, Tuple[int, int, int, int]]]:
        return [
            ("status", "Exp Analyzer", self.status_pane),
            ("actions", "Timers", self.actions_pane),
            ("skills", "Skills", self.skills_pane),
            ("controls", "Actions", self.controls_pane),
        ]

    def _options_visible_defs(self) -> list[tuple[str, str, bool]]:
        return [
            ("Exp Analyzer", "status", self.show_exp),
            ("Timers", "actions", self.show_timers),
            ("Skills", "skills", self.show_skills),
        ]

    def _options_skill_names(self) -> list[str]:
        return ["Fist", "Club", "Sword", "Axe", "Distance"]

    def _skills_ui_layout(self, pane_width: int) -> dict:
        """
        Return relative rects for skill selector, shield mode toggles, afk toggle and content offset.
        Rects are relative to the top-left of the skills pane.
        """
        start_y = 6
        row_h = 22
        padding_x = 6
        selector_w = max(120, min(pane_width - 2 * padding_x, 200))
        selector_x = max(padding_x, (pane_width - selector_w) // 2)
        rows = {}
        # Skill selector row
        rows["skill_select"] = (selector_x, start_y, selector_w, row_h)
        # Shield mode checkboxes (two columns)
        shield_y = start_y + row_h + 6
        col_w = max(100, (pane_width - 2 * padding_x - 6) // 2)
        rows["shield_1"] = (padding_x, shield_y, col_w, row_h)
        rows["shield_2"] = (padding_x + col_w + 6, shield_y, col_w, row_h)
        # AFK toggle
        afk_y = shield_y + row_h + 6
        rows["afk_toggle"] = (padding_x, afk_y, max(120, pane_width - 2 * padding_x), row_h)
        rows["content_offset"] = afk_y + row_h + 10
        return rows

    def _pane_sizes_snapshot(self) -> dict:
        return {
            "status": (self.status_pane[2], self.status_pane[3]),
            "actions": (self.actions_pane[2], self.actions_pane[3]),
            "skills": (self.skills_pane[2], self.skills_pane[3]),
            "controls": (self.controls_pane[2], self.controls_pane[3]),
        }

    def _change_pane_size(self, pane_name: str, dw: int, dh: int) -> None:
        if pane_name == "status":
            x, y, w, h = self.status_pane
            self.status_pane = self._clamp_pane((x, y, w + dw, h + dh))
        elif pane_name == "actions":
            x, y, w, h = self.actions_pane
            self.actions_pane = self._clamp_pane((x, y, w + dw, h + dh))
        elif pane_name == "skills":
            x, y, w, h = self.skills_pane
            self.skills_pane = self._clamp_pane((x, y, w + dw, h + dh))
        elif pane_name == "controls":
            x, y, w, h = self.controls_pane
            self.controls_pane = self._clamp_pane((x, y, w + dw, h + dh))
        self._layout_status_reset_button()
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
        self.skills_pane = self._clamp_pane(self.skills_pane)
        self.controls_pane = self._clamp_pane(self.controls_pane)
        self._layout_status_reset_button()
        if invalidate and self._hwnd:
            win32gui.InvalidateRect(self._hwnd, None, True)
        self._capture_relative_positions()
        if self.on_panes_changed:
            self.on_panes_changed(self._pane_sizes_snapshot())

    def _options_required_height(self, row_h: int = 24) -> int:
        title_h = 24
        content_y = title_h + 8
        windows_rows = max(1, len(self.available_windows) if self.available_windows else 1)
        list_height = windows_rows * row_h
        list_y = content_y + 18
        # only AFK test button now
        extra_block = row_h + 6
        panels_y = list_y + list_height + 10 + extra_block
        vis_opts = self._options_visible_defs()
        vis_block = 20 + len(vis_opts) * (row_h + 2) + 12
        panes_y = panels_y + vis_block
        pane_rows = self._options_panel_defs()
        row_y = panes_y + 20
        apply_y = row_y + len(pane_rows) * 28 + 10
        bottom = apply_y + 26 + 12
        return bottom

    def _fit_options_rect_to_content(self) -> None:
        if not self.options_rect:
            return
        x, y, w, h = self.options_rect
        available_w = max(200, self.window.width - 10)
        available_h = max(200, self.window.height - 10)
        min_w = min(max(360, int(self.window.width * 0.5)), available_w)
        min_h = min(max(220, int(self.window.height * 0.4)), available_h)
        required_h = self._options_required_height()
        w = max(min_w, min(w, available_w))
        h = max(min_h, min(max(required_h, h), available_h))
        x = max(0, min(x, self.window.width - w))
        y = max(0, min(y, self.window.height - h))
        self.options_rect = (x, y, w, h)

    def _sync_to_window(self) -> None:
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
            self._fit_options_rect_to_content()
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
                elif name == "skills":
                    x, y, _, _ = self.skills_pane
                    self.skills_pane = self._clamp_pane((x, y, int(w), int(h)))
                elif name == "controls":
                    x, y, _, _ = self.controls_pane
                    self.controls_pane = self._clamp_pane((x, y, int(w), int(h)))
            self._clamp_panes_to_window()
        if self._selected_window_backup is not None:
            self.selected_window_hwnd = self._selected_window_backup
        if self._selected_melee_backup is not None:
            self.selected_melee = self._selected_melee_backup
        if self._selected_shield_mode_backup is not None:
            self.selected_shield_mode = self._selected_shield_mode_backup
        if getattr(self, "_afk_alert_backup", None) is not None:
            self.afk_alert_enabled = self._afk_alert_backup
        if getattr(self, "_afk_volume_backup", None) is not None:
            self.afk_alert_volume = self._afk_volume_backup
        if self._show_backup:
            self.show_exp = self._show_backup.get("status", True)
            self.show_timers = self._show_backup.get("actions", True)
            self.show_skills = self._show_backup.get("skills", True)
        if self._hwnd:
            win32gui.InvalidateRect(self._hwnd, None, True)
        self._capture_relative_positions()

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
        self._selected_melee_backup = self.selected_melee
        self._selected_shield_mode_backup = getattr(self, "selected_shield_mode", None)
        self._afk_alert_backup = getattr(self, "afk_alert_enabled", None)
        self._afk_volume_backup = getattr(self, "afk_alert_volume", None)
        self._show_backup = {"status": self.show_exp, "actions": self.show_timers, "skills": self.show_skills}
        self.options_rect = None
        self.set_available_windows(windows, current_hwnd=current_hwnd)
        self._ensure_options_rect()
        self.options_modal_visible = True
        self._fit_options_rect_to_content()
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
            elif name == "skills":
                x, y, _, _ = self.skills_pane
                self.skills_pane = self._clamp_pane((x, y, int(w), int(h)))
            elif name == "controls":
                x, y, _, _ = self.controls_pane
                self.controls_pane = self._clamp_pane((x, y, int(w), int(h)))
        self._layout_status_reset_button()
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
