from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple


class OverlayPersistenceMixin:
    def _load_positions(self) -> None:
        if self._positions_path.exists():
            try:
                data = json.loads(self._positions_path.read_text(encoding="utf-8"))
                sp = data.get("status")
                ap = data.get("actions")
                cp = data.get("controls")
                sk = data.get("skills")
                melee = data.get("selected_melee")
                shield_mode = data.get("selected_shield_mode")
                afk_enabled = data.get("afk_alert_enabled")
                afk_volume = data.get("afk_alert_volume")
                show_exp = data.get("show_exp")
                show_timers = data.get("show_timers")
                show_skills = data.get("show_skills")
                self._relative_positions = {}
                if sp and len(sp) == 4:
                    self.status_pane = self._pane_from_saved(sp, "status")
                if ap and len(ap) == 4:
                    self.actions_pane = self._pane_from_saved(ap, "actions")
                if cp and len(cp) == 4:
                    self.controls_pane = self._pane_from_saved(cp, "controls")
                if sk and len(sk) == 4:
                    self.skills_pane = self._pane_from_saved(sk, "skills")
                if isinstance(melee, str):
                    self.selected_melee = melee
                if isinstance(shield_mode, int) and shield_mode in (1, 2):
                    self.selected_shield_mode = shield_mode
                if isinstance(afk_enabled, bool):
                    self.afk_alert_enabled = afk_enabled
                if isinstance(afk_volume, int):
                    self.afk_alert_volume = max(0, min(100, afk_volume))
                if isinstance(show_exp, bool):
                    self.show_exp = show_exp
                if isinstance(show_timers, bool):
                    self.show_timers = show_timers
                if isinstance(show_skills, bool):
                    self.show_skills = show_skills
            except Exception:
                self._relative_positions = {}

    def _save_positions(self) -> None:
        self._capture_relative_positions()
        data = {k: v for k, v in self._relative_positions.items()}
        data["selected_melee"] = self.selected_melee
        data["selected_shield_mode"] = self.selected_shield_mode
        data["afk_alert_enabled"] = self.afk_alert_enabled
        data["afk_alert_volume"] = self.afk_alert_volume
        data["show_exp"] = self.show_exp
        data["show_timers"] = self.show_timers
        data["show_skills"] = self.show_skills
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
            available_w = max(200, self.window.width - 20)
            available_h = max(200, self.window.height - 20)
            w = min(max(360, int(self.window.width * 0.65)), available_w)
            h = min(max(260, int(self.window.height * 0.65)), available_h)
            x = (self.window.width - w) // 2
            y = (self.window.height - h) // 2
            self.options_rect = (x, y, w, h)
            return
        x, y, w, h = self.options_rect
        available_w = max(200, self.window.width - 10)
        available_h = max(200, self.window.height - 10)
        w = min(available_w, max(360, w))
        h = min(available_h, max(220, h))
        x = max(0, min(x, self.window.width - w))
        y = max(0, min(y, self.window.height - h))
        self.options_rect = (x, y, w, h)

    def _pane_from_saved(self, saved: list, name: str) -> tuple[int, int, int, int]:
        try:
            if all(isinstance(v, (int, float)) and v <= 1.0 for v in saved):
                rel = tuple(float(v) for v in saved)
                self._relative_positions[name] = rel
                return self._pane_from_relative(rel)
        except Exception:
            pass
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
        self._relative_positions["skills"] = self._pane_to_relative(self.skills_pane)
        self._relative_positions["controls"] = self._pane_to_relative(self.controls_pane)
