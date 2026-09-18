from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException

from app.db import conn, now_iso
from app.modules.document_import.purchase_contract import ItemMasterCalculateClient, load_item_master_details
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
    """Translate a mapped import job into Madhushala Purchase inputs.

    Document/QR data owns physical document facts and quantities. Madhushala
    Item Master owns commercial/tax metadata. The full Item Master detail is
    loaded before Calculate; imported bottle quantity is the only quantity input
    that overrides Item Master-derived commercial values.
    """

    def __init__(self, document_service: Any):
        self.document_service = document_service
        self.item_master_snapshot: dict[str, dict[str, Any]] = {}

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
        job = self.document_service.get_job(session, job_id)
        is_qr = str(job.get("source_type") or "").upper() == "QR_HTML"
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

        # Do not rely on the Purchase dropdown summary here. Calculate requires
        # the full Item Master record for each mapped item, so call /api/items/{id}
        # first and retain that exact snapshot for the Calculate client facade.
        item_master = await load_item_master_details(
            reference_data_service,
            session,
            [code for _, code in row_codes],
        )
        self.item_master_snapshot = item_master
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

            master_packing = _int_value(_dict_value(master, "packing", "bottlePerCase", "bottlesPerCase", "caseQty"))
            raw_packing = raw_int("packing", "bottlePerCase", "bottlesPerCase", "caseQty", fallback=row["packing"])
            packing = master_packing or raw_packing

            box = raw_int("box", "boxes", "case", "cases", fallback=0)
            loose = raw_int("loose", "looseQty", fallback=0)
            document_qnty = raw_int(
                "qnty",
                "qty",
                "totalQty",
                "totalQuantity",
                "No of Bottles Dispatched",
                "Bottles Dispatched",
                "No of Bottles Requested",
                "Bottles Requested",
                fallback=0,
            )

            # Source QR/PDF bottle quantity must stay as loose units end-to-end.
            # Never reinterpret extracted bottle count as cases/boxes.
            has_explicit_bottles = any(
                _key(alias) in normalized_raw
                for alias in (
                    "qnty",
                    "No of Bottles Dispatched",
                    "Bottles Dispatched",
                    "No of Bottles Requested",
                    "Bottles Requested",
                )
            )
            if document_qnty and (is_qr or has_explicit_bottles):
                old_box, old_loose = box, loose
                box = 0
                loose = document_qnty
                logger.info(
                    "purchase_quantity_as_loose jobId=%s itemCode=%s sourceBox=%s sourceLoose=%s qnty=%s box=0 loose=%s",
                    job_id,
                    mapped,
                    old_box,
                    old_loose,
                    document_qnty,
                    loose,
                )

            qnty = document_qnty or ((box * packing + loose) if packing else (box + loose))

            # Commercial values are seeded from Item Master. Calculate remains
            # authoritative for the final amounts that are merged into Save.
            purchase_rate = _money(
                _dict_value(master, "purchaseRate", "purchaseRateLoose", "looseRate", "rate", "itemRate")
            )
            purchase_case_rate = _money(
                _dict_value(master, "purchaseRateCase", "boxRate", "caseRate", "purchaseCaseRate")
            )
            if not purchase_rate and purchase_case_rate and packing:
                purchase_rate = _money(purchase_case_rate / packing)
            if not purchase_case_rate and purchase_rate and packing:
                purchase_case_rate = _money(purchase_rate * packing)
            loose_rate = purchase_rate
            box_rate = purchase_case_rate
            rate = purchase_rate or box_rate
            mrp = _money(_dict_value(master, "mrp", "itemMrp", "mrpPerUnit", "saleRate"))

            amount = 0.0
            if box_rate and (box or loose):
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
                "boxRate": box_rate,
                "looseRate": loose_rate,
                "mrp": mrp,
                "itemAmount": amount,
                "discount": _money(_dict_value(master, "purchaseDiscountAmount", "purchaseDiscount", "discountAmount", "discount")),
                "cgst": _money(_dict_value(master, "cgst", "cgstAmount")),
                "sgst": _money(_dict_value(master, "sgst", "sgstAmount")),
                "cess": _money(_dict_value(master, "cess", "cessAmount")),
                "addCess": _money(_dict_value(master, "addCess", "adCess", "addCessAmount", "adCessAmount")),
                "igst": _money(_dict_value(master, "igst", "igstAmount")),
                "t1Amt": _money(_dict_value(master, "t1Amt", "t1Amount", "t1")),
                "t2Amt": _money(_dict_value(master, "t2Amt", "t2Amount", "t2")),
                "t3Amt": _money(_dict_value(master, "t3Amt", "t3Amount", "t3")),
                "t4Amt": _money(_dict_value(master, "t4Amt", "t4Amount", "t4")),
                "etd": _money(_dict_value(master, "etd", "etdAmount")),
                "cgstInptLdgr": "",
                "sgstInptLdgr": "",
                "cessInptLdgr": "",
                "adCessInptLdgr": "",
                "igstInptLdgr": "",
                "packing": packing,
                "t1Rate": _money(_dict_value(master, "t1Rate", "tax1Rate")),
                "t2Rate": _money(_dict_value(master, "t2Rate", "tax2Rate")),
                "t3Rate": _money(_dict_value(master, "t3Rate", "tax3Rate")),
                "t4Rate": _money(_dict_value(master, "t4Rate", "tax4Rate")),
            }
            items.append(item)

        logger.info("purchase_items_enriched jobId=%s itemCount=%s", job_id, len(items))
        return items

    def calculation_client(self, session: dict[str, Any], items: list[dict[str, Any]]) -> ItemMasterCalculateClient:
        if not self.item_master_snapshot:
            raise HTTPException(status_code=500, detail="Item Master snapshot is not available for purchase calculation")
        return ItemMasterCalculateClient(
            reference_data_service.client_for_session(session),
            items,
            self.item_master_snapshot,
        )

    async def save_purchase(self, session: dict[str, Any], job_id: str, header: dict[str, Any]) -> dict[str, Any]:
        job = self.document_service.get_job(session, job_id)
        items = await self.purchase_items(session, job_id)
        client = self.calculation_client(session, items)
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