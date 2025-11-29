"""
Minimal screen capture helpers for the Ironcore client window.
"""
from __future__ import annotations

from typing import Tuple

from PIL import Image
from mss import mss
import win32gui


def _absolute_region(hwnd: int, region: Tuple[int, int, int, int]) -> dict[str, int]:
    """
    Convert a region relative to hwnd into an absolute monitor rect for mss.
    Region is (left, top, width, height) relative to the window's top-left corner.
    """
    window_left, window_top, _, _ = win32gui.GetWindowRect(hwnd)
    rel_left, rel_top, width, height = region
    return {
        "left": window_left + rel_left,
        "top": window_top + rel_top,
        "width": width,
        "height": height,
    }


def capture_region(hwnd: int, region: Tuple[int, int, int, int]) -> Image.Image:
    """
    Capture a region of the window and return it as a PIL Image in RGB.
    """
    abs_region = _absolute_region(hwnd, region)
    with mss() as sct:
        raw = sct.grab(abs_region)
    return Image.frombytes("RGB", raw.size, raw.rgb)


def capture_full_window(hwnd: int) -> Image.Image:
    """
    Capture the full client window in RGB.
    """
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top
    return capture_region(hwnd, (0, 0, width, height))
