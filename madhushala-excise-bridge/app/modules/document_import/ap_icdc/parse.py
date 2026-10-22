"""Deterministic AP ICDC cell parser; never use an LLM to read digits."""
from __future__ import annotations
import re
from .validate import money, recover_cases

BRAND = re.compile(r"^[A-Z]?\d{3,4}$")
RATE = re.compile(r"([\d,.]+)\s*/\s*([\d,.]+)")
TYPES = ("WHISKY", "BRANDY", "VODKA", "BEER", "WINE", "RTD", "RUM", "GIN")
CUT = re.compile(r"IMFL\s*/\s*FL|Particulars|Invoice Qty", re.I)

def digits(value):
    raw = str(value or "").upper().translate(str.maketrans({"I": "1", "L": "1", "|": "1", "O": "0"}))
    return re.sub(r"[^0-9]", "", raw)

def number(value):
    s = digits(value)
    return int(s) if s else None

def dec(value):
    s = str(value or "").replace(",", "").strip()
    # Correct only unambiguous OCR character confusions; never infer missing digits.
    s = s.upper().replace("O", "0").replace("|", "1")
    return money(s)

def clean_name(value):
    return " ".join(CUT.split(str(value or ""), 1)[0].split()).strip()

def merge_continuation(rows):
    """Append name-only orphan row to preceding page's last item."""
    out = []
    for row in rows:
        if not str(row.get("sl_no") or "").strip() and not str(row.get("brand_number") or "").strip() and clean_name(row.get("brand_name")):
            if out and row.get("continuation_position") == "page_start" and out[-1].get("page_open_end"):
                out[-1]["brand_name"] = clean_name(out[-1].get("brand_name", "") + " " + row["brand_name"])
                out[-1]["pages"] = sorted(set(out[-1].get("pages", []) + row.get("pages", [])))
                out[-1].setdefault("flags", []).append("page_boundary_name_merge")
            else:
                row.setdefault("flags", []).append("unmatched_continuation")
                out.append(row)
        else:
            out.append(row)
    return out

def parse_cells(cells, page, confidences=None, name_alternative=None):
    """Parse exactly ten positional columns; return unverified row candidate."""
    c = list(cells)[:10] + [""] * max(0, 10 - len(cells))
    serial = number(c[0])
    brand_number = re.sub(r"\s", "", c[1]).upper()
    flags = []
    if not BRAND.fullmatch(brand_number):
        flags.append("invalid_brand_number")
    category = re.sub(r"[^A-Z]", "", c[3].upper())
    pack = re.sub(r"[^A-Z]", "", c[4].upper())
    if category not in TYPES and len(category) > 1 and category[-1:] in "GCP":
        category, pack = category[:-1], category[-1]
    if category not in TYPES:
        flags.append("invalid_product_type")
    pack = pack[-1:] if pack else ""
    rate = RATE.search(c[8].replace("O", "0"))
    case_rate = dec(rate.group(1)) if rate else None
    btl_rate = dec(rate.group(2)) if rate else None
    if not rate:
        flags.append("missing_rate_pair")
    original_cases = number(c[6])
    total = dec(c[9])
    new_cases, recovered = recover_cases(original_cases, case_rate, total)
    flags.extend(recovered)
    if confidences and min(confidences) < 0.8:
        flags.append("low_ocr_confidence")
    name = clean_name(c[2])
    if name_alternative and re.sub(r"\s+", "", name).casefold() == re.sub(r"\s+", "", name_alternative).casefold():
        name = clean_name(name_alternative)
    if not name:
        flags.append("missing_brand_name")
    return {
        "sl_no": serial, "brand_number": brand_number, "brand_name": name,
        "product_type": category, "pack_type": pack, "size_ml": number(c[5]),
        "qty_cases": new_cases, "qty_bottles": number(c[7]) or 0,
        "case_rate": float(case_rate) if case_rate is not None else None,
        "btl_rate": float(btl_rate) if btl_rate is not None else None,
        "total": float(total) if total is not None else None,
        "pages": [page], "flags": list(dict.fromkeys(flags)),
        "audit": {"ocr_qty_cases": original_cases, "ocr_cells": c},
    }
