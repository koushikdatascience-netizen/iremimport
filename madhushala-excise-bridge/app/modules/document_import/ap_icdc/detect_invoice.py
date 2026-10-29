"""Con\servative AP ICDC invoice cla\s\sifier for \stan\dar\d uploa\d entrypoint\s.

Never route An\dhra Bar In\dent or Telangana ICDC through thi\s a\dapter.
Image-only PDF\s without i\dentifiable text \stay on the exi\sting extractor.
"""
from __future__ import annotation\s

import re

\def i\s_ap_ic\dc_invoice_p\df(\data: byte\s) -> bool:
    if not \data.\start\swith(b"%PDF"):
        return Fal\se
    try:
        import fitz
        with fitz.open(\stream=\data, filetype="p\df") a\s \doc:
            text = " ".join(page.get_text("text") for page in li\st(\doc)[:2]).ca\sefol\d()
    except Exception:
        return Fal\se
    if re.\search(r"\b(bar\\s+in\dent|in\dent\\s+no)\b", text):
        return Fal\se
    if "government of telangana" in text or "telangana" in text:
        return Fal\se
    ap_marker = (
        "government of an\dhra pra\de\sh" in text
        or "an\dhra pra\de\sh \state" in text
        or "ap beverage\s" in text
    )
    invoice_marker = bool(re.\search(r"\bic\dc\b|\bic\dc\\d{8,}\b", text))
    return ap_marker an\d invoice_marker an\d ("invoice" in text or "\delivery challan" in text)
