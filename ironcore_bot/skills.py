"""
Wykrywanie panelu Skills na podstawie skills.png oraz odczyt Experience/Level.
- Panel lokalizowany po nagłówku (template matching) lub ręcznie przez MANUAL_REGION.
- Pozycje etykiet Experience/Level wyznaczane offsetami względem panelu (ręczne lub domyślne).
- Wartości odczytywane szablonami znaków (0-9, %, (, ), przecinek) z assets/templates/skills/.
"""
from __future__ import annotations

import math
import re
import time
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
from .skill_tables import get_distance_brackets, get_seconds_to_next
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
SKILL_ALIASES = {
    "fist fighting": "fist",
    "fist": "fist",
    "club fighting": "club",
    "club": "club",
    "sword fighting": "sword",
    "sword": "sword",
    "axe fighting": "axe",
    "axe": "axe",
    "distance fighting": "distance",
    "distance": "distance",
    "shielding": "shielding",
    "shield": "shielding",
    "defending": "shielding",
}
SKILL_TARGETS = {alias: key for alias, key in SKILL_ALIASES.items()}


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
    skills: Dict[str, str]


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


def _ocr_box_value(image: Image.Image, bbox: tuple[int, int, int, int]) -> Optional[str]:
    x, y, w, h = bbox
    if w <= 0 or h <= 0:
        return None
    region = image.crop((x, y, x + w, y + h))
    region = ImageOps.autocontrast(region.convert("L"))
    region = _normalize_bw(region, threshold=170)
    text = pytesseract.image_to_string(
        region,
        config="--psm 7 --oem 1 -c tessedit_char_whitelist=0123456789()% -c user_defined_dpi=220",
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


def _normalize_skill_value(raw: str) -> str:
    """
    Normalize skill value to form 'X (Y%)' or 'X' if percent missing.
    Tolerates noisy OCR like '10(6%0)' or '39 (66%o)'.
    """
    if not raw:
        return raw
    m = re.search(r"^\s*(\d+)\s*(?:\(\s*([0-9]+)\s*%?\s*\))?\s*$", raw)
    if m:
        base = m.group(1)
        pct = m.group(2)
        if pct:
            return f"{base} ({pct}%)"
        return base
    # fallback: strip stray characters around % and digits
    base = re.search(r"(\d+)", raw)
    pct = re.search(r"\((\d+)", raw)
    if base and pct:
        return f"{base.group(1)} ({pct.group(1)}%)"
    if base:
        return base.group(1)
    return raw.strip()


def _parse_skill_value(raw: str) -> tuple[Optional[int], Optional[int]]:
    """
    Return (level, percent) from value like '39 (67%)'. Percent may be None.
    """
    normalized = _normalize_skill_value(raw or "")
    m = re.search(r"(\d+)\s*\(\s*([0-9]+)\s*%\s*\)", normalized)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)", normalized)
    if m:
        return int(m.group(1)), None
    return None, None


def _parse_skill_lines(text: str) -> Dict[str, str]:
    """
    Extract skill values from OCR'ed skills panel text.
    Returns lowercase label -> value (with parentheses when present).
    """
    results: Dict[str, str] = {}
    for line in text.splitlines():
        lower = line.lower()
        matched = False
        for alias, key in SKILL_ALIASES.items():
            if alias in lower:
                match = re.search(r"(\d+\s*(?:\(\s*[^)]+\s*\))?)", line)
                if match:
                    results[key] = _normalize_skill_value(match.group(1).strip())
                    matched = True
                    break
        if matched:
            continue
        generic = re.search(
            r"(?i)(fist|club|sword|axe|distance|shielding|shield|defending)\s+(\d+\s*(?:\(\s*[^)]+\s*\))?)",
            line,
        )
        if generic:
            key = SKILL_ALIASES.get(generic.group(1).lower())
            if key:
                results[key] = _normalize_skill_value(generic.group(2).strip())
    return results


def _extract_skills_from_data(
    crop: Image.Image,
    crop_ocr: Image.Image,
    data: dict,
    return_regions: bool = False,
) -> Dict[str, object]:
    """
    Parse skill rows using pytesseract data with bounding boxes; OCR values in value box with digit-only whitelist.
    When return_regions=True returns key -> bbox for debug saving.
    """
    lines: dict[tuple[int, int, int], dict[str, object]] = {}
    for idx, text in enumerate(data.get("text", [])):
        if not text or not text.strip():
            continue
        key = (data["block_num"][idx], data["par_num"][idx], data["line_num"][idx])
        x, y, w, h = data["left"][idx], data["top"][idx], data["width"][idx], data["height"][idx]
        entry = lines.setdefault(key, {"words": [], "boxes": []})
        entry["words"].append(text)
        entry["boxes"].append((x, y, w, h))

    results: Dict[str, object] = {}
    aliases_sorted = sorted(SKILL_ALIASES.keys(), key=lambda a: (-len(a.split()), -len(a)))

    for entry in lines.values():
        words = entry["words"]
        boxes = entry["boxes"]
        lower_words = [w.lower() for w in words]
        matched_alias = None
        start_idx = None
        alias_len = 0
        for alias in aliases_sorted:
            tokens = alias.split()
            for i in range(len(lower_words) - len(tokens) + 1):
                if all(lower_words[i + j].startswith(tokens[j]) for j in range(len(tokens))):
                    matched_alias = alias
                    start_idx = i
                    alias_len = len(tokens)
                    break
            if matched_alias:
                break
        if matched_alias is None or start_idx is None:
            continue
        value_words = words[start_idx + alias_len :]
        value_boxes = boxes[start_idx + alias_len :]
        if not value_words or not value_boxes:
            continue
        x0 = min(b[0] for b in value_boxes)
        y0 = min(b[1] for b in value_boxes)
        x1 = max(b[0] + b[2] for b in value_boxes)
        y1 = max(b[1] + b[3] for b in value_boxes)
        bbox = (x0, y0, x1 - x0, y1 - y0)
        key = SKILL_ALIASES.get(matched_alias, matched_alias)
        if return_regions:
            results[key] = bbox
        else:
            val = _ocr_box_value(crop, bbox) or _ocr_box_value(crop_ocr, bbox) or "cant find skill"
            if isinstance(val, str):
                val = _normalize_skill_value(val)
            results[key] = val.strip() if isinstance(val, str) else val
    return results


def _save_debug_slices(
    crop: Image.Image,
    crop_ocr: Image.Image,
    exp_box: Region,
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        crop.save(out_dir / "skills_panel_full.png")
        crop_ocr.save(out_dir / "skills_panel_ocr.png")
    except Exception:
        pass

    def _save_region(img: Image.Image, bbox: tuple[int, int, int, int], name: str) -> None:
        x, y, w, h = bbox
        if w <= 0 or h <= 0:
            return
        try:
            img.crop((x, y, x + w, y + h)).save(out_dir / name)
        except Exception:
            pass

    exp_region = _extract_value_region(crop, exp_box)
    if exp_region:
        try:
            exp_region.save(out_dir / "experience_region.png")
        except Exception:
            pass

    try:
        data = pytesseract.image_to_data(
            crop_ocr,
            output_type=pytesseract.Output.DICT,
            config="--psm 6 --oem 1 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789()% ",
        )
        regions = _extract_skills_from_data(crop, crop_ocr, data, return_regions=True)
        if isinstance(regions, dict):
            for key, box in regions.items():
                if isinstance(box, tuple) and len(box) == 4:
                    _save_region(crop, box, f"skill_{key}.png")
    except Exception:
        pass


def _is_valid_experience(val: Optional[str]) -> bool:
    return bool(val and re.fullmatch(r"[0-9][0-9,\.]*", val))


def _is_valid_level(val: Optional[str]) -> bool:
    return bool(val and re.fullmatch(r"\d+(\s*\(\s*\d+%?\s*\))?", val))


def analyze_skills(window: WindowInfo, save_debug: bool = False) -> SkillsInfo:
    full_img = capture_full_window(window.hwnd)

    if MANUAL_REGION:
        left, top, width, height = MANUAL_REGION
        region = Region(left, top, width, height)
    else:
        anchor = _find_skills_anchor(full_img)
        if not anchor:
            return SkillsInfo(region=None, experience=None, level=None, skills={})
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

    # Parse skills list (melee/distance/shielding) from the whole panel OCR
    text_variants = [
        pytesseract.image_to_string(
            crop_ocr,
            config="--psm 6 --oem 1 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789()% ",
        ),
        pytesseract.image_to_string(
            crop,
            config="--psm 6 --oem 1 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789()% ",
        ),
    ]
    skills: Dict[str, str] = {}
    try:
        ocr_data = pytesseract.image_to_data(
            crop_ocr,
            output_type=pytesseract.Output.DICT,
            config="--psm 6 --oem 1 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789()% ",
        )
    except Exception:
        ocr_data = None
    if ocr_data:
        skills.update(_extract_skills_from_data(crop, crop_ocr, ocr_data))
    for variant in text_variants:
        parsed = _parse_skill_lines(variant)
        for key, val in parsed.items():
            if val and key not in skills:
                skills[key] = val

    if save_debug:
        try:
            debug_dir = Path("debug_skills")
            _save_debug_slices(
                crop,
                crop_ocr,
                exp_box,
                debug_dir,
            )
        except Exception:
            pass

    return SkillsInfo(region=region, experience=experience_clean, level=level_clean, skills=skills)


class SkillsWatcher:
    def __init__(self, window: WindowInfo, overlay=None, interval: float = 1.0, tracker=None, actions_runner=None) -> None:
        self.window = window
        self.overlay = overlay
        self.analyze_interval = interval
        self.emit_interval = 0.1
        self.tick_interval = min(0.2, max(0.05, interval / 2))
        self.last_region: Optional[Region] = None
        self.last_experience: Optional[str] = None
        self.last_level: Optional[str] = None
        self.last_skills: Dict[str, str] = {}
        self.tracker = tracker
        self.actions_runner = actions_runner
        self._stop_event = Event()
        self._thread = Thread(target=self._run, daemon=True)
        self._debug_saved = False
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

    def update_window(self, window: WindowInfo) -> None:
        """Switch the target window while running."""
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

        # Skills panel lines
        skill_lines: list[str] = []
        selected = (self.overlay.selected_melee if self.overlay else None) or ""
        sel_key = selected.lower()
        value_for_selected = self.last_skills.get(sel_key)
        # reset ETA when switching selected skill
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
                lvl, pct = _parse_skill_value(value_for_selected)
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
                lvl, pct = _parse_skill_value(value_for_selected)
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
                        max(
                            0,
                            math.ceil(
                                self._distance_state["base_stones_min"] * (eta_min / base_sec_min)
                            ),
                        )
                    )
                    stones_max_left = int(
                        max(
                            0,
                            math.ceil(
                                self._distance_state["base_stones_max"] * (eta_max / base_sec_max)
                            ),
                        )
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

        # Shielding section (always shown)
        shield_val = self.last_skills.get("shielding")
        skill_lines.append(f"Shielding: {shield_val or '?'}")
        if shield_val:
            lvl, pct = _parse_skill_value(shield_val)
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
                if self.overlay and not self.overlay.show_skills:
                    self._eta_seconds = None
                    self._eta_snapshot = None
                    self._distance_state = None
                    self._shield_eta_seconds = None
                    self._shield_snapshot = None
                    self._stop_event.wait(self.tick_interval)
                    continue
                # detect selection change
                current_sel = (self.overlay.selected_melee if self.overlay else "") or ""
                current_sel = current_sel.lower()
                if current_sel != self._last_selected_melee:
                    self._last_selected_melee = current_sel
                    self._eta_seconds = None
                    self._eta_snapshot = None
                    self._distance_state = None
                    self._eta_last_ts = time.monotonic()
                shield_mode = getattr(self.overlay, "selected_shield_mode", 1) or 1
                if shield_mode != self._last_shield_mode:
                    self._last_shield_mode = shield_mode
                    self._shield_eta_seconds = None
                    self._shield_snapshot = None
                    self._shield_last_ts = time.monotonic()
                now = time.monotonic()
                if now - self._last_analyze >= self.analyze_interval:
                    info = analyze_skills(self.window, save_debug=not self._debug_saved)
                    if not self._debug_saved and info.region:
                        self._debug_saved = True
                    self.last_region = info.region
                    if _is_valid_experience(info.experience):
                        self.last_experience = info.experience
                    if _is_valid_level(info.level):
                        self.last_level = info.level
                    self.last_skills = info.skills or {}
                    self._last_analyze = now
                    # reset ETA timestamps on fresh read
                    self._eta_last_ts = now

                if now - self._last_emit >= self.emit_interval:
                    self._update_status()
                    self._last_emit = now
            except Exception:
                pass
            finally:
                self._stop_event.wait(self.tick_interval)
