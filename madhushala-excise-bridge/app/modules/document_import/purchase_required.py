from __future__ import annotations

import json
import re
from datetime import datetime
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


def _date_only(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""

    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)

    # Excise/invoice PDFs commonly expose Indian DD/MM/YYYY dates. Normalize
    # source-document dates to the ISO shape expected by Madhushala Purchase.
    candidates = [text, text.split()[0]]
    for candidate in dict.fromkeys(candidates):
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d-%b-%Y", "%d/%b/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    return text


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
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Resolve the master values required by the current Purchase screen.

    The observed manual Purchase payload permits yearCode, tpPassNo and
    schemeCode to be empty. Supplier, Store, Purchase A/c and User are the
    master values that must be resolved before submitting the import.

    QR/PDF/image extraction owns source-document facts. Document number and
    document date must come from the extracted source rather than browser
    defaults whenever extraction supplied those values.
    """
    job = service.get_job(session, job_id)
    resolved = dict(header or {})

    source_type = str(job.get("source_type") or "").strip().upper()
    source_doc_no = _text(job.get("invoice_number"))
    source_doc_date = _date_only(job.get("invoice_date"))
    source_is_document = source_type in {"QR_HTML", "DOCUMENT_PDF", "DOCUMENT_IMAGE"}

    # For document imports, extracted values beat automatic browser
    # defaults. An explicit review edit is authoritative only when the frontend
    # marks that field as user-edited.
    doc_no_edited = bool(resolved.pop("_docNoEdited", False))
    doc_date_edited = bool(resolved.pop("_docDateEdited", False))

    if source_is_document:
        if source_doc_no and not doc_no_edited:
            resolved["docNo"] = source_doc_no
        elif not _text(resolved.get("docNo")) and source_doc_no:
            resolved["docNo"] = source_doc_no

        if source_doc_date and not doc_date_edited:
            resolved["docDate"] = source_doc_date
        elif not _text(resolved.get("docDate")) and source_doc_date:
            resolved["docDate"] = source_doc_date
    else:
        if not _text(resolved.get("docNo")) and source_doc_no:
            resolved["docNo"] = source_doc_no
        if not _text(resolved.get("docDate")) and source_doc_date:
            resolved["docDate"] = source_doc_date

    if not _text(resolved.get("tpPassNo")):
        resolved["tpPassNo"] = _transport_pass_from_job(job, job_id)
    if not _text(resolved.get("tpPassNo")) and _text(resolved.get("docNo")):
        # Madhushala accepts the same identifier in both fields when the
        # source document exposes only one invoice/challan/pass number.
        resolved["tpPassNo"] = _text(resolved.get("docNo"))

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
        for name in (*needed, "schemeCode"):
            if not _text(resolved.get(name)) and _text(defaults.get(name)):
                resolved[name] = _text(defaults[name])

    resolved.setdefault("yearCode", "")
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