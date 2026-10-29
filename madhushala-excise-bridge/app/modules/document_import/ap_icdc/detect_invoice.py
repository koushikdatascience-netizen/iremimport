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
    """Opt in conservatively only for named AP scans, after OCR confirms identity.

    Keeps routine uploads for other states unchanged; callers must run in a
    thread because single-page OCR is synchronous.
    """
    name = str(filename or "").casefold()
    if not any(marker in name for marker in ("andra", "andhra", "icdc", "apsbcl")):
        return False
    if not data.startswith(b"%PDF"):
        return False
    try:
        import fitz
        import numpy as np
        import cv2
        from app.modules.document_import.ap_icdc.main import ocr_boxes, _ocr_lock
        from app.modules.document_import.ap_icdc import main as icdc
        with fitz.open(stream=data, filetype="pdf") as doc:
            if not doc:
                return False
            page = doc[0]
            if len(page.get_text("text").strip()) > 120:
                return False  # existing readable-PDF classifier owns this
            pix = page.get_pixmap(matrix=fitz.Matrix(130 / 72, 130 / 72), alpha=False)
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
            image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        with _ocr_lock:
            if icdc._ocr_engine is None:
                from rapidocr_onnxruntime import RapidOCR
                icdc._ocr_engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
            boxes = ocr_boxes(icdc._ocr_engine, image)
        text = " ".join(b["text"] for b in boxes).casefold()
        return (("andhra pradesh" in text or "apsbcl" in text)
                and "invoice" in text and "icdc" in text
                and "bar indent" not in text and "telangana" not in text)
    except Exception:
        return False
