from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageOps

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates" / "skills"
_TEMPLATE_CACHE: Dict[str, Image.Image] = {}
_DIGIT_TEMPLATES_NP: Optional[dict[str, np.ndarray]] = None


def binarize(img: Image.Image, threshold: int = 128) -> Image.Image:
    return img.convert("L").point(lambda x: 0 if x < threshold else 255)


def normalize_bw(img: Image.Image, threshold: int = 170) -> Image.Image:
    """Zapewnia czarny znak na białym tle, odwraca jeżeli trzeba."""
    binar = binarize(img, threshold=threshold)
    histogram = binar.histogram()
    if histogram[0] > histogram[255]:
        binar = ImageOps.invert(binar)
    return binar


def segment_glyphs(line_img: Image.Image) -> list[Image.Image]:
    """Prosta segmentacja znaków po pustych kolumnach."""
    line = binarize(line_img, threshold=200)
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
        if bbox:
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
            img = normalize_bw(img, threshold=170)
            templates[key] = img
        except Exception:
            continue
    _TEMPLATE_CACHE = templates
    return templates


def read_with_templates(value_img: Image.Image) -> Optional[str]:
    templates = _load_templates()
    if not templates:
        return None
    glyphs = segment_glyphs(value_img)
    if not glyphs:
        return None
    chars: list[str] = []
    for glyph in glyphs:
        best_char = None
        best_score = None
        for char, tmpl in templates.items():
            resized = glyph.resize(tmpl.size, Image.LANCZOS)
            diff = ImageChops.difference(resized, tmpl)
            score = sum(diff.getdata())
            if best_score is None or score < best_score:
                best_score = score
                best_char = char
        if best_char is None:
            return None
        chars.append(best_char)
    return "".join(chars)


def _load_digit_templates_np() -> dict[str, np.ndarray]:
    global _DIGIT_TEMPLATES_NP
    if _DIGIT_TEMPLATES_NP is not None:
        return _DIGIT_TEMPLATES_NP
    templates = _load_templates()
    digits = {ch: np.array(img, dtype=np.uint8) for ch, img in templates.items() if ch.isdigit()}
    _DIGIT_TEMPLATES_NP = digits
    return digits


def find_number_box(gray: np.ndarray, expected: str) -> Optional[tuple[int, int, int, int]]:
    """
    Zlokalizuj oczekiwany ciąg cyfr w obrazie binarnym przy pomocy dopasowania szablonów cyfr.
    """
    digits = _load_digit_templates_np()
    if not expected or not expected.isdigit() or not digits:
        return None
    first = expected[0]
    first_t = digits.get(first)
    if first_t is None:
        return None
    res = cv2.matchTemplate(gray, first_t, cv2.TM_CCOEFF_NORMED)
    candidates = []
    thresh = 0.55
    locs = np.where(res >= thresh)
    for y, x in zip(*locs):
        candidates.append((res[y, x], x, y))
    candidates.sort(reverse=True, key=lambda c: c[0])
    best = None
    best_score = -1
    for score, x, y in candidates[:50]:
        cur_x = x
        cur_y = y
        total_score = score
        prev_t = first_t
        for ch in expected[1:]:
            tmpl = digits.get(ch)
            if tmpl is None:
                total_score = -1
                break
            tw, th = tmpl.shape[1], tmpl.shape[0]
            prev_w = prev_t.shape[1] if prev_t is not None else tw
            best_local = None
            best_local_x = None
            for dx in range(-2, 6):
                search_x = cur_x + prev_w + dx
                roi = gray[cur_y : cur_y + th, search_x : search_x + tw]
                if roi.shape != tmpl.shape:
                    continue
                s = cv2.matchTemplate(roi, tmpl, cv2.TM_CCOEFF_NORMED)
                val = s[0][0] if s.size else 0
                if best_local is None or val > best_local:
                    best_local = val
                    best_local_x = search_x
            if best_local is None or best_local < thresh:
                total_score = -1
                break
            total_score += best_local
            cur_x = best_local_x
            prev_t = tmpl
        if total_score > best_score:
            last_t = digits.get(expected[-1])
            width = (cur_x - x) + (last_t.shape[1] if last_t is not None else 0)
            height = max(first_t.shape[0], last_t.shape[0] if last_t is not None else first_t.shape[0])
            best = (x, y, width, height)
            best_score = total_score
    return best


def ocr_digits_image(img: Image.Image) -> Optional[str]:
    img = ImageOps.autocontrast(img.convert("L"))
    img = normalize_bw(img, threshold=170)
    return read_with_templates(img)
