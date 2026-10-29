"""Conservative classifier for text-readable AP ICDC purchase invoices.

Bar Indent, Telangana ICDC and image-only PDFs stay on their existing path.
"""
from __future__ import annotations

import re


def is_ap_icdc_invoice_pdf(data: bytes) -> bool:
    if not data.startswith(b"%PDF"):
        return False
    try:
        import fitz
        with fitz.open(stream=data, filetype="pdf") as doc:
            text = " ".join(doc[i].get_text("text") for i in range(min(2, len(doc)))).casefold()
    except Exception:
        return False
    if re.search(r"\b(bar\s+indent|indent\s+no)\b", text):
        return False
    if "telangana" in text:
        return False
    ap_marker = (
        "government of andhra pradesh" in text
        or "andhra pradesh state" in text
        or "ap beverages" in text
    )
    invoice_marker = bool(re.search(r"\bicdc\b|\bicdc\d{8,}\b", text))
    return ap_marker and invoice_marker and ("invoice" in text or "delivery challan" in text)


def is_scanned_ap_icdc_pdf(data: bytes, filename: str) -> bool:
    return scanned_ap_icdc_evidence(data, filename) is not None


def scanned_ap_icdc_evidence(data: bytes, filename: str) -> dict | None:
    """Confirm a named AP scan using the first full-resolution page once.

    Evidence is request-local and reused by the dedicated extractor. Other
    state filenames and ambiguous headings stay on their existing route.
    """
    import time
    started = time.perf_counter()
    name = str(filename or "").casefold()
    if not any(marker in name for marker in ("andra", "andhra", "icdc", "apsbcl")) or not data.startswith(b"%PDF"):
        return None
    try:
        import fitz
        import numpy as np
        import cv2
        import hashlib
        from app.modules.document_import.ap_icdc import main as icdc
        with icdc._ocr_lock:
            with fitz.open(stream=data, filetype="pdf") as doc:
                if not doc or len(doc[0].get_text("text").strip()) > 120:
                    return None
                pix = doc[0].get_pixmap(matrix=fitz.Matrix(250 / 72, 250 / 72), alpha=False)
                rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
                image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            if icdc._ocr_engine is None:
                from rapidocr_onnxruntime import RapidOCR
                icdc._ocr_engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
            boxes = icdc.ocr_boxes(icdc._ocr_engine, image)
            text = " ".join(b["text"] for b in boxes).casefold()
            if not (("andhra pradesh" in text or "apsbcl" in text)
                    and "invoice" in text and "icdc" in text
                    and "bar indent" not in text and "telangana" not in text):
                return None
            return {"boxes": boxes, "width": pix.width, "height": pix.height,
                    "sha256": hashlib.sha256(data).hexdigest(), "dpi": 250,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
    except Exception:
        return None
