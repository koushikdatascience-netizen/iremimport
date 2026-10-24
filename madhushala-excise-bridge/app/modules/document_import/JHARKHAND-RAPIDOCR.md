# Jharkhand scanned-invoice RapidOCR (experimental)

Isolated CLI-only prototype, **not production-connected**. This reuses the Andhra Pradesh PyMuPDF/OpenCV/RapidOCR ONNX primitives but implements Jharkhand's 11-column invoice semantics separately. No West Bengal, Odisha, Calculate or Purchase Save changes.

## Actual customer PDF diagnostic (2026-10-24)

On `jharkhand1(1).pdf` (2 image-only pages) at 250 DPI, AP OpenCV grid detection found only **2** usable vertical rules on page 1 and **0** on page 2; full rectangular 12-rule parsing cannot run on this scan. Do not claim successful automatic extraction from this invoice. A column-anchored / OCR row-region segmentation fallback must be implemented and measured against 27 rows, 159 cases, actual printed invoice amount and taxes, then matched to the Form-T.

## Optional isolated setup

```bash
python -m pip install -r app/modules/document_import/ap_icdc/requirements-ocr.txt
python -m pytest -q tests/test_jharkhand_rapidocr.py
python -m app.modules.document_import.jharkhand_rapidocr 'jharkhand1(1).pdf' -o jharkhand-audit.json
```

On detection or uncertainty this tool raises a ValueError and **does not** produce a Purchase Save payload. Even valid row OCR is tagged `REVIEW_REQUIRED`/`validated=false` until financial totals and permit reconciliation are implemented. No silent LlamaParse substitution or guessed financial amounts.
