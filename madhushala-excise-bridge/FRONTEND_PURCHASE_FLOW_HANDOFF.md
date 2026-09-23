# Excise Import Frontend Handoff: QR/PDF -> Mapping -> Purchase

> Full request/response/header contract: see [DOCUMENT_IMPORT_API.md](DOCUMENT_IMPORT_API.md). That document is the canonical integration reference for payloads, headers, source-quantity semantics, Calculate Preview, and Purchase Save.

## Scope

This change is frontend-only. Do not change the current extraction, mapping, Item Master, Calculate, duplicate-check, Purchase Save, database, session, or document-processing pipelines.

The existing extraction/review screen is intentionally retained in the codebase as a fallback. It is no longer part of the normal successful QR/PDF flow.

## Required user flow

### QR

1. User uploads/scans QR or submits the QR URL.
2. Keep using the existing QR extraction endpoint.
3. If the extracted rows are valid, confirm the extracted rows with the existing review-confirm endpoint automatically.
4. Open Mapping immediately. Do not show the source/extracted-products preview screen in the normal successful path.
5. User reviews/maps/re-maps the extracted items.
6. User clicks **Next: Review Purchase**.
7. Open the Purchase screen with the existing purchase fields pre-filled.
8. Call **Calculate Preview** and render the returned `purchasePayload`.
9. User reviews/edits the Purchase form.
10. Any edit invalidates the previous validation.
11. Before final Save, run Calculate Preview again.
12. If validation succeeds, call the existing Purchase Save endpoint.
13. Show the Madhushala save result/TRN number.

### PDF / image

Use exactly the same post-extraction flow:

`Upload -> Extract -> automatic review confirm -> Mapping -> Next -> Purchase -> Calculate Preview -> Save`

Do not create a separate purchase implementation for PDF and QR.

## Existing endpoints to use

All endpoints already exist. No backend contract change is required.

### 1. QR extraction

`POST /api/v1/document-import/qr/extract`

Request:

```json
{
  "url": "https://..."
}
```

### 2. PDF/image extraction

`POST /api/v1/document-import/upload/batch/pdf`

or

`POST /api/v1/document-import/upload/batch/image`

Use the existing multipart `files` body.

### 3. Confirm extracted rows

`POST /api/v1/document-import/jobs/{jobId}/review/confirm`

This call must NOT be removed even though the review screen is skipped. It canonicalizes the document quantities and prepares the mapping workspace.

Request shape:

```json
{
  "items": [
    {
      "id": "import-row-id",
      "name": "Extracted item name",
      "brand": "Extracted brand",
      "ml": 750,
      "batchNo": "BATCH-1",
      "box": 2,
      "loose": 3
    }
  ]
}
```

If this call fails because extracted values require correction, show the existing extraction/review screen as the fallback. Do not delete that UI.

### 4. Save mapping

`POST /api/v1/document-import/jobs/{jobId}/mapping/save`

Keep the existing row-safe mapping request/behavior. Do not alter company scope or retry with another company code.

### 5. Purchase master/context

`GET /api/v1/document-import/purchase/context?supplierName={extractedSupplierName}`

Pass the extracted supplier name when it is available so the existing server-side matcher can select the correct Supplier Code. Use the current response to populate Supplier, Store, Scheme, Purchase A/c, User, and other current Purchase fields. Do not guess or switch company scope when a supplier/item lookup fails.

### 6. Validate / Calculate Preview

`POST /api/v1/document-import/jobs/{jobId}/purchase/calculate-preview`

Request:

```json
{
  "header": {
    "yearCode": "",
    "trnDate": "2026-09-16",
    "docDate": "2026-09-16",
    "docNo": "1451",
    "tpPassNo": "",
    "supplierCode": "J00001",
    "storeCode": "S00001",
    "schemeCode": "",
    "purchaseAccCode": "P00002",
    "userCode": "A00001",
    "taxMode": "ITEMWISE",
    "narration": ""
  }
}
```

The important response field is:

```json
{
  "success": true,
  "validated": true,
  "jobId": "...",
  "purchasePayload": {
    "...": "..."
  },
  "calculation": {},
  "calculationDebug": {}
}
```

**The frontend must render `purchasePayload` as the final Purchase payload preview.**

Do not reconstruct this payload client-side from mapping/extraction data. The server is authoritative because it adds Item Master values and the Madhushala Calculate result.

### 7. Final Save

`POST /api/v1/document-import/jobs/{jobId}/purchase/save`

The browser still sends only the header:

```json
{
  "header": {
    "yearCode": "",
    "trnDate": "2026-09-16",
    "docDate": "2026-09-16",
    "docNo": "1451",
    "tpPassNo": "",
    "supplierCode": "J00001",
    "storeCode": "S00001",
    "schemeCode": "",
    "purchaseAccCode": "P00002",
    "userCode": "A00001",
    "taxMode": "ITEMWISE",
    "narration": ""
  }
}
```

Do **not** POST the previewed `purchasePayload` back from the browser. The backend intentionally rebuilds and re-runs Calculate immediately before Save so stale mappings, changed headers, or changed Madhushala values cannot bypass validation.

## Final Madhushala Purchase payload

QR and PDF use the **same final JSON contract**. Only the source-derived values differ.

Representative ITEMWISE payload:

```json
{
  "shopCode": "hedu_test",
  "companyCode": "2",
  "yearCode": "",
  "trnDate": "2026-09-16",
  "docDate": "2026-09-16",
  "docNo": "1451",
  "tpPassNo": "",
  "supplierCode": "J00001",
  "storeCode": "S00001",
  "schemeCode": "",
  "purchaseAccCode": "P00002",
  "narration": "",
  "userCode": "A00001",
  "billType": "AI",
  "pType": "purchase",
  "taxMode": "ITEMWISE",
  "grossAmount": 500.0,
  "taxAmount": 110.0,
  "netAmount": 610.0,
  "discount": 0.0,
  "salesTaxOnMRP": 0.0,
  "roundOff": 0.0,
  "saletaxIncludingFree": false,
  "items": [
    {
      "itemCode": "100003",
      "itemName": "100 PIPER DELUX 180ML",
      "batchNo": "1",
      "box": 1,
      "loose": 0,
      "qnty": 48,
      "freeQnty": 0,
      "rate": 10.42,
      "mrp": 280.0,
      "itemAmount": 500.0,
      "discount": 0.0,
      "cgst": 0.0,
      "sgst": 0.0,
      "cess": 0.0,
      "addCess": 0.0,
      "igst": 0.0,
      "t1Amt": 100.0,
      "t2Amt": 0.0,
      "t3Amt": 0.0,
      "t4Amt": 0.0,
      "etd": 10.0,
      "cgstInptLdgr": "",
      "sgstInptLdgr": "",
      "cessInptLdgr": "",
      "adCessInptLdgr": "",
      "igstInptLdgr": ""
    }
  ],
  "taxes": []
}
```

The following are calculation/helper fields only and are removed before the final Save payload:

`packing`, `boxRate`, `looseRate`, `t1Rate`, `t2Rate`, `t3Rate`, `t4Rate`, `_canonicalQuantityVersion`.

For `ITEMWISE`, `taxes` is an empty array. For `BILLWISE`, the existing backend creates the bill-wise taxes and applies the existing item-tax rules. The frontend must not reproduce this logic.

## Case / loose financial semantics

Reviewed quantities must stay separate through mapping and Calculate:

```text
box   = source cases/cartons
loose = source single bottles/units
qnty  = (box * Item Master packing) + loose
```

For **PDF imports**, rate ownership is exact:

```text
boxRate   = Item Master purchaseRateCase
looseRate = Item Master purchaseRate
```

Do not derive, divide, multiply, or substitute one PDF rate from the other. A zero Item Master rate must remain zero in the Calculate request. PDF financial values are not calculated locally before Calculate; Madhushala Calculate is authoritative for final `rate`, `itemAmount`, taxes and totals.

Legacy non-PDF flows retain their existing behavior.

## Date format note

The Calculate Preview `purchasePayload` can contain date-only values such as:

```json
{
  "trnDate": "2026-09-16",
  "docDate": "2026-09-16"
}
```

Immediately before the downstream Madhushala `POST /api/purchase/save`, the existing client normalizes them to ISO date-time strings:

```json
{
  "trnDate": "2026-09-16T00:00:00",
  "docDate": "2026-09-16T00:00:00"
}
```

This is expected and requires no frontend conversion.

## Frontend rendering requirements

On the final Purchase screen, show:

- the existing editable Purchase header fields;
- a compact item table using the validated `purchasePayload.items`;
- Item Code, Item Name, Batch, Box, Loose, Qty, Rate, MRP, and Amount;
- Gross Amount, Tax Amount, Net Amount, and item count;
- the exact validated `purchasePayload` JSON under technical details;
- **Validate Purchase** and **Save Purchase**;
- **Back to Mapping**.

When any Purchase field changes, the old validation must be invalidated. Save must remain protected by the existing revalidation-before-save behavior.

## Do not change

Do not change or duplicate:

- backend endpoints;
- QR extraction;
- PDF/image extraction;
- mapping persistence;
- Madhushala Item Master enrichment;
- companyCode/shopCode scope;
- Calculate logic;
- duplicate-bill checks;
- Purchase Save logic;
- tax calculations;
- quantity semantics;
- yearCode behavior;
- batch preservation;
- current error handling and idempotency/transaction safeguards.

The frontend owns presentation and navigation only.
