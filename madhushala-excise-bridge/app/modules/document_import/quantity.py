from __future__ import annotations

import re
import unicodedata
from typing import Any


_EXACT_QUANTITY_KEYS = {
    "quantity",
    "qty",
    "qnty",
    "physicalqty",
    "physicalquantity",
    "physicalbottleqty",
    "physicalbottlequantity",
    "bottleqty",
    "bottlequantity",
    "bottles",
    "noofbottles",
    "noofbottlesdispatched",
    "bottlesdispatched",
    "noofbottlesrequested",
    "bottlesrequested",
    "totalbottles",
    "totalqty",
    "totalquantity",
    "dispatchqty",
    "dispatchedqty",
    "issuedqty",
    "physicalstock",
    "physicalstockqty",
    "physicalstockquantity",
}

_NON_QUANTITY_MARKERS = (
    "rate",
    "amount",
    "price",
    "mrp",
    "pack",
    "percase",
    "case",
    "carton",
    "ml",
    "litre",
    "liter",
    "bulk",
    "strength",
    "free",
    "discount",
    "tax",
    "percent",
    "size",
)


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]", "", text)


def _positive_int(value: Any) -> int:
    if value in (None, "", False):
        return 0
    try:
        text = unicodedata.normalize("NFKC", str(value)).replace(",", "")
        match = re.search(r"\d+(?:\.\d+)?", text)
        if not match:
            return 0
        number = float(match.group(0))
        return int(number) if number > 0 else 0
    except (TypeError, ValueError, ArithmeticError):
        return 0


def _looks_like_quantity_key(name: Any) -> bool:
    key = _key(name)
    if not key:
        return False
    if key in _EXACT_QUANTITY_KEYS:
        return True
    if any(marker in key for marker in _NON_QUANTITY_MARKERS):
        return False

    # Extractors and source invoices use many variations: "Physical Stock
    # (Btls.)", "Issue Qty.", "Bottle Count", "Dispatched Bottle Quantity", etc.
    if "qty" in key or "quantity" in key:
        return True
    if "bottle" in key or "btl" in key:
        return True
    if "physical" in key and any(marker in key for marker in ("stock", "count", "unit")):
        return True
    return False


def extract_physical_quantity(value: Any) -> int:
    """Return the first trustworthy physical bottle/unit quantity from extractor data.

    Only values whose *field names* look like physical quantities are considered.
    Product names, ML, rates, case packing, MRP, tax and amount fields are ignored,
    so a number such as "180" in "SIGNATURE 180ML" can never become quantity.
    """
    if isinstance(value, dict):
        # Canonical/quantity-like values at the current row win before nested
        # audit structures such as rawPdfRow.
        for name, candidate in value.items():
            if _looks_like_quantity_key(name):
                parsed = _positive_int(candidate)
                if parsed > 0:
                    return parsed

        for candidate in value.values():
            parsed = extract_physical_quantity(candidate)
            if parsed > 0:
                return parsed

    elif isinstance(value, list):
        for candidate in value:
            parsed = extract_physical_quantity(candidate)
            if parsed > 0:
                return parsed

    return 0


def resolve_document_quantity(value: Any) -> int:
    """Resolve canonical physical quantity for a PDF/image extraction row.

    First use explicitly named quantity fields. Then repair the specific
    LlamaParse continuation-page column shift seen on scanned invoices where:
      - sourcePage > 1
      - quantity/loose/box are missing
      - packing contains a small positive count
      - commercial columns (rate/boxRate/looseRate/mrp/discount) are empty

    The guarded fallback intentionally does *not* treat packing as quantity in
    ordinary rows, because real case packing must come from Item Master later.
    """
    explicit = extract_physical_quantity(value)
    if explicit > 0:
        return explicit
    if not isinstance(value, dict):
        return 0

    source_page = _positive_int(value.get("sourcePage"))
    packing = _positive_int(value.get("packing"))
    if source_page < 2 or packing <= 0:
        return 0

    for key in ("quantity", "qty", "qnty", "loose", "box"):
        if _positive_int(value.get(key)) > 0:
            return 0

    commercial_fields = ("rate", "boxRate", "looseRate", "mrp", "discount")
    if any(value.get(key) not in (None, "") for key in commercial_fields):
        return 0

    # A continuation-page physical count can be larger than a normal case
    # packing, so avoid an artificially tiny threshold. The surrounding
    # signature is the safety condition.
    return packing
