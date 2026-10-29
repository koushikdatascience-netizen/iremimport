# State document extraction performance — 29 October 2026

Accuracy takes priority. No DPI reduction, crop activation, cheaper cloud mode, prompt change, row omission, reconciliation relaxation, Calculate or Save change is included.

## Evidence

- Recent production Jharkhand job: 27 rows, 58.86 seconds from creation through later READY state. Odisha: 28 rows, 5.81 seconds including later review/mapping work. These are job lifetime measurements, not extraction-only timings.
- Authorized live Jharkhand scan benchmark in the actual API image: 57.68 seconds before, 45.85 seconds after (20.5% faster). Both extracted 27 products and passed existing financial validation. The canonical hash for names, ML, cases, loose, row amounts, identifiers and financial totals was identical: af2f1bfda0a1b9e1ab9af5c6eee9bbcedaa1003ced840accec6c041d16d582e1. Line amounts total 282597.87 in both. The existing printed-AED warning is retained unchanged. Cloud latency varies; this is one paired benchmark, not a universal latency guarantee.
- Supplied Odisha readable samples: 28 rows in 1.866 seconds and 69 rows in 3.253 seconds on the local benchmark. Most time is table detection. The third sample fails the existing strict validation; this change does not relax it.
- AP OCR thread comparison inside the production image: 38.877 vs 37.729 seconds on the supplied two-page sample, with identical extraction hashes. That sample is not accepted as an ICDC invoice by either run (no confident ICDC columns), so this is an equivalence and overhead check, not evidence of a valid invoice or a large AP latency improvement. A local first-page comparison at 1 vs 2 intra-op threads produced identical 156 OCR texts and box coordinates.

## Changes

- Reuse HTTP connections across a scanned document's cloud requests instead of creating a new connection pool for every upload, poll and result request.
- Reuse the same uploaded page file ID for the independent Jharkhand financial pass. No data or financial pass is omitted. Upload reuse lasts only for the current extraction call.
- Start Jharkhand financial verification as soon as a completed page identifies the profile, overlapping slower product pages rather than waiting for the entire first round. Preserve the existing three-request cap, page order, merge rules and financial reconciliation. Cancel and drain tasks before removing temporary inputs on failure.
- Reuse one serialized RapidOCR engine per API process, with bounded ONNX threads. Keep 250 DPI, full-page OCR and the same parser/validation. This avoids repeated initialization and competing mutable inference sessions; concurrent OCR calls queue rather than corrupting shared state or exhausting the VPS.
- Run synchronous local PDF parsing in a worker thread, so readable Odisha imports do not block the API event loop behind other local extraction work. Odisha parsing and all arithmetic checks are untouched.
- Add sanitized per-page product/financial timing diagnostics, without document contents, file names, credentials or amounts.

## Tests and limits

Local suite: 358 passed, 7 skipped. Added regressions establish product/financial overlap, unchanged concurrency cap, financial upload reuse, safe cancellation and cleanup, OCR serialization/reuse at unchanged resolution, and local parsing off the API event loop. Existing state extraction and universal handoff regressions remain in the full suite. CI also runs the production Dockerfile image.

Image-only AP documents on the generic cloud path still depend on provider response time. Odisha already extracts the known readable layout quickly. There is no promise that every large scan finishes under one minute; such a promise would require changing accuracy mode or bypassing verification, which this release does not do. No real Purchase Save is performed.
