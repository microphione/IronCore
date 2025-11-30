from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set

import json
import win32api
import win32con

CONFIG_PATH = Path("custom_actions.json")


@dataclass
class ActionConfig:
    name: str
    action1: str
    action2: str
    count: int


@dataclass
class PendingAction:
    cfg: ActionConfig


class CustomActionsRunner:
    """
    Prost y poller klawiatury/myszki:
    - wykrywa sekwencje action1 -> action2
    - dla każdej wykonanej sekwencji uruchamia licznik malejący (count w sekundach)
    - zwraca statusy do wyświetlenia
    """

    def __init__(
        self,
        poll_interval: float = 0.05,
        emit_interval: float = 0.05,
        on_update: Optional[Callable[[List[str]], None]] = None,
        active_window: Optional[Callable[[], Optional[int]]] = None,
    ) -> None:
        self.poll_interval = poll_interval
        self.emit_interval = emit_interval
        self.on_update = on_update
        self.active_window = active_window
        self.actions: List[ActionConfig] = []
        self.pending: List[PendingAction] = []
        self.active: List[dict] = []
        self._down: Set[str] = set()
        self._occurrence_counter: int = 0
        self.load()
        self._stop = False
        self._thread = None
        self._last_emit = 0.0
        self._latest_lines: List[str] = []

    def start(self) -> None:
        if self._thread:
            return
        import threading

        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        if self._thread:
            self._thread.join(timeout=0.5)
        self._thread = None

    def load(self) -> None:
        self.actions.clear()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                for row in data:
                    try:
                        count = int(str(row.get("count", "1")))
                    except ValueError:
                        count = 1
                    self.actions.append(
                        ActionConfig(
                            name=str(row.get("name", "")),
                            action1=str(row.get("action1", "")),
                            action2=str(row.get("action2", "")),
                            count=count,
                        )
                    )
            except Exception:
                pass

    def reload(self) -> None:
        self.load()

    def _loop(self) -> None:
        while not self._stop:
            self.tick()
            now = time.time()
            if now - self._last_emit >= self.emit_interval:
                self._emit_lines()
                self._last_emit = now
            time.sleep(self.poll_interval)

    def tick(self) -> None:
        if self.active_window:
            try:
                import win32gui

                fg = win32gui.GetForegroundWindow()
                target = self.active_window()
                if target and fg != target:
                    time.sleep(self.poll_interval)
                    return
            except Exception:
                pass
        now = time.time()
        pressed = self._poll_inputs()
        # detekcja action1
        for name in pressed:
            for cfg in self.actions:
                if cfg.action1 == name:
                    self.pending.append(PendingAction(cfg=cfg))
        # detekcja action2 -> aktywacja
        for name in pressed:
            to_activate = [p for p in self.pending if p.cfg.action2 == name]
            if not to_activate:
                continue
            for p in to_activate:
                self.pending.remove(p)
                self._occurrence_counter += 1
                duration = max(1.0, float(p.cfg.count))
                self.active.append(
                    {
                        "cfg": p.cfg,
                        "start": now,
                        "end": now + duration,
                        "occurrence": self._occurrence_counter,
                    }
                )
        # usuwamy zakończone
        self.active = [a for a in self.active if now < a["end"]]

    def get_status_lines(self) -> List[str]:
        lines: List[str] = []
        now = time.time()
        # też sprzątamy wygasłe na wszelki wypadek
        self.active = [a for a in self.active if a["end"] > now]
        for a in self.active:
            remaining = max(0.0, a["end"] - now)
            suffix = f"({a['occurrence']})" if a.get("occurrence") else ""
            lines.append(f"{a['cfg'].name}{suffix}: {remaining:0.1f}")
        return lines

    def _emit_lines(self) -> None:
        lines = self.get_status_lines()
        if lines != self._latest_lines:
            self._latest_lines = lines
            if self.on_update:
                self.on_update(lines)

    def _poll_inputs(self) -> List[str]:
        names: List[str] = []
        # Mouse buttons
        mouse_map = {0x01: "MouseLeft", 0x02: "MouseRight"}
        for vk, name in mouse_map.items():
            if self._is_down(vk):
                if name not in self._down:
                    names.append(name)
                    self._down.add(name)
            else:
                self._down.discard(name)
        # Keyboard
        for vk in range(1, 256):
            if vk in mouse_map:
                continue
            if self._is_down(vk):
                key_name = self._key_name_from_vk(vk)
                if key_name:
                    if key_name not in self._down:
                        names.append(key_name)
                    self._down.add(key_name)
            else:
                # remove any entry for this vk name
                key_name = self._key_name_from_vk(vk)
                if key_name in self._down:
                    self._down.discard(key_name)
        return names

    def _is_down(self, vk: int) -> bool:
        state = win32api.GetAsyncKeyState(vk)
        return bool(state & 0x8000)

    def _key_name_from_vk(self, vk: int) -> Optional[str]:
        try:
            scan = win32api.MapVirtualKey(vk, 0) << 16
            name = win32api.GetKeyNameText(scan)
            return name if name else str(vk)
        except Exception:
            return str(vk)
