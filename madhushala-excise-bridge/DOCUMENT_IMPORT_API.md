# Document Import API Contract

This document is the implementation-facing contract for QR/PDF/image import, review, mapping, Purchase Calculate Preview, and Purchase Save.

The examples below use concrete source values from the WB Form No. 3 test document used during validation:

- Transport Pass: `tFLDR/2026-2027/07246088/P`
- Invoice/Consignment No: `2026-2027/W/2022/015/01/032195`
- Document date: `2026-09-22`
- Loose-only source row: `Absolut Vodka`, 50 ml, `box=0`, `loose=1`

Master codes such as Supplier, Store, Purchase A/c, User and mapped Madhushala Item Code must always come from the active company-scoped Madhushala APIs. The concrete codes in examples are examples of the wire shape; never hard-code them.

## Common bridge headers

Every `/api/v1/document-import/**` endpoint requires the integration-session bearer token created by the bridge session flow.

```http
Authorization: Bearer <integration-session-token>
Accept: application/json
```

For JSON requests:

```http
Content-Type: application/json
X-Correlation-ID: doc-import-20260923-001
```

`X-Correlation-ID` is optional. Calculate Preview and Save create one automatically if omitted.

Do not send the Madhushala service/JWT token from the browser. It is stored server-side in the integration session.

## Quantity and rate contract

Reviewed source quantities are semantic and remain separate:

```text
box   = cases/cartons
loose = individual bottles/units
qnty  = (box * packing) + loose
```

Examples:

```text
1 case, packing 12, no loose     -> box=1, loose=0, qnty=12
2 cases, packing 12, 3 loose     -> box=2, loose=3, qnty=27
Absolut 50 ml loose-only         -> box=0, loose=1, qnty=1
```

For **PDF imports**, commercial rate inputs are forwarded exactly from Madhushala Item Master:

```text
boxRate   <- itemmst.purchaseRateCase
looseRate <- itemmst.purchaseRate
mrp       <- itemmst.salesRate
packing   <- Item Master packing/bottles-per-case
```

The bridge does **not** divide, multiply, derive, or substitute PDF `boxRate`/`looseRate`. In particular, when `purchaseRate=0`, PDF Calculate receives `looseRate=0`; it is not replaced with `purchaseRateCase`.

For PDF imports the bridge also does not calculate a pre-Calculate financial amount. It sends reviewed `box`/`loose` plus the exact Item Master rates to Madhushala `POST /api/purchase/calculate`, which is the financial source of truth for `rate`, `itemAmount`, taxes and totals.

Legacy non-PDF flows retain their previous rate fallback behavior.

---

## 1. Upload one PDF

`POST /api/v1/document-import/upload/pdf`

Headers:

```http
Authorization: Bearer <integration-session-token>
Accept: application/json
Content-Type: multipart/form-data
X-Correlation-ID: wb-pdf-20260923-001
```

Multipart body:

```text
file=@10.pdf;type=application/pdf
```

Representative response:

```json
{
  "job": {
    "id": "a334329290324704812559a70407d0ea",
    "source_type": "DOCUMENT_PDF",
    "status": "REVIEW_REQUIRED",
    "invoice_number": "2026-2027/W/2022/015/01/032195",
    "invoice_date": "22/09/2026",
    "extracted_count": 9,
    "mapped_count": 0
  },
  "summary": {
    "detected": 9,
    "recognized": 0,
    "needMapping": 9,
    "valid": 9,
    "needsAttention": 0,
    "reviewRequired": true,
    "sourceFiles": 1
  },
  "extraction": {
    "engine": "pymupdf-state-adapter",
    "usable": true,
    "profile": "WEST_BENGAL_FORM3",
    "state": "WEST_BENGAL",
    "needsFallback": false,
    "originalPageNumbers": [1, 2],
    "files": []
  },
  "sourceFiles": ["10.pdf"],
  "extractedDocument": {
    "documentType": "invoice",
    "supplierName": null,
    "invoiceNumber": "2026-2027/W/2022/015/01/032195",
    "transportPassNo": "tFLDR/2026-2027/07246088/P",
    "invoiceDate": "22/09/2026",
    "items": [
      {
        "itemName": "Absolut Vodka",
        "brand": "Absolut Vodka",
        "ml": 50,
        "box": 0,
        "loose": 1,
        "batchNo": "NA& Not Available"
      }
    ]
  },
  "normalizedItems": [
    {
      "source": "DOCUMENT_PDF",
      "sourceItemId": "document_pdf-1",
      "rawName": "Absolut Vodka",
      "normalizedName": "absolut vodka",
      "brand": "Absolut Vodka",
      "ml": 50,
      "packing": null,
      "quantity": 1.0,
      "box": null,
      "loose": 1,
      "rate": null,
      "mrp": null,
      "amount": null,
      "batchNo": "NA& Not Available",
      "rawData": {
        "canonicalBox": 0,
        "canonicalLoose": 1,
        "canonicalQuantityVersion": 2
      }
    }
  ],
  "reviewItems": [
    {
      "id": "f45c0f6bf0a54a6aa32bb8f0a150e802",
      "name": "Absolut Vodka",
      "brand": "Absolut Vodka",
      "ml": 50,
      "batchNo": "NA& Not Available",
      "box": 0,
      "loose": 1,
      "sourceFile": "10.pdf",
      "sourcePart": 1,
      "issues": [],
      "valid": true
    }
  ]
}
```

The response contains all extracted rows; the single-item array above focuses on the loose-only regression case.

## 2. Upload one image

`POST /api/v1/document-import/upload/image`

Same headers as PDF with multipart content. File must be JPG/JPEG/PNG.

```text
file=@invoice-page-1.jpg;type=image/jpeg
```

Response shape is the same as PDF upload. Extraction engine may be `llamaparse`.

## 3. Batch upload

`POST /api/v1/document-import/upload/batch/pdf`

or

`POST /api/v1/document-import/upload/batch/image`

Headers:

```http
Authorization: Bearer <integration-session-token>
Accept: application/json
Content-Type: multipart/form-data
```

Multipart body:

```text
files=@page-1.pdf;type=application/pdf
files=@page-2.pdf;type=application/pdf
```

PDF and image types cannot be mixed in the same batch. Response is the same upload envelope with `summary.sourceFiles`, `sourceFiles`, and per-file extraction metadata.

## 4. Backward-compatible mixed upload

`POST /api/v1/document-import/upload`

Multipart:

```text
file=@10.pdf;type=application/pdf
```

The backend detects PDF/JPG/JPEG/PNG and returns the same upload envelope.

## 5. Get job

`GET /api/v1/document-import/jobs/{jobId}`

Example:

```http
GET /api/v1/document-import/jobs/a334329290324704812559a70407d0ea
Authorization: Bearer <integration-session-token>
Accept: application/json
```

Response is the persisted import job row, including job id, source type, status, document metadata, counts, timestamps, and error when present.

## 6. Get job items and review rows

`GET /api/v1/document-import/jobs/{jobId}/items`

Response:

```json
{
  "items": [
    {
      "id": "f45c0f6bf0a54a6aa32bb8f0a150e802",
      "raw_name": "Absolut Vodka",
      "brand": "Absolut Vodka",
      "ml": 50,
      "quantity": 1.0,
      "box": 0,
      "loose": 1,
      "mapping_status": "PENDING"
    }
  ],
  "reviewItems": [
    {
      "id": "f45c0f6bf0a54a6aa32bb8f0a150e802",
      "name": "Absolut Vodka",
      "brand": "Absolut Vodka",
      "ml": 50,
      "batchNo": "NA& Not Available",
      "box": 0,
      "loose": 1,
      "sourceFile": "10.pdf",
      "sourcePart": 1,
      "issues": [],
      "valid": true
    }
  ]
}
```

## 7. Confirm review

`POST /api/v1/document-import/jobs/{jobId}/review/confirm`

Headers:

```http
Authorization: Bearer <integration-session-token>
Accept: application/json
Content-Type: application/json
X-Correlation-ID: review-a3343292
```

Request:

```json
{
  "items": [
    {
      "id": "f45c0f6bf0a54a6aa32bb8f0a150e802",
      "name": "Absolut Vodka",
      "brand": "Absolut Vodka",
      "ml": 50,
      "box": 0,
      "loose": 1
    }
  ]
}
```

`batchNo` is preserved by the backend when an older frontend omits it. Purchase handoff uses the document batch first, then the mapped Item Master batch where available. If neither supplies a nonblank batch, the outgoing `batchNo` is `"NA"`; it is never an empty string.

Response:

```json
{
  "job": {
    "id": "a334329290324704812559a70407d0ea",
    "status": "MAPPING_REQUIRED",
    "mapped_count": 0
  },
  "reviewItems": [
    {
      "id": "f45c0f6bf0a54a6aa32bb8f0a150e802",
      "name": "Absolut Vodka",
      "brand": "Absolut Vodka",
      "ml": 50,
      "batchNo": "NA& Not Available",
      "box": 0,
      "loose": 1,
      "issues": [],
      "valid": true
    }
  ],
  "summary": {
    "detected": 1,
    "recognized": 0,
    "needMapping": 1
  }
}
```

## 8. Save document-row mapping

`POST /api/v1/document-import/jobs/{jobId}/mapping/save`

Headers:

```http
Authorization: Bearer <integration-session-token>
Accept: application/json
Content-Type: application/json
```

Request:

```json
{
  "mappings": [
    {
      "jobItemId": "f45c0f6bf0a54a6aa32bb8f0a150e802",
      "exciseItemCode": "1623",
      "itemCode": "100010"
    }
  ]
}
```

`itemCode` is validated against the current company catalogue. The backend does not retry using another company code.

Response:

```json
{
  "mappedCount": 1,
  "globalMappedCount": 1,
  "localOnlyCount": 0,
  "conflictingExciseCodes": [],
  "response": {
    "mappedCount": 1
  },
  "status": "COMPLETED"
}
```

## 9. QR decode

`POST /api/v1/document-import/qr/decode`

Multipart:

```text
file=@transport-pass-qr.png;type=image/png
```

Response:

```json
{
  "value": "https://cms.upexciseonline.co/transport-pass-tracking/?tpnum=WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674&tptype=FG&tpyear=2026"
}
```

## 10. QR extract

`POST /api/v1/document-import/qr/extract`

Request:

```json
{
  "url": "https://cms.upexciseonline.co/transport-pass-tracking/?tpnum=WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674&tptype=FG&tpyear=2026"
}
```

Response uses the same job/summary/extractedDocument/normalizedItems/reviewItems flow as document upload.

## 11. Purchase context

`GET /api/v1/document-import/purchase/context?supplierName=Bevco%20-%20Jangipur%20Depot`

Headers:

```http
Authorization: Bearer <integration-session-token>
Accept: application/json
```

Response contract:

```json
{
  "shopCode": "hedu_test",
  "companyCode": "2",
  "billType": "AI",
  "supplierHint": "Bevco - Jangipur Depot",
  "purchaseTaxMode": "ITEMWISE",
  "catalogueCount": 1250,
  "taxTagCount": 4,
  "jwtContext": {
    "companyCode": "2",
    "companyName": "Demo Company",
    "yearCode": "2026-27",
    "userCode": "A00001"
  },
  "options": {
    "suppliers": [{"code": "J00001", "name": "BEVCO JANGIPUR"}],
    "storages": [{"code": "S00001", "name": "MAIN STORE"}],
    "accounts": [{"code": "P00002", "name": "PURCHASE"}],
    "users": [{"code": "A00001", "name": "ADMIN"}],
    "schemes": []
  },
  "defaults": {
    "supplierCode": "J00001",
    "storeCode": "S00001",
    "schemeCode": "",
    "purchaseAccCode": "P00002",
    "userCode": "A00001",
    "yearCode": ""
  },
  "warnings": []
}
```

Counts and master records are live values and vary by company/shop.

## 12. Purchase handoff

`GET /api/v1/document-import/jobs/{jobId}/purchase/handoff`

This resolves available header defaults, loads Item Master, runs live Calculate, and returns the same core envelope as Calculate Preview with `handoffCalculated=true`.

## 13. Calculate Preview

`POST /api/v1/document-import/jobs/{jobId}/purchase/calculate-preview`

Headers:

```http
Authorization: Bearer <integration-session-token>
Accept: application/json
Content-Type: application/json
X-Correlation-ID: calc-a3343292
```

Browser request:

```json
{
  "header": {
    "yearCode": "",
    "trnDate": "2026-09-23",
    "docDate": "2026-09-22",
    "docNo": "2026-2027/W/2022/015/01/032195",
    "tpPassNo": "tFLDR/2026-2027/07246088/P",
    "supplierCode": "J00001",
    "storeCode": "S00001",
    "schemeCode": "",
    "purchaseAccCode": "P00002",
    "userCode": "A00001",
    "taxMode": "ITEMWISE",
    "narration": "WB Form No. 3 import"
  }
}
```

The bridge then sends Madhushala Calculate an Item-Master-enriched request. For a loose-only row the wire contract is:

```json
{
  "shopCode": "hedu_test",
  "companyCode": "2",
  "schemeCode": "",
  "salesTaxRate": 0.0,
  "salesTaxIncludingFree": false,
  "items": [
    {
      "itemCode": "100010",
      "box": 0,
      "loose": 1,
      "free": 0,
      "boxRate": 4800.0,
      "looseRate": 100.0,
      "mrp": 150.0,
      "discount": 0.0,
      "cgst": 0.0,
      "sgst": 0.0,
      "cess": 0.0,
      "addCess": 0.0,
      "igst": 0.0,
      "t1Amt": 0.0,
      "t2Amt": 0.0,
      "t3Amt": 0.0,
      "t4Amt": 0.0,
      "etd": 0.0,
      "packing": 48,
      "t1Rate": 0.0,
      "t2Rate": 0.0,
      "t3Rate": 0.0,
      "t4Rate": 0.0
    }
  ]
}
```

The numeric commercial values above demonstrate the exact PDF field mapping. Production values are forwarded directly from the mapped Item Master record: `boxRate=itemmst.purchaseRateCase` and `looseRate=itemmst.purchaseRate`, with no bridge-side conversion or fallback.

Successful bridge response:

```json
{
  "success": true,
  "validated": true,
  "jobId": "a334329290324704812559a70407d0ea",
  "purchasePayload": {
    "shopCode": "hedu_test",
    "companyCode": "2",
    "yearCode": "",
    "trnDate": "2026-09-23",
    "docDate": "2026-09-22",
    "docNo": "2026-2027/W/2022/015/01/032195",
    "tpPassNo": "tFLDR/2026-2027/07246088/P",
    "supplierCode": "J00001",
    "storeCode": "S00001",
    "schemeCode": "",
    "purchaseAccCode": "P00002",
    "narration": "WB Form No. 3 import",
    "userCode": "A00001",
    "billType": "AI",
    "pType": "purchase",
    "taxMode": "ITEMWISE",
    "grossAmount": 100.0,
    "taxAmount": 0.0,
    "netAmount": 100.0,
    "discount": 0.0,
    "salesTaxOnMRP": 0.0,
    "roundOff": 0.0,
    "saletaxIncludingFree": false,
    "items": [
      {
        "itemCode": "100010",
        "itemName": "Absolut Vodka",
        "batchNo": "NA& Not Available",
        "box": 0,
        "loose": 1,
        "qnty": 1,
        "freeQnty": 0,
        "rate": 100.0,
        "mrp": 150.0,
        "itemAmount": 100.0,
        "discount": 0.0,
        "cgst": 0.0,
        "sgst": 0.0,
        "cess": 0.0,
        "addCess": 0.0,
        "igst": 0.0,
        "t1Amt": 0.0,
        "t2Amt": 0.0,
        "t3Amt": 0.0,
        "t4Amt": 0.0,
        "etd": 0.0,
        "cgstInptLdgr": "",
        "sgstInptLdgr": "",
        "cessInptLdgr": "",
        "adCessInptLdgr": "",
        "igstInptLdgr": ""
      }
    ],
    "taxes": []
  },
  "calculation": {
    "items": [
      {
        "itemCode": "100010",
        "qnty": 1,
        "rate": 100.0,
        "mrp": 150.0,
        "itemAmount": 100.0
      }
    ],
    "grossAmount": 100.0,
    "taxAmount": 0.0,
    "netAmount": 100.0
  },
  "calculationDebug": {
    "requestUrl": "/api/purchase/calculate",
    "requestHeaders": {
      "accept": "application/json",
      "Content-Type": "application/json",
      "Authorization": "[REDACTED]"
    },
    "request": {
      "shopCode": "hedu_test",
      "companyCode": "2",
      "schemeCode": "",
      "salesTaxRate": 0.0,
      "salesTaxIncludingFree": false,
      "items": [
        {
          "itemCode": "100010",
          "box": 0,
          "loose": 1,
          "free": 0,
          "boxRate": 4800.0,
          "looseRate": 100.0,
          "mrp": 150.0,
          "discount": 0.0,
          "cgst": 0.0,
          "sgst": 0.0,
          "cess": 0.0,
          "addCess": 0.0,
          "igst": 0.0,
          "t1Amt": 0.0,
          "t2Amt": 0.0,
          "t3Amt": 0.0,
          "t4Amt": 0.0,
          "etd": 0.0,
          "packing": 48,
          "t1Rate": 0.0,
          "t2Rate": 0.0,
          "t3Rate": 0.0,
          "t4Rate": 0.0
        }
      ]
    },
    "response": {
      "items": [
        {
          "itemCode": "100010",
          "qnty": 1,
          "rate": 100.0,
          "itemAmount": 100.0
        }
      ],
      "grossAmount": 100.0,
      "taxAmount": 0.0,
      "netAmount": 100.0
    }
  }
}
```

The bridge never exposes the real downstream Authorization token in `calculationDebug`.

Calculate failure response example:

```json
{
  "detail": {
    "stage": "CALCULATE",
    "message": "Madhushala purchase calculation failed.",
    "statusCode": 502,
    "requestUrl": "/api/purchase/calculate",
    "requestHeaders": {
      "accept": "application/json",
      "Content-Type": "application/json",
      "Authorization": "[REDACTED]"
    },
    "request": {
      "shopCode": "hedu_test",
      "companyCode": "2",
      "schemeCode": "",
      "salesTaxRate": 0.0,
      "salesTaxIncludingFree": false,
      "items": []
    },
    "response": "upstream error"
  }
}
```

## 14. Final Purchase Save

`POST /api/v1/document-import/jobs/{jobId}/purchase/save`

Browser headers and body are the same shape as Calculate Preview. The browser sends only `header`; it must not send the previously previewed purchase payload.

The backend:

1. resolves required header values;
2. reloads mapped Item Master details in the same company scope;
3. rebuilds source quantities;
4. re-runs Madhushala Calculate;
5. validates item coverage;
6. applies ITEMWISE/BILLWISE tax behavior;
7. checks duplicate bill number;
8. sends `POST /api/purchase/save`;
9. performs transaction/readback safeguards.

Representative response:

```json
{
  "success": true,
  "jobId": "a334329290324704812559a70407d0ea",
  "purchasePayload": {
    "shopCode": "hedu_test",
    "companyCode": "2",
    "yearCode": "",
    "trnDate": "2026-09-23",
    "docDate": "2026-09-22",
    "docNo": "2026-2027/W/2022/015/01/032195",
    "tpPassNo": "tFLDR/2026-2027/07246088/P",
    "supplierCode": "J00001",
    "storeCode": "S00001",
    "schemeCode": "",
    "purchaseAccCode": "P00002",
    "narration": "WB Form No. 3 import",
    "userCode": "A00001",
    "billType": "AI",
    "pType": "purchase",
    "taxMode": "ITEMWISE",
    "grossAmount": 100.0,
    "taxAmount": 0.0,
    "netAmount": 100.0,
    "discount": 0.0,
    "salesTaxOnMRP": 0.0,
    "roundOff": 0.0,
    "saletaxIncludingFree": false,
    "items": [
      {
        "itemCode": "100010",
        "itemName": "Absolut Vodka",
        "batchNo": "NA& Not Available",
        "box": 0,
        "loose": 1,
        "qnty": 1,
        "freeQnty": 0,
        "rate": 100.0,
        "mrp": 150.0,
        "itemAmount": 100.0,
        "discount": 0.0,
        "cgst": 0.0,
        "sgst": 0.0,
        "cess": 0.0,
        "addCess": 0.0,
        "igst": 0.0,
        "t1Amt": 0.0,
        "t2Amt": 0.0,
        "t3Amt": 0.0,
        "t4Amt": 0.0,
        "etd": 0.0,
        "cgstInptLdgr": "",
        "sgstInptLdgr": "",
        "cessInptLdgr": "",
        "adCessInptLdgr": "",
        "igstInptLdgr": ""
      }
    ],
    "taxes": []
  },
  "madhushalaResponse": {
    "success": true,
    "trnNo": "PUR-20260923-0001"
  },
  "job": {
    "id": "a334329290324704812559a70407d0ea",
    "status": "PURCHASE_SAVED"
  }
}
```

Before downstream `POST /api/purchase/save`, date-only values are normalized to ISO date-time strings, for example:

```json
{
  "trnDate": "2026-09-23T00:00:00",
  "docDate": "2026-09-22T00:00:00"
}
```

Helper fields removed before final Save include:

`packing`, `boxRate`, `looseRate`, `t1Rate`, `t2Rate`, `t3Rate`, `t4Rate`, `_canonicalQuantityVersion`.

## 15. Purchase transaction diagnostics

`GET /api/v1/document-import/jobs/{jobId}/purchase/transaction`

Response:

```json
{
  "transaction": null,
  "events": []
}
```

After/while Save is active these fields contain the recorded purchase transaction state and event history.

## Error status summary

- `400`: invalid file/type/body, missing required Purchase master values, invalid review input.
- `401`: missing/invalid/expired integration session.
- `409`: stale mapping, unmapped item, company-scope mismatch, duplicate/conflicting state.
- `422`: extraction/review semantic failure such as no positive Box/Loose quantity.
- `502`: upstream Madhushala/Calculate/Item Master failure or incomplete Calculate coverage.

## Source ownership rules

- PDF/QR review owns: item identity as extracted, ML, batch, box/case, loose/bottle.
- Madhushala Item Master owns: packing, purchase case rate, purchase loose rate, MRP/sales rate, commercial/tax master values.
- Madhushala Calculate owns: final rate, itemAmount, calculated tax amounts, gross/tax/net totals.
- Browser owns only editable Purchase header selections and explicit review edits.


## MP Delivery Challan printed financials

MP payment-summary financials are optional source evidence. They are populated only when a printed payment footer exists. Missing values are not inferred from Item Master or Calculate.

`totalAmount` and `subTotal` carry the printed Manufacturing Amount, `taxes.vat` carries the printed VAT Amount, `taxes.incomeTax` carries the printed Income Tax, and `totalNet` carries the printed payment Grand Total. `roundedOff` stays null because no rounding amount is printed. `taxes.tcs` stays null: Income Tax is not automatically renamed to TCS.

`sourceFinancials.dutyAmount` retains the liquor-table duty total separately. Never add that duty again to the payment Grand Total. Product sums and table totals must agree with the payment footer, and Manufacturing Amount + VAT + Income Tax must exactly equal payment Grand Total. Financial discrepancies mark rows for review and block financial handoff. Documents without a payment footer retain the previous nullable-financial behavior.

The handoff adds `sourceFinancials` and `calculationMappingRequired: ["incomeTax"]` for this MP source. These are source/validation fields, not authorization to add a tax to a calculated purchase. The inspected upstream Calculate schema has no incomeTax field and the active company tax-tag lookup returns HTTP 403; an upstream supported field or confirmed ledger mapping is still required. The frontend must handle this requirement before applying source Income Tax in Calculate/Save, and must avoid adding it again if the calculated result already includes it. This bridge does not silently populate TCS, overwrite Item Master tax amounts, or add a second tax adjustment.

Earlier import jobs retain their original stored source data; re-upload the PDF after deploying this feature to obtain the financial evidence.


### MP per-item manufacturing amounts

For a reconciled MP challan with a payment footer, each product is joined to its printed financial row by the full FL/CL registration reference, exact ML and exact case quantity. It is never joined by array position or fuzzy name. Missing, reused or ambiguous rows invalidate the source financials and block handoff. All matched Mfg Amount values must sum exactly to the printed Manufacturing Amount.

The printed per-line Mfg Amount is persisted as invoiceAmount/source amount and returned as both items[].amount and items[].itemAmount in the read-only handoff. sourceLineFinancials keeps the registration, manufacturingAmount, dutyAmount and vat separately. Duty is not substituted for the item amount, and VAT is not silently added to it. Existing Item Master rates and tax inputs remain unchanged. The Calculate adapter still rebuilds the upstream request from the master and reviewed quantities, and the authoritative calculated result owns the final purchase values. Other states retain their existing amount semantics. Old stored jobs are not automatically rewritten; re-upload after this release to obtain per-line evidence.
