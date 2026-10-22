# OCR runtime tuning and accuracy guardrails

The default remains `--ocr-mode full` at **250 DPI**. Its OCR result is the
reference and remains unchanged for the 41/41-item success case.

`--ocr-mode table` crops **only middle pages**, conditional on finding exactly
11 vertical rules and a credible full table grid. First and last page always
receive full OCR to retain the ICDC identity and financial summary. OCR boxes
are translated back into original coordinates before table assignment. A
document's validation still fails closed when summaries, quantities or
arithmetic do not reconcile.

The CLI instantiates ONNX models once for **all PDFs in the folder** (previously
once per PDF). This removes unnecessary model startup cost for batch use.

Run both modes and compare normalized item arrays before accepting an OCR
optimization:

```bash
python -m app.modules.document_import.ap_icdc.main andra2.pdf --ocr-mode full -o full.json
python -m app.modules.document_import.ap_icdc.main andra2.pdf --ocr-mode table -o table.json
```

```python
import json
a=json.load(open('full.json'))['items']
b=json.load(open('table.json'))['items']
keys=['sl_no','brand_number','brand_name','size_ml','qty_cases','qty_bottles','case_rate','btl_rate','total']
assert [[x.get(k) for k in keys] for x in a] == [[x.get(k) for k in keys] for x in b]
```

**Do not replace full OCR or deploy the optimization without an actual measured
timing/accuracy comparison.** Tesseract brand spacing and AP Bar Indent
financial summary handling remain separate future quality gates.
