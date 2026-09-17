from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


_LABEL_TO_EXCISE_FIELD = {
    "SP FEE": "specialPurposeFee",
    "SPECIAL PURPOSE FEE": "specialPurposeFee",
    "SPECIAL LEVY": "specialPurposeFee",
    "ROUNDING OFF": "roundOffGovt",
    "ROUND OFF": "roundOffGovt",
    "ROUNDING": "roundOffGovt",
    "TCS": "tcs",
    "OTHERS": "others",
    "OTHER": "others",
    "RETAILER MARGIN": "retailerMargin",
}


def _normalize_label(value: Any) -> str:
    text = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper())
    return re.sub(r"\s+", " ", text).strip()


def _as_decimal(value: Any) -> Decimal | None:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _case_amount(per_bottle_value: Any, bottles_per_case: Any) -> str:
    value = _as_decimal(per_bottle_value)
    packing = _as_decimal(bottles_per_case)
    if value is None or packing is None or packing <= 0:
        return ""
    amount = (value * packing).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(amount, ".2f")


def apply_excise_tax_tags(payload: dict[str, Any], tax_tags: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill t1..t4 from Madhushala TaxTag labels and WB Excise per-bottle values.

    TaxTag defines what each generic T field means (for example T1 = SP FEE).
    The actual amount comes from the corresponding WB Excise field and is converted
    to a per-case amount by multiplying it by bottlesPerCase.

    Missing source fields, such as TCS when WB Excise does not expose it, stay blank.
    """
    result = dict(payload)
    for code in ("T1", "T2", "T3", "T4"):
        result[code.casefold()] = ""

    candidates: dict[str, dict[str, Any]] = {}
    for row in tax_tags or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("taxCode") or "").strip().upper()
        if code not in {"T1", "T2", "T3", "T4"}:
            continue
        item_type = str(row.get("itemType") or "").strip().upper()
        current = candidates.get(code)
        # Prefer AI-specific configuration over generic ALL configuration.
        if current is None or (item_type == "AI" and str(current.get("itemType") or "").strip().upper() != "AI"):
            candidates[code] = row

    bottles_per_case = result.get("bottlesPerCase")
    for code, row in candidates.items():
        label = _normalize_label(row.get("taxLabel"))
        source_field = _LABEL_TO_EXCISE_FIELD.get(label)
        if not source_field:
            continue
        result[code.casefold()] = _case_amount(result.get(source_field), bottles_per_case)

    return result
