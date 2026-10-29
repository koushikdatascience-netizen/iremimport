"""Conservative AP ICDC invoice classifier for standard upload entrypoints.

Never route Andhra Bar Indent or Telangana ICDC through this adapter.
Image-only PDFs without identifiable text stay on the existing extractor.
"""
from __future__ import annotations

import re

def is_ap_icdc_invoice_pdf(data: bytes) -> bool:
    if not data.startswith(b"%PDF"):
        return False
    try:
        import fitz
        with fitz.open(stream=data, filetype="pdf") as doc:
            text = " ".join(page.get_text("text") for page in list(doc)[:2]).casefold()
    except Exception:
        return False
    if re.search(r"\b(bar\s+indent|indent\s+no)\b", text):
        return False
    if "government of telangana" in text or "telangana" in text:
        return False
    ap_marker = (
        "government of andhra pradesh" in text
        or "andhra pradesh state" in text
        or "ap beverages" in text
    )
    invoice_marker = bool(re.search(r"\bicdc\b|\bicdc\d{8,}\b", text))
    return ap_marker and invoice_marker and ("invoice" in text or "delivery challan" in text)
