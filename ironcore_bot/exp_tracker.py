from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional


@dataclass
class ExpSnapshot:
    timestamp: float
    exp: int


class ExpTracker:
    """
    Śledzi exp w czasie i liczy przyrosty w oknach 10/15/60 minut.
    """

    MAX_WINDOW_SEC = 60 * 60

    def __init__(self) -> None:
        self.baseline: Optional[int] = None
        self.history: Deque[ExpSnapshot] = deque()

    def reset(self, exp: Optional[int], timestamp: Optional[float] = None) -> None:
        self.baseline = exp
        self.history.clear()
        if exp is not None:
            ts = timestamp if timestamp is not None else time.time()
            self.history.append(ExpSnapshot(timestamp=ts, exp=exp))

    def update(self, exp: Optional[int]) -> dict[str, Optional[int]]:
        if exp is None:
            return {"10m": None, "60m": None, "1m": None, "total": None}
        now = time.time()
        if self.baseline is None:
            self.reset(exp, timestamp=now)
        else:
            self.history.append(ExpSnapshot(timestamp=now, exp=exp))
        self._prune(now)
        return {
            "10m": self._delta_for_window(now, 10 * 60),
            "60m": self._delta_for_window(now, 60 * 60),
            "1m": self._delta_for_window(now, 1 * 60),
            "total": exp - (self.baseline if self.baseline is not None else exp),
        }

    def _prune(self, now: float) -> None:
        cutoff = now - self.MAX_WINDOW_SEC
        while self.history and self.history[0].timestamp < cutoff:
            self.history.popleft()

    def _delta_for_window(self, now: float, window_sec: int) -> Optional[int]:
        cutoff = now - window_sec
        baseline = self._baseline_for_window(cutoff)
        latest = self.history[-1].exp if self.history else None
        if baseline is None or latest is None:
            return None
        return latest - baseline

    def _baseline_for_window(self, cutoff: float) -> Optional[int]:
        if not self.history:
            return None
        # Prefer najstarszy snapshot >= cutoff; jeśli brak, weź ostatni przed cutoff.
        candidate_after = None
        for snap in self.history:
            if snap.timestamp >= cutoff:
                candidate_after = snap.exp
                break
        if candidate_after is not None:
            return candidate_after
        # Brak punktu w oknie -> użyj ostatniego przed cutoff
        for snap in reversed(self.history):
            if snap.timestamp <= cutoff:
                return snap.exp
        return None
