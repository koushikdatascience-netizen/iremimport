# AP ICDC six-page production reconciliation investigation

## Source and reproduction

Authorized source: local `andra2.pdf`, six image-only pages, verified visually against invoice ICDC08326281144107 and TP08326281144107. SHA-256: d6cf33bdfa2697e7842c46e459b4dc55cb1608f465c35a2575ebbbc420749bf5. This is the invoice requested as andra2(1).pdf, not a fabricated replacement.

Production image baseline: Git SHA 85152efe9e59b1d67c5ebd5539942defe56f605f; RapidOCR 1.4.4, ONNX Runtime 1.31.1, PyMuPDF 2.0, OpenCV 4.14.0.94. PDF bytes were streamed into an isolated diagnostic process using an in-memory file descriptor. No deployed code or server files were modified.

Dedicated baseline: 97.963 seconds, 42 candidates, FAILED. Failed checks: serial_sequence, row_arithmetic, case_quantity, issue_price. The logged real normal-upload failure took 130.32 seconds (generic cloud extraction followed by full dedicated OCR). Job-lifetime and isolated-extractor timings are reported separately.

## Demonstrated causes

1. Product parsing scanned every horizontal table band, including the first-page licence header. A name-only License No fragment became a false orphan product, producing a 42nd candidate with no quantity or amount.
2. The first product's printed serial 1 was absent from full-page OCR. All actual 41 product amounts were otherwise present. The serial is now reread from its ruled image cell at the unchanged rendering resolution; production-independent replay observed text 1 at 0.9894568 confidence. It is never assigned from the expected sequence or totals.
3. The challan row contains a long challan number and a separate ampersand-prefixed monetary value. Both became numeric candidates, so the old parser returned null for challan_amount. The amount is now taken only from the explicitly marked ampersand portion of the same financial cell, not from arithmetic or reference constants.
4. The direct AP upload handler referenced the removed local variable products after reconciliation. Its normalization-count guard now uses doc.items, and a full upload/persistence/handoff regression exercises it.
5. Image-only AP routing previously used generic cloud extraction to establish identity, then ran the complete local OCR again. The replacement incorporates PR #179's early AP routing and confirms named AP scans using one full-resolution first-page pass. Those same request-local OCR boxes are reused by dedicated extraction; remaining pages are each OCRed once. Other state filenames/layouts and AP Bar Indent stay on their existing paths. No downscaling, crop mode, cloud model change or weaker checks are introduced.

## Reconciliation

Full fixed OCR rerun in the exact production image: 99.166 seconds, 41 rows, 190 cases, zero loose, serials 1–41. Every individually transcribed printed line amount matches, and their sum is 730822.00. Existing continuation merges at rows 27 and 37 retain names across pages 3/4 and 4/5. Row 40 retains its OCR review flag. All monetary and quantity checks pass; REVIEW_REQUIRED preserves the normal operator review rather than treating flags as missing source money.

All printed fields match: Issue Price 730822.00; CESS 4463.89; Additional Privilege Fee 15738.72; Additional Retail Excise Tax 76968.00; Privilege Fee for Clubs 0.00; ICDC Value 827992.61; TCS 16560.00; Challan Amount 844548.00; Previous Credit 199.65; Sub Total 844747.65; Less This Invoice Value 844552.61; Amount Credited 195.04.

Independent arithmetic checks:
- Product sum = Issue Price = 730822.00.
- Issue Price + CESS + applicable privilege/excise fees = ICDC Value 827992.61.
- ICDC Value + TCS = invoice value 844552.61.
- Challan Amount + Previous Credit = Sub Total 844747.65. A missing or conflicting challan now fails this additional check.
- Sub Total − invoice value = Amount Credited 195.04.

No reference values are present in production parser code. They appear only in regression expectations and this report. Genuinely absent VAT and rounding remain null. Item Master Calculate and Purchase Save logic are untouched.

## Regression coverage and delivery

The committed fixture contains sanitized OCR boxes and grid geometry captured from this actual production runtime, with private licencee/driver header content removed. It replays all six pages through the real parser and asserts every row amount and every financial field. The private PDF itself is not committed. AP_ICDC_REAL_PDF enables an additional full actual-OCR regression using the original PDF and its SHA check; full actual OCR is also exercised by production diagnostics and final normal API verification.

Tests include rejection of missing serials and missing/conflicting challan amounts, page-boundary names, source amounts and financials through normalization/persistence/universal handoff, first-page OCR reuse, and a forbidden-cloud assertion for confirmed AP uploads. Local complete suite before final timing-only logging changes: 370 passed, 8 skipped. CI runs the complete suite inside the production Dockerfile image as well.

The unchanged 250-DPI OCR itself remains approximately 99 seconds on this VPS. The performance improvement removes the prior generic cloud pass and duplicate first-page identification OCR. It is not claimed as a faster OCR model or a guarantee of sub-minute six-page scans. Final normal-upload timing, mapping and read-only handoff results are recorded after deployment. No Purchase Save is performed.
