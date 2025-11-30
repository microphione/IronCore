from __future__ import annotations

from typing import List, Tuple

import win32api
import win32con
import win32gui

from .panel import Panel


class OverlayDrawingMixin:
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
        if self.show_exp:
            self._draw_pane(
                hdc,
                self.status_pane,
                self.status_lines,
                include_buttons=bool(self.status_reset_rect),
                title="Exp Analyzer",
                pane_key="status",
            )
        if self.show_timers:
            self._draw_pane(hdc, self.actions_pane, self.actions_lines, include_buttons=False, title="Timers")
        if self.show_skills:
            self._draw_pane(
                hdc,
                self.skills_pane,
                self.skills_lines,
                include_buttons=False,
                title="Skills",
                pane_key="skills",
                draw_extra=self._draw_skills_ui,
            )
        self._draw_pane(hdc, self.controls_pane, [], include_buttons=True, title="Actions", pane_key="controls")

    def _draw_pane(
        self,
        hdc: int,
        pane: Tuple[int, int, int, int],
        lines: List[str],
        include_buttons: bool,
        title: str = "",
        pane_key: str = "",
        draw_extra=None,
    ) -> None:
        x, y, w, h = pane
        px, py = x, y
        title_h = 20
        content_offset = 0
        bar_brush = win32gui.CreateSolidBrush(win32api.RGB(50, 50, 50))
        bar_pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(120, 120, 120))
        old_pen = win32gui.SelectObject(hdc, bar_pen)
        old_brush = win32gui.SelectObject(hdc, bar_brush)
        try:
            win32gui.Rectangle(hdc, px, py, px + w, py + title_h)
            if title:
                win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
                win32gui.SetTextColor(hdc, win32api.RGB(220, 220, 220))
                win32gui.DrawText(
                    hdc,
                    title,
                    -1,
                    (px + 6, py + 2, px + w - 6, py + title_h),
                    win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
                )
        finally:
            win32gui.SelectObject(hdc, old_pen)
            win32gui.SelectObject(hdc, old_brush)
            win32gui.DeleteObject(bar_pen)
            win32gui.DeleteObject(bar_brush)

        if draw_extra:
            content_offset = draw_extra(hdc, px, py + title_h, w)

        if lines:
            text = "\n".join(lines)
            rect = (px + 6, py + title_h + 4 + content_offset, px + w - 6, py + h - 6)
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
            if pane_key == "controls":
                if self.button_rect:
                    bx, by, bw, bh = self.button_rect
                    self._draw_button_rect(hdc, pane_offset_x + bx, pane_offset_y + by, bw, bh, self.button_label)
                if self.custom_btn_rect:
                    cx, cy, cw, ch = self.custom_btn_rect
                    self._draw_button_rect(hdc, pane_offset_x + cx, pane_offset_y + cy, cw, ch, "Custom")
                if self.options_btn_rect:
                    ox, oy, ow, oh = self.options_btn_rect
                    self._draw_button_rect(hdc, pane_offset_x + ox, pane_offset_y + oy, ow, oh, "Options")
            elif pane_key == "status" and self.status_reset_rect:
                bx, by, bw, bh = self.status_reset_rect
                self._draw_button_rect(hdc, pane_offset_x + bx, pane_offset_y + by, bw, bh, self.status_reset_label)
        return content_offset

    def _draw_checkbox(self, hdc: int, rect: Tuple[int, int, int, int], label: str, checked: bool) -> None:
        x, y, w, h = rect
        box_size = min(14, h - 4)
        box_rect = (x, y + (h - box_size) // 2, x + box_size, y + (h + box_size) // 2)
        pen = win32gui.CreatePen(win32con.PS_SOLID, 1, win32api.RGB(180, 180, 180))
        brush = win32gui.GetStockObject(win32con.NULL_BRUSH)
        old_pen = win32gui.SelectObject(hdc, pen)
        old_brush = win32gui.SelectObject(hdc, brush)
        try:
            win32gui.Rectangle(hdc, *box_rect)
            if checked:
                fill = win32gui.CreateSolidBrush(win32api.RGB(80, 180, 80))
                win32gui.FillRect(hdc, box_rect, fill)
                win32gui.DeleteObject(fill)
        finally:
            win32gui.SelectObject(hdc, old_pen)
            win32gui.SelectObject(hdc, old_brush)
            win32gui.DeleteObject(pen)
        win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
        win32gui.SetTextColor(hdc, win32api.RGB(220, 220, 220))
        win32gui.DrawText(
            hdc,
            label,
            -1,
            (x + box_size + 6, y, x + w, y + h),
            win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
        )

    def _draw_skills_ui(self, hdc: int, px: int, py: int, w: int) -> int:
        layout = self._skills_ui_layout(w)
        start_y = py
        sel_rect = layout.get("skill_select", (58, 24, 140, 18))
        sx, sy, sw, sh = sel_rect
        selector_x = px + sx
        selector_y = start_y + sy
        self._draw_button_rect(
            hdc,
            selector_x,
            selector_y,
            sw,
            sh,
            self.selected_melee,
        )
        s1 = layout.get("shield_1", (8, 48, 90, 18))
        s2 = layout.get("shield_2", (108, 48, 90, 18))
        self._draw_checkbox(
            hdc, (px + s1[0], start_y + s1[1], s1[2], s1[3]), "1 mob", self.selected_shield_mode == 1
        )
        self._draw_checkbox(
            hdc, (px + s2[0], start_y + s2[1], s2[2], s2[3]), "2 mobs", self.selected_shield_mode == 2
        )
        afk = layout.get("afk_toggle", (8, 72, 120, 18))
        self._draw_checkbox(
            hdc, (px + afk[0], start_y + afk[1], afk[2], afk[3]), "AFK alert", self.afk_alert_enabled
        )
        return layout.get("content_offset", 0)

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
        self._fit_options_rect_to_content()
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

            skill_y = list_y + list_height + 10
            # Sound test
            test_y = skill_y
            self._draw_button_rect(hdc, content_x, test_y, 120, row_h, "SOUND TEST")

            panels_y = test_y + row_h + 10
            win32gui.DrawText(
                hdc,
                "Visible panels:",
                -1,
                (content_x, panels_y, px + w - 16, panels_y + 18),
                win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
            )
            vis_opts = self._options_visible_defs()
            vis_y = panels_y + 20
            for idx, (label, _, enabled) in enumerate(vis_opts):
                ry = vis_y + idx * (row_h + 2)
                self._draw_checkbox(hdc, (content_x, ry, 180, row_h), label, enabled)

            panes_y = vis_y + len(vis_opts) * (row_h + 2) + 12
            win32gui.DrawText(
                hdc,
                "Panel sizes (w/h):",
                -1,
                (content_x, panes_y, px + w - 16, panes_y + 18),
                win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
            )
            row_y = panes_y + 20
            pane_rows = self._options_panel_defs()
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
                self._draw_button_rect(hdc, content_x + 70, py_row, 20, 22, "-")
                self._draw_input(hdc, content_x + 92, py_row, 46, 22, w_val, active=False)
                self._draw_button_rect(hdc, content_x + 140, py_row, 20, 22, "+")
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
