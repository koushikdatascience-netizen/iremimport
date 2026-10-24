"""Experimental, isolated Jharkhand scanned-invoice extractor.

Reuses AP's PyMuPDF/OpenCV/RapidOCR primitives but NOT its 10-cell parser.
No production import/save routing. Always fail closed on missing/uncertain data.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from app.modules.document_import.ap_icdc.extract import render_pages, grid, ocr_boxes
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct

# Jharkhand State Beverages printed invoice: nine columns separated by ten rules.
COLS = ("serial", "brand_group", "label", "unit", "cases", "wholesale",
        "taxable", "vat", "aed", "vat_on_aed", "amount")
# Table can have 11 semantic columns (serial, brand, label, unit, qty,
# wholesale, taxable, VAT, AED, VAT on AED, amount), so demand 12 boundaries.
EXPECTED_COLUMNS = 11
MONEY = re.compile(r"^\d{1,3}(?:,?\d{2,3})*(?:\.\d{2})?$")


def _money(value: str) -> Decimal:
    raw = value.replace(",", "").strip()
    if not raw or not re.fullmatch(r"\d+(?:\.\d{2})?", raw):
        raise ValueError(f"Invalid amount {value!r}")
    return Decimal(raw)


def _qty(value: str) -> tuple[int, int]:
    # Printed source uses CASES.LOOSE, not fractional cases.
    match = re.fullmatch(r"(\d+)\.(\d{2})", value.strip())
    if not match:
        raise ValueError(f"Ambiguous Jharkhand quantity {value!r}")
    return int(match.group(1)), int(match.group(2))


def parse_cells(rows: list[list[str]]) -> list[dict]:
    """Strict pure parser for one or more geometric OCR table rows."""
    result = []
    for cells in rows:
        if len(cells) != EXPECTED_COLUMNS:
            raise ValueError("Jharkhand invoice requires eleven semantic columns")
        serial_text = cells[0].strip()
        if not re.fullmatch(r"\d+", serial_text):
            continue  # header/footer; sequential serials checked below
        serial = int(serial_text)
        name = " ".join(cells[2].split())
        ml_match = re.search(r"(\d{2,4})\s*M\.?L\.?", cells[3], re.I)
        if not name or not ml_match:
            raise ValueError(f"Row {serial}: missing name or unit")
        cases, loose = _qty(cells[4])
        if cases + loose == 0:
            raise ValueError(f"Row {serial}: zero quantity")
        commercial = {key: _money(cells[i]) for key, i in (
            ("wholesale", 5), ("taxable", 6), ("vat", 7),
            ("aed", 8), ("vatOnAed", 9), ("amount", 10))}
        result.append({"serial": serial, "name": name, "ml": int(ml_match.group(1)),
                       "box": cases, "loose": loose, **{k: str(v) for k, v in commercial.items()}})
    if not result or [r["serial"] for r in result] != list(range(1, len(result) + 1)):
        raise ValueError("Jharkhand invoice rows missing, repeated, or out of order")
    return result


def _cells(boxes: list[dict], xs: list[int], ys: list[int]) -> list[list[str]]:
    import numpy as np
    if len(xs) != EXPECTED_COLUMNS + 1 or len(ys) < 2:
        raise ValueError("Jharkhand table has incomplete column/row grid")
    rows = []
    for lo, hi in zip(ys, ys[1:]):
        cells = [[] for _ in range(EXPECTED_COLUMNS)]
        for box in boxes:
            cx = (box["x0"] + box["x1"]) / 2
            cy = (box["y0"] + box["y1"]) / 2
            if not lo < cy < hi:
                continue
            ci = int(np.searchsorted(xs, cx, side="right") - 1)
            if 0 <= ci < EXPECTED_COLUMNS:
                cells[ci].append(box)
        rows.append([" ".join(b["text"] for b in sorted(parts, key=lambda p: (p["y0"], p["x0"])))
                     for parts in cells])
    return rows


# Anchor columns to the actual Jharkhand invoice layout, not AP grid rules.
# Fractions measured from the page width. Fail closed on nonmatching layouts.
COLUMN_EDGES = (.033, .064, .126, .333, .391, .438, .513, .601, .660, .732, .793, .907)


def column_anchored_rows(boxes: list[dict], width: int, height: int) -> list[list[str]]:
    """Segment source rows by left serial anchors, then text by column centers."""
    if width <= 0 or height <= 0 or len(COLUMN_EDGES) != EXPECTED_COLUMNS + 1:
        raise ValueError("Invalid Jharkhand page geometry")
    # Invoice grid starts below the 25% header band on page 1; page 2
    # begins with the continuation table. Never count handwritten footer ink.
    anchors = []
    for b in boxes:
        cx = (b["x0"] + b["x1"]) / (2 * width)
        cy = (b["y0"] + b["y1"]) / (2 * height)
        value = str(b["text"]).strip()
        if COLUMN_EDGES[0] <= cx < COLUMN_EDGES[1] and .04 < cy < .80 and re.fullmatch(r"\d{1,2}", value):
            anchors.append((int(value), (b["y0"] + b["y1"]) / 2))
    anchors.sort(key=lambda a: a[1])
    if not anchors:
        raise ValueError("No trusted Jharkhand serial-row anchors")
    if len({v for v, _ in anchors}) != len(anchors):
        raise ValueError("Duplicate Jharkhand serial anchors")
    # Ignore upper header text by requiring monotonically consecutive rows.
    if any(anchors[i+1][0] != anchors[i][0] + 1 for i in range(len(anchors)-1)):
        raise ValueError("Jharkhand serial anchors missing or out of order")
    rows = []
    for idx, (serial, top) in enumerate(anchors):
        lower = (top + anchors[idx+1][1]) / 2 if idx + 1 < len(anchors) else min(height * .82, top + height * .026)
        upper = (top + anchors[idx-1][1]) / 2 if idx else max(0, top - height * .01)
        columns = [[] for _ in range(EXPECTED_COLUMNS)]
        for b in boxes:
            cy = (b["y0"] + b["y1"]) / 2
            cx = (b["x0"] + b["x1"]) / (2 * width)
            if not upper <= cy < lower:
                continue
            column = next((i for i in range(EXPECTED_COLUMNS) if COLUMN_EDGES[i] <= cx < COLUMN_EDGES[i+1]), None)
            if column is not None:
                columns[column].append(b)
        values = [" ".join(v["text"] for v in sorted(col, key=lambda v:(round(v["y0"]/12), v["x0"])))
                  for col in columns]
        values[0] = str(serial)
        rows.append(values)
    return rows


def extract(pdf: Path, engine) -> dict:
    """Run strictly local OCR; never call the live Purchase Save endpoint."""
    all_rows = []
    diagnostics = []
    for page_no, image in render_pages(pdf, dpi=250):
        xs, ys = grid(image)
        boxes = ocr_boxes(engine, image)
        if not boxes:
            raise ValueError(f"Page {page_no}: no OCR text detected")
        if len(xs) == EXPECTED_COLUMNS + 1 and len(ys) >= 2:
            rows = _cells(boxes, xs, ys)
            method = "grid"
        else:
            rows = column_anchored_rows(boxes, image.shape[1], image.shape[0])
            method = "column-anchors"
        # Header and continuation pages may start on a non-1 serial.
        page_rows = []
        for cells in rows:
            if re.fullmatch(r"\d+", cells[0].strip()):
                page_rows.append(cells)
        all_rows.extend(page_rows)
        diagnostics.append({"page": page_no, "ocrBoxes": len(boxes), "sourceRows": len(page_rows), "segmentation": method})
    products = parse_cells(all_rows)
    return {"status": "REVIEW_REQUIRED", "validated": False,
            "reason": "OCR rows need printed invoice financial and Form-T reconciliation",
            "items": products, "diagnostics": diagnostics}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    from rapidocr_onnxruntime import RapidOCR
    result = extract(args.pdf, RapidOCR())
    args.output.write_text(json.dumps(result, indent=2))
    print(f"{result['status']}: {len(result['items'])} rows; purchase disabled")


if __name__ == "__main__":
    main()
