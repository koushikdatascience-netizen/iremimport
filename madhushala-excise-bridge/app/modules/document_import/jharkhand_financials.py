"""Printed Jharkhand invoice evidence; never uses Item Master commercial data."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from app.modules.document_import.schemas import ExtractedDocument


MONEY_FIELDS = ("invoiceAmount", "grandTotal", "vat", "aed", "vatOnAed", "liquorTransportation", "aedIncludingVat")
MONEY = {"type": ["string", "null"]}
FINANCIAL_SCHEMA = {
    "type": "object", "properties": {
        "invoiceNumber": {"type": ["string", "null"], "description": "Full printed Invoice No, not Permit No"},
        "transportPassNo": {"type": ["string", "null"], "description": "Printed Permit No in header; join its wrapped lines, keeping the district prefix and separators"},
        "totalCases": {"type": ["string", "number", "null"], "description": "Printed Total Qty case count"},
        "lastRowNumber": {"type": ["integer", "null"]},
        "hasPrintedInvoiceSummary": {"type": "boolean", "description": "True only if a printed final Total Qty/Amount/Grand Total summary is visible on this page. False on product-only pages. Never sum rows to create a summary."},
        "invoiceAmount": {**MONEY, "description": "Printed rightmost Amount table total / footer Amount"},
        "grandTotal": {**MONEY, "description": "Printed footer Grand Total"},
        "vat": {**MONEY, "description": "Printed VAT table total"},
        "tableAedWithoutVat": {**MONEY, "description": "Total on Total Qty row underneath TABLE column AED w/o VAT. Excludes VAT On AED. Never use the separately labeled footer AED."},
        "vatOnAed": {**MONEY, "description": "Printed VAT On AED table total"},
        "liquorTransportation": {**MONEY, "description": "Printed I.T. Calculated footer amount"},
        "footerAed": {**MONEY, "description": "Value next to AED in footer after Amount and VAT; not the AED w/o VAT table total"},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "sourceRowNumber": {"type": "integer"},
            "sourceAmountText": {"type": ["string", "null"]},
        }, "required": ["sourceRowNumber", "sourceAmountText"]}},
    }, "required": ["invoiceNumber", "transportPassNo", "totalCases", "lastRowNumber", "hasPrintedInvoiceSummary",
                    "invoiceAmount", "grandTotal", "vat", "tableAedWithoutVat", "vatOnAed", "liquorTransportation", "footerAed", "items"],
}

FINANCIAL_PROMPT = (
    "Read printed source financial evidence from this Jharkhand State Beverages scanned invoice page. "
    "Do not calculate or infer any monetary value. Return every visible product serial as sourceRowNumber. "
    "Copy its RIGHTMOST column headed Amount into sourceAmountText, preserving decimal digits. "
    "Columns from left to right after Quantity (Cases) are Wholesale Price (W/O ETD,VAT,AED), "
    "Taxable Value, VAT, AED w/o VAT, VAT On AED, then Amount. Taxable Value is NOT Amount. "
    "A continuation page may have no headers: use the same rightmost Amount column. "
    "Never substitute Wholesale Price, Taxable Value, VAT, AED or VAT On AED for Amount. "
    "Return null for unreadable cells; retain their serial. Do not include the Total Qty summary as a product. "
    "Read invoiceNumber and transportPassNo from the printed Invoice No and Permit No headers, "
    "including any prefix and full separators. Never construct a permit from the invoice number. "
    "Read lastRowNumber as the last visible product serial and totalCases only from the printed Total Qty. "
    "Read ALL requested header and financial summary fields as well as items. Use null only when absent or unreadable. "
    "The Permit No may wrap onto a second line: join those printed lines with no whitespace. "
    "On the final page, invoiceAmount is the rightmost Amount table total, also labeled "
    "Amount in the footer. grandTotal is the separate footer Grand Total. vat is the VAT table total, "
    "tableAedWithoutVat is the AED w/o VAT TABLE total, vatOnAed is the VAT On AED table total. The footer AED may be "
    "a different combined value: retain it only as footerAed, never as tableAedWithoutVat. "
    "liquorTransportation is the footer I.T. Calculated amount if legible. "
    "On a product-only page with no printed final summary, set hasPrintedInvoiceSummary=false, totalCases=null "
    "and all summary money fields null. Do NOT add up product rows. Only return actually printed figures "
    "from this page. No Item Master, rate multiplication or guessed totals."
)


def money(value: Any) -> Decimal | None:
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"\d+(?:,\d{2,3})*(?:\.\d{1,2})?", text):
        return None
    try:
        result = Decimal(text.replace(",", ""))
        return result.quantize(Decimal("0.01")) if result.is_finite() else None
    except InvalidOperation:
        return None


def apply_financial_evidence(document: ExtractedDocument, pages: list[dict[str, Any]]) -> ExtractedDocument:
    """Join on page+printed serial, then verify completeness and source arithmetic."""
    errors: list[str] = []
    warnings: list[str] = []
    evidence: dict[tuple[int, int], dict[str, Any]] = {}
    totals: dict[str, str] = {}
    permits: set[str] = set()
    last_serial = 0
    total_cases: Decimal | None = None
    for page in pages:
        page_no, data = page["page"], page["data"]
        for row in data.get("items") or []:
            if not isinstance(row, dict):
                errors.append("Unreadable financial product row")
                continue
            try:
                serial = int(str(row.get("sourceRowNumber")))
            except (TypeError, ValueError):
                errors.append("Unreadable financial row serial")
                continue
            key = (page_no, serial)
            if key in evidence:
                errors.append(f"Duplicate financial serial {serial} on page {page_no}")
            evidence[key] = row
        try:
            last_serial = max(last_serial, int(data.get("lastRowNumber") or 0))
        except (ValueError, TypeError):
            errors.append("Unreadable final product serial")
        cases = money(data.get("totalCases"))
        if cases is not None:
            if total_cases is not None and total_cases != cases:
                errors.append("Conflicting printed case totals")
            total_cases = cases
        financials = data.get("invoiceFinancials") or {}
        if not isinstance(financials, dict):
            errors.append("Unreadable printed financial summary")
            financials = {}
        for name, raw in financials.items():
            if name not in MONEY_FIELDS or raw in (None, ""):
                continue
            value = money(raw)
            if value is None:
                errors.append(f"Unreadable printed {name}")
                continue
            if name in totals and Decimal(totals[name]) != value:
                errors.append(f"Conflicting printed {name}")
            totals[name] = str(value)
        permit = re.sub(r"\s+", "", str(data.get("transportPassNo") or ""))
        if permit:
            permits.add(permit)
        invoice = str(data.get("invoiceNumber") or "").strip()
        if invoice and document.invoiceNumber and re.sub(r"\s+", "", invoice) != re.sub(r"\s+", "", document.invoiceNumber):
            errors.append("Financial invoice header conflicts with extracted invoice")

    items = []
    used: set[tuple[int, int]] = set()
    serials = []
    amount_sum = Decimal(0)
    for item in document.items:
        payload = item.model_dump()
        try:
            key = (int(payload.get("sourcePage") or 0), int(str(payload.get("sourceRowNumber"))))
        except (TypeError, ValueError):
            key = (0, 0)
        row = evidence.get(key, {})
        value = money(row.get("sourceAmountText"))
        if key in used or key == (0, 0):
            errors.append("Missing or duplicate source product serial")
        used.add(key)
        serials.append(key[1])
        if value is None or value <= 0:
            errors.append(f"Printed Amount unreadable for row {key[1]}")
        else:
            amount_sum += value
        payload.update(sourceState="JHARKHAND", invoiceAmount=str(value) if value is not None else None,
                       amount=str(value) if value is not None else None,
                       sourceAmountText=row.get("sourceAmountText"))
        items.append(payload)
    if not items or set(evidence) != used or sorted(serials) != list(range(1, last_serial + 1)):
        errors.append("Printed financial rows are incomplete or do not match source products")
    for required in ("invoiceAmount", "grandTotal", "vat", "aed", "vatOnAed"):
        if required not in totals:
            errors.append(f"Missing printed {required}")
    if totals.get("invoiceAmount") is not None and amount_sum != Decimal(totals["invoiceAmount"]):
        errors.append("Printed line Amount sum does not reconcile with printed invoice Amount")
    if total_cases is None or sum(Decimal(str(item.box or 0)) for item in document.items) != total_cases:
        errors.append("Source cases do not reconcile with printed Total Qty")
    if len(permits) != 1 or not re.fullmatch(r"[A-Z]{2,6}-\d{4}-\d{4}/\d+", next(iter(permits), "")):
        errors.append("Printed Jharkhand permit is missing or conflicting")
    if all(key in totals for key in ("invoiceAmount", "liquorTransportation", "grandTotal")):
        if Decimal(totals["invoiceAmount"]) + Decimal(totals["liquorTransportation"]) != Decimal(totals["grandTotal"]):
            errors.append("Printed grand total does not reconcile with Amount and I.T. Calculated")
    if all(key in totals for key in ("aed", "vatOnAed", "aedIncludingVat")):
        if Decimal(totals["aed"]) + Decimal(totals["vatOnAed"]) != Decimal(totals["aedIncludingVat"]):
            warnings.append("Printed AED footer differs from AED table total plus VAT on AED; source values retained")
    validation = {"status": "review_required" if errors else "verified", "errors": sorted(set(errors)),
                  "warnings": warnings, "lineCount": len(items), "lineAmountSum": str(amount_sum), "printedAmount": totals.get("invoiceAmount")}
    for item in items:
        item.update(invoiceFinancials=totals, sourceFinancialValidation=validation,
                    transportPassNo=next(iter(permits)) if len(permits) == 1 else None)
    payload = document.model_dump()
    payload.update(items=items, invoiceFinancials=totals, sourceFinancialValidation=validation,
                   transportPassNo=next(iter(permits)) if len(permits) == 1 else None)
    return ExtractedDocument.model_validate(payload)


def require_verified_financials(rows: list[dict[str, Any]]) -> None:
    """Do not turn a flagged scanned invoice into a seemingly complete handoff."""
    import json
    for row in rows:
        try:
            raw = json.loads(row.get("raw_data_json") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        if str(raw.get("sourceState") or "").upper() != "JHARKHAND":
            continue
        validation = raw.get("sourceFinancialValidation")
        if isinstance(validation, dict) and validation.get("status") != "verified":
            raise HTTPException(status_code=409, detail="Jharkhand printed financials require review: " + "; ".join(validation.get("errors") or ["incomplete financial evidence"]))
