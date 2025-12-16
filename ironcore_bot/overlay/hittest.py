from __future__ import annotations

from typing import Optional, Tuple

import win32api
import win32gui


class OverlayHitTestMixin:
    def _hit_titlebar(self, sx: int, sy: int) -> bool:
        return self._which_titlebar(sx, sy) is not None

    def _which_titlebar(self, sx: int, sy: int) -> Optional[str]:
        for name, pane in (
            ("status", self.status_pane),
            ("actions", self.actions_pane),
            ("skills", self.skills_pane),
            ("controls", self.controls_pane),
        ):
            if name == "status" and not self.show_exp:
                continue
            if name == "actions" and not self.show_timers:
                continue
            if name == "skills" and not self.show_skills:
                continue
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

    def _inflate_rect(self, rect: Tuple[int, int, int, int], padding: int = 10) -> Tuple[int, int, int, int]:
        x, y, w, h = rect
        return (x - padding, y - padding, w + 2 * padding, h + 2 * padding)

    def _hit_skills_ui(self, sx: int, sy: int) -> bool:
        if not self.show_skills:
            return False
        px, py, pw, ph = self.skills_pane
        if not (px <= sx <= px + pw and py <= sy <= py + ph):
            return False
        title_h = 20
        local_x = sx - px
        local_y = sy - py - title_h
        pane_width = pw
        layout = self._skills_ui_layout(pane_width)
        targets = [
            layout.get("skill_select"),
            layout.get("shield_1"),
            layout.get("shield_2"),
            layout.get("afk_toggle"),
        ]
        for rect in targets:
            if not rect:
                continue
            if self._point_in_rect(local_x, local_y, self._inflate_rect(rect)):
                return True
        return False

    def _hit_status_reset(self, sx: int, sy: int) -> bool:
        if not self.show_exp or not self.status_reset_rect:
            return False
        px, py = self.status_pane[0], self.status_pane[1]
        x, y, w, h = self.status_reset_rect
        return px + x <= sx <= px + x + w and py + y <= sy <= py + y + h

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
        if self._point_in_rect(sx, sy, (px + w - 70, py + h - 32, 60, 24)):
            self._save_custom_actions()
            self.custom_modal_visible = False
            self._end_modal_drag()
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
            return True
        plus_rect = (content_x, content_y + len(self.custom_rows) * row_h, 24, row_h - 4)
        if self._point_in_rect(sx, sy, plus_rect):
            self.custom_rows.append({"name": "", "action1": "select", "action2": "select", "count": "1"})
            win32gui.InvalidateRect(self._hwnd, None, True)
            return True
        for idx, row in enumerate(self.custom_rows):
            ry = content_y + idx * row_h
            if self._point_in_rect(sx, sy, (content_x, ry, 140, row_h - 4)):
                self._custom_active_field = ("name", idx)
                self._custom_capture_action = None
                win32gui.SetForegroundWindow(self._hwnd)
                win32gui.SetFocus(self._hwnd)
                return True
            if self._point_in_rect(sx, sy, (content_x + 150, ry, 80, row_h - 4)):
                self._custom_capture_action = ("action1", idx)
                self._custom_active_field = None
                win32gui.SetForegroundWindow(self._hwnd)
                win32gui.SetFocus(self._hwnd)
                return True
            if self._point_in_rect(sx, sy, (content_x + 260, ry, 80, row_h - 4)):
                self._custom_capture_action = ("action2", idx)
                self._custom_active_field = None
                win32gui.SetForegroundWindow(self._hwnd)
                win32gui.SetFocus(self._hwnd)
                return True
            if self._point_in_rect(sx, sy, (content_x + 345, ry, 40, row_h - 4)):
                self._custom_active_field = ("count", idx)
                self._custom_capture_action = None
                win32gui.SetForegroundWindow(self._hwnd)
                win32gui.SetFocus(self._hwnd)
                return True
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
        self._fit_options_rect_to_content()
        sx = win32api.LOWORD(lparam)
        sy = win32api.HIWORD(lparam)
        x, y, w, h = self.options_rect
        px, py = x, y
        title_h = 24
        content_x = px + 8
        content_y = py + title_h + 8
        list_y = content_y + 18
        row_h = 24
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
        # Sound test only
        test_rect = (content_x, panes_y, 120, row_h)
        if self._point_in_rect(sx, sy, test_rect):
            if getattr(self, "on_test_afk_sound", None):
                self.on_test_afk_sound()
            return True
        panels_y = panes_y + row_h + 10
        vis_opts = self._options_visible_defs()
        vis_y = panels_y + 20
        for idx, (label, key, enabled) in enumerate(vis_opts):
            ry = vis_y + idx * (row_h + 2)
            rect = (content_x, ry, 180, row_h)
            if self._point_in_rect(sx, sy, rect):
                if key == "status":
                    self.show_exp = not self.show_exp
                    self._layout_status_reset_button()
                elif key == "actions":
                    self.show_timers = not self.show_timers
                elif key == "skills":
                    self.show_skills = not self.show_skills
                if self._hwnd:
                    win32gui.InvalidateRect(self._hwnd, None, True)
                return True

        panes_y = vis_y + len(vis_opts) * (row_h + 2) + 12
        row_y = panes_y + 20
        pane_rows = [(name, pane) for name, _, pane in self._options_panel_defs()]
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
            self._end_modal_drag()
            if self.on_apply_options:
                self.on_apply_options(
                    self.selected_window_hwnd, pane_sizes, self.selected_melee, self.selected_shield_mode
                )
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
            return True
        if self._point_in_rect(sx, sy, cancel_rect):
            self._restore_options_backup()
            self.options_modal_visible = False
            self._end_modal_drag()
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
            return True

        return False

    def _point_in_rect(self, px: int, py: int, rect: Tuple[int, int, int, int]) -> bool:
        x, y, w, h = rect
        return x <= px <= x + w and y <= py <= y + h

    def _inflate_rect(self, rect: Tuple[int, int, int, int], padding: int = 10) -> Tuple[int, int, int, int]:
        x, y, w, h = rect
        return (x - padding, y - padding, w + 2 * padding, h + 2 * padding)

    def _handle_skills_panel_click(self, sx: int, sy: int) -> bool:
        if not self.show_skills:
            return False
        pane = self.skills_pane
        px, py, pw, ph = pane
        if not (px <= sx <= px + pw and py <= sy <= py + ph):
            return False
        layout = self._skills_ui_layout(pw)
        sel_rect = layout.get("skill_select", (58, 24, 140, 18))
        shield1 = layout.get("shield_1", (8, 48, 90, 18))
        shield2 = layout.get("shield_2", (108, 48, 90, 18))
        afk = layout.get("afk_toggle", (8, 72, 120, 18))
        local_x = sx - px
        title_h = 20
        local_y = sy - py - title_h
        if self._point_in_rect(local_x, local_y, self._inflate_rect(sel_rect, padding=4)):
            options = self._options_skill_names()
            try:
                idx = options.index(self.selected_melee)
            except ValueError:
                idx = -1
            next_val = options[(idx + 1) % len(options)] if options else self.selected_melee
            self.selected_melee = next_val
            try:
                self._save_positions()
            except Exception:
                pass
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
            return True
        if self._point_in_rect(local_x, local_y, self._inflate_rect(shield1, padding=6)):
            self.selected_shield_mode = 1
            try:
                self._save_positions()
            except Exception:
                pass
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
            return True
        if self._point_in_rect(local_x, local_y, self._inflate_rect(shield2, padding=6)):
            self.selected_shield_mode = 2
            try:
                self._save_positions()
            except Exception:
                pass
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
            return True
        if self._point_in_rect(local_x, local_y, self._inflate_rect(afk, padding=6)):
            self.afk_alert_enabled = not self.afk_alert_enabled
            try:
                self._save_positions()
            except Exception:
                pass
            if self._hwnd:
                win32gui.InvalidateRect(self._hwnd, None, True)
            return True
        return False
