"""PyMuPDF raster -> RapidOCR ONNX boxes -> OpenCV geometric cells."""
from __future__ import annotations
import re
from pathlib import Path
import cv2
import fitz
import numpy as np

def render_pages(pdf_path, dpi=250):
    with fitz.open(str(pdf_path)) as pdf:
        for idx, page in enumerate(pdf):
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            raw = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            rgb = raw[:, :, :3].copy()
            yield idx + 1, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

def ocr_boxes(engine, image):
    result = engine(image)
    # rapidocr-onnxruntime returns (lines, elapsed); modern rapidocr returns result object.
    raw = result[0] if isinstance(result, tuple) else getattr(result, "boxes", None)
    if raw is None and hasattr(result, "txts"):
        raw = [(b, t, s) for b, t, s in zip(result.boxes or [], result.txts or [], result.scores or [])]
    boxes = []
    for record in raw or []:
        try:
            polygon, text, score = record[:3]
            vertices = np.asarray(polygon, dtype=float).reshape(-1, 2)
            x0, y0 = vertices.min(axis=0)
            x1, y1 = vertices.max(axis=0)
            boxes.append({"text": str(text), "confidence": float(score), "x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1)})
        except (ValueError, TypeError, IndexError):
            continue
    return boxes

def clusters(indices, gap=8):
    seq = sorted(map(int, indices))
    if not seq:
        return []
    groups = [[seq[0]]]
    for value in seq[1:]:
        if value - groups[-1][-1] <= gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [int(round(sum(group) / len(group))) for group in groups]

def grid(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 13)
    h, w = gray.shape
    horizontal = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 25), 1)))
    vertical = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 40))))
    xs = clusters(np.flatnonzero((vertical > 0).sum(axis=0) > h * .055))
    ys = clusters(np.flatnonzero((horizontal > 0).sum(axis=1) > w * .24))
    return xs, ys

def choose_columns(page_grids):
    # Prefer continuation pages with exactly eleven credible vertical rules.
    matches = [x for page, x, y in page_grids if len(x) == 11]
    if not matches:
        return None
    return matches[len(matches)//2]

def assign_cells(boxes, xs, ys):
    result = {}
    if len(xs) != 11 or len(ys) < 2:
        return result
    for box in boxes:
        xc = (box["x0"] + box["x1"]) / 2
        yc = (box["y0"] + box["y1"]) / 2
        ci = int(np.searchsorted(xs, xc, side="right") - 1)
        ri = int(np.searchsorted(ys, yc, side="right") - 1)
        if 0 <= ci < 10 and 0 <= ri < len(ys)-1:
            result.setdefault((ri, ci), []).append(box)
    return result

def cell_text(parts):
    return " ".join(x["text"] for x in sorted(parts, key=lambda b: (round(b["y0"]/12), b["x0"])))

def table_rows(boxes, xs, ys, page, image_height=None):
    # A page-cut product may have no closing horizontal rule. Add an OCR-supported
    # interval only when ink/words occur below the last grid line and above footer.
    ys = list(ys)
    if image_height and len(ys) >= 2:
        footer_limit = int(image_height * 0.966)
        tail = [b for b in boxes if ys[-1] + 5 < (b["y0"] + b["y1"]) / 2 < footer_limit
                and xs and xs[0] <= (b["x0"] + b["x1"])/2 <= xs[-1]]
        if tail:
            ys.append(min(footer_limit, int(max(b["y1"] for b in tail) + 12)))
    cells = assign_cells(boxes, xs, ys)
    result = []
    for r in range(len(ys)-1):
        values = [cell_text(cells.get((r, c), [])) for c in range(10)]
        if not any(values):
            continue
        confidences = [b["confidence"] for c in range(10) for b in cells.get((r,c), [])]
        result.append({"page": page, "cells": values, "confidences": confidences, "band": (ys[r], ys[r+1])})
    return result

def ocr_first_page_heading(boxes):
    return " ".join(b["text"] for b in sorted(boxes, key=lambda x: (round(x["y0"]/18),x["x0"]))[:120]).upper()
