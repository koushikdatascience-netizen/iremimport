from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException

from app.config import settings
from app.integrations.madhushala.client import MadhushalaApiError, MadhushalaClient
from app.services.purchase_transaction_service import purchase_transaction_service
from app.services.reference_data_service import reference_data_service


logger = logging.getLogger("madhushala-excise-bridge.purchase")


def _money(value: Any) -> float:
    try:
        return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


def _int_value(value: Any) -> int:
    try:
        return max(0, int(Decimal(str(value or 0))))
    except Exception:
        return 0


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _dict_value(row: dict[str, Any], *aliases: str) -> Any:
    normalized = {_key(name): value for name, value in row.items()}
    for alias in aliases:
        value = normalized.get(_key(alias))
        if value not in (None, ""):
            return value
    return None


def _year_code_for_date(value: str) -> str:
    try:
        effective = date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return ""
    start_year = effective.year if effective.month >= 4 else effective.year - 1
    return f"{start_year}-{str((start_year + 1) % 100).zfill(2)}"


class PurchaseOrchestrator:
    REQUIRED_TEXT_FIELDS = (
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

    TAX_ITEM_FIELDS = (
        ("T1", "t1Amt"),
        ("T2", "t2Amt"),
        ("T3", "t3Amt"),
        ("T4", "t4Amt"),
        ("CGST", "cgst"),
        ("SGST", "sgst"),
        ("CESS", "cess"),
        ("ADDCESS", "addCess"),
        ("IGST", "igst"),
        ("ETD", "etd"),
    )

    def build_calculation_request(self, payload: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
        calc_items = []
        for item in payload.get("items") or []:
            calc_items.append({
                "itemCode": item.get("itemCode", ""),
                "box": _int_value(item.get("box")),
                "loose": _int_value(item.get("loose")),
                "free": _int_value(item.get("freeQnty")),
                "boxRate": _money(item.get("boxRate") if item.get("boxRate") not in (None, "") else item.get("rate")),
                "looseRate": _money(item.get("looseRate")),
                "mrp": _money(item.get("mrp")),
                "discount": _money(item.get("discount")),
                "cgst": _money(item.get("cgst")),
                "sgst": _money(item.get("sgst")),
                "cess": _money(item.get("cess")),
                "addCess": _money(item.get("addCess")),
                "igst": _money(item.get("igst")),
                "t1Amt": _money(item.get("t1Amt")),
                "t2Amt": _money(item.get("t2Amt")),
                "t3Amt": _money(item.get("t3Amt")),
                "t4Amt": _money(item.get("t4Amt")),
                "etd": _money(item.get("etd")),
                "packing": _int_value(item.get("packing") or item.get("qnty")),
                "t1Rate": _money(item.get("t1Rate")),
                "t2Rate": _money(item.get("t2Rate")),
                "t3Rate": _money(item.get("t3Rate")),
                "t4Rate": _money(item.get("t4Rate")),
            })
        return {
            "shopCode": payload["shopCode"],
            "companyCode": payload["companyCode"],
            "schemeCode": payload.get("schemeCode", ""),
            "salesTaxRate": _money(
                header.get("salesTaxRate")
                if header.get("salesTaxRate") not in (None, "")
                else settings.PURCHASE_DEFAULT_SALES_TAX_RATE
            ),
            "salesTaxIncludingFree": bool(
                header.get("salesTaxIncludingFree")
                if header.get("salesTaxIncludingFree") is not None
                else header.get("saletaxIncludingFree")
                if header.get("saletaxIncludingFree") is not None
                else settings.PURCHASE_DEFAULT_SALES_TAX_INCLUDING_FREE
            ),
            "items": calc_items,
        }

    @staticmethod
    def _calculated_items(response: Any) -> list[dict[str, Any]]:
        if isinstance(response, list):
            return [item for item in response if isinstance(item, dict)]
        if not isinstance(response, dict):
            return []
        candidates = [response]
        for key in ("data", "result", "calculation", "payload"):
            nested = response.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
        for candidate in candidates:
            items = candidate.get("items") or candidate.get("Items") or candidate.get("itemDetails")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    def merge_calculation(self, payload: dict[str, Any], response: Any) -> None:
        calculated = self._calculated_items(response)
        by_code = {str(item.get("itemCode") or item.get("code") or "").strip(): item for item in calculated}
        merge_fields = (
            "qnty", "rate", "mrp", "itemAmount", "discount", "cgst", "sgst", "cess", "addCess", "igst",
            "t1Amt", "t2Amt", "t3Amt", "t4Amt", "etd", "cgstInptLdgr", "sgstInptLdgr", "cessInptLdgr",
            "adCessInptLdgr", "igstInptLdgr",
        )
        for index, item in enumerate(payload.get("items") or []):
            calc = by_code.get(str(item.get("itemCode") or "").strip()) or (calculated[index] if index < len(calculated) else None)
            if not calc:
                continue
            for field in merge_fields:
                value = calc.get(field)
                if value is None and field == "addCess":
                    value = calc.get("addcess") or calc.get("adCess")
                if value is not None:
                    item[field] = value if field.endswith("Ldgr") else _money(value)

        candidates = [response] if isinstance(response, dict) else []
        if isinstance(response, dict):
            for key in ("data", "result", "calculation", "payload"):
                nested = response.get(key)
                if isinstance(nested, dict):
                    candidates.append(nested)
        for candidate in candidates:
            for field in ("grossAmount", "taxAmount", "netAmount", "discount", "salesTaxOnMRP", "roundOff"):
                value = candidate.get(field)
                if value is None:
                    value = candidate.get(field[:1].upper() + field[1:])
                if value is not None:
                    payload[field] = _money(value)
            taxes = candidate.get("taxes") or candidate.get("Taxes")
            if isinstance(taxes, list):
                payload["taxes"] = taxes

    @staticmethod
    def _duplicate_flag(response: Any) -> bool:
        if isinstance(response, bool):
            return response
        if isinstance(response, str):
            return response.strip().casefold() in {"true", "duplicate", "exists", "yes"}
        if not isinstance(response, dict):
            return False
        candidates = [response]
        for key in ("data", "result"):
            nested = response.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
            elif isinstance(nested, bool):
                return nested
        for candidate in candidates:
            for key in ("isDuplicate", "duplicate", "exists", "isExists", "alreadyExists"):
                value = candidate.get(key)
                if isinstance(value, bool):
                    return value
                if isinstance(value, (int, float)):
                    return bool(value)
                if isinstance(value, str) and value.strip().casefold() in {"true", "yes", "1"}:
                    return True
        return False

    @staticmethod
    def _tax_tag_index(tags: list[Any]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in tags:
            if not isinstance(row, dict):
                continue
            code = str(_dict_value(row, "taxCode", "code") or "").strip().upper()
            if code:
                result[code] = row
        return result

    async def _build_billwise_taxes(self, session: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        existing = payload.get("taxes")
        if isinstance(existing, list) and existing:
            return existing

        tags = await reference_data_service.tax_tags(session)
        index = self._tax_tag_index(tags)
        taxes: list[dict[str, Any]] = []
        missing: list[str] = []

        for tax_code, item_field in self.TAX_ITEM_FIELDS:
            amount = _money(sum(_money(item.get(item_field)) for item in payload.get("items") or []))
            if not amount:
                continue
            tag = index.get(tax_code)
            ledger = str(_dict_value(tag or {}, "inputLedgerCode", "ledgerCode", "inputLedger") or "").strip()
            if not ledger:
                missing.append(tax_code)
                continue
            taxes.append({
                "ledgerCode": ledger,
                "amount": amount,
                "sign": "-",
                "taxCode": tax_code,
            })

        if missing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "BILLWISE purchase requires Madhushala Tax Tag input ledgers for: "
                    + ", ".join(missing)
                    + ". Configure Tax Tagging or have the Calculate API return taxes[]."
                ),
            )
        return taxes

    @staticmethod
    def _validate_final_payload(payload: dict[str, Any]) -> None:
        missing = [field for field in PurchaseOrchestrator.REQUIRED_TEXT_FIELDS if not str(payload.get(field) or "").strip()]
        if missing:
            raise HTTPException(
                status_code=400,
                detail="Purchase is not ready. Missing required fields: " + ", ".join(missing),
            )
        if not isinstance(payload.get("items"), list) or not payload["items"]:
            raise HTTPException(status_code=400, detail="Purchase is not ready. items must be non-empty")
        for index, item in enumerate(payload["items"], start=1):
            if not str(item.get("itemCode") or "").strip():
                raise HTTPException(status_code=400, detail=f"Purchase item {index} has no Madhushala itemCode")
        for field in ("grossAmount", "taxAmount", "netAmount"):
            if payload.get(field) is None:
                raise HTTPException(status_code=400, detail=f"Purchase is not ready. {field} is missing")

    async def execute(
        self,
        *,
        session: dict[str, Any],
        job_id: str,
        job: dict[str, Any],
        header: dict[str, Any],
        items: list[dict[str, Any]],
        client: MadhushalaClient,
    ) -> dict[str, Any]:
        async with purchase_transaction_service.lock(job_id):
            prior = purchase_transaction_service.get(job_id)
            if prior and prior.get("status") == "SAVED":
                response = {}
                try:
                    response = json.loads(prior.get("response_json") or "{}")
                except Exception:
                    pass
                return {
                    "success": True,
                    "jobId": job_id,
                    "idempotentReplay": True,
                    "trnNo": prior.get("madhushala_trn_no") or "",
                    "madhushalaResponse": response,
                }

            doc_date = str(header.get("docDate") or job.get("invoice_date") or "").strip()
            trn_date = str(header.get("trnDate") or date.today().isoformat()).strip()
            year_code = str(header.get("yearCode") or "").strip() or _year_code_for_date(doc_date or trn_date)
            try:
                tax_mode = await reference_data_service.tax_mode(session)
            except Exception as exc:
                tax_mode = str(header.get("taxMode") or "ITEMWISE").strip().upper() or "ITEMWISE"
                logger.warning("purchase_tax_mode_fallback jobId=%s mode=%s error=%s", job_id, tax_mode, exc)
            if tax_mode not in {"ITEMWISE", "BILLWISE"}:
                tax_mode = "ITEMWISE"

            gross = _money(sum(_money(item.get("itemAmount")) for item in items))
            payload = {
                "shopCode": str(session.get("shop_code") or "").strip(),
                "companyCode": str(session.get("company_code") or "").strip(),
                "yearCode": year_code,
                "trnDate": trn_date,
                "docDate": doc_date,
                "docNo": str(header.get("docNo") or job.get("invoice_number") or "").strip(),
                "tpPassNo": str(header.get("tpPassNo") or "").strip(),
                "supplierCode": str(header.get("supplierCode") or "").strip(),
                "storeCode": str(header.get("storeCode") or "").strip(),
                "schemeCode": str(header.get("schemeCode") or "").strip(),
                "purchaseAccCode": str(header.get("purchaseAccCode") or "").strip(),
                "narration": str(header.get("narration") or "Document import").strip(),
                "userCode": str(header.get("userCode") or "").strip(),
                "billType": "AI",
                "pType": "PURCHASE",
                "taxMode": tax_mode,
                "grossAmount": _money(header.get("grossAmount") if header.get("grossAmount") is not None else gross),
                "taxAmount": _money(header.get("taxAmount") or 0),
                "netAmount": _money(header.get("netAmount") if header.get("netAmount") is not None else gross),
                "discount": _money(header.get("discount") or 0),
                "salesTaxOnMRP": _money(header.get("salesTaxOnMRP") or 0),
                "roundOff": _money(header.get("roundOff") or 0),
                "saletaxIncludingFree": bool(
                    header.get("saletaxIncludingFree")
                    if header.get("saletaxIncludingFree") is not None
                    else settings.PURCHASE_DEFAULT_SALES_TAX_INCLUDING_FREE
                ),
                "items": items,
                "taxes": header.get("taxes") if isinstance(header.get("taxes"), list) else [],
            }

            tx = purchase_transaction_service.ensure(
                job_id=job_id,
                shop_code=payload["shopCode"],
                company_code=payload["companyCode"],
                supplier_code=payload["supplierCode"],
                doc_no=payload["docNo"],
            )
            tx_id = str(tx.get("id") or "")
            purchase_transaction_service.update(
                job_id,
                "CALCULATING",
                supplier_code=payload["supplierCode"],
                doc_no=payload["docNo"],
            )

            calc_request = self.build_calculation_request(payload, header)
            calc_started = time.perf_counter()
            duplicate_task = None
            if payload["supplierCode"] and payload["docNo"]:
                duplicate_task = asyncio.create_task(
                    client.check_duplicate_bill(
                        payload["companyCode"],
                        payload["supplierCode"],
                        payload["docNo"],
                    )
                )
            calculate_task = asyncio.create_task(client.calculate_purchase(calc_request))

            calculation: Any = None
            duplicate_response: Any = None
            try:
                calculation = await calculate_task
                self.merge_calculation(payload, calculation)
                duration_ms = int((time.perf_counter() - calc_started) * 1000)
                purchase_transaction_service.record_event(
                    tx_id, job_id, "CALCULATE", "OK", duration_ms=duration_ms,
                    details={"itemCount": len(items), "taxMode": tax_mode},
                )
            except Exception as exc:
                purchase_transaction_service.record_event(tx_id, job_id, "CALCULATE", "FAILED", details={"error": str(exc)})
                if settings.PURCHASE_CALCULATION_REQUIRED:
                    purchase_transaction_service.update(job_id, "FAILED", error=f"Purchase calculation failed: {exc}")
                    if duplicate_task is not None and not duplicate_task.done():
                        duplicate_task.cancel()
                    raise
                logger.warning("purchase_calculation_fallback jobId=%s error=%s", job_id, exc)

            if duplicate_task is not None:
                try:
                    duplicate_response = await duplicate_task
                    duplicate = self._duplicate_flag(duplicate_response)
                    purchase_transaction_service.record_event(
                        tx_id, job_id, "DUPLICATE_CHECK", "DUPLICATE" if duplicate else "OK",
                        details={"response": duplicate_response},
                    )
                    if duplicate:
                        purchase_transaction_service.update(job_id, "DUPLICATE", error="Supplier bill/document already exists")
                        raise HTTPException(
                            status_code=409,
                            detail="A Madhushala purchase already exists for this supplier and document number.",
                        )
                except HTTPException:
                    raise
                except Exception as exc:
                    purchase_transaction_service.record_event(tx_id, job_id, "DUPLICATE_CHECK", "FAILED", details={"error": str(exc)})
                    if settings.PURCHASE_DUPLICATE_CHECK_REQUIRED:
                        purchase_transaction_service.update(job_id, "FAILED", error=f"Duplicate check failed: {exc}")
                        raise
                    logger.warning("duplicate_check_unavailable jobId=%s error=%s", job_id, exc)

            if tax_mode == "BILLWISE":
                payload["taxes"] = await self._build_billwise_taxes(session, payload)
                for item in payload["items"]:
                    for _, key in self.TAX_ITEM_FIELDS:
                        item[key] = 0
            else:
                payload["taxes"] = []

            for item in payload["items"]:
                for helper_key in ("packing", "boxRate", "looseRate", "t1Rate", "t2Rate", "t3Rate", "t4Rate"):
                    item.pop(helper_key, None)

            self._validate_final_payload(payload)
            payload_hash = purchase_transaction_service.payload_hash(payload)
            purchase_transaction_service.update(job_id, "READY", payload_hash=payload_hash)

            save_started = time.perf_counter()
            purchase_transaction_service.update(job_id, "SUBMITTING")
            try:
                response = await client.save_purchase(payload)
            except Exception as exc:
                # Do not auto-retry purchase/save. The upstream may have committed
                # before a timeout. Persist UNKNOWN so an operator/reconciliation
                # path can verify the bill before any later retry.
                status = "UNKNOWN" if isinstance(exc, MadhushalaApiError) and exc.status_code is None else "FAILED"
                purchase_transaction_service.update(job_id, status, error=str(exc))
                purchase_transaction_service.record_event(tx_id, job_id, "SAVE", status, details={"error": str(exc)})
                raise

            duration_ms = int((time.perf_counter() - save_started) * 1000)
            trn_no = purchase_transaction_service.response_trn_no(response)
            purchase_transaction_service.update(
                job_id,
                "SAVED",
                madhushala_trn_no=trn_no,
                response_json=json.dumps(response, ensure_ascii=False, default=str),
                error=None,
            )
            purchase_transaction_service.record_event(
                tx_id,
                job_id,
                "SAVE",
                "OK",
                duration_ms=duration_ms,
                details={"trnNo": trn_no, "payloadHash": payload_hash},
            )
            logger.info(
                "purchase_saved jobId=%s trnNo=%s taxMode=%s itemCount=%s durationMs=%s",
                job_id,
                trn_no,
                tax_mode,
                len(payload["items"]),
                duration_ms,
            )
            return {
                "success": True,
                "jobId": job_id,
                "trnNo": trn_no,
                "purchasePayload": payload,
                "calculation": calculation,
                "duplicateCheck": duplicate_response,
                "madhushalaResponse": response,
                "transaction": purchase_transaction_service.get(job_id),
            }


purchase_orchestrator = PurchaseOrchestrator()
