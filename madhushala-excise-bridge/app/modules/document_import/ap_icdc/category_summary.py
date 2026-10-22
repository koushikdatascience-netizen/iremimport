"""Drop Andhra Pradesh category-summary totals before product normalization.

Intentionally opt-in based on invoice identity or INDENT filename AND a paired
IMFL/FL + Beer summary signature. No global name-based filtering.
"""
from __future__ import annotations
import re
from app.modules.document_import.schemas import ExtractedDocument

IMFL = re.compile(r"^IMFL\s*/\s*FL$", re.I)
BEER = re.compile(r"^BEER$", re.I)
BRAND_CODE = re.compile(r"^[A-Z]?\d{3,4}$", re.I)


def _label(item):
    return " ".join(str(item.itemName or item.brand or "").split()).strip()


def _quantity(item):
    for val in (item.box, item.quantity):
        if val is None:
            continue
        try:
            parsed = float(str(val).replace(",", ""))
            if parsed.is_integer() and parsed >= 0:
                return int(parsed)
        except (ValueError, TypeError):
            pass
    return None


def _ap_context(document, filename):
    invoice_number = str(document.invoiceNumber or "").strip().upper()
    source_state = str(getattr(document, "sourceState", "") or "").strip().upper().replace(" ", "_")
    name = str(filename or "").lower()
    return (
        invoice_number.startswith("ICDC")
        or source_state in {"ANDHRA_PRADESH", "AP"}
        or bool(re.search(r"(?:^|[^a-z])indent\s*\d*\.pdf$", name))
    )


def strip_ap_category_totals(document: ExtractedDocument, filename: str):
    """Return (document, audit); fail closed on ambiguous AP category rows.

    Category labels alone are insufficient to identify AP; verify the document
    context and BOTH category rows, with numerical quantities.
    """
    items = list(document.items or [])
    if not _ap_context(document, filename):
        return document, {"apSummaryFiltered": False}

    imfl = [(i, x) for i, x in enumerate(items) if IMFL.fullmatch(_label(x))]
    beer = [(i, x) for i, x in enumerate(items) if BEER.fullmatch(_label(x))]
    if not imfl and not beer:
        return document, {"apSummaryFiltered": False}
    if len(imfl) != 1 or len(beer) != 1:
        raise ValueError("Ambiguous AP category-summary rows: manual review required")

    found = [(imfl[0][0], imfl[0][1], "imfl_fl"), (beer[0][0], beer[0][1], "beer")]
    totals = {key: _quantity(item) for _, item, key in found}
    if any(v is None for v in totals.values()):
        raise ValueError("AP category-summary quantity unreadable: manual review required")

    excluded = {idx for idx, _, _ in found}
    products = [item for idx, item in enumerate(items) if idx not in excluded]
    if not products:
        raise ValueError("AP summary found without product rows")
    actual = []
    for product in products:
        q = _quantity(product)
        if q is None:
            raise ValueError("AP product case quantity unreadable: manual review required")
        actual.append(q)
    if sum(actual) != sum(totals.values()):
        raise ValueError(
            f"AP quantity-summary mismatch: product cases {sum(actual)} != category cases {sum(totals.values())}; purchase blocked"
        )
    payload = document.model_dump()
    payload["items"] = [x.model_dump() for x in products]
    # Preserve the authoritative category totals for audit; never map them.
    payload["apCategorySummary"] = {
        "imfl_fl_cases": totals["imfl_fl"],
        "beer_cases": totals["beer"],
        "total_cases": sum(totals.values()),
        "filtered_product_rows": len(products),
        "excluded_labels": ["IMFL/FL", "Beer"],
    }
    return ExtractedDocument.model_validate(payload), {
        "apSummaryFiltered": True, **payload["apCategorySummary"]
    }
