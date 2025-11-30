from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
import re

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageOps

from .capture import capture_full_window
from .client_window import WindowInfo
from .ocr_utils import TEMPLATES_DIR, normalize_bw, read_with_templates
from .skills_parser import (
    SKILL_ALIASES,
    extract_skills_from_data,
    is_valid_experience,
    is_valid_level,
    parse_skill_lines,
)
from config import (
    MANUAL_EXPERIENCE_OFFSET,
    MANUAL_LEVEL_OFFSET,
    MANUAL_REGION,
    SAVE_DEBUG_CROPS,
    DEBUG_EXP_PATH,
    DEBUG_LEVEL_PATH,
)

HEADER_HEIGHT = 18
PANEL_WIDTH = 220
PANEL_HEIGHT = 200
EXPERIENCE_LABEL_BOX = (10, HEADER_HEIGHT + 4, 70, 16)
LEVEL_LABEL_BOX = (10, HEADER_HEIGHT + 24, 70, 16)

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
    skills: Dict[str, str]


def _load_skills_header_template() -> Optional[np.ndarray]:
    global _SKILLS_HEADER_TEMPLATE
    if _SKILLS_HEADER_TEMPLATE is not None:
        return _SKILLS_HEADER_TEMPLATE
    path = TEMPLATES_DIR / "skills.png"
    if not path.exists():
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    _SKILLS_HEADER_TEMPLATE = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    return _SKILLS_HEADER_TEMPLATE


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
    x0 = max(0, label.x + label.width + 2)
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
    if SAVE_DEBUG_CROPS:
        try:
            region.save(debug_path)
        except Exception:
            pass
    region = normalize_bw(region, threshold=170)
    return read_with_templates(region)


def _ocr_value_region(image: Image.Image, label: Optional[Region], debug_path: str) -> Optional[str]:
    if not label:
        return None
    region = _extract_value_region(image, label)
    if region is None:
        return None
    if SAVE_DEBUG_CROPS:
        try:
            region.save(debug_path)
        except Exception:
            pass
    region = ImageOps.autocontrast(region.convert("L"))
    region = normalize_bw(region, threshold=170)
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
    return match.group(1).replace(" ", "").replace(".", ",")


def _clean_level(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    match = re.search(r"(\d+)", raw)
    return match.group(1) if match else raw


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
    crop_templates = normalize_bw(crop_templates, threshold=180)

    crop_ocr = ImageOps.autocontrast(crop.convert("L"))
    crop_ocr = normalize_bw(crop_ocr, threshold=150)

    exp_box = Region(*MANUAL_EXPERIENCE_OFFSET) if MANUAL_EXPERIENCE_OFFSET else Region(*EXPERIENCE_LABEL_BOX)
    lvl_box = Region(*MANUAL_LEVEL_OFFSET) if MANUAL_LEVEL_OFFSET else Region(*LEVEL_LABEL_BOX)

    exp_val = _read_value_with_templates(crop_templates, exp_box, DEBUG_EXP_PATH)
    lvl_val = _read_value_with_templates(crop_templates, lvl_box, DEBUG_LEVEL_PATH)

    if not is_valid_experience(exp_val):
        exp_val = _ocr_value_region(crop_ocr, exp_box, DEBUG_EXP_PATH)
    if not is_valid_level(lvl_val):
        lvl_val = _ocr_value_region(crop_ocr, lvl_box, DEBUG_LEVEL_PATH)

    experience_clean = _clean_experience(exp_val)
    level_clean = _clean_level(lvl_val)

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
        skills.update(extract_skills_from_data(crop, crop_ocr, ocr_data))
    for variant in text_variants:
        parsed = parse_skill_lines(variant)
        for key, val in parsed.items():
            if val and key not in skills:
                skills[key] = val

    if save_debug:
        try:
            debug_dir = Path("debug_skills")
            debug_dir.mkdir(exist_ok=True)
            crop.save(debug_dir / "skills_panel_full.png")
            crop_ocr.save(debug_dir / "skills_panel_ocr.png")
        except Exception:
            pass

    return SkillsInfo(
        region=region,
        experience=experience_clean,
        level=level_clean,
        skills=skills,
    )
