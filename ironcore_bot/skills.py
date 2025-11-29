"""
Wykrywanie panelu Skills na podstawie skills.png oraz odczyt Experience/Level.
- Panel lokalizowany po nagłówku (template matching) lub ręcznie przez MANUAL_REGION.
- Pozycje etykiet Experience/Level wyznaczane offsetami względem panelu (ręczne lub domyślne).
- Wartości odczytywane szablonami znaków (0-9, %, (, ), przecinek) z assets/templates/skills/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Dict, Optional

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageChops, ImageOps

from .capture import capture_full_window
from .client_window import WindowInfo
from config import (
    MANUAL_REGION,
    MANUAL_EXPERIENCE_OFFSET,
    MANUAL_LEVEL_OFFSET,
    SAVE_DEBUG_CROPS,
    DEBUG_EXP_PATH,
    DEBUG_LEVEL_PATH,
)

DEBUG = False  # logi wyłączone

# Ustawienia domyślne panelu i etykiet (względem lewego górnego rogu panelu)
HEADER_HEIGHT = 18
PANEL_WIDTH = 220
PANEL_HEIGHT = 200
EXPERIENCE_LABEL_BOX = (10, HEADER_HEIGHT + 4, 70, 16)
LEVEL_LABEL_BOX = (10, HEADER_HEIGHT + 24, 70, 16)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates" / "skills"
_TEMPLATE_CACHE: Dict[str, Image.Image] = {}
_SKILLS_HEADER_TEMPLATE: Optional[np.ndarray] = None


@dataclass
class Region:
    x: int
    y: int
    width: int
    height: int


@dataclass
class SkillsInfo:
    region: Optional[Region]
    experience: Optional[str]
    level: Optional[str]


def _binarize(img: Image.Image, threshold: int = 128) -> Image.Image:
    return img.convert("L").point(lambda x: 0 if x < threshold else 255)


def _normalize_bw(img: Image.Image, threshold: int = 170) -> Image.Image:
    """Zapewnia czarny znak na białym tle, odwraca jeśli trzeba."""
    binar = _binarize(img, threshold=threshold)
    histogram = binar.histogram()
    black = histogram[0]
    white = histogram[255]
    if black > white:
        binar = ImageOps.invert(binar)
    return binar


def _segment_glyphs(line_img: Image.Image) -> list[Image.Image]:
    """Prosta segmentacja znaków po pustych kolumnach."""
    line = _binarize(line_img, threshold=200)
    width, height = line.size
    pixels = line.load()
    cols = []
    for x in range(width):
        has_ink = any(pixels[x, y] == 0 for y in range(height))
        cols.append(has_ink)

    segments: list[tuple[int, int]] = []
    in_seg = False
    start = 0
    for x, ink in enumerate(cols):
        if ink and not in_seg:
            start = x
            in_seg = True
        elif not ink and in_seg:
            segments.append((start, x))
            in_seg = False
    if in_seg:
        segments.append((start, width))

    glyphs: list[Image.Image] = []
    for left, right in segments:
        box = (left, 0, right, height)
        glyph = line.crop(box)
        bbox = glyph.getbbox()
        if not bbox:
            continue
        glyphs.append(glyph.crop(bbox))
    return glyphs


def _load_templates() -> Dict[str, Image.Image]:
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE
    if not TEMPLATES_DIR.exists():
        return {}
    templates: Dict[str, Image.Image] = {}
    for path in TEMPLATES_DIR.glob("*.png"):
        key = path.stem
        try:
            img = Image.open(path).convert("L")
            img = ImageOps.autocontrast(img)
            img = _normalize_bw(img, threshold=170)
            templates[key] = img
        except Exception:
            continue
    _TEMPLATE_CACHE = templates
    return templates


def _load_skills_header_template() -> Optional[np.ndarray]:
    global _SKILLS_HEADER_TEMPLATE
    if _SKILLS_HEADER_TEMPLATE is not None:
        return _SKILLS_HEADER_TEMPLATE
    path = TEMPLATES_DIR / "skills.png"
    if not path.exists():
        _SKILLS_HEADER_TEMPLATE = None
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        _SKILLS_HEADER_TEMPLATE = None
        return None
    img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    _SKILLS_HEADER_TEMPLATE = img
    return _SKILLS_HEADER_TEMPLATE


def _match_glyph(glyph: Image.Image, templates: Dict[str, Image.Image]) -> Optional[str]:
    best_char = None
    best_score = None
    for char, tmpl in templates.items():
        resized = glyph.resize(tmpl.size, Image.LANCZOS)
        diff = ImageChops.difference(resized, tmpl)
        score = sum(diff.getdata())
        if best_score is None or score < best_score:
            best_score = score
            best_char = char
    return best_char


def _read_with_templates(value_img: Image.Image) -> Optional[str]:
    templates = _load_templates()
    if not templates:
        return None
    glyphs = _segment_glyphs(value_img)
    if not glyphs:
        return None
    chars: list[str] = []
    for glyph in glyphs:
        ch = _match_glyph(glyph, templates)
        if ch is None:
            return None
        chars.append(ch)
    return "".join(chars)


def _find_skills_anchor(full_img: Image.Image) -> Optional[Region]:
    tmpl = _load_skills_header_template()
    if tmpl is None:
        return None
    gray = cv2.cvtColor(np.array(full_img), cv2.COLOR_RGB2GRAY)
    res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val < 0.6:
        return None
    h, w = tmpl.shape
    x, y = max_loc
    return Region(x, y, w, h)


def _normalize_region(region: Region, window: WindowInfo) -> Region:
    left = max(0, min(region.x, window.width - region.width))
    top = max(0, min(region.y, window.height - region.height))
    width = min(region.width, window.width - left)
    height = min(region.height, window.height - top)
    return Region(left, top, width, height)


def _extract_value_region(crop: Image.Image, label: Region) -> Optional[Image.Image]:
    margin_y = 2
    x0 = max(0, label.x + label.width + 4)
    x1 = crop.width
    y0 = max(0, label.y - margin_y)
    y1 = min(crop.height, label.y + label.height + margin_y)
    if x0 >= x1 or y0 >= y1:
        return None
    return crop.crop((x0, y0, x1, y1))


def _read_value_with_templates(crop: Image.Image, label: Optional[Region], debug_path: str) -> Optional[str]:
    if not label:
        return None
    region = _extract_value_region(crop, label)
    if region is None:
        return None
    if SAVE_DEBUG_CROPS and DEBUG:
        region.save(debug_path)
    region = _normalize_bw(region, threshold=170)
    return _read_with_templates(region)


def _ocr_value_region(image: Image.Image, label: Optional[Region], debug_path: str) -> Optional[str]:
    if not label:
        return None
    region = _extract_value_region(image, label)
    if region is None:
        return None
    if SAVE_DEBUG_CROPS and DEBUG:
        region.save(debug_path)
    region = ImageOps.autocontrast(region.convert("L"))
    region = _normalize_bw(region, threshold=170)
    text = pytesseract.image_to_string(
        region,
        config="--psm 7 --oem 1 -c tessedit_char_whitelist=0123456789,()% -c user_defined_dpi=220",
    ).strip()
    return text or None


def _clean_experience(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    match = re.search(r"([\d][\d\s,\.]*)", raw)
    if not match:
        return raw
    cleaned = match.group(1).replace(" ", "").replace(".", ",")
    return cleaned


def _clean_level(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    match = re.search(r"(\d+)\s*\(\s*([0-9]+%?)\s*\)", raw)
    if match:
        return f"{match.group(1)} ({match.group(2)})"
    match = re.search(r"(\d+)", raw)
    return match.group(1) if match else raw


def _is_valid_experience(val: Optional[str]) -> bool:
    return bool(val and re.fullmatch(r"[0-9][0-9,\.]*", val))


def _is_valid_level(val: Optional[str]) -> bool:
    return bool(val and re.fullmatch(r"\d+(\s*\(\s*\d+%?\s*\))?", val))


def analyze_skills(window: WindowInfo) -> SkillsInfo:
    full_img = capture_full_window(window.hwnd)

    if MANUAL_REGION:
        left, top, width, height = MANUAL_REGION
        region = Region(left, top, width, height)
    else:
        anchor = _find_skills_anchor(full_img)
        if not anchor:
            return SkillsInfo(region=None, experience=None, level=None)
        region = Region(anchor.x, anchor.y, PANEL_WIDTH, PANEL_HEIGHT)

    region = _normalize_region(region, window)

    crop_left, crop_top = region.x, region.y
    crop = full_img.crop((crop_left, crop_top, crop_left + region.width, crop_top + region.height))

    crop_templates = ImageOps.autocontrast(crop.convert("L"))
    crop_templates = _normalize_bw(crop_templates, threshold=180)

    crop_ocr = ImageOps.autocontrast(crop.convert("L"))
    crop_ocr = _normalize_bw(crop_ocr, threshold=150)

    # Etykiety: offsety ręczne lub domyślne
    if MANUAL_EXPERIENCE_OFFSET:
        dx, dy, w, h = MANUAL_EXPERIENCE_OFFSET
        exp_box = Region(dx, dy, w, h)
    else:
        lx, ly, lw, lh = EXPERIENCE_LABEL_BOX
        exp_box = Region(lx, ly, lw, lh)

    if MANUAL_LEVEL_OFFSET:
        dx, dy, w, h = MANUAL_LEVEL_OFFSET
        lvl_box = Region(dx, dy, w, h)
    else:
        lx, ly, lw, lh = LEVEL_LABEL_BOX
        lvl_box = Region(lx, ly, lw, lh)

    exp_val = _read_value_with_templates(crop_templates, exp_box, DEBUG_EXP_PATH)
    lvl_val = _read_value_with_templates(crop_templates, lvl_box, DEBUG_LEVEL_PATH)

    if not _is_valid_experience(exp_val):
        exp_val = _ocr_value_region(crop_ocr, exp_box, DEBUG_EXP_PATH)
    if not _is_valid_level(lvl_val):
        lvl_val = _ocr_value_region(crop_ocr, lvl_box, DEBUG_LEVEL_PATH)

    experience_clean = _clean_experience(exp_val)
    level_clean = _clean_level(lvl_val)

    return SkillsInfo(region=region, experience=experience_clean, level=level_clean)


class SkillsWatcher:
    def __init__(self, window: WindowInfo, overlay=None, interval: float = 1.0, tracker=None, actions_runner=None) -> None:
        self.window = window
        self.overlay = overlay
        self.interval = interval
        self.last_region: Optional[Region] = None
        self.last_experience: Optional[str] = None
        self.last_level: Optional[str] = None
        self.tracker = tracker
        self.actions_runner = actions_runner
        self._stop_event = Event()
        self._thread = Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=1.0)

    def update_window(self, window: WindowInfo) -> None:
        """Switch the target window while running."""
        self.window = window

    def _update_status(self) -> None:
        if not self.overlay:
            return
        lines = []
        if self.tracker and self.last_experience:
            try:
                exp_int = int(str(self.last_experience).replace(",", "").replace(".", ""))
                deltas = self.tracker.update(exp_int)
                lines.append(f"exp/10 min: {deltas.get('10m') or 0}")
                lines.append(f"exp/h: {deltas.get('60m') or 0}")
                lines.append(f"exp total: {deltas.get('total') or 0}")
            except Exception:
                pass
        self.overlay.set_status(lines)
        if self.actions_runner:
            self.overlay.set_actions_status(self.actions_runner.get_status_lines())

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                info = analyze_skills(self.window)
                self.last_region = info.region
                if _is_valid_experience(info.experience):
                    self.last_experience = info.experience
                if _is_valid_level(info.level):
                    self.last_level = info.level
                self._update_status()
            except Exception:
                pass
            finally:
                self._stop_event.wait(self.interval)
