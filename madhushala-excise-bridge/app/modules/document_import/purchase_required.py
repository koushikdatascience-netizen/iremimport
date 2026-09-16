from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from app.db import conn
from app.modules.document_import.purchase_context import build_purchase_context
from app.modules.document_import.up_excise_qr import parse_up_transport_url


_REQUIRED_LABELS = {
    "tpPassNo": "TP Pass No",
    "supplierCode": "Supplier",
    "storeCode": "Store",
    "schemeCode": "Scheme",
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


def _transport_pass_from_job(job: dict[str, Any], job_id: str) -> str:
    from_rows = _raw_value(job_id, "transportPassNo", "tpPassNo", "transport_pass_no")
    if from_rows:
        return from_rows
    source = _text(job.get("source_filename"))
    if source:
        return _text(parse_up_transport_url(source).get("transportPassNo"))
    return ""


async def resolve_required_purchase_header(
    service: Any,
    session: dict[str, Any],
    job_id: str,
    header: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fill fields the live Madhushala purchase API has proven to require."""
    job = service.get_job(session, job_id)
    resolved = dict(header or {})

    if not _text(resolved.get("tpPassNo")):
        resolved["tpPassNo"] = _transport_pass_from_job(job, job_id)

    missing_master = any(
        not _text(resolved.get(name))
        for name in ("supplierCode", "storeCode", "schemeCode")
    )
    if missing_master:
        try:
            context = await build_purchase_context(
                session,
                supplier_name=_text(job.get("supplier_name")),
            )
        except Exception:
            context = {}
        defaults = context.get("defaults") if isinstance(context, dict) else {}
        if not isinstance(defaults, dict):
            defaults = {}
        for name in ("supplierCode", "storeCode", "schemeCode"):
            if not _text(resolved.get(name)) and _text(defaults.get(name)):
                resolved[name] = _text(defaults[name])

    missing = [name for name in _REQUIRED_LABELS if not _text(resolved.get(name))]
    if missing:
        labels = ", ".join(_REQUIRED_LABELS[name] for name in missing)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Madhushala requires these purchase details: {labels}. "
                "Select them on the document review screen before continuing to mapping."
            ),
        )

    return resolved
