from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from app.db import conn
from app.integrations.madhushala.masters import MadhushalaMasterService


def _money(service: Any, item: dict[str, Any] | None, *aliases: str) -> float:
    return service._catalogue_money(item, *aliases)


async def build_purchase_items(service: Any, session: dict[str, Any], job_id: str) -> list[dict[str, Any]]:
    """Build PurchaseItemRequest rows from document quantities + Madhushala Item Master.

    Source document data owns quantities/physical facts. Madhushala owns commercial and
    tax master values. Master data is read-through cached and missing item detail calls
    are concurrency-limited by MadhushalaMasterService.
    """
    service.get_job(session, job_id)
    records: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    missing: list[str] = []

    with conn() as db:
        rows = db.execute(
            "SELECT * FROM import_items WHERE job_id=? ORDER BY created_at, id",
            (job_id,),
        ).fetchall()
        for raw_row in rows:
            row = dict(raw_row)
            try:
                raw_data = json.loads(row.get("raw_data_json") or "{}")
            except Exception:
                raw_data = {}
            if not isinstance(raw_data, dict):
                raw_data = {}

            mapped = str(row.get("mapped_item_code") or "").strip()
            if not mapped and row.get("excise_item_code"):
                map_row = db.execute(
                    "SELECT madhushala_item_code FROM mappings WHERE shop_code=? AND excise_item_code=?",
                    (session["shop_code"], str(row["excise_item_code"])),
                ).fetchone()
                mapped = str(map_row["madhushala_item_code"] if map_row else "").strip()
            if not mapped:
                missing.append(str(row.get("raw_name") or row.get("normalized_name") or row.get("id")))
                continue
            records.append((row, raw_data, mapped))

    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"Map all items before saving purchase: {', '.join(missing[:5])}",
        )
    if not records:
        raise HTTPException(status_code=400, detail="No items available for purchase save")

    master = MadhushalaMasterService(session)
    enriched = await master.purchase_items_by_codes([mapped for _, _, mapped in records])
    catalogue_by_code = {
        str(service._catalogue_value(item, "itemCode", "code", "value", "id") or "").strip(): item
        for item in enriched
        if isinstance(item, dict)
    }

    items: list[dict[str, Any]] = []
    for row, raw_data, mapped in records:
        raw_normalized = {service._catalogue_key(key): value for key, value in raw_data.items()}

        def raw_value(*aliases: str) -> Any:
            for alias in aliases:
                value = raw_normalized.get(service._catalogue_key(alias))
                if value not in (None, ""):
                    return value
            return None

        def raw_money(*aliases: str, fallback: Any = None) -> float:
            value = raw_value(*aliases)
            return service._catalogue_money({"value": fallback if value in (None, "") else value}, "value")

        def raw_int(*aliases: str, fallback: Any = None) -> int:
            value = raw_value(*aliases)
            return service._catalogue_int({"value": fallback if value in (None, "") else value}, "value")

        fallback_name = str(row.get("normalized_name") or row.get("raw_name") or mapped)
        catalogue_item = catalogue_by_code.get(mapped) or {"itemCode": mapped}
        catalogue_packing = service._catalogue_int(
            catalogue_item,
            "packing",
            "bottlePerCase",
            "bottlesPerCase",
            "caseQty",
        )

        packing = raw_int(
            "packing",
            "bottlePerCase",
            "bottlesPerCase",
            "caseQty",
            fallback=row.get("packing") or catalogue_packing,
        )
        box = raw_int("box", "boxes", "case", "cases", fallback=row.get("quantity") or 1)
        loose = raw_int("loose", "looseQty", fallback=0)
        qnty = raw_int("qnty", "qty", "totalQty", "totalQuantity", fallback=0)
        if not qnty:
            qnty = (box * packing + loose) if packing else (box + loose)

        catalogue_mrp = _money(service, catalogue_item, "mrp", "itemMrp", "mrpPerUnit")
        unit_rate = _money(
            service,
            catalogue_item,
            "purchaseRate",
            "looseRate",
            "bottleRate",
            "rate",
            "itemRate",
        )
        case_rate = _money(service, catalogue_item, "purchaseRateCase", "boxRate", "caseRate")
        if not case_rate and unit_rate and packing:
            case_rate = round(unit_rate * packing, 2)

        rate = raw_money("rate", "purchaseRate", fallback=row.get("rate") or unit_rate)
        box_rate = raw_money("boxRate", "caseRate", "purchaseRateCase", fallback=case_rate or rate)
        loose_rate = raw_money("looseRate", "bottleRate", fallback=unit_rate or rate)
        amount = raw_money("itemAmount", "amount", "lineAmount", "totalAmount", fallback=row.get("amount"))
        if not amount:
            if box_rate and box:
                amount = round((box_rate * box) + (loose_rate * loose), 2)
            elif rate and qnty:
                amount = round(rate * qnty, 2)

        items.append(
            {
                "itemCode": mapped,
                "itemName": service._purchase_item_name(mapped, enriched, fallback_name),
                "batchNo": str(
                    raw_value("batchNo", "batch")
                    or service._catalogue_value(catalogue_item, "batchNo", "batch")
                    or ""
                ),
                "box": box,
                "loose": loose,
                "qnty": qnty,
                "freeQnty": raw_int("freeQnty", "freeQty", "free", fallback=0),
                "rate": rate or loose_rate,
                "boxRate": box_rate or rate,
                "looseRate": loose_rate,
                "mrp": raw_money("mrp", fallback=row.get("mrp") or catalogue_mrp),
                "itemAmount": amount,
                "discount": raw_money(
                    "discount",
                    "disc",
                    fallback=_money(service, catalogue_item, "purchaseDiscountAmount", "discount"),
                ),
                "cgst": raw_money("cgst", fallback=_money(service, catalogue_item, "cgst")),
                "sgst": raw_money("sgst", fallback=_money(service, catalogue_item, "sgst")),
                "cess": raw_money("cess", fallback=_money(service, catalogue_item, "cess")),
                "addCess": raw_money(
                    "addCess",
                    "adCess",
                    "additionalCess",
                    fallback=_money(service, catalogue_item, "addCess", "adCess"),
                ),
                "igst": raw_money("igst", fallback=_money(service, catalogue_item, "igst")),
                "t1Amt": raw_money("t1Amt", "t1", fallback=_money(service, catalogue_item, "t1Amt")),
                "t2Amt": raw_money("t2Amt", "t2", fallback=_money(service, catalogue_item, "t2Amt")),
                "t3Amt": raw_money("t3Amt", "t3", fallback=_money(service, catalogue_item, "t3Amt")),
                "t4Amt": raw_money("t4Amt", "t4", fallback=_money(service, catalogue_item, "t4Amt")),
                "etd": raw_money("etd", fallback=_money(service, catalogue_item, "etd")),
                "cgstInptLdgr": str(raw_value("cgstInptLdgr") or service._catalogue_value(catalogue_item, "cgstInptLdgr") or ""),
                "sgstInptLdgr": str(raw_value("sgstInptLdgr") or service._catalogue_value(catalogue_item, "sgstInptLdgr") or ""),
                "cessInptLdgr": str(raw_value("cessInptLdgr") or service._catalogue_value(catalogue_item, "cessInptLdgr") or ""),
                "adCessInptLdgr": str(raw_value("adCessInptLdgr", "addCessInptLdgr") or service._catalogue_value(catalogue_item, "adCessInptLdgr", "addCessInptLdgr") or ""),
                "igstInptLdgr": str(raw_value("igstInptLdgr") or service._catalogue_value(catalogue_item, "igstInptLdgr") or ""),
                # Calculate-only helper fields; orchestrator strips these before Save.
                "packing": packing,
                "t1Rate": raw_money("t1Rate", fallback=_money(service, catalogue_item, "t1Rate")),
                "t2Rate": raw_money("t2Rate", fallback=_money(service, catalogue_item, "t2Rate")),
                "t3Rate": raw_money("t3Rate", fallback=_money(service, catalogue_item, "t3Rate")),
                "t4Rate": raw_money("t4Rate", fallback=_money(service, catalogue_item, "t4Rate")),
            }
        )

    return items
