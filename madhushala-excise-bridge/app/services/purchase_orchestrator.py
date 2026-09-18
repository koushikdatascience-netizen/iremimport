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


class PurchaseOrchestrator:
    """Mirror Madhushala's current Purchase business flow for imported bills.

    Browser-only bootstrap/master screens are not replayed sequentially here.
    Stable reference data is cached by ``reference_data_service``; the live
    transaction path uses Madhushala's own Calculate API and then Save API.
    """

    # yearCode is intentionally not required here. The current Madhushala
    # Purchase screen sends yearCode="" and lets its backend/session context
    # resolve the effective year.
    REQUIRED_TEXT_FIELDS = (
        "shopCode",
        "companyCode",
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

    CALCULATION_ZERO_ITEM_FIELDS = (
        "box",
        "free",
        "boxRate",
        "looseRate",
        "mrp",
        "discount",
        "cgst",
        "sgst",
        "cess",
        "addCess",
        "igst",
        "t1Amt",
        "t2Amt",
        "t3Amt",
        "t4Amt",
        "etd",
        "packing",
        "t1Rate",
        "t2Rate",
        "t3Rate",
        "t4Rate",
    )

    CALCULATION_OWNED_ITEM_FIELDS = (
        "rate",
        "mrp",
        "itemAmount",
        "discount",
        "cgst",
        "sgst",
        "cess",
        "addCess",
        "igst",
        "t1Amt",
        "t2Amt",
        "t3Amt",
        "t4Amt",
        "etd",
    )

    CALCULATION_ITEM_ALIASES = {
        "qnty": ("qnty", "quantity", "qty"),
        "rate": ("rate", "looseRate", "unitRate"),
        "mrp": ("mrp", "itemMrp"),
        "itemAmount": ("itemAmount", "amount", "lineAmount"),
        "discount": ("discount", "discountAmount"),
        "cgst": ("cgst", "cgstAmount"),
        "sgst": ("sgst", "sgstAmount"),
        "cess": ("cess", "cessAmount"),
        "addCess": ("addCess", "adCess", "addCessAmount", "adCessAmount"),
        "igst": ("igst", "igstAmount"),
        "t1Amt": ("t1Amt", "t1Amount"),
        "t2Amt": ("t2Amt", "t2Amount"),
        "t3Amt": ("t3Amt", "t3Amount"),
        "t4Amt": ("t4Amt", "t4Amount"),
        "etd": ("etd", "etdAmount"),
        "cgstInptLdgr": ("cgstInptLdgr", "cgstInputLedger"),
        "sgstInptLdgr": ("sgstInptLdgr", "sgstInputLedger"),
        "cessInptLdgr": ("cessInptLdgr", "cessInputLedger"),
        "adCessInptLdgr": ("adCessInptLdgr", "addCessInptLdgr", "addCessInputLedger"),
        "igstInptLdgr": ("igstInptLdgr", "igstInputLedger"),
    }

    CALCULATION_TOTAL_ALIASES = {
        "grossAmount": ("grossAmount", "totalGrossPlus"),
        "taxAmount": ("taxAmount", "totalTax", "totalTaxAmount"),
        "netAmount": ("netAmount", "net"),
        "discount": ("discount", "totalDiscount"),
        "salesTaxOnMRP": ("salesTaxOnMRP",),
        "roundOff": ("roundOff",),
    }

    def build_calculation_request(self, payload: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
        """Build Madhushala's Calculate shape with itemCode + reviewed box/loose populated.

        Madhushala Calculate is authoritative for commercial/tax values. ASP.NET's
        request DTO uses non-nullable numeric/boolean fields, so unused numeric
        properties are sent as zero. Source quantity identity remains separate:
        box carries reviewed cases and loose carries reviewed single bottles.
        """
        calc_items: list[dict[str, Any]] = []
        for item in payload.get("items") or []:
            box = _int_value(item.get("box"))
            loose = _int_value(item.get("loose"))
            if box <= 0 and loose <= 0:
                # Legacy callers may still supply only qnty.
                loose = _int_value(item.get("qnty"))
            calc_item = {
                "itemCode": str(item.get("itemCode") or "").strip(),
            }
            for field in self.CALCULATION_ZERO_ITEM_FIELDS:
                calc_item[field] = 0
            # Quantity identity is source/review-owned, not a zero-value
            # commercial placeholder. Set it after the defaults above.
            calc_item["box"] = box
            calc_item["loose"] = loose
            calc_items.append(calc_item)

        return {
            "shopCode": payload["shopCode"],
            "companyCode": payload["companyCode"],
            "schemeCode": payload.get("schemeCode", ""),
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
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

    def _reset_calculation_owned_values(self, payload: dict[str, Any]) -> None:
        """Prevent Item Master/source-document financial values from leaking into Save.

        Quantity identity remains intact, but fields Madhushala Calculate owns begin
        at zero and are filled only from the Calculate response.
        """
        for item in payload.get("items") or []:
            for field in self.CALCULATION_OWNED_ITEM_FIELDS:
                item[field] = 0
            for field in (
                "cgstInptLdgr",
                "sgstInptLdgr",
                "cessInptLdgr",
                "adCessInptLdgr",
                "igstInptLdgr",
            ):
                item[field] = ""
        for field in ("grossAmount", "taxAmount", "netAmount", "discount", "salesTaxOnMRP", "roundOff"):
            payload[field] = 0

    def merge_calculation(self, payload: dict[str, Any], response: Any) -> None:
        """Merge Madhushala Calculate output and treat it as the financial source of truth."""
        calculated = self._calculated_items(response)
        by_code = {
            str(_dict_value(item, "itemCode", "code") or "").strip(): item
            for item in calculated
        }

        for index, item in enumerate(payload.get("items") or []):
            calc = by_code.get(str(item.get("itemCode") or "").strip()) or (
                calculated[index] if index < len(calculated) else None
            )
            if not calc:
                continue

            for field, aliases in self.CALCULATION_ITEM_ALIASES.items():
                value = _dict_value(calc, *aliases)
                if value is None:
                    continue
                if field.endswith("Ldgr"):
                    item[field] = str(value or "").strip()
                elif field == "qnty":
                    item[field] = _int_value(value)
                else:
                    item[field] = _money(value)

        candidates = [response] if isinstance(response, dict) else []
        if isinstance(response, dict):
            for key in ("data", "result", "calculation", "payload"):
                nested = response.get(key)
                if isinstance(nested, dict):
                    candidates.append(nested)

        for candidate in candidates:
            for field, aliases in self.CALCULATION_TOTAL_ALIASES.items():
                value = _dict_value(candidate, *aliases)
                if value is not None:
                    payload[field] = _money(value)

            # Some Calculate responses expose the rounding components rather than
            # a single roundOff property. Preserve Madhushala's values and only
            # translate them into the Purchase Save field.
            if _dict_value(candidate, "roundOff") is None:
                rounding_plus = _dict_value(candidate, "roundingPlus")
                rounding_minus = _dict_value(candidate, "roundingMinus")
                if rounding_plus is not None or rounding_minus is not None:
                    payload["roundOff"] = _money(_money(rounding_plus) - _money(rounding_minus))

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
        # Prefer taxes[] produced by Madhushala Calculate if it supplies them.
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
            # Match the current manual Purchase screen. It sends yearCode="";
            # do not invent a financial year unless a caller explicitly gives it.
            year_code = str(header.get("yearCode") or "").strip()

            try:
                tax_mode = await reference_data_service.tax_mode(session)
            except Exception as exc:
                tax_mode = str(header.get("taxMode") or "ITEMWISE").strip().upper() or "ITEMWISE"
                logger.warning("purchase_tax_mode_fallback jobId=%s mode=%s error=%s", job_id, tax_mode, exc)
            if tax_mode not in {"ITEMWISE", "BILLWISE"}:
                tax_mode = "ITEMWISE"

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
                "narration": str(header.get("narration") or "").strip(),
                "userCode": str(header.get("userCode") or "").strip(),
                "billType": "AI",
                "pType": "purchase",
                "taxMode": tax_mode,
                "grossAmount": 0,
                "taxAmount": 0,
                "netAmount": 0,
                "discount": 0,
                "salesTaxOnMRP": 0,
                "roundOff": 0,
                "saletaxIncludingFree": False,
                "items": items,
                "taxes": header.get("taxes") if isinstance(header.get("taxes"), list) else [],
            }
            self._reset_calculation_owned_values(payload)

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

            # These values are needed only by PurchaseCalculationRequest. The
            # current PurchaseRequest/ItemRequest sent by the manual screen does
            # not include them as item properties.
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
                # Never blindly retry Purchase Save. A network failure can happen
                # after Madhushala commits the accounting transaction.
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
                "calculationDebug": {
                    "requestUrl": "/api/purchase/calculate",
                    "requestHeaders": {
                        "accept": "application/json",
                        "Content-Type": "application/json",
                        "Authorization": "[REDACTED]",
                    },
                    "request": calc_request,
                    "response": calculation,
                },
                "duplicateCheck": duplicate_response,
                "madhushalaResponse": response,
                "transaction": purchase_transaction_service.get(job_id),
            }


purchase_orchestrator = PurchaseOrchestrator()
