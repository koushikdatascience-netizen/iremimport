from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.config import settings
from app.db import conn, now_iso


async def save_document_row_mappings(
    service: Any,
    session: dict[str, Any],
    job_id: str,
    selections: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist document/QR row mappings even when an Excise code is not available yet.

    Document rows are identified by their unique import_items.id (jobItemId). If a
    row has a valid Excise item code, its mapping is also sent to the shared
    Excise -> Madhushala mapping API. Rows without an Excise code remain local to
    the import job, which is sufficient for building and saving the purchase.
    """

    clean_job_id = str(job_id or "").strip()
    if not clean_job_id:
        raise HTTPException(status_code=400, detail="jobId is required")

    service.get_job(session, clean_job_id)

    # Never accept a row mapping for an item code that does not exist in the
    # current company catalogue. This prevents stale mappings from a different
    # company from reaching Purchase / Item Master.
    company_code = str(session.get("company_code") or settings.DEFAULT_COMPANY_CODE).strip()
    bill_type = str(session.get("bill_type") or settings.DEFAULT_BILL_TYPE).strip()
    catalogue = await service.mapping_service._client_for_session(session).get_dropdown_items(
        company_code,
        bill_type,
    )
    valid_item_codes = {
        str(item.get("itemCode") or "").strip()
        for item in (catalogue or [])
        if isinstance(item, dict) and str(item.get("itemCode") or "").strip()
    }

    if not selections:
        return {
            "mappedCount": 0,
            "globalMappedCount": 0,
            "localOnlyCount": 0,
            "response": None,
        }

    clean_rows: list[dict[str, Any]] = []
    seen_job_items: set[str] = set()
    global_by_excise: dict[str, str] = {}
    conflicting_excise_codes: set[str] = set()

    with conn() as db:
        for item in selections:
            job_item_id = str(item.get("jobItemId") or "").strip()
            item_code = str(item.get("itemCode") or "").strip()
            if not job_item_id or not item_code:
                raise HTTPException(status_code=400, detail="jobItemId and itemCode are required")
            if item_code not in valid_item_codes:
                raise HTTPException(
                    status_code=409,
                    detail=f"Madhushala item {item_code} is not available in company {company_code}. Refresh and choose a current-company item.",
                )
            if job_item_id in seen_job_items:
                raise HTTPException(status_code=400, detail="Duplicate document row in mapping request")
            seen_job_items.add(job_item_id)

            row = db.execute(
                "SELECT excise_item_code FROM import_items WHERE id=? AND job_id=?",
                (job_item_id, clean_job_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail="Document mapping row was not found")

            actual_excise_code = str(row["excise_item_code"] or "").strip()
            requested_excise_code = str(item.get("exciseItemCode") or "").strip()
            if requested_excise_code and requested_excise_code != actual_excise_code:
                raise HTTPException(status_code=409, detail="Document mapping row changed; refresh and try again")

            clean_rows.append(
                {
                    "jobItemId": job_item_id,
                    "exciseItemCode": actual_excise_code or None,
                    "itemCode": item_code,
                }
            )

            if actual_excise_code.isdigit() and int(actual_excise_code) > 0:
                previous = global_by_excise.get(actual_excise_code)
                if previous is None:
                    global_by_excise[actual_excise_code] = item_code
                elif previous != item_code:
                    # A global Excise mapping can only point to one Madhushala item.
                    # Keep both document-row choices locally and skip the ambiguous
                    # global write rather than allowing one row to overwrite another.
                    conflicting_excise_codes.add(actual_excise_code)

    global_mappings = [
        {"exciseItemCode": int(excise_code), "itemCode": item_code}
        for excise_code, item_code in global_by_excise.items()
        if excise_code not in conflicting_excise_codes
    ]

    remote_result: dict[str, Any] = {"mappedCount": 0, "response": None}
    if global_mappings:
        remote_result = await service.mapping_service.save_session_mappings(
            session,
            global_mappings,
            job_id=None,
        )

    mapped_at = now_iso()
    with conn() as db:
        for item in clean_rows:
            db.execute(
                """
                UPDATE import_items
                SET mapping_status='MAPPED', mapped_item_code=?, updated_at=?
                WHERE id=? AND job_id=?
                """,
                (item["itemCode"], mapped_at, item["jobItemId"], clean_job_id),
            )

        counts = db.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN mapping_status='MAPPED' THEN 1 ELSE 0 END) AS mapped
            FROM import_items WHERE job_id=?
            """,
            (clean_job_id,),
        ).fetchone()
        total = counts["total"] or 0
        mapped = counts["mapped"] or 0
        status = "COMPLETED" if total and mapped >= total else "MAPPING_REQUIRED"
        db.execute(
            """
            UPDATE import_jobs
            SET mapped_count=?, status=?,
                completed_at=CASE WHEN ?='COMPLETED' THEN ? ELSE completed_at END,
                updated_at=?
            WHERE id=? AND shop_code=? AND session_id=?
            """,
            (
                mapped,
                status,
                status,
                mapped_at,
                mapped_at,
                clean_job_id,
                session["shop_code"],
                session["session_id"],
            ),
        )

    global_codes = {str(item["exciseItemCode"]) for item in global_mappings}
    local_only_count = sum(
        1
        for item in clean_rows
        if not item["exciseItemCode"] or str(item["exciseItemCode"]) not in global_codes
    )
    return {
        "mappedCount": len(clean_rows),
        "globalMappedCount": len(global_mappings),
        "localOnlyCount": local_only_count,
        "conflictingExciseCodes": sorted(conflicting_excise_codes),
        "response": remote_result.get("response"),
        "status": status,
    }
