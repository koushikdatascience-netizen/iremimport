from __future__ import annotations

import re
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


def _raw_amount(value: Any) -> str:
    """Return the captured Excise value unchanged apart from surrounding whitespace.

    TaxTag only decides which source field belongs in T1..T4. The bridge must not
    convert per-bottle values to per-case values because some Excise rows do not
    expose bottlesPerCase and Madhushala expects the captured value itself.
    """
    if value in (None, ""):
        return ""
    return str(value).strip()


def apply_excise_tax_tags(payload: dict[str, Any], tax_tags: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill t1..t4 from Madhushala TaxTag labels using raw WB Excise values.

    TaxTag defines what each generic T field means (for example T1 = SP FEE).
    The corresponding captured Excise field is copied directly. No multiplication
    by bottlesPerCase or other case-level conversion is performed.

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

    for code, row in candidates.items():
        label = _normalize_label(row.get("taxLabel"))
        source_field = _LABEL_TO_EXCISE_FIELD.get(label)
        if not source_field:
            continue
        result[code.casefold()] = _raw_amount(result.get(source_field))

    return result
