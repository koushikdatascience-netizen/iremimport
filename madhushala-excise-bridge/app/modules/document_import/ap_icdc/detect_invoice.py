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
