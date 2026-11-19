"""Printed MP challan financial evidence, separate from Purchase Calculate."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.modules.document_import.schemas import ExtractedProduct


MONEY = r"([0-9][0-9,]*\.[0-9]{2})"

REGISTRATION = r"\[\s*(FL|CL)/([0-9]{4})\s*[-\s]*([0-9]{4})/([0-9]+)\]"


def attach_mp_line_amounts(
    page_texts: list[str], products: list[ExtractedProduct], financials: dict[str, Any] | None,
) -> list[ExtractedProduct]:
    """Join printed monetary evidence by registration, ML and cases, not order."""
    if financials is None:
        return products  # Older layouts without a verified footer remain compatible.
    evidence: dict[tuple[str, int, int], list[dict[str, str]]] = {}
    pattern = (REGISTRATION + rf"\s*(\d{{2,5}})\s*\([^)]*\)\s*(\d+)"
               + rf"\s*{MONEY}\s*{MONEY}\s*{MONEY}")
    for text in page_texts:
        for match in re.finditer(pattern, text, re.I):
            kind, first, second, number, ml, cases, duty, mfg, vat = match.groups()
            registration = f"{kind.upper()}/{first}-{second}/{number}"
            row = {"registration": registration, "dutyAmount": duty.replace(",", ""),
                   "manufacturingAmount": mfg.replace(",", ""), "vat": vat.replace(",", "")}
            evidence.setdefault((registration, int(ml), int(cases)), []).append(row)

    errors = list((financials.get("validation") or {}).get("errors") or [])
    matched: list[dict[str, str] | None] = []
    used: set[tuple[str, int, int]] = set()
    for item in products:
        raw = item.model_dump().get("rawPdfRow") or {}
        # table.extract can displace spaces/hyphens, but the printed registration
        # digits are stable. Ignore separators only inside that explicit ID.
        clean = re.sub(r"[^A-Za-z0-9]", "", str(raw.get("Label Name") or item.itemName))
        match = re.search(r"(FL|CL)(\d{4})(\d{4})(\d+)$", clean, re.I)
        registration = f"{match[1].upper()}/{match[2]}-{match[3]}/{match[4]}" if match else ""
        key = registration, int(item.ml or 0), int(item.box or 0)
        candidates = evidence.get(key, [])
        if len(candidates) != 1 or key in used:
            errors.append(f"Missing or ambiguous printed financial row for {registration or 'unknown registration'}")
            matched.append(None)
        else:
            used.add(key)
            matched.append(candidates[0])
    if sum(len(rows) for rows in evidence.values()) != len(products) or len(used) != len(products):
        errors.append("Not every printed financial row matched exactly one MP product")
    if not errors:
        total = sum((Decimal(row["manufacturingAmount"]) for row in matched if row), Decimal(0))
        if total != Decimal(str(financials["manufacturingAmount"])):
            errors.append("Matched item manufacturing amounts do not sum to the printed subtotal")
    financials["reconciled"] = not errors
    financials["validation"] = {"status": "review_required" if errors else "verified", "errors": errors}
    result = []
    for item, evidence_row in zip(products, matched):
        row = item.model_dump()
        row["sourceFinancialValidation"] = financials["validation"]
        row["invoiceFinancials"] = financials
        if not errors and evidence_row:
            row["invoiceAmount"] = evidence_row["manufacturingAmount"]
            row["sourceLineFinancials"] = evidence_row
        result.append(ExtractedProduct.model_validate(row))
    return result


def extract_mp_financials(
    page_texts: list[str], product_count: int, cases: int,
) -> dict[str, Any] | None:
    # The liquor table's Grand Total includes excise duty. The payment footer
    # has a different Grand Total; never use the former as the payable amount.
    footers = [text for text in page_texts if re.search(
        r"(?:Manufacturing\s+Amount|VAT\s+Amount|Income\s+Tax)\s*:", text, re.I,
    )]
    if not footers:
        return None

    errors: list[str] = []
    values: dict[str, Decimal] = {}
    labels = {
        "manufacturingAmount": r"Manufacturing\s+Amount",
        "vat": r"VAT\s+Amount",
        "incomeTax": r"Income\s+Tax",
        "grandTotal": r"Grand\s+Total",
    }
    for key, label in labels.items():
        matches = [Decimal(value.replace(",", "")) for text in footers
                   for value in re.findall(
                       rf"^\s*{label}\s*:\s*{MONEY}\s*$", text, re.I | re.M,
                   )]
        if not matches:
            errors.append(f"Missing printed {key}")
        elif len(set(matches)) != 1:
            errors.append(f"Conflicting printed {key}")
        else:
            values[key] = matches[0]

    text = "\n".join(page_texts)
    # Independently reconcile product amounts from the text layer. This also
    # avoids table.extract() moving decimal points to the end of numeric cells.
    rows = re.findall(
        rf"\[(?:FL|CL)/\d{{4}}\s*[-\u2013]\s*\d{{4}}/\d+\]"
        rf"\s*\d{{2,5}}\s*\([^)]*\)\s*(\d+)\s*{MONEY}\s*{MONEY}\s*{MONEY}",
        text, re.I,
    )
    table_totals = re.findall(
        rf"^\s*Grand\s+Total\s*\n\s*(\d+)\s*{MONEY}\s*{MONEY}\s*{MONEY}\s*$",
        text, re.I | re.M,
    )
    duty: Decimal | None = None
    if len(rows) != product_count:
        errors.append("Printed financial row count does not match extracted products")
    if sum(int(row[0]) for row in rows) != cases:
        errors.append("Printed financial row cases do not match extracted cases")
    sums = [sum((Decimal(row[i].replace(",", "")) for row in rows), Decimal(0))
            for i in (1, 2, 3)]
    if len(table_totals) != 1:
        errors.append("Missing or ambiguous printed table Grand Total")
    else:
        count, *money = table_totals[0]
        printed = [Decimal(value.replace(",", "")) for value in money]
        duty = printed[0]
        if int(count) != cases or sums != printed:
            errors.append("Product sums do not match printed table totals")
        for key, value in zip(("manufacturingAmount", "vat"), printed[1:]):
            if key in values and values[key] != value:
                errors.append(f"Table and payment footer disagree on {key}")
    if all(key in values for key in labels):
        if sum((values[key] for key in ("manufacturingAmount", "vat", "incomeTax")),
               Decimal(0)) != values["grandTotal"]:
            errors.append("Manufacturing amount + VAT + Income Tax != payment Grand Total")

    def amount(key: str) -> str | None:
        return format(values[key], ".2f") if key in values else None

    return {
        "manufacturingAmount": amount("manufacturingAmount"),
        "dutyAmount": format(duty, ".2f") if duty is not None else None,
        "incomeTax": amount("incomeTax"),
        "taxes": {"vat": amount("vat"), "incomeTax": amount("incomeTax")},
        "totalAmount": amount("manufacturingAmount"),
        "subTotal": amount("manufacturingAmount"),
        "totalNet": amount("grandTotal"),
        "roundedOff": None,
        "reconciled": not errors,
        "validation": {"status": "review_required" if errors else "verified", "errors": errors},
        # The current Calculate schema has no incomeTax field. Preserve the
        # evidence, but do not silently alias it to TCS or apply it twice.
        "calculationMappingRequired": ["incomeTax"] if values.get("incomeTax") else [],
    }
