from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageChops, ImageOps

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates" / "skills"
_DIGIT_TEMPLATES_PIL: dict[str, Image.Image] = {}
_DIGIT_TEMPLATES_NP: Optional[dict[str, np.ndarray]] = None
_SLASH_TEMPLATE: Optional[np.ndarray] = None
_HP_FRAG_TEMPLATE: Optional[np.ndarray] = None

# Prosty offset względem znalezionego fragmentu HP/Mana: (dx, dy, d_width, d_height)
HP_REGION_OFFSETS = (100, 0, 0, 0)


def _normalize_bw(img: Image.Image, threshold: int = 170) -> Image.Image:
    binar = img.convert("L").point(lambda x: 0 if x < threshold else 255)
    hist = binar.histogram()
    if hist[0] > hist[255]:
        binar = ImageOps.invert(binar)
    return binar


def _segment_glyphs(line_img: Image.Image) -> list[Image.Image]:
    line = _normalize_bw(line_img, threshold=200)
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


def _load_digit_templates_pil() -> dict[str, Image.Image]:
    global _DIGIT_TEMPLATES_PIL
    if _DIGIT_TEMPLATES_PIL:
        return _DIGIT_TEMPLATES_PIL
    if not TEMPLATES_DIR.exists():
        return {}
    templates: dict[str, Image.Image] = {}
    for path in TEMPLATES_DIR.glob("*.png"):
        key = path.stem
        if not key.isdigit():
            continue
        try:
            img = Image.open(path).convert("L")
            img = ImageOps.autocontrast(img)
            img = _normalize_bw(img, threshold=170)
            templates[key] = img
        except Exception:
            continue
    _DIGIT_TEMPLATES_PIL = templates
    return templates


def _read_with_templates(value_img: Image.Image) -> Optional[str]:
    templates = _load_digit_templates_pil()
    if not templates:
        return None
    glyphs = _segment_glyphs(value_img)
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
    templates = _load_digit_templates_pil()
    digits = {ch: np.array(img, dtype=np.uint8) for ch, img in templates.items()}
    _DIGIT_TEMPLATES_NP = digits
    return digits


def _find_number_box(gray: np.ndarray, expected: str) -> Optional[tuple[int, int, int, int]]:
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


def _load_slash_template() -> Optional[np.ndarray]:
    global _SLASH_TEMPLATE
    if _SLASH_TEMPLATE is not None:
        return _SLASH_TEMPLATE
    path = TEMPLATES_DIR / "slash.png"
    if not path.exists():
        _SLASH_TEMPLATE = None
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        _SLASH_TEMPLATE = None
        return None
    img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    _SLASH_TEMPLATE = img
    return _SLASH_TEMPLATE


def _load_hp_fragment_template() -> Optional[np.ndarray]:
    global _HP_FRAG_TEMPLATE
    if _HP_FRAG_TEMPLATE is not None:
        return _HP_FRAG_TEMPLATE
    path = TEMPLATES_DIR / "hpmanafragment.png"
    if not path.exists():
        _HP_FRAG_TEMPLATE = None
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        _HP_FRAG_TEMPLATE = None
        return None
    img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
    _HP_FRAG_TEMPLATE = img
    return _HP_FRAG_TEMPLATE


def _ocr_digits(img: Image.Image) -> Optional[str]:
    img = ImageOps.autocontrast(img.convert("L"))
    img = _normalize_bw(img, threshold=170)
    return _read_with_templates(img)


def find_hp(
    full_img: Image.Image, level_str: Optional[str]
) -> tuple[Optional[int], Optional[int], Optional[tuple[int, int, int, int]], Optional[tuple[int, int, int, int]], Optional[tuple[int, int, int, int]]]:
    """
    Zwraca (current_hp, max_hp, left_box, right_box, slash_box).
    Każdorazowo wyznacza region względem znalezionego hpmanafragment.png i prostych offsetów (HP_REGION_OFFSETS).
    """
    tmpl = _load_slash_template()
    lvl_int = None
    if level_str:
        try:
            lvl_int = int("".join(ch for ch in level_str if ch.isdigit()))
        except Exception:
            lvl_int = None
    expected_max = 100 + (lvl_int * 5) if lvl_int is not None else None

    gray = cv2.cvtColor(np.array(full_img), cv2.COLOR_RGB2GRAY)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    _, gray_bin = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
    gray_bin = cv2.bitwise_not(gray_bin)

    max_box: Optional[tuple[int, int, int, int]] = None
    max_val_found: Optional[int] = None

    if expected_max is not None:
        frag = _load_hp_fragment_template()
        if frag is not None:
            res = cv2.matchTemplate(gray, frag, cv2.TM_CCOEFF_NORMED)
            _, mval, _, mloc = cv2.minMaxLoc(res)
            if mval >= 0.5:
                fx, fy = mloc
                fw, fh = frag.shape[1], frag.shape[0]
                dx, dy, dw, dh = HP_REGION_OFFSETS
                sx0 = max(0, fx + dx)
                sy0 = max(0, fy + dy)
                sx1 = min(gray_bin.shape[1], fx + fw + dw)
                sy1 = min(gray_bin.shape[0], fy + fh + dh)
                if sx1 > sx0 and sy1 > sy0:
                    sub_bin = gray_bin[sy0:sy1, sx0:sx1]
                    # debug: zapis obszaru
                    try:
                        dbg_dir = Path("debug_hp")
                        dbg_dir.mkdir(exist_ok=True)
                        full_img.crop((sx0, sy0, sx1, sy1)).save(dbg_dir / "hp_search_region.png")
                        Image.fromarray(sub_bin).save(dbg_dir / "hp_search_region_bin.png")
                    except Exception:
                        pass

                    box_roi = _find_number_box(sub_bin, str(expected_max))
                    if box_roi:
                        x, y, w, h = box_roi
                        max_box = (sx0 + x, sy0 + y, w, h)
                        max_val_found = expected_max
                    else:
                        sub_img = full_img.crop((sx0, sy0, sx1, sy1))
                        try:
                            data = pytesseract.image_to_data(
                                sub_img,
                                output_type=pytesseract.Output.DICT,
                                config="--psm 6 --oem 1 -c tessedit_char_whitelist=0123456789/ ",
                            )
                            for i, text in enumerate(data.get("text", [])):
                                txt = (text or "").strip()
                                if txt == str(expected_max):
                                    x0, y0, w0, h0 = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                                    max_box = (sx0 + x0, sy0 + y0, w0, h0)
                                    max_val_found = expected_max
                                    break
                            if max_box is None:
                                best_num = None
                                best_box = None
                                for i, text in enumerate(data.get("text", [])):
                                    txt = (text or "").strip()
                                    if txt.isdigit():
                                        val = int(txt)
                                        if best_num is None or val > best_num:
                                            best_num = val
                                            x0, y0, w0, h0 = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                                            best_box = (sx0 + x0, sy0 + y0, w0, h0)
                                if best_box is not None:
                                    max_box = best_box
                                    max_val_found = best_num
                        except Exception:
                            pass

    if max_box is None:
        return None, None, None, None, None

    slash_box = None
    if tmpl is not None:
        th, tw = tmpl.shape
        margin_x = max(40, max_box[2] * 2)
        margin_y = max(12, max_box[3] * 2)
        sx0 = max(0, max_box[0] - margin_x)
        sx1 = max_box[0] + max_box[2]
        sy0 = max(0, max_box[1] - margin_y // 2)
        sy1 = min(full_img.height, max_box[1] + max_box[3] + margin_y // 2)
        sub = gray[sy0:sy1, sx0:sx1]
        try:
            res = cv2.matchTemplate(sub, tmpl, cv2.TM_CCOEFF_NORMED)
            _, mval, _, mloc = cv2.minMaxLoc(res)
            if mval >= 0.4:
                slash_box = (sx0 + mloc[0], sy0 + mloc[1], tw, th)
        except Exception:
            slash_box = None

    gap = 6
    if slash_box:
        slash_x, slash_y, tw, th = slash_box
    else:
        tw, th = tmpl.shape if tmpl is not None else (6, max_box[3])
        slash_x = max(0, max_box[0] - tw - gap)
        slash_y = max(0, max_box[1] + (max_box[3] - th) // 2)
        slash_box = (slash_x, slash_y, tw, th)

    if slash_box:
        gap = max(gap, max(0, max_box[0] - (slash_box[0] + slash_box[2])))
    right_box = max_box

    left_w = right_box[2]
    left_h = right_box[3]
    left_x = max(0, slash_box[0] - gap - left_w)
    left_y = max(0, right_box[1])
    left_box = (left_x, left_y, left_w, left_h)
    right_box = max_box

    cur_val = None
    try:
        if left_box[2] > 4 and left_box[3] > 4:
            cur_crop = full_img.crop((left_box[0], left_box[1], left_box[0] + left_box[2], left_box[1] + left_box[3]))
            cur_text = _ocr_digits(cur_crop)
            if cur_text and cur_text.isdigit():
                cur_val = int(cur_text)
    except Exception:
        cur_val = None

    if max_val_found is None:
        try:
            max_crop = full_img.crop((right_box[0], right_box[1], right_box[0] + right_box[2], right_box[1] + right_box[3]))
            max_text = _ocr_digits(max_crop)
            if max_text and max_text.isdigit():
                max_val_found = int(max_text)
        except Exception:
            max_val_found = None

    final_max = max_val_found or expected_max
    return cur_val, final_max, left_box, right_box, slash_box
