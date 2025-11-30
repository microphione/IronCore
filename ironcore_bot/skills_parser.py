from __future__ import annotations

import re
from typing import Dict, Optional

from PIL import Image, ImageOps
import pytesseract

from .ocr_utils import normalize_bw, read_with_templates

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


def _normalize_skill_value(raw: str) -> str:
    m = re.search(r"^\s*(\d+)\s*(?:\(\s*([0-9]+)\s*%?\s*\))?\s*$", raw or "")
    if m:
        base = m.group(1)
        pct = m.group(2)
        if pct:
            return f"{base} ({pct}%)"
        return base
    base = re.search(r"(\d+)", raw or "")
    pct = re.search(r"\((\d+)", raw or "")
    if base and pct:
        return f"{base.group(1)} ({pct.group(1)}%)"
    if base:
        return base.group(1)
    return (raw or "").strip()


def parse_skill_value(raw: str) -> tuple[Optional[int], Optional[int]]:
    normalized = _normalize_skill_value(raw or "")
    m = re.search(r"(\d+)\s*\(\s*([0-9]+)\s*%\s*\)", normalized)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)", normalized)
    if m:
        return int(m.group(1)), None
    return None, None


def parse_skill_lines(text: str) -> Dict[str, str]:
    results: Dict[str, str] = {}
    for line in text.splitlines():
        lower = line.lower()
        for alias, key in SKILL_ALIASES.items():
            if alias in lower:
                match = re.search(r"(\d+\s*(?:\(\s*[^)]+\s*\))?)", line)
                if match:
                    results[key] = _normalize_skill_value(match.group(1).strip())
                break
    return results


def _expand_bbox(bbox: tuple[int, int, int, int], pad: int, max_w: int, max_h: int) -> tuple[int, int, int, int]:
    x, y, w, h = bbox
    x = max(0, x - pad)
    y = max(0, y - pad)
    w = min(max_w - x, w + 2 * pad)
    h = min(max_h - y, h + 2 * pad)
    return (x, y, max(1, w), max(1, h))


def extract_skills_from_data(
    crop: Image.Image,
    crop_ocr: Image.Image,
    data: dict,
) -> Dict[str, str]:
    lines: dict[tuple[int, int, int], dict[str, object]] = {}
    for idx, text in enumerate(data.get("text", [])):
        if not text or not text.strip():
            continue
        key = (data["block_num"][idx], data["par_num"][idx], data["line_num"][idx])
        x, y, w, h = data["left"][idx], data["top"][idx], data["width"][idx], data["height"][idx]
        entry = lines.setdefault(key, {"words": [], "boxes": []})
        entry["words"].append(text)
        entry["boxes"].append((x, y, w, h))

    results: Dict[str, str] = {}
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
        value_boxes = boxes[start_idx + alias_len :]
        key = SKILL_ALIASES.get(matched_alias, matched_alias)
        if value_boxes:
            x0 = min(b[0] for b in value_boxes)
            y0 = min(b[1] for b in value_boxes)
            x1 = max(b[0] + b[2] for b in value_boxes)
            y1 = max(b[1] + b[3] for b in value_boxes)
            bbox = (x0, y0, x1 - x0, y1 - y0)
            if key == "shielding":
                bbox = _expand_bbox(bbox, pad=2, max_w=crop.width, max_h=crop.height)
            val = _ocr_box_value(crop, bbox) or _ocr_box_value(crop_ocr, bbox)
            if val:
                results[key] = _normalize_skill_value(val)
            continue

        # Fallback: spróbuj odczytać wartość w prostokącie na prawo od aliasu
        alias_boxes = boxes[start_idx : start_idx + alias_len]
        if not alias_boxes:
            continue
        right_start = max(b[0] + b[2] for b in alias_boxes)
        y_top = min(b[1] for b in alias_boxes)
        y_bottom = max(b[1] + b[3] for b in alias_boxes)
        box_h = max(1, y_bottom - y_top)
        bbox = (right_start + 2, y_top - 2, 80, box_h + 4)
        if key == "shielding":
            bbox = _expand_bbox(bbox, pad=2, max_w=crop.width, max_h=crop.height)
        val = _ocr_box_value(crop, bbox) or _ocr_box_value(crop_ocr, bbox)
        if val:
            results[key] = _normalize_skill_value(val)
    return results


def _ocr_box_value(image: Image.Image, bbox: tuple[int, int, int, int]) -> Optional[str]:
    x, y, w, h = bbox
    if w <= 0 or h <= 0:
        return None
    pad = 3  # lekkie poszerzenie o 2 px (łącznie) dla lepszego odczytu, m.in. shielding
    left = max(0, x - pad)
    top = max(0, y - pad)
    right = min(image.width, x + w + pad)
    bottom = min(image.height, y + h + pad)
    if right <= left or bottom <= top:
        return None
    region = image.crop((left, top, right, bottom))
    region = ImageOps.autocontrast(region.convert("L"))
    region = normalize_bw(region, threshold=170)
    text = pytesseract.image_to_string(
        region,
        config="--psm 7 --oem 1 -c tessedit_char_whitelist=0123456789()% -c user_defined_dpi=220",
    ).strip()
    return text or None


def is_valid_experience(val: Optional[str]) -> bool:
    return bool(val and re.fullmatch(r"[0-9][0-9,\.]*", val))


def is_valid_level(val: Optional[str]) -> bool:
    return bool(val and re.fullmatch(r"\d+(\s*\(\s*\d+%?\s*\))?", val))
