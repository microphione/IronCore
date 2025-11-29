"""
Helpers for locating the Ironcore game window and basic geometry utilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import psutil
import win32con
import win32gui
import win32process


@dataclass
class WindowInfo:
    hwnd: int
    process_id: int
    rect: tuple[int, int, int, int]

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


def _matches_process(hwnd: int, target_process_name: str) -> Optional[WindowInfo]:
    """Return WindowInfo when hwnd belongs to the target process and is visible."""
    if not win32gui.IsWindowVisible(hwnd):
        return None

    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid)
        if process.name().lower() != target_process_name.lower():
            return None
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None

    rect = win32gui.GetWindowRect(hwnd)
    return WindowInfo(hwnd=hwnd, process_id=pid, rect=rect)


def find_window_for_process(process_name: str) -> Optional[WindowInfo]:
    """
    Locate the top-level window for the given process name.

    Returns the largest matching visible window (useful when the process spawns multiple windows).
    """
    matches: list[WindowInfo] = []

    def handler(hwnd: int, _: int) -> None:
        info = _matches_process(hwnd, process_name)
        if info:
            matches.append(info)

    win32gui.EnumWindows(handler, 0)
    if not matches:
        return None

    matches.sort(key=lambda w: w.width * w.height, reverse=True)
    return matches[0]


def center_in_window(window: WindowInfo, region_width: int, region_height: int) -> tuple[int, int, int, int]:
    """
    Return a rectangle centered within the window: (left, top, width, height).
    """
    left = window.rect[0] + (window.width - region_width) // 2
    top = window.rect[1] + (window.height - region_height) // 2
    return left, top, region_width, region_height


def run_with_window(process_name: str, callback: Callable[[WindowInfo], None]) -> None:
    """
    Find the window and pass it to callback, raising a helpful error if missing.
    """
    window = find_window_for_process(process_name)
    if not window:
        raise RuntimeError(f"Nie znaleziono okna procesu {process_name!r}. Upewnij się, że gra jest uruchomiona.")
    callback(window)
