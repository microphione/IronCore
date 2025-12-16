from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import traceback
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
PANEL_HEIGHT = 230  # +30px to include lower rows (club/shield)
EXPERIENCE_LABEL_BOX = (10, HEADER_HEIGHT + 4, 70, 16)
LEVEL_LABEL_BOX = (10, HEADER_HEIGHT + 24, 70, 16)

_SKILLS_HEADER_TEMPLATE: Optional[np.ndarray] = None
LOG_EXP_DEBUG = os.getenv("IRONCORE_EXP_DEBUG", "0").lower() not in ("0", "false", "no")
_TESSERACT_INITIALIZED = False


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


def _log(msg: str) -> None:
    if LOG_EXP_DEBUG:
        print(f"[exp] {msg}", flush=True)


def _init_tesseract() -> None:
    global _TESSERACT_INITIALIZED
    if _TESSERACT_INITIALIZED:
        return
    _TESSERACT_INITIALIZED = True

    env_path = os.getenv("IRONCORE_TESSERACT") or os.getenv("TESSERACT_CMD")
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    found = shutil.which("tesseract")
    if found:
        candidates.append(Path(found))
    candidates.append(Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
    candidates.append(Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"))

    for cand in candidates:
        if cand and cand.exists():
            pytesseract.pytesseract.tesseract_cmd = str(cand)
            _log(f"using tesseract at {cand}")
            return
    _log("tesseract executable not found; set IRONCORE_TESSERACT with full path")


def _find_skills_anchor(full_img: Image.Image) -> Optional[Region]:
    tmpl = _load_skills_header_template()
    if tmpl is None:
        _log("skills.png template not found")
        return None
    gray = cv2.cvtColor(np.array(full_img), cv2.COLOR_RGB2GRAY)
    res = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val < 0.6:
        _log(f"skills header match too low: {max_val:.3f}")
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
        _log("label missing for template read")
        return None
    region = _extract_value_region(crop, label)
    if region is None:
        _log("value region empty for template read")
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
        _log("label missing for OCR read")
        return None
    region = _extract_value_region(image, label)
    if region is None:
        _log("value region empty for OCR read")
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
    try:
        _init_tesseract()
        _log(f"analyze start hwnd={window.hwnd} size={window.width}x{window.height}")
        full_img = capture_full_window(window.hwnd)
        _log(f"full capture size {full_img.size}")

        if MANUAL_REGION:
            left, top, width, height = MANUAL_REGION
            region = Region(left, top, width, height)
            _log(f"using MANUAL_REGION {region}")
        else:
            anchor = _find_skills_anchor(full_img)
            if not anchor:
                _log("skills anchor not found, aborting analyze")
                return SkillsInfo(region=None, experience=None, level=None, skills={})
            region = Region(anchor.x, anchor.y, PANEL_WIDTH, PANEL_HEIGHT)
            _log(f"anchor at ({anchor.x},{anchor.y}) -> region {region}")

        region = _normalize_region(region, window)
        _log(f"normalized region {region}")
        crop_left, crop_top = region.x, region.y
        crop = full_img.crop((crop_left, crop_top, crop_left + region.width, crop_top + region.height))
        _log(f"crop size {crop.size}")

        crop_templates = ImageOps.autocontrast(crop.convert("L"))
        crop_templates = normalize_bw(crop_templates, threshold=180)

        crop_ocr = ImageOps.autocontrast(crop.convert("L"))
        crop_ocr = normalize_bw(crop_ocr, threshold=150)

        exp_box = Region(*MANUAL_EXPERIENCE_OFFSET) if MANUAL_EXPERIENCE_OFFSET else Region(*EXPERIENCE_LABEL_BOX)
        lvl_box = Region(*MANUAL_LEVEL_OFFSET) if MANUAL_LEVEL_OFFSET else Region(*LEVEL_LABEL_BOX)
        _log(f"exp_box={exp_box} lvl_box={lvl_box}")

        exp_val_tpl = _read_value_with_templates(crop_templates, exp_box, DEBUG_EXP_PATH)
        lvl_val_tpl = _read_value_with_templates(crop_templates, lvl_box, DEBUG_LEVEL_PATH)
        _log(f"template exp={exp_val_tpl!r} lvl={lvl_val_tpl!r}")

        exp_val = exp_val_tpl
        lvl_val = lvl_val_tpl

        if not is_valid_experience(exp_val):
            _log(f"exp template invalid -> fallback OCR, current={exp_val!r}")
            exp_val = _ocr_value_region(crop_ocr, exp_box, DEBUG_EXP_PATH)
            _log(f"ocr exp={exp_val!r}")
        if not is_valid_level(lvl_val):
            _log(f"lvl template invalid -> fallback OCR, current={lvl_val!r}")
            lvl_val = _ocr_value_region(crop_ocr, lvl_box, DEBUG_LEVEL_PATH)
            _log(f"ocr lvl={lvl_val!r}")

        experience_clean = _clean_experience(exp_val)
        level_clean = _clean_level(lvl_val)
        _log(f"cleaned exp={experience_clean!r} lvl={level_clean!r}")

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
            _log(f"image_to_data words={len(ocr_data.get('text', [])) if ocr_data else 0}")
        except Exception as exc:
            _log(f"image_to_data failed: {exc}")
            ocr_data = None
        if ocr_data:
            extracted = extract_skills_from_data(crop, crop_ocr, ocr_data)
            skills.update(extracted)
            _log(f"skills from data: {extracted}")
        for variant in text_variants:
            parsed = parse_skill_lines(variant)
            for key, val in parsed.items():
                if val and key not in skills:
                    skills[key] = val
        if skills:
            _log(f"skills parsed merged: {skills}")
        else:
            _log("no skills parsed")

        if save_debug:
            try:
                debug_dir = Path("debug_skills")
                debug_dir.mkdir(exist_ok=True)
                crop.save(debug_dir / "skills_panel_full.png")
                crop_ocr.save(debug_dir / "skills_panel_ocr.png")
            except Exception as exc:
                _log(f"debug save failed: {exc}")

        return SkillsInfo(
            region=region,
            experience=experience_clean,
            level=level_clean,
            skills=skills,
        )
    except Exception as exc:
        _log(f"analyze exception: {exc}")
        _log(traceback.format_exc())
        return SkillsInfo(region=None, experience=None, level=None, skills={})
