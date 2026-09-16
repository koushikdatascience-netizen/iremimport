from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from app.db import conn
from app.modules.document_import.purchase_context import build_purchase_context, up_supplier_hint
from app.modules.document_import.up_excise_qr import (
    _fetch_up_transport_page,
    _payload_from_html,
    parse_up_transport_url,
)


_REQUIRED_LABELS = {
    "supplierCode": "Supplier",
    "storeCode": "Store",
    "purchaseAccCode": "Purchase A/c",
    "userCode": "User",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _raw_value(job_id: str, *aliases: str) -> str:
    wanted = {"".join(ch for ch in alias.casefold() if ch.isalnum()) for alias in aliases}
    with conn() as db:
        rows = db.execute(
            "SELECT raw_data_json FROM import_items WHERE job_id=? ORDER BY created_at, id",
            (job_id,),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["raw_data_json"] or "{}")
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            normalized = "".join(ch for ch in str(key).casefold() if ch.isalnum())
            if normalized in wanted and _text(value):
                return _text(value)
    return ""


def _up_source_url(job: dict[str, Any]) -> str:
    source = _text(job.get("source_filename"))
    return source if parse_up_transport_url(source) else ""


def _transport_pass_from_job(job: dict[str, Any], job_id: str) -> str:
    from_rows = _raw_value(job_id, "transportPassNo", "tpPassNo", "transport_pass_no")
    if from_rows:
        return from_rows
    source = _up_source_url(job)
    if source:
        return _text(parse_up_transport_url(source).get("transportPassNo"))
    return ""


async def _supplier_hint_for_job(service: Any, job: dict[str, Any], job_id: str) -> str:
    """Recover the UP Excise consignor for old QR jobs created before it was persisted."""
    saved = _text(job.get("supplier_name"))
    if saved:
        return saved

    source = _up_source_url(job)
    if not source:
        return ""
    try:
        fetched = await _fetch_up_transport_page(source)
        hint = up_supplier_hint(_payload_from_html(fetched.get("text") or ""))
    except Exception:
        return ""
    if hint:
        service._update_job(job_id, supplier_name=hint)
    return _text(hint)


async def resolve_required_purchase_header(
    service: Any,
    session: dict[str, Any],
    job_id: str,
    header: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve Purchase header data without inventing Madhushala business values.

    tpPassNo and schemeCode are optional in the agreed PurchaseRequest contract.
    Supplier, Store, Purchase A/c and User are required and are resolved from
    Madhushala master data when an unambiguous default exists.
    """
    job = service.get_job(session, job_id)
    resolved = dict(header or {})

    # Optional but useful for transport-pass imports; recover it automatically.
    if not _text(resolved.get("tpPassNo")):
        resolved["tpPassNo"] = _transport_pass_from_job(job, job_id)

    needed = ("supplierCode", "storeCode", "purchaseAccCode", "userCode")
    if any(not _text(resolved.get(name)) for name in needed):
        supplier_hint = await _supplier_hint_for_job(service, job, job_id)
        try:
            context = await build_purchase_context(session, supplier_name=supplier_hint)
        except Exception:
            context = {}
        defaults = context.get("defaults") if isinstance(context, dict) else {}
        if not isinstance(defaults, dict):
            defaults = {}
        for name in (*needed, "schemeCode", "yearCode"):
            if not _text(resolved.get(name)) and _text(defaults.get(name)):
                resolved[name] = _text(defaults[name])

    # Preserve optional fields as empty strings so the upstream .NET DTO sees
    # the expected JSON properties rather than missing members.
    resolved.setdefault("tpPassNo", "")
    resolved.setdefault("schemeCode", "")

    missing = [name for name in _REQUIRED_LABELS if not _text(resolved.get(name))]
    if missing:
        labels = ", ".join(_REQUIRED_LABELS[name] for name in missing)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Purchase requires: {labels}. "
                "Select the missing Madhushala master values before Save Purchase."
            ),
        )

    return resolved
