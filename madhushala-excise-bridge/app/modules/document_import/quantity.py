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
