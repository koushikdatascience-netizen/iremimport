from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException

from app.db import conn, now_iso
from app.modules.document_import.purchase_contract import ItemMasterCalculateClient, load_item_master_details
from app.modules.document_import.quantity import extract_physical_quantity
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
        source_type = str(job.get("source_type") or "").upper()
        is_qr = source_type == "QR_HTML"
        is_document_upload = source_type in {"DOCUMENT_PDF", "DOCUMENT_IMAGE"}
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
        missing_document_quantities: list[str] = []

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

            if is_document_upload:
                # PDF/image is normalized to the same canonical purchase rule as
                # QR bottle totals: document identity + one physical unit count.
                # Mapping must never change that count.
                persisted_qnty = _int_value(row["quantity"])
                recovered_qnty = extract_physical_quantity(raw)
                document_qnty = persisted_qnty or recovered_qnty

                # Self-heal older/current jobs that were extracted correctly but
                # persisted before quantity aliases were normalized.
                if recovered_qnty and not persisted_qnty:
                    with conn() as db:
                        db.execute(
                            "UPDATE import_items SET quantity=?, updated_at=? WHERE id=?",
                            (float(recovered_qnty), now_iso(), row["id"]),
                        )

                box = 0
                loose = document_qnty
                qnty = document_qnty
            else:
                document_qnty = raw_int(
                    "qnty",
                    "qty",
                    "totalQty",
                    "totalQuantity",
                    "No of Bottles Dispatched",
                    "Bottles Dispatched",
                    "No of Bottles Requested",
                    "Bottles Requested",
                    fallback=row["quantity"],
                )

                has_explicit_bottle_total = any(
                    _key(alias) in normalized_raw
                    for alias in (
                        "No of Bottles Dispatched",
                        "Bottles Dispatched",
                        "No of Bottles Requested",
                        "Bottles Requested",
                    )
                )
                represented_qnty = ((box * packing) + loose) if packing else (box + loose)
                has_valid_case_loose_shape = bool(
                    document_qnty
                    and (box or loose)
                    and represented_qnty == document_qnty
                )

                if document_qnty and (
                    has_explicit_bottle_total
                    or (is_qr and not has_valid_case_loose_shape)
                ):
                    box = 0
                    loose = document_qnty

                qnty = document_qnty or ((box * packing + loose) if packing else (box + loose))

            # Mirror the exact Madhushala Calculate contract used below:
            #   boxRate   <- itemmst.purchaseRateCase
            #   looseRate <- itemmst.purchaseRate when non-zero, otherwise
            #                itemmst.purchaseRateCase directly (no division).
            #   mrp       <- itemmst.salesRate
            purchase_rate = _money(_dict_value(master, "purchaseRate"))
            purchase_case_rate = _money(_dict_value(master, "purchaseRateCase"))
            loose_rate = purchase_rate if purchase_rate else purchase_case_rate
            box_rate = purchase_case_rate
            rate = loose_rate or box_rate
            mrp = _money(_dict_value(master, "salesRate", "mrp", "itemMrp", "mrpPerUnit", "saleRate"))

            amount = 0.0
            if box_rate and (box or loose):
                amount = _money((box_rate * box) + (loose_rate * loose))
            elif rate and qnty:
                amount = _money(rate * qnty)

            extracted_name = str(
                row["raw_name"]
                or row["normalized_name"]
                or raw_value("brand", "itemName", "labelName", "productName")
                or ""
            ).strip()
            master_name = str(
                _dict_value(master, "itemName", "name", "label", "text")
                or mapped
            ).strip()

            # For uploaded documents, preserve the brand/item identity exactly
            # as extracted from the source document. Mapping contributes the
            # Madhushala itemCode and all commercial/master values.
            item_name = extracted_name if is_document_upload and extracted_name else master_name

            if is_document_upload and qnty <= 0:
                missing_document_quantities.append(item_name)

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
                # Document Purchase never imports Item Master discount. The
                # Calculate request also sends discount=0 and Madhushala remains
                # authoritative for any calculation-time adjustments.
                "discount": 0.0,
                "cgst": _money(_dict_value(master, "cgst", "cgstAmount")),
                "sgst": _money(_dict_value(master, "sgst", "sgstAmount")),
                "cess": _money(_dict_value(master, "cess", "cessAmount")),
                "addCess": _money(_dict_value(master, "addCess", "adCess", "addCessAmount", "adCessAmount")),
                "igst": _money(_dict_value(master, "igst", "igstAmount")),
                "t1Amt": _money(_dict_value(master, "vat", "t1Amt", "t1Amount", "t1")),
                "t2Amt": _money(_dict_value(master, "tcs", "t2Amt", "t2Amount", "t2")),
                "t3Amt": _money(_dict_value(master, "tp", "t3Amt", "t3Amount", "t3")),
                "t4Amt": _money(_dict_value(master, "others", "t4Amt", "t4Amount", "t4")),
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

        if missing_document_quantities:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Document extraction did not provide a positive bottle quantity for: "
                    + ", ".join(missing_document_quantities[:5])
                    + ". Purchase was not saved. Check the extracted quantities and re-upload the document."
                ),
            )

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