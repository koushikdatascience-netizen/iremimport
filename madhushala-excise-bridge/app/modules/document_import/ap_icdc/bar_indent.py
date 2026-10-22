"""AP BAR INDENT parser, deliberately distinct from 10-column ICDC invoice.

11 columns/12 vertical borders: Sl, Product Code, Brand Code, Description,
Product Type, Size Code, Pack Qty/Size, Pack Type, Cases, Issue Price, Amount.
"""
from __future__ import annotations
import re
from decimal import Decimal
from .extract import table_rows
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
    if not sl.isdigit() or not BRAND.fullmatch(brand):
        return None
    cases_text = re.sub(r"[^0-9]", "", values[8].upper().replace("I","1").replace("O","0"))
    cases = int(cases_text) if cases_text else None
    price, total = money(values[9]), money(values[10])
    flags = []
    if cases is None or price is None or total is None or Decimal(cases)*price != total:
        flags.append("manual_check")
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
    output=[]
    for row in table_rows(boxes,columns,horizontal_lines,page,image_height=height):
        item=parse_bar_row(row["cells"],page,min(row["confidences"] or [0]))
        if item:
            output.append(item)
    return output

def validate_bar_items(items):
    serials=[item["sl_no"] for item in items]
    checks={"serial_sequence":bool(items) and serials==list(range(1,len(items)+1)),
            "row_arithmetic":all("manual_check" not in item["flags"] for item in items)}
    return {"status":"REVIEW_REQUIRED" if all(checks.values()) else "FAILED",
            "checks":checks,"rows_needing_review":[i["sl_no"] for i in items if i["flags"]],
            "note":"Bar Indent is not an ICDC tax invoice; purchase saving remains disabled pending document-specific business approval."}
