"""
Sample screen-reading helpers that target a first fixed region of the Ironcore client.
"""
from __future__ import annotations

from typing import Optional

from PIL import Image

from .capture import capture_region
from .client_window import WindowInfo

# Region relative to the game window: (left, top, width, height)
FIRST_SAMPLE_REGION = (20, 20, 320, 160)


def capture_primary_region(window: WindowInfo, save_path: Optional[str] = None) -> Image.Image:
    """
    Capture the primary sample region and optionally save it to disk.
    """
    image = capture_region(window.hwnd, FIRST_SAMPLE_REGION)
    if save_path:
        image.save(save_path)
    return image
