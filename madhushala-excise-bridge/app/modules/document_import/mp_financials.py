"""Printed MP challan financial evidence, separate from Purchase Calculate."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any


MONEY = r"([0-9][0-9,]*\.[0-9]{2})"


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
