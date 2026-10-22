# Andhra Pradesh APSBCL ICDC experimental CPU extractor

Standalone, **NOT wired to live PDF/QR routes**, so existing import behavior is unchanged.
Designed for image-only PDFs. PyMuPDF is used solely for raster rendering.

## Setup (separate virtual environment)

Install `rapidocr-onnxruntime`, `onnxruntime`, `opencv-python-headless`, `numpy`, `PyMuPDF`, `pytesseract`, `pytest`.
The optional name-only Tesseract pass remains a pending enhancement, not an active dependency.

## CLI

From `madhushala-excise-bridge`:

```bash
python -m app.modules.document_import.ap_icdc.main path/to/andra2.pdf -o /tmp/andra2.json
python -m app.modules.document_import.ap_icdc.main path/to/pdf-folder -o /tmp/ap-output
pytest -q tests/test_ap_icdc_core.py
```

**Status:** Experimental. Detects grid columns and OCR positions, but header segmentation,
cut rows, wrapped summary labels and brand-name re-OCR need full fixture verification.
It marks documents FAILED if required values are unavailable. Never automatically hand
the generated data to purchase save.

Existing production routes are intentionally unchanged until both AP sample invoices
pass row-level reconciliation and regression tests.
