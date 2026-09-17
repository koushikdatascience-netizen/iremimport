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


def _number(value: Any) -> float:
    """Keep Item Master calculation precision instead of forcing 2 decimals."""
    try:
        return float(Decimal(str(value or 0)))
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
    """Mirror Madhushala's Purchase business flow for imported bills."""

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

    @staticmethod
    def _master_or_item(
        master: dict[str, Any],
        item: dict[str, Any],
        master_aliases: tuple[str, ...],
        item_key: str,
    ) -> Any:
        value = _dict_value(master, *master_aliases) if master else None
        if value in (None, ""):
            value = item.get(item_key)
        return value

    def build_calculation_request(
        self,
        payload: dict[str, Any],
        header: dict[str, Any],
        master_items: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build Madhushala's PurchaseCalculationRequest.

        Production execution supplies ``master_items`` from Madhushala Item Master.
        QR contributes the mapped item identity and physical bottle quantity; Item
        Master contributes packing, rates, MRP, taxes, T1-T4 and ETD. The optional
        no-master branch is kept for compatibility with older unit callers.
        """
        calc_items: list[dict[str, Any]] = []
        for item in payload.get("items") or []:
            item_code = str(item.get("itemCode") or "").strip()

            if master_items is None:
                bottle_quantity = item.get("qnty")
                if bottle_quantity in (None, ""):
                    bottle_quantity = item.get("loose")
                calc_item = {
                    "itemCode": item_code,
                    "loose": _int_value(bottle_quantity),
                }
                for field in self.CALCULATION_ZERO_ITEM_FIELDS:
                    calc_item[field] = 0
                calc_items.append(calc_item)
                continue

            master = master_items.get(item_code, {}) if isinstance(master_items, dict) else {}
            calc_items.append({
                "itemCode": item_code,
                "packing": _int_value(self._master_or_item(master, item, ("packing", "bottlePerCase", "bottlesPerCase", "caseQty"), "packing")),
                "box": _int_value(item.get("box")),
                "loose": _int_value(item.get("loose")),
                "free": _int_value(item.get("freeQnty")),
                "looseRate": _number(self._master_or_item(master, item, ("purchaseRate", "looseRate", "rate", "itemRate"), "looseRate")),
                "boxRate": _number(self._master_or_item(master, item, ("purchaseRateCase", "boxRate", "caseRate"), "boxRate")),
                "mrp": _number(self._master_or_item(master, item, ("mrp", "itemMrp", "mrpPerUnit"), "mrp")),
                "discount": _number(self._master_or_item(master, item, ("purchaseDiscountAmount", "purchaseDiscount", "discount"), "discount")),
                "cgst": _number(self._master_or_item(master, item, ("cgst", "cgstAmount"), "cgst")),
                "sgst": _number(self._master_or_item(master, item, ("sgst", "sgstAmount"), "sgst")),
                "cess": _number(self._master_or_item(master, item, ("cess", "cessAmount"), "cess")),
                "addCess": _number(self._master_or_item(master, item, ("addCess", "adCess", "addCessAmount", "adCessAmount"), "addCess")),
                "igst": _number(self._master_or_item(master, item, ("igst", "igstAmount"), "igst")),
                "t1Amt": _number(self._master_or_item(master, item, ("t1Amt", "t1Amount", "t1"), "t1Amt")),
                "t2Amt": _number(self._master_or_item(master, item, ("t2Amt", "t2Amount", "t2"), "t2Amt")),
                "t3Amt": _number(self._master_or_item(master, item, ("t3Amt", "t3Amount", "t3"), "t3Amt")),
                "t4Amt": _number(self._master_or_item(master, item, ("t4Amt", "t4Amount", "t4"), "t4Amt")),
                "t1Rate": _number(self._master_or_item(master, item, ("t1Rate",), "t1Rate")),
                "t2Rate": _number(self._master_or_item(master, item, ("t2Rate",), "t2Rate")),
                "t3Rate": _number(self._master_or_item(master, item, ("t3Rate",), "t3Rate")),
                "t4Rate": _number(self._master_or_item(master, item, ("t4Rate",), "t4Rate")),
                "etd": _number(self._master_or_item(master, item, ("etd", "etdAmount"), "etd")),
            })

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
        """Ensure final Save financial values are populated by Calculate response."""
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

    @staticmethod
    def _apply_qr_bottle_quantity(job: dict[str, Any], payload: dict[str, Any]) -> None:
        """For QR imports the QR bottle count is Purchase ``loose``; box is unused."""
        if str(job.get("source_type") or "").strip().upper() != "QR_HTML":
            return
        for item in payload.get("items") or []:
            bottles = _int_value(item.get("qnty"))
            if not bottles:
                bottles = _int_value(item.get("loose"))
            item["box"] = 0
            item["loose"] = bottles

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
            self._apply_qr_bottle_quantity(job, payload)

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

            item_codes = [str(item.get("itemCode") or "").strip() for item in payload["items"]]
            try:
                master_items = await reference_data_service.items(session, item_codes)
            except Exception as exc:
                # purchase_adapter already enriched these rows from Item Master. A
                # refresh/cache lookup failure must not erase that known master data.
                logger.warning("purchase_item_master_refresh_fallback jobId=%s error=%s", job_id, exc)
                master_items = {}

            calc_request = self.build_calculation_request(payload, header, master_items)
            # Do not allow the pre-calculation Item Master values to become the
            # final Purchase Save values. Calculate response remains authoritative.
            self._reset_calculation_owned_values(payload)

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
