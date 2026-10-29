"""Standalone, conservative AP ICDC OCR CLI. No production route enabled.

python -m app.modules.document_import.ap_icdc.main path/to/invoice.pdf -o extracted.json
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import time
from pathlib import Path
from threading import Lock
from .extract import render_pages, ocr_boxes, grid, choose_columns, table_rows
from .performance import ocr_page
from .bar_indent import select_bar_columns, parse_bar_rows, validate_bar_items, parse_bar_summary
from .parse import parse_cells, merge_continuation, BRAND, clean_name
from .validate import validate_document

FIELDS = ("issue_price", "cess", "additional_previlage_fee", "additional_retail_excise_tax",
          "previlage_fee_clubs", "icdc_value", "tcs", "challan_amount",
          "previous_credit_leftover", "sub_total", "less_this_invoice_value", "amount_credited")
LABEL_PATTERNS = {
    "issue_price": r"issue\s*price", "cess": r"\bcess\b",
    "additional_previlage_fee": r"additional\s*pr[eiv]+l[ea]ge\s*fee",
    "additional_retail_excise_tax": r"additional\s*retail\s*excise\s*tax",
    "previlage_fee_clubs": r"pr[eiv]+l[ea]ge\s*fee\s*for\s*clubs",
    "icdc_value": r"icdc\s*value", "tcs": r"\btcs\b",
    "challan_amount": r"challan\s*(?:no\.?\s*&\s*)?amount",
    "previous_credit_leftover": r"previous\s*credit",
    "sub_total": r"sub\s*total",
    # Last financial row is broken by a physical page boundary: page 5 has
    # "(-)Less this" and its printed value; page 6 says "Invoice Value".
    "less_this_invoice_value": r"\b(?:less\s*this|less\s*invoice\s*value)\b",
    "amount_credited": r"amount\s*credited",
}
AMOUNT = re.compile(r"(?<![\w/])\d[\d,]*(?:\.\d{1,2})?(?![\w/])")

def summary_panel_lines(image):
    """Horizontal boundaries in the right-hand financial summary panel only.

    The general product grid does not include these short horizontal rules.
    Geometry is source evidence; no values are inferred from arithmetic.
    """
    import cv2
    import numpy as np
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    left, right = int(w * .68), int(w * .985)
    mask = cv2.threshold(gray[:, left:right], 190, 255, cv2.THRESH_BINARY_INV)[1]
    lines = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(80, w // 15), 1))
    )
    y_values = np.flatnonzero((lines > 0).sum(axis=1) > (right - left) * .30)
    groups = []
    for y in map(int, y_values):
        if not groups or y - groups[-1][-1] > max(7, h // 220):
            groups.append([y])
        else:
            groups[-1].append(y)
    return [round(sum(g) / len(g)) for g in groups]


def panel_summary_from_boxes(boxes, w, boundaries, image_height):
    """Read printed values in independently ruled AP financial-summary cells.

    Wrapped OCR labels are joined within a bounded cell. Ambiguous monetary
    cells stay null; the separate document reconciliation remains mandatory.
    """
    out = {field: None for field in FIELDS}
    if not boundaries or len(boundaries) < 3:
        return out
    bounds = sorted(set(boundaries))
    bounds.append(int(image_height * .97))
    for lo, hi in zip(bounds, bounds[1:]):
        if hi <= lo + 7 or hi - lo > image_height * .15:
            continue
        inside = [b for b in boxes
                  if lo + 1 <= (b["y0"] + b["y1"]) / 2 < hi - 1
                  and (b["x0"] + b["x1"]) / 2 >= w * .68]
        labels = [b for b in inside
                  if b["x0"] < w * .83 and re.search(r"[A-Za-z]", b["text"])]
        label_text = " ".join(b["text"] for b in sorted(labels, key=lambda b: (b["y0"], b["x0"])))
        label_text = " ".join(label_text.split())
        found_fields = [key for key, pattern in LABEL_PATTERNS.items()
                        if re.search(pattern, label_text, re.I)]
        if len(found_fields) != 1:
            continue
        field = found_fields[0]
        values = []
        for b in inside:
            if (b["x0"] + b["x1"]) / 2 < w * .81:
                continue
            text = str(b["text"]).strip()
            if re.search("[A-Za-z]", text):
                continue
            numbers = AMOUNT.findall(text)
            if field == "challan_amount" and len(numbers) == 2:
                # The printed cell includes a long Challan No before '& amount'.
                numbers = numbers[-1:]
            if len(numbers) == 1:
                values.append(float(numbers[0].replace(",", "")))
        if len(values) == 1:
            out[field] = values[0]
    return out


def invoice_qty_from_boxes(boxes, w):
    """Read the separate printed three-part IMFL/Beer/Total quantity row."""
    groups = []
    eligible = [b for b in boxes
                if w * .27 < (b["x0"] + b["x1"]) / 2 < w * .70
                and re.fullmatch(r"\s*\d+\s*/\s*\d+\s*", b["text"])]
    for b in sorted(eligible, key=lambda b: (b["y0"], b["x0"])):
        y = (b["y0"] + b["y1"]) / 2
        placed = next((g for g in groups if abs(g[0][0] - y) < 25), None)
        if placed is None:
            placed = []
            groups.append(placed)
        placed.append((y, b))
    totals = []
    for group in groups:
        ordered = sorted((box for _y, box in group), key=lambda b: b["x0"])
        if len(ordered) != 3:
            continue
        vals = [tuple(map(int, re.findall(r"\d+", b["text"]))) for b in ordered]
        if (all(len(v) == 2 and v[1] == 0 for v in vals)
                and vals[0][0] + vals[1][0] == vals[2][0]):
            y = sum((b["y0"] + b["y1"]) / 2 for b in ordered) / 3
            # Only accept the printed invoice/total-cartons row, not breakage.
            label = " ".join(b["text"] for b in boxes
                             if b["x0"] < w * .33 and abs((b["y0"] + b["y1"])/2 - y) < 58)
            if re.search(r"invoice\s*qty|total\s*cartons", label, re.I):
                totals.append((vals[0][0], vals[1][0], vals[2][0]))
    return totals[0] if len(set(totals)) == 1 and totals else None


def summary_from_boxes(boxes, w):
    """Labels + proximity only. Missing/ambiguous values remain None."""
    result = {key: None for key in FIELDS}
    # Assemble text into visual lines to handle wrapped labels.
    ordered = sorted(boxes, key=lambda b:(b["y0"],b["x0"]))
    for key, pattern in LABEL_PATTERNS.items():
        candidates = [b for b in ordered if re.search(pattern, b["text"], re.I)]
        for label in candidates:
            nearby = [b for b in ordered if b["x0"] > label["x0"] + 8
                      and abs((b["y0"]+b["y1"])/2 - (label["y0"]+label["y1"])/2) < max(22, (label["y1"]-label["y0"])*1.6)
                      and AMOUNT.search(b["text"])]
            if len(nearby) == 1:
                result[key] = float(AMOUNT.search(nearby[0]["text"]).group().replace(",",""))
                break
    return result

def header_from_text(text):
    def capture(expr):
        m = re.search(expr, text, re.I)
        return m.group(1).strip() if m else None
    return {
      "icdc_number": capture(r"\b(ICDC\d{8,})\b"),
      "tp_number": capture(r"\b(TP\d{8,})\b"),
      "invoice_date": capture(r"Invoice\s*Date\s*[:\-]?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{4})"),
      "retailer_code": capture(r"\b(\d{2}[A-Z]\d{6})\b"),
      "driver_number": capture(r"Driver\s*Number\s*[:\-]?\s*(\d{10})"),
      "driver_name": capture(r"Driver\s*Name\s*[:\-]?\s*([A-Za-z ]{3,30})"),
    }

def bar_indent_header_from_boxes(boxes):
    """Extract indent identity without using ICDC invoice assumptions."""
    ordered = sorted(boxes, key=lambda x: (round(x["y0"] / 18), x["x0"]))
    text = " ".join(str(x["text"]) for x in ordered)
    def match(pattern):
        found = re.search(pattern, text, re.I)
        return found.group(1).strip() if found else None
    return {
        "indent_number": match(r"(?:Bar\\s*)?Indent\\s*(?:No|Number)\\.?\\s*[:#-]?\\s*([A-Z0-9/-]{4,})"),
        "indent_date": match(r"Indent\\s*Date\\s*[:#-]?\\s*(\\d{1,2}[-/]\\d{1,2}[-/]\\d{4})"),
    }

# RapidOCR owns mutable inference buffers. Serialize reuse instead of sharing
# one engine between concurrent uploads; initialization is paid once per worker.
_ocr_lock = Lock()
_ocr_engine = None


def extract_pdf(path, dpi=250, threads=2, ocr_mode='full', document_type='icdc', engine=None):
    if engine is not None:
        return _extract_pdf(path, dpi, threads, ocr_mode, document_type, engine)
    global _ocr_engine
    with _ocr_lock:
        from rapidocr_onnxruntime import RapidOCR
        if _ocr_engine is None:
            _ocr_engine = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)
        return _extract_pdf(path, dpi, threads, ocr_mode, document_type, _ocr_engine)


def _extract_pdf(path, dpi, threads, ocr_mode, document_type, engine):
    start = time.perf_counter()
    with __import__('fitz').open(str(path)) as _source:
        page_count = len(_source)
    rendered = []
    for page, img in render_pages(path, dpi=dpi):
        xs, ys = grid(img)
        boxes, optimization = ocr_page(engine, img, page, page_count, mode=ocr_mode)
        if document_type == 'icdc':
            optimization['summary_panel_lines'] = summary_panel_lines(img)
        rendered.append((page, boxes, xs, ys, img.shape[1], img.shape[0], optimization))
    boundaries = (select_bar_columns if document_type == 'bar_indent' else choose_columns)([(p,x,y) for p,_,x,y,_,_,_ in rendered])
    if document_type == 'bar_indent':
        found = []
        for page, boxes, xs, ys, width, height, optimization in rendered:
            if boundaries is None:
                continue
            cols = [round(x * width / rendered[0][4]) for x in boundaries]
            found.extend(parse_bar_rows(boxes, cols, ys, page, height))
        summaries = [parse_bar_summary(boxes) for _, boxes, *_ in rendered]
        complete = [value for value in summaries if value["total_cases"] is not None and value["issue_price"] is not None]
        summary = complete[-1] if complete else {"total_cases":None, "issue_price":None}
        checks = validate_bar_items(found, expected_total_cases=summary["total_cases"],
                                    expected_total_amount=summary["issue_price"])
        if boundaries is None:
            checks.update(status='FAILED', reason='bar_table_columns_not_found')
        return {'source':str(path),'document_type':'AP_BAR_INDENT',
                'header':bar_indent_header_from_boxes(rendered[0][1] if rendered else []),
                'items':found,'summary':summary,'validation':checks,
                'diagnostics':{'page_count':len(rendered),'elapsed_seconds':round(time.perf_counter()-start,2),
                               'ocr_modes':[r[-1]['mode'] for r in rendered]}}
    candidates = []
    all_text = []
    for page, boxes, xs, ys, width, height, optimization in rendered:
        all_text.extend([b["text"] for b in boxes])
        if boundaries is None:
            continue
        # Column coordinates are scaled if page dimensions differ.
        master_width = rendered[0][4]
        cols = [round(x * width/master_width) for x in boundaries]
        # Repeated header is identified from the first few ruled bands.
        for band in table_rows(boxes, cols, ys, page, image_height=height):
            cells = band["cells"]
            if not BRAND.fullmatch(cells[1].replace(" ","").upper()):
                # Preserve only possible orphan continuations for manual inspection.
                if cells[2] and not any(cells[i] for i in (0,1,3,4,5,6,7,8,9)):
                    candidates.append({"sl_no": None, "brand_number":"", "brand_name":clean_name(cells[2]),
                         "pages":[page], "flags":[], "continuation_position":"page_start" if band["band"][0] < height *.25 else "other"})
                continue
            item = parse_cells(cells,page,band["confidences"])
            item["page_open_end"] = band["band"][1] > height * .78
            candidates.append(item)
    items = merge_continuation(candidates)
    # Do not fabricate serials without an independent valid item identity.
    expected = list(range(1, len(items)+1))
    summary = {key: None for key in FIELDS}
    summary.update({"invoice_qty_imfl_fl":None,"invoice_qty_beer":None,"invoice_qty_total":None})
    # Conservative: summary fields may span multiple OCR boxes; keep null until identified.
    for page, boxes, _, _, width, _, optimization in rendered:
        if page >= max(1, len(rendered)-1):
            values = summary_from_boxes(boxes,width)
            ruled = panel_summary_from_boxes(
                boxes, width, optimization.get("summary_panel_lines", []),
                rendered[page-1][5],
            )
            for key, val in ruled.items():
                if val is not None:
                    # An OCR disagreement is ambiguous evidence, not a choice
                    # between two numbers. Strict validation rejects null.
                    if values.get(key) is not None and values[key] != val:
                        values[key] = None
                    else:
                        values[key] = val
            for key, val in values.items():
                if val is not None:
                    summary[key] = val
            # Accept three independently OCRed quantity cells only when the
            # displayed IMFL + Beer = Total and the row is explicitly labeled.
            quantity_parts = invoice_qty_from_boxes(boxes, width)
            if quantity_parts:
                summary.update(zip(("invoice_qty_imfl_fl", "invoice_qty_beer", "invoice_qty_total"), quantity_parts))
            for b in boxes:
                m = re.search(r"(\d+)\s*/\s*0\s+(\d+)\s*/\s*0\s+(\d+)\s*/\s*0",b["text"])
                if m:
                    summary.update(zip(("invoice_qty_imfl_fl","invoice_qty_beer","invoice_qty_total"),map(int,m.groups())))
    validation = validate_document(items,summary)
    if boundaries is None:
        validation["status"] = "FAILED"
        validation["reason"] = "table_columns_not_confidently_detected"
    return {
        "source":str(path), "header":header_from_text(" ".join(all_text)),
        "items":items, "summary":summary,"validation":validation,
        "diagnostics":{"page_count":len(rendered),"ocr_elapsed_seconds":round(time.perf_counter()-start,2),
             "grid_columns_detected":bool(boundaries),"candidate_count":len(items),
             "ocr_modes":[r[-1]["mode"] for r in rendered],
             "warning":"Experimental extractor; FAIL-CLOSED. Never auto-save."}
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",help="PDF file or directory")
    p.add_argument("-o","--output",default=None)
    p.add_argument("--dpi",type=int,default=250)
    p.add_argument("--ocr-mode",choices=["full","table"],default="full",help="Use cautious cropped OCR on middle pages")
    p.add_argument("--document-type",choices=["icdc","bar_indent"],default="icdc")
    args=p.parse_args()
    paths = sorted(Path(args.input).glob("*.pdf")) if Path(args.input).is_dir() else [Path(args.input)]
    dest=Path(args.output) if args.output else Path("ap_icdc_output")
    if len(paths)==1 and dest.suffix.lower()==".json":
        dest.parent.mkdir(parents=True,exist_ok=True)
    else:
        dest.mkdir(parents=True,exist_ok=True)
    all_items=[]
    from rapidocr_onnxruntime import RapidOCR
    shared_engine = RapidOCR()
    for path in paths:
        result=extract_pdf(path,dpi=args.dpi,ocr_mode=args.ocr_mode,document_type=args.document_type,engine=shared_engine)
        output=dest if dest.suffix.lower()==".json" and len(paths)==1 else dest / (path.stem+".json")
        output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
        print(f"{path.name}: {result['validation']['status']} | {len(result['items'])} rows | {result['diagnostics']['ocr_elapsed_seconds']}s")
        for item in result["items"]:
            all_items.append({"source":path.name,**{k:v for k,v in item.items() if k not in ("audit","flags","pages")},"flags":";".join(item.get("flags",[]))})
    if all_items:
        csvfile=(dest.parent if dest.suffix else dest)/"combined_items.csv"
        with csvfile.open("w",newline="",encoding="utf-8-sig") as f:
            writer=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for row in all_items for k in row)))
            writer.writeheader()
            writer.writerows(all_items)

if __name__=="__main__":
    main()
