from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from typing import Any

from fastapi import HTTPException

from app.db import conn, now_iso
from app.integrations.madhushala.client import MadhushalaApiError
from app.integrations.madhushala.masters import MadhushalaMasterService, _value
from app.modules.document_import.purchase_context import _jwt_claims
from app.modules.document_import.purchase_required import resolve_required_purchase_header


logger = logging.getLogger("madhushala-excise-bridge")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _financial_year(value: str) -> str:
    try:
        effective = date.fromisoformat(_text(value)[:10])
    except ValueError:
        return ""
    start = effective.year if effective.month >= 4 else effective.year - 1
    return f"{start}-{str((start + 1) % 100).zfill(2)}"


def _payload_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _duplicate_found(response: Any) -> bool:
    if isinstance(response, bool):
        return response
    if isinstance(response, (int, float)):
        return bool(response)
    if isinstance(response, str):
        value = response.strip().casefold()
        return value in {"true", "1", "yes", "duplicate", "exists", "found"}
    if not isinstance(response, dict):
        return False
    normalized = {str(key).casefold(): value for key, value in response.items()}
    for key in ("isduplicate", "duplicate", "exists", "alreadyexists", "found"):
        if key in normalized:
            return _duplicate_found(normalized[key])
    for key in ("data", "result", "value"):
        if key in normalized and isinstance(normalized[key], (dict, bool, int, float, str)):
            if _duplicate_found(normalized[key]):
                return True
    return False


def _response_transaction_no(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    for key in ("trnNo", "transactionNo", "purchaseNo", "voucherNo"):
        value = response.get(key)
        if value not in (None, ""):
            return _text(value)
    for key in ("data", "result"):
        nested = response.get(key)
        if isinstance(nested, dict):
            value = _response_transaction_no(nested)
            if value:
                return value
    return ""


class PurchaseOrchestrator:
    """Coordinates a purchase while keeping Madhushala as the accounting authority."""

    def __init__(self, document_service: Any):
        self.service = document_service

    def _existing_transaction(self, job_id: str) -> dict[str, Any] | None:
        with conn() as db:
            row = db.execute("SELECT * FROM purchase_transactions WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def _record_transaction(
        self,
        job_id: str,
        session: dict[str, Any],
        payload: dict[str, Any],
        status: str,
        *,
        response: Any | None = None,
        error: str = "",
        trn_no: str = "",
    ) -> None:
        now = now_iso()
        with conn() as db:
            db.execute(
                """
                INSERT INTO purchase_transactions(
                    job_id, shop_code, company_code, supplier_code, doc_no,
                    payload_hash, status, request_json, response_json, error,
                    madhushala_trn_no, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    shop_code=excluded.shop_code,
                    company_code=excluded.company_code,
                    supplier_code=excluded.supplier_code,
                    doc_no=excluded.doc_no,
                    payload_hash=excluded.payload_hash,
                    status=excluded.status,
                    request_json=excluded.request_json,
                    response_json=excluded.response_json,
                    error=excluded.error,
                    madhushala_trn_no=excluded.madhushala_trn_no,
                    updated_at=excluded.updated_at
                """,
                (
                    job_id,
                    _text(session.get("shop_code")),
                    _text(payload.get("companyCode")),
                    _text(payload.get("supplierCode")),
                    _text(payload.get("docNo")),
                    _payload_hash(payload),
                    status,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    json.dumps(response, ensure_ascii=False, default=str) if response is not None else "",
                    _text(error),
                    _text(trn_no),
                    now,
                    now,
                ),
            )

    def _claim_save(self, job_id: str, session: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        """Atomic SQLite claim prevents double-click/concurrent saves on the current single-node deployment."""
        now = now_iso()
        with conn() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM purchase_transactions WHERE job_id=?", (job_id,)).fetchone()
            existing = dict(row) if row else None
            if existing and existing.get("status") == "PURCHASE_SAVED":
                return existing
            if existing and existing.get("status") in {"SUBMITTING", "SAVE_STATUS_UNKNOWN"}:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This purchase has a previous save attempt whose final status is not safely retryable. "
                        "Check the supplier bill in Madhushala before retrying."
                    ),
                )
            db.execute(
                """
                INSERT INTO purchase_transactions(
                    job_id, shop_code, company_code, supplier_code, doc_no,
                    payload_hash, status, request_json, response_json, error,
                    madhushala_trn_no, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'SUBMITTING', ?, '', '', '', ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    shop_code=excluded.shop_code,
                    company_code=excluded.company_code,
                    supplier_code=excluded.supplier_code,
                    doc_no=excluded.doc_no,
                    payload_hash=excluded.payload_hash,
                    status='SUBMITTING',
                    request_json=excluded.request_json,
                    response_json='',
                    error='',
                    updated_at=excluded.updated_at
                """,
                (
                    job_id,
                    _text(session.get("shop_code")),
                    _text(payload.get("companyCode")),
                    _text(payload.get("supplierCode")),
                    _text(payload.get("docNo")),
                    _payload_hash(payload),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    now,
                    now,
                ),
            )
        return None

    @staticmethod
    def _session_claim(session: dict[str, Any], *claim_names: str) -> str:
        claims = _jwt_claims(str(session.get("madhushala_token") or ""))
        wanted = {name.casefold() for name in claim_names}
        for key, value in claims.items():
            if str(key).casefold() in wanted and value not in (None, ""):
                return _text(value)
        return ""

    async def _base_payload(
        self,
        session: dict[str, Any],
        job_id: str,
        header: dict[str, Any],
        master: MadhushalaMasterService,
    ) -> dict[str, Any]:
        job = self.service.get_job(session, job_id)
        doc_date = _text(header.get("docDate") or job.get("invoice_date"))
        trn_date = _text(header.get("trnDate") or date.today().isoformat())
        year_code = _text(header.get("yearCode")) or self._session_claim(session, "yearCode", "year_code")
        if not year_code:
            year_code = _financial_year(doc_date or trn_date)

        tax_mode = _text(header.get("taxMode")).upper()
        if tax_mode not in {"ITEMWISE", "BILLWISE"}:
            tax_mode = await master.purchase_tax_mode()

        items = await self.service._purchase_items_for_job(session, job_id)
        gross = _money(sum(_money(item.get("itemAmount")) for item in items))
        return {
            "shopCode": _text(session.get("shop_code")),
            "companyCode": _text(session.get("company_code")) or self._session_claim(session, "companyCode", "company_code"),
            "yearCode": year_code,
            "trnDate": trn_date,
            "docDate": doc_date,
            "docNo": _text(header.get("docNo") or job.get("invoice_number")),
            # Keep optional string properties in the JSON. The live ASP.NET DTO can
            # model-bind empty strings while missing properties previously caused confusing validation errors.
            "tpPassNo": _text(header.get("tpPassNo")),
            "supplierCode": _text(header.get("supplierCode")),
            "storeCode": _text(header.get("storeCode")),
            "schemeCode": _text(header.get("schemeCode")),
            "purchaseAccCode": _text(header.get("purchaseAccCode")),
            "narration": _text(header.get("narration") or "AI document import"),
            "userCode": _text(header.get("userCode")),
            "billType": "AI",
            "pType": "PURCHASE",
            "taxMode": tax_mode,
            "grossAmount": _money(header.get("grossAmount") if header.get("grossAmount") is not None else gross),
            "taxAmount": _money(header.get("taxAmount")),
            "netAmount": _money(header.get("netAmount") if header.get("netAmount") is not None else gross),
            "discount": _money(header.get("discount")),
            "salesTaxOnMRP": _money(header.get("salesTaxOnMRP")),
            "roundOff": _money(header.get("roundOff")),
            "saletaxIncludingFree": bool(
                header.get("saletaxIncludingFree")
                if header.get("saletaxIncludingFree") is not None
                else header.get("salesTaxIncludingFree", False)
            ),
            "items": items,
            "taxes": list(header.get("taxes") or []) if isinstance(header.get("taxes"), list) else [],
        }

    @staticmethod
    def _validate_payload(payload: dict[str, Any]) -> None:
        required_text = (
            "shopCode",
            "companyCode",
            "yearCode",
            "trnDate",
            "docDate",
            "docNo",
            "supplierCode",
            "storeCode",
            "purchaseAccCode",
            "userCode",
            "billType",
            "pType",
        )
        missing = [name for name in required_text if not _text(payload.get(name))]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Purchase is not ready. Missing required fields: {', '.join(missing)}",
            )
        if payload.get("taxMode") not in {"ITEMWISE", "BILLWISE"}:
            raise HTTPException(status_code=400, detail="Purchase taxMode must be ITEMWISE or BILLWISE")
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise HTTPException(status_code=400, detail="Purchase requires at least one mapped item")
        invalid = [index + 1 for index, item in enumerate(items) if not isinstance(item, dict) or not _text(item.get("itemCode"))]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Purchase items missing itemCode at rows: {invalid[:10]}")

    @staticmethod
    def _tax_tag_index(tags: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for tag in tags:
            code = _text(_value(tag, "taxCode", "code")).upper()
            if code:
                result[code] = tag
        return result

    async def _apply_billwise_tax(
        self,
        payload: dict[str, Any],
        master: MadhushalaMasterService,
    ) -> None:
        if payload.get("taxes"):
            # Calculate API already supplied the authoritative bill-wise ledger rows.
            for item in payload["items"]:
                for key in ("cgst", "sgst", "cess", "addCess", "igst", "t1Amt", "t2Amt", "t3Amt", "t4Amt", "etd"):
                    item[key] = 0
            return

        components = {
            "T1": "t1Amt",
            "T2": "t2Amt",
            "T3": "t3Amt",
            "T4": "t4Amt",
            "CGST": "cgst",
            "SGST": "sgst",
            "CESS": "cess",
            "ADDCESS": "addCess",
            "IGST": "igst",
            "ETD": "etd",
        }
        amounts = {
            code: _money(sum(_money(item.get(field)) for item in payload["items"]))
            for code, field in components.items()
        }
        nonzero = {code: amount for code, amount in amounts.items() if amount}
        if nonzero:
            tags = self._tax_tag_index(await master.tax_tags())
            taxes: list[dict[str, Any]] = []
            missing_tags: list[str] = []
            for code, amount in nonzero.items():
                tag = tags.get(code)
                ledger = _text(_value(tag, "inputLedgerCode", "inptLedgerCode", "ledgerCode"))
                if not ledger:
                    missing_tags.append(code)
                    continue
                effect = _text(_value(tag, "effect", "sign"))
                sign = effect if effect in {"+", "-"} else "-"
                taxes.append({"ledgerCode": ledger, "amount": amount, "sign": sign, "taxCode": code})
            if missing_tags:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Madhushala BILLWISE calculation produced tax amounts but no Tax Tag ledger was returned for: "
                        + ", ".join(missing_tags)
                    ),
                )
            payload["taxes"] = taxes

        for item in payload["items"]:
            for key in components.values():
                item[key] = 0

    async def prepare(
        self,
        session: dict[str, Any],
        job_id: str,
        header: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], Any]:
        resolved = await resolve_required_purchase_header(self.service, session, job_id, header)
        master = MadhushalaMasterService(session)
        payload = await self._base_payload(session, job_id, resolved, master)
        client = master.client

        calc_request = self.service._build_purchase_calculation_request(payload, resolved)
        # salesTaxRate is not exposed by any documented read endpoint today. Use an
        # explicit caller value when present; otherwise 0 as the existing API sample does.
        if resolved.get("salesTaxRate") in (None, ""):
            logger.warning(
                "event=purchase_sales_tax_rate_defaulted jobId=%s value=0 reason=no_documented_source_api",
                job_id,
            )

        logger.info(
            "event=purchase_prepare_start jobId=%s itemCount=%s taxMode=%s",
            job_id,
            len(payload.get("items") or []),
            payload.get("taxMode"),
        )
        import asyncio

        calculation_task = asyncio.create_task(client.calculate_purchase(calc_request))
        duplicate_task = asyncio.create_task(
            client.check_duplicate_bill(
                company_code=payload["companyCode"],
                supplier_code=payload["supplierCode"],
                doc_no=payload["docNo"],
            )
        )
        calculation, duplicate_response = await asyncio.gather(calculation_task, duplicate_task)
        if _duplicate_found(duplicate_response):
            raise HTTPException(
                status_code=409,
                detail=f"Purchase bill already exists in Madhushala for supplier {payload['supplierCode']} and document {payload['docNo']}",
            )

        self.service._merge_purchase_calculation(payload, calculation)
        if payload["taxMode"] == "BILLWISE":
            await self._apply_billwise_tax(payload, master)
        else:
            payload["taxes"] = []

        # Helper fields are for Calculate only and are not part of PurchaseItemRequest.
        for item in payload["items"]:
            for helper_key in ("packing", "boxRate", "looseRate", "t1Rate", "t2Rate", "t3Rate", "t4Rate"):
                item.pop(helper_key, None)
            for ledger_key in ("cgstInptLdgr", "sgstInptLdgr", "cessInptLdgr", "adCessInptLdgr", "igstInptLdgr"):
                item[ledger_key] = _text(item.get(ledger_key))
            for int_key in ("box", "loose", "qnty", "freeQnty"):
                item[int_key] = _int(item.get(int_key))

        self._validate_payload(payload)
        logger.info(
            "event=purchase_prepare_ready jobId=%s gross=%s tax=%s net=%s",
            job_id,
            payload.get("grossAmount"),
            payload.get("taxAmount"),
            payload.get("netAmount"),
        )
        return payload, duplicate_response

    async def preview(
        self,
        session: dict[str, Any],
        job_id: str,
        header: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload, duplicate_response = await self.prepare(session, job_id, header)
        self._record_transaction(job_id, session, payload, "PREPARED")
        return {
            "success": True,
            "status": "READY",
            "jobId": job_id,
            "duplicate": _duplicate_found(duplicate_response),
            "purchasePayload": payload,
        }

    async def save(
        self,
        session: dict[str, Any],
        job_id: str,
        header: dict[str, Any] | None,
    ) -> dict[str, Any]:
        existing = self._existing_transaction(job_id)
        if existing and existing.get("status") == "PURCHASE_SAVED":
            try:
                response = json.loads(existing.get("response_json") or "{}")
                payload = json.loads(existing.get("request_json") or "{}")
            except Exception:
                response, payload = {}, {}
            logger.info("event=purchase_save_idempotent_hit jobId=%s", job_id)
            return {
                "success": True,
                "jobId": job_id,
                "purchasePayload": payload,
                "madhushalaResponse": response,
                "idempotentReplay": True,
            }

        payload, _ = await self.prepare(session, job_id, header)
        replay = self._claim_save(job_id, session, payload)
        if replay:
            try:
                response = json.loads(replay.get("response_json") or "{}")
            except Exception:
                response = {}
            return {"success": True, "jobId": job_id, "madhushalaResponse": response, "idempotentReplay": True}

        client = MadhushalaMasterService(session).client
        try:
            logger.info("event=purchase_save_submit jobId=%s docNo=%s", job_id, payload.get("docNo"))
            response = await client.save_purchase(payload)
        except MadhushalaApiError as exc:
            status = "SAVE_STATUS_UNKNOWN" if exc.status_code is None or exc.status_code >= 500 else "FAILED"
            self._record_transaction(job_id, session, payload, status, error=str(exc))
            logger.error(
                "event=purchase_save_failed jobId=%s status=%s httpStatus=%s reason=%s",
                job_id,
                status,
                exc.status_code,
                exc,
            )
            raise
        except Exception as exc:
            self._record_transaction(job_id, session, payload, "SAVE_STATUS_UNKNOWN", error=str(exc))
            logger.exception("event=purchase_save_unknown jobId=%s", job_id)
            raise

        trn_no = _response_transaction_no(response)
        self._record_transaction(
            job_id,
            session,
            payload,
            "PURCHASE_SAVED",
            response=response,
            trn_no=trn_no,
        )
        with conn() as db:
            db.execute(
                "UPDATE import_jobs SET status=?, completed_at=?, updated_at=? WHERE id=?",
                ("PURCHASE_SAVED", now_iso(), now_iso(), job_id),
            )
        logger.info("event=purchase_save_success jobId=%s trnNo=%s", job_id, trn_no or "-")
        return {
            "success": True,
            "jobId": job_id,
            "purchasePayload": payload,
            "madhushalaResponse": response,
            "trnNo": trn_no,
            "idempotentReplay": False,
        }
