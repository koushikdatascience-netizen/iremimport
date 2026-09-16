from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException

from app.db import conn, now_iso
from app.services.purchase_orchestrator import purchase_orchestrator
from app.services.reference_data_service import reference_data_service


logger = logging.getLogger("madhushala-excise-bridge.purchase-adapter")


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


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


def _dict_value(row: dict[str, Any] | None, *aliases: str) -> Any:
    if not row:
        return None
    normalized = {_key(name): value for name, value in row.items()}
    for alias in aliases:
        value = normalized.get(_key(alias))
        if value not in (None, ""):
            return value
    return None


class DocumentPurchaseAdapter:
    """Purchase-specific adapter around the existing DocumentImportService.

    Extraction/mapping remain untouched. Purchase enrichment/orchestration is
    isolated here so the upstream Madhushala contract can evolve without
    coupling the entire document-import service to accounting API changes.
    """

    def __init__(self, document_service: Any):
        self.document_service = document_service

    def _mapped_code(self, db: Any, session: dict[str, Any], row: Any) -> str:
        mapped = str(row["mapped_item_code"] or "").strip()
        if mapped:
            return mapped
        if row["excise_item_code"]:
            mapping = db.execute(
                "SELECT madhushala_item_code FROM mappings WHERE shop_code=? AND excise_item_code=?",
                (session["shop_code"], str(row["excise_item_code"])),
            ).fetchone()
            if mapping:
                return str(mapping["madhushala_item_code"] or "").strip()
        return ""

    async def purchase_items(self, session: dict[str, Any], job_id: str) -> list[dict[str, Any]]:
        self.document_service.get_job(session, job_id)
        with conn() as db:
            rows = db.execute(
                "SELECT * FROM import_items WHERE job_id=? ORDER BY created_at, id",
                (job_id,),
            ).fetchall()
            row_codes = [(row, self._mapped_code(db, session, row)) for row in rows]

        missing = [
            str(row["raw_name"] or row["normalized_name"] or row["id"])
            for row, code in row_codes
            if not code
        ]
        if missing:
            raise HTTPException(status_code=409, detail=f"Map all items before saving purchase: {', '.join(missing[:5])}")
        if not row_codes:
            raise HTTPException(status_code=400, detail="No items available for purchase save")

        item_master = await reference_data_service.items(session, [code for _, code in row_codes])
        items: list[dict[str, Any]] = []

        for row, mapped in row_codes:
            master = item_master.get(mapped, {})
            try:
                raw = json.loads(row["raw_data_json"] or "{}")
            except Exception:
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            normalized_raw = {_key(name): value for name, value in raw.items()}

            def raw_value(*aliases: str) -> Any:
                for alias in aliases:
                    value = normalized_raw.get(_key(alias))
                    if value not in (None, ""):
                        return value
                return None

            def raw_int(*aliases: str, fallback: Any = None) -> int:
                value = raw_value(*aliases)
                return _int_value(fallback if value in (None, "") else value)

            def raw_money(*aliases: str, fallback: Any = None) -> float:
                value = raw_value(*aliases)
                return _money(fallback if value in (None, "") else value)

            # Commercial/master values are Madhushala-owned. Document values
            # remain authoritative for physical quantities and document facts.
            master_packing = _int_value(_dict_value(master, "packing", "bottlePerCase", "bottlesPerCase", "caseQty"))
            raw_packing = raw_int("packing", "bottlePerCase", "bottlesPerCase", "caseQty", fallback=row["packing"])
            packing = master_packing or raw_packing

            box = raw_int("box", "boxes", "case", "cases", fallback=row["quantity"] or 0)
            loose = raw_int("loose", "looseQty", fallback=0)
            document_qnty = raw_int("qnty", "qty", "bottles", "totalQty", "totalQuantity", fallback=0)
            qnty = document_qnty or ((box * packing + loose) if packing else (box + loose))

            purchase_rate = _money(_dict_value(master, "purchaseRate", "rate", "itemRate"))
            purchase_case_rate = _money(_dict_value(master, "purchaseRateCase", "boxRate", "caseRate"))
            loose_rate = purchase_rate or raw_money("looseRate", "bottleRate", fallback=0)
            box_rate = purchase_case_rate or raw_money("boxRate", "caseRate", fallback=row["rate"] or 0)
            rate = purchase_rate or raw_money("rate", fallback=row["rate"] or 0) or box_rate
            mrp = _money(_dict_value(master, "mrp", "itemMrp", "mrpPerUnit")) or raw_money("mrp", fallback=row["mrp"] or 0)

            amount = raw_money("itemAmount", "amount", "lineAmount", "totalAmount", fallback=row["amount"])
            if not amount:
                if box_rate and box:
                    amount = _money((box_rate * box) + (loose_rate * loose))
                elif rate and qnty:
                    amount = _money(rate * qnty)

            item_name = str(
                _dict_value(master, "itemName", "name", "label", "text")
                or row["normalized_name"]
                or row["raw_name"]
                or mapped
            ).strip()

            item = {
                "itemCode": mapped,
                "itemName": item_name,
                "batchNo": str(raw_value("batchNo", "batch") or _dict_value(master, "batchNo", "batch") or ""),
                "box": box,
                "loose": loose,
                "qnty": qnty,
                "freeQnty": raw_int("freeQnty", "freeQty", "free", fallback=0),
                "rate": rate,
                "boxRate": box_rate or rate,
                "looseRate": loose_rate,
                "mrp": mrp,
                "itemAmount": amount,
                "discount": raw_money("discount", "disc", fallback=_dict_value(master, "purchaseDiscountAmount") or 0),
                "cgst": raw_money("cgst", fallback=_dict_value(master, "cgst") or 0),
                "sgst": raw_money("sgst", fallback=_dict_value(master, "sgst") or 0),
                "cess": raw_money("cess", fallback=_dict_value(master, "cess") or 0),
                "addCess": raw_money("addCess", "adCess", fallback=_dict_value(master, "addCess", "adCess") or 0),
                "igst": raw_money("igst", fallback=_dict_value(master, "igst") or 0),
                "t1Amt": raw_money("t1Amt", "t1", fallback=_dict_value(master, "t1Amt") or 0),
                "t2Amt": raw_money("t2Amt", "t2", fallback=_dict_value(master, "t2Amt") or 0),
                "t3Amt": raw_money("t3Amt", "t3", fallback=_dict_value(master, "t3Amt") or 0),
                "t4Amt": raw_money("t4Amt", "t4", fallback=_dict_value(master, "t4Amt") or 0),
                "etd": raw_money("etd", fallback=_dict_value(master, "etd") or 0),
                # AI Purchase contract expects these ledger fields to be empty;
                # BILLWISE ledgers belong in taxes[].
                "cgstInptLdgr": "",
                "sgstInptLdgr": "",
                "cessInptLdgr": "",
                "adCessInptLdgr": "",
                "igstInptLdgr": "",
                # Calculation-only helpers removed before Purchase Save.
                "packing": packing,
                "t1Rate": raw_money("t1Rate", fallback=_dict_value(master, "t1Rate") or 0),
                "t2Rate": raw_money("t2Rate", fallback=_dict_value(master, "t2Rate") or 0),
                "t3Rate": raw_money("t3Rate", fallback=_dict_value(master, "t3Rate") or 0),
                "t4Rate": raw_money("t4Rate", fallback=_dict_value(master, "t4Rate") or 0),
            }
            items.append(item)

        logger.info("purchase_items_enriched jobId=%s itemCount=%s", job_id, len(items))
        return items

    async def save_purchase(self, session: dict[str, Any], job_id: str, header: dict[str, Any]) -> dict[str, Any]:
        job = self.document_service.get_job(session, job_id)
        items = await self.purchase_items(session, job_id)
        client = reference_data_service.client_for_session(session)
        try:
            result = await purchase_orchestrator.execute(
                session=session,
                job_id=job_id,
                job=job,
                header=dict(header or {}),
                items=items,
                client=client,
            )
        except Exception as exc:
            self.document_service._update_job(job_id, error=str(exc))
            raise

        self.document_service._update_job(
            job_id,
            status="PURCHASE_SAVED",
            completed_at=now_iso(),
            error=None,
        )
        result["job"] = self.document_service.get_job(session, job_id)
        return result
