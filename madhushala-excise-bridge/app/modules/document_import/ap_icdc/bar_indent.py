"""AP BAR INDENT parser, deliberately distinct from 10-column ICDC invoice.

11 columns/12 vertical borders: Sl, Product Code, Brand Code, Description,
Product Type, Size Code, Pack Qty/Size, Pack Type, Cases, Issue Price, Amount.
"""
from __future__ import annotations
import re
from decimal import Decimal
from .extract import cell_text
from .validate import money

COLUMNS = ("sl_no", "product_code", "brand_code", "brand_name", "product_type",
           "size_code", "pack_qty_size", "pack_type", "qty_cases", "issue_price", "amount")
BRAND = re.compile(r"^[A-Z]?\d{3,4}$", re.I)

def select_bar_columns(page_grids):
    choices = [xs for _, xs, _ in page_grids if len(xs) == 12]
    return choices[len(choices)//2] if choices else None

def parse_bar_row(values, page, confidence=1):
    values = list(values)
    if len(values) != 11:
        return None
    sl = re.sub(r"\D", "", values[0])
    brand = re.sub(r"\s+", "", values[2]).upper()
    if not sl.isdigit() or int(sl) <= 0 or not BRAND.fullmatch(brand):
        return None
    cases_text = re.sub(r"[^0-9]", "", values[8].upper().replace("I","1").replace("O","0"))
    cases = int(cases_text) if cases_text else None
    price, total = money(values[9]), money(values[10])
    flags = []
    if cases is None or cases <= 0 or price is None or total is None or Decimal(cases)*price != total:
        flags.append("manual_check")
    if not " ".join(values[3].split()):
        flags.append("missing_brand_name")
    if confidence < .8:
        flags.append("low_ocr_confidence")
    pack_shape = re.search(r"(\d+)\s*/\s*(\d+)\s*ML", values[6].upper())
    return {
        "sl_no": int(sl), "product_code":values[1].strip(), "brand_number":brand,
        "brand_name":" ".join(values[3].split()), "product_type":values[4].strip(),
        "size_code":values[5].strip(), "pack_qty_size":values[6].strip(),
        "pack_type":values[7].strip(), "size_ml":int(pack_shape.group(2)) if pack_shape else None,
        "bottles_per_case":int(pack_shape.group(1)) if pack_shape else None,
        "qty_cases":cases, "qty_bottles":0,
        "issue_price":float(price) if price is not None else None,
        "total":float(total) if total is not None else None,
        "pages":[page], "flags":flags,
    }

def parse_bar_rows(boxes, columns, horizontal_lines, page, height):
    """Spatially assign OCR text to all 11 Bar Indent columns."""
    if len(columns) != 12:
        return []
    lines = sorted(horizontal_lines)
    if len(lines) < 2:
        return []
    import bisect
    cells = {}
    for box in boxes:
        xc = (box["x0"] + box["x1"]) / 2
        yc = (box["y0"] + box["y1"]) / 2
        col = bisect.bisect_right(columns, xc) - 1
        row = bisect.bisect_right(lines, yc) - 1
        if 0 <= col < 11 and 0 <= row < len(lines)-1:
            cells.setdefault((row,col), []).append(box)
    output = []
    for row in range(len(lines)-1):
        values = [cell_text(cells.get((row,col),[])) for col in range(11)]
        scores = [b["confidence"] for col in range(11) for b in cells.get((row,col),[])]
        item = parse_bar_row(values,page,min(scores or [0]))
        if item:
            output.append(item)
    return output

def validate_bar_items(items, *, expected_total_cases=None, expected_total_amount=None):
    """Fail closed if invoice-page summary and product rows cannot be reconciled."""
    serials = [item["sl_no"] for item in items]
    amounts = [money(item.get("total")) for item in items]
    cases = [item.get("qty_cases") for item in items]
    checks = {
        "serial_sequence": bool(items) and serials == list(range(1, len(items) + 1)),
        "row_arithmetic": bool(items) and all("manual_check" not in x["flags"] for x in items),
        "size_and_identity": bool(items) and all(x.get("size_ml") and x.get("brand_name") and x.get("brand_number") for x in items),
        "case_quantity": expected_total_cases is not None and all(x is not None for x in cases) and sum(cases) == expected_total_cases,
        "total_amount": expected_total_amount is not None and all(x is not None for x in amounts) and sum(amounts, Decimal(0)) == money(expected_total_amount),
    }
    needs_review = sorted({x["sl_no"] for x in items if x.get("flags")})
    status = "FAILED" if not all(checks.values()) else ("REVIEW_REQUIRED" if needs_review else "PASSED")
    return {"status":status, "checks":checks, "rows_needing_review":needs_review,
            "expected_cases":expected_total_cases, "expected_amount":float(money(expected_total_amount)) if money(expected_total_amount) is not None else None,
            "note":"Bar Indent is an order document, not a verified purchase invoice; saving requires explicit operator confirmation and upstream acceptance."}

def parse_bar_summary(ocr_boxes):
    """Locate Total label and adjacent cases/amount by spatial alignment.

    Ignore unrelated subtotal-looking numbers; ambiguous evidence returns None.
    """
    import re
    labels = [b for b in ocr_boxes if re.fullmatch(r"total\s*:?", b["text"].strip(), re.I)]
    for label in reversed(labels):
        yc = (label["y0"] + label["y1"]) / 2
        neighbors = [b for b in ocr_boxes if b["x0"] > label["x1"] and abs((b["y0"]+b["y1"])/2-yc) < 17]
        neighbors.sort(key=lambda b:b["x0"])
        nums = [b["text"].strip().replace(",","") for b in neighbors if re.fullmatch(r"\d+(?:\.\d{1,2})?",b["text"].strip().replace(",",""))]
        if len(nums) >= 2:
            cases = next((int(n) for n in nums if re.fullmatch(r"\d+",n)), None)
            amount = money(nums[-1])
            if cases is not None and amount is not None and amount >= 0:
                return {"total_cases":cases, "issue_price":float(amount)}
    return {"total_cases":None, "issue_price":None}
