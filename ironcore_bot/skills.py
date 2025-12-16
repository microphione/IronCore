from __future__ import annotations

import math
import time
from threading import Event, Thread
from typing import Dict, Optional

from .skills_analyzer import SkillsInfo, analyze_skills
from .skills_parser import is_valid_experience, is_valid_level, parse_skill_value
from .skill_tables import get_distance_brackets, get_seconds_to_next


class SkillsWatcher:
    def __init__(self, window, overlay=None, interval: float = 1.0, tracker=None, actions_runner=None) -> None:
        self.window = window
        self.overlay = overlay
        self.analyze_interval = interval
        self.emit_interval = 0.1
        self.tick_interval = min(0.2, max(0.05, interval / 2))
        self.last_region: Optional[SkillsInfo] = None
        self.last_experience: Optional[str] = None
        self.last_level: Optional[str] = None
        self.last_skills: Dict[str, str] = {}
        self.tracker = tracker
        self.actions_runner = actions_runner
        self._stop_event = Event()
        self._thread = Thread(target=self._run, daemon=True)
        self._eta_seconds: Optional[float] = None
        self._eta_last_ts: float = time.monotonic()
        self._eta_snapshot: Optional[tuple[str, Optional[int], Optional[int]]] = None
        self._last_emit: float = 0.0
        self._last_analyze: float = 0.0
        self._last_selected_melee: str = ""
        self._distance_state: Optional[dict] = None
        self._shield_eta_seconds: Optional[float] = None
        self._shield_snapshot: Optional[tuple[int, Optional[int], int]] = None  # (level, pct, mode)
        self._shield_last_ts: float = time.monotonic()
        self._last_shield_mode: int = 1

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=1.0)

    def update_window(self, window) -> None:
        self.window = window

    def _update_status(self) -> None:
        if not self.overlay:
            return
        status_lines = []
        if self.tracker and self.last_experience:
            try:
                exp_int = int(str(self.last_experience).replace(",", "").replace(".", ""))
                deltas = self.tracker.update(exp_int)
                status_lines.append(f"exp/10 min: {deltas.get('10m') or 0}")
                status_lines.append(f"exp/h: {deltas.get('60m') or 0}")
                status_lines.append(f"exp total: {deltas.get('total') or 0}")
            except Exception:
                pass
        self.overlay.set_status(status_lines)

        skill_lines: list[str] = []
        selected = (self.overlay.selected_melee if self.overlay else None) or ""
        sel_key = selected.lower()
        value_for_selected = self.last_skills.get(sel_key)
        if self._eta_snapshot and self._eta_snapshot[0] != sel_key:
            self._eta_seconds = None
            self._eta_snapshot = None
            self._distance_state = None
            self._eta_last_ts = time.monotonic()
        if sel_key and sel_key not in self.last_skills:
            skill_lines.append(f"{selected}: cant find skill")
        else:
            skill_lines.append(f"{selected or 'Skill'}: {value_for_selected or '?'}")
            if sel_key in ("fist", "club", "sword", "axe") and value_for_selected:
                lvl, pct = parse_skill_value(value_for_selected)
                seconds_to_next = get_seconds_to_next("melee", lvl or -1) if lvl is not None else None
                if seconds_to_next:
                    remaining = self._eta_seconds
                    if self._eta_snapshot != (sel_key, lvl, pct):
                        remaining = None
                    if remaining is None:
                        if pct is not None:
                            remaining = max(0.0, seconds_to_next * max(0, 100 - pct) / 100)
                        else:
                            remaining = float(seconds_to_next)
                        self._eta_seconds = remaining
                        self._eta_snapshot = (sel_key, lvl, pct)
                        self._eta_last_ts = time.monotonic()
                    else:
                        now = time.monotonic()
                        elapsed = max(0, now - self._eta_last_ts)
                        self._eta_last_ts = now
                        remaining = max(0.0, remaining - elapsed)
                        self._eta_seconds = remaining
                        self._eta_snapshot = (sel_key, lvl, pct)
                    rem_int = int(remaining)
                    hours = rem_int // 3600
                    minutes = (rem_int % 3600) // 60
                    seconds = rem_int % 60
                    skill_lines.append(f"ETA: {hours:02}:{minutes:02}:{seconds:02}")
                else:
                    skill_lines.append("ETA: ?")
            elif sel_key == "distance" and value_for_selected:
                lvl, pct = parse_skill_value(value_for_selected)
                sec_min, sec_max, stones_min, stones_max = get_distance_brackets(lvl or -1)
                if sec_min is not None and sec_max is not None:
                    pct_left = max(0, 100 - (pct or 0))
                    now = time.monotonic()
                    base_min = max(0.0, sec_min * pct_left / 100.0)
                    base_max = max(0.0, sec_max * pct_left / 100.0)
                    base_stones_min = max(0, int(math.ceil((stones_min or 0) * pct_left / 100)))
                    base_stones_max = max(0, int(math.ceil((stones_max or 0) * pct_left / 100)))
                    if not self._distance_state or self._eta_snapshot != (sel_key, lvl, pct):
                        self._distance_state = {
                            "sec_min": base_min,
                            "sec_max": base_max,
                            "base_sec_min": base_min,
                            "base_sec_max": base_max,
                            "stones_min": base_stones_min,
                            "stones_max": base_stones_max,
                            "base_stones_min": base_stones_min,
                            "base_stones_max": base_stones_max,
                        }
                        self._eta_snapshot = (sel_key, lvl, pct)
                        self._eta_last_ts = now
                    else:
                        elapsed = max(0, now - self._eta_last_ts)
                        self._eta_last_ts = now
                        self._distance_state["sec_min"] = max(0.0, self._distance_state["sec_min"] - elapsed)
                        self._distance_state["sec_max"] = max(0.0, self._distance_state["sec_max"] - elapsed)
                    eta_min = self._distance_state["sec_min"]
                    eta_max = self._distance_state["sec_max"]
                    base_sec_min = max(1e-6, self._distance_state["base_sec_min"])
                    base_sec_max = max(1e-6, self._distance_state["base_sec_max"])
                    stones_min_left = int(
                        max(0, math.ceil(self._distance_state["base_stones_min"] * (eta_min / base_sec_min)))
                    )
                    stones_max_left = int(
                        max(0, math.ceil(self._distance_state["base_stones_max"] * (eta_max / base_sec_max)))
                    )
                    self._distance_state["stones_min"] = stones_min_left
                    self._distance_state["stones_max"] = stones_max_left
                    skill_lines.append(
                        f"ETA: {int(eta_min)//3600:02}:{(int(eta_min)%3600)//60:02}:{int(eta_min)%60:02} - "
                        f"{int(eta_max)//3600:02}:{(int(eta_max)%3600)//60:02}:{int(eta_max)%60:02}"
                    )
                    if stones_min is not None and stones_max is not None:
                        skill_lines.append(f"Stones: {stones_min_left} - {stones_max_left}")
                else:
                    skill_lines.append("ETA: ?")

        shield_val = self.last_skills.get("shielding")
        skill_lines.append(f"Shielding: {shield_val or '?'}")
        if shield_val:
            lvl, pct = parse_skill_value(shield_val)
            sec = get_seconds_to_next("melee", lvl or -1) if lvl is not None else None
            mode = getattr(self.overlay, "selected_shield_mode", 1) or 1
            if sec:
                if mode == 2:
                    sec = sec / 2.0
                if self._shield_snapshot != (lvl, pct, mode) or self._shield_eta_seconds is None:
                    remaining = sec * max(0, 100 - (pct or 0)) / 100 if pct is not None else sec
                    self._shield_eta_seconds = max(0.0, float(remaining))
                    self._shield_snapshot = (lvl, pct, mode)
                    self._shield_last_ts = time.monotonic()
                else:
                    now = time.monotonic()
                    elapsed = max(0, now - self._shield_last_ts)
                    self._shield_last_ts = now
                    self._shield_eta_seconds = max(0.0, (self._shield_eta_seconds or 0.0) - elapsed)
                rem = max(0.0, self._shield_eta_seconds or 0.0)
                rem_int = int(rem)
                skill_lines.append(f"ETA: {rem_int//3600:02}:{(rem_int%3600)//60:02}:{rem_int%60:02}")
            else:
                skill_lines.append("ETA: ?")

        self.overlay.set_skills_status(skill_lines)

        if self.actions_runner:
            self.overlay.set_actions_status(self.actions_runner.get_status_lines())

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.overlay:
                    shield_mode = getattr(self.overlay, "selected_shield_mode", 1) or 1
                    if shield_mode != self._last_shield_mode:
                        self._last_shield_mode = shield_mode
                        self._shield_eta_seconds = None
                        self._shield_snapshot = None
                        self._shield_last_ts = time.monotonic()
                now = time.monotonic()
                if now - self._last_analyze >= self.analyze_interval:
                    info = analyze_skills(self.window, save_debug=False)
                    self.last_region = info.region
                    if is_valid_experience(info.experience):
                        self.last_experience = info.experience
                    if is_valid_level(info.level):
                        self.last_level = info.level
                    self.last_skills = info.skills or {}
                    self._last_analyze = now
                    self._eta_last_ts = now

                if now - self._last_emit >= self.emit_interval:
                    self._update_status()
                    self._last_emit = now
            except Exception:
                pass
            finally:
                self._stop_event.wait(self.tick_interval)
