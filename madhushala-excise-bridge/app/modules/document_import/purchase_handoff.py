from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except Exception:
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _raw(row: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(row.get("raw_data_json") or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _first(raw: dict[str, Any], *names: str) -> Any:
    normalized = {
        "".join(ch for ch in str(key).casefold() if ch.isalnum()): value
        for key, value in raw.items()
    }
    for name in names:
        key = "".join(ch for ch in name.casefold() if ch.isalnum())
        value = normalized.get(key)
        if value not in (None, ""):
            return value
    return None


def build_purchase_handoff(
    service: Any,
    session: dict[str, Any],
    job_id: str,
) -> dict[str, Any]:
    """Build a non-blocking prefill payload for the real Madhushala Purchase UI.

    This deliberately does not resolve Purchase masters and does not call
    Madhushala Calculate. Supplier/store/account/user selection belongs to the
    real Madhushala Purchase screen after the redirect.
    """
    job = service.get_job(session, job_id)
    rows = service.get_items(session, job_id)
    if not rows:
        raise HTTPException(status_code=400, detail="No items are available for Purchase handoff")

    items: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in rows:
        mapped = _text(row.get("mapped_item_code"))
        if not mapped:
            missing.append(_text(row.get("raw_name") or row.get("normalized_name") or row.get("id")))
            continue

        raw = _raw(row)
        box = _int(row.get("box")) or _int(_first(raw, "canonicalBox", "box", "boxes", "case", "cases"))
        loose = _int(row.get("loose")) or _int(_first(raw, "canonicalLoose", "loose", "looseQty"))
        quantity = _float(row.get("quantity"))
        if quantity <= 0:
            quantity = float(box + loose)

        items.append(
            {
                "itemCode": mapped,
                "itemName": _text(row.get("raw_name") or row.get("normalized_name")),
                "exciseItemCode": _text(row.get("excise_item_code")),
                "batchNo": _text(_first(raw, "batchNo", "batch")),
                "box": box,
                "loose": loose,
                "qnty": quantity,
                "quantity": quantity,
                "ml": _int(row.get("ml")),
                "packing": _int(row.get("packing")),
                "rate": _float(row.get("rate")),
                "mrp": _float(row.get("mrp")),
                "amount": _float(row.get("amount")),
            }
        )

    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"Map all items before opening Purchase: {', '.join(missing[:5])}",
        )

    payload = {
        "shopCode": _text(session.get("shop_code")),
        "companyCode": _text(session.get("company_code")),
        "billType": _text(session.get("bill_type")) or "AI",
        "jobId": job_id,
        "sourceType": _text(job.get("source_type")),
        "docNo": _text(job.get("invoice_number")),
        "docDate": _text(job.get("invoice_date")),
        "supplierName": _text(job.get("supplier_name")),
        "narration": "Excise document import",
        "items": items,
    }
    return {"success": True, "jobId": job_id, "purchasePayload": payload}
