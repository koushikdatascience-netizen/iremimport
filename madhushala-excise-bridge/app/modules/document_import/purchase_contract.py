from __future__ import annotations

import asyncio
import copy
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException

from app.config import settings


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
    if not isinstance(row, dict):
        return None
    normalized = {_key(name): value for name, value in row.items()}
    for alias in aliases:
        value = normalized.get(_key(alias))
        if value not in (None, ""):
            return value
    return None


def _unwrap_item(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    for key in ("data", "result", "item", "value"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return value


def _response_container(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    for key in ("data", "result", "calculation", "payload"):
        nested = response.get(key)
        if isinstance(nested, dict) and isinstance(
            nested.get("items") or nested.get("Items") or nested.get("itemDetails"),
            list,
        ):
            return nested
    return response


async def load_item_master_details(reference_service: Any, session: dict[str, Any], item_codes: list[str]) -> dict[str, dict[str, Any]]:
    """Load the full Item Master record for every mapped purchase item.

    The Purchase Calculate contract needs more than the dropdown summary. Always
    call the item-detail endpoint so packing, rates, MRP and tax fields come from
    the same Item Master source used when an operator selects an item manually.
    """
    codes = list(dict.fromkeys(str(code or "").strip() for code in item_codes if str(code or "").strip()))
    if not codes:
        return {}

    client = reference_service.client_for_session(session)
    company_code = str(session.get("company_code") or settings.DEFAULT_COMPANY_CODE).strip()
    semaphore = asyncio.Semaphore(max(1, settings.MADHUSHALA_ITEM_FETCH_CONCURRENCY))

    async def load(code: str) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            try:
                detail = _unwrap_item(await client.get_item(code, company_code))
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Could not load Madhushala Item Master detail for item {code}: {exc}",
                ) from exc
            if not detail:
                raise HTTPException(
                    status_code=502,
                    detail=f"Madhushala Item Master returned no detail for item {code}",
                )
            return code, detail

    pairs = await asyncio.gather(*(load(code) for code in codes))
    return {code: detail for code, detail in pairs}


def build_item_master_calculation_request(
    base_request: dict[str, Any],
    purchase_items: list[dict[str, Any]],
    item_master: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the real Calculate request from Item Master + extracted bottle qty.

    Physical quantity comes from the QR/PDF import. Per the manual Purchase flow,
    imported bottle quantity is sent as ``loose`` and ``box`` is zero. Every
    commercial/tax input is taken from the full Madhushala Item Master detail.
    """
    request_items: list[dict[str, Any]] = []

    for item in purchase_items:
        code = str(item.get("itemCode") or "").strip()
        master = item_master.get(code) or {}
        quantity = _int_value(item.get("qnty") if item.get("qnty") not in (None, "") else item.get("loose"))
        packing = _int_value(_dict_value(master, "packing", "bottlePerCase", "bottlesPerCase", "caseQty"))

        loose_rate = _money(
            _dict_value(
                master,
                "purchaseRate",
                "purchaseRateLoose",
                "looseRate",
                "unitRate",
                "rate",
                "itemRate",
            )
        )
        box_rate = _money(_dict_value(master, "purchaseRateCase", "boxRate", "caseRate", "purchaseCaseRate"))
        if not loose_rate and box_rate and packing:
            loose_rate = _money(box_rate / packing)
        if not box_rate and loose_rate and packing:
            box_rate = _money(loose_rate * packing)

        request_items.append(
            {
                "itemCode": code,
                "box": 0,
                "loose": quantity,
                "free": _int_value(item.get("freeQnty")),
                "boxRate": box_rate,
                "looseRate": loose_rate,
                "mrp": _money(_dict_value(master, "mrp", "itemMrp", "mrpPerUnit", "saleRate")),
                "discount": _money(
                    _dict_value(
                        master,
                        "purchaseDiscountAmount",
                        "purchaseDiscount",
                        "discountAmount",
                        "discount",
                    )
                ),
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
                "packing": packing,
                "t1Rate": _money(_dict_value(master, "t1Rate", "tax1Rate")),
                "t2Rate": _money(_dict_value(master, "t2Rate", "tax2Rate")),
                "t3Rate": _money(_dict_value(master, "t3Rate", "tax3Rate")),
                "t4Rate": _money(_dict_value(master, "t4Rate", "tax4Rate")),
            }
        )

    return {
        "shopCode": str(base_request.get("shopCode") or "").strip(),
        "companyCode": str(base_request.get("companyCode") or "").strip(),
        "schemeCode": str(base_request.get("schemeCode") or "").strip(),
        "salesTaxRate": _money(base_request.get("salesTaxRate")),
        "salesTaxIncludingFree": bool(base_request.get("salesTaxIncludingFree", False)),
        "items": request_items,
    }


def _augment_calculation_response(
    response: Any,
    purchase_items: list[dict[str, Any]],
    item_master: dict[str, dict[str, Any]],
) -> Any:
    """Keep Calculate authoritative while preserving master fields it omits."""
    if not isinstance(response, dict):
        return response

    output = copy.deepcopy(response)
    container = _response_container(output)
    if not isinstance(container, dict):
        return output

    rows = container.get("items") or container.get("Items") or container.get("itemDetails")
    if isinstance(rows, list):
        master_by_code = item_master
        source_by_code = {str(item.get("itemCode") or "").strip(): item for item in purchase_items}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(_dict_value(row, "itemCode", "code") or "").strip()
            master = master_by_code.get(code) or {}
            source = source_by_code.get(code) or {}
            packing = _int_value(_dict_value(master, "packing", "bottlePerCase", "bottlesPerCase", "caseQty"))
            loose_rate = _money(
                _dict_value(master, "purchaseRate", "purchaseRateLoose", "looseRate", "unitRate", "rate", "itemRate")
            )
            box_rate = _money(_dict_value(master, "purchaseRateCase", "boxRate", "caseRate", "purchaseCaseRate"))
            if not loose_rate and box_rate and packing:
                loose_rate = _money(box_rate / packing)
            if not box_rate and loose_rate and packing:
                box_rate = _money(loose_rate * packing)

            if _dict_value(row, "quantity", "qnty", "qty") is None:
                row["quantity"] = _int_value(source.get("qnty"))
            if _dict_value(row, "looseRate", "rate", "unitRate") is None:
                row["looseRate"] = loose_rate
            if _dict_value(row, "boxRate", "caseRate") is None:
                row["boxRate"] = box_rate
            if _dict_value(row, "mrp", "itemMrp") is None:
                row["mrp"] = _money(_dict_value(master, "mrp", "itemMrp", "mrpPerUnit", "saleRate"))

    # Some live Calculate responses expose tax families separately rather than a
    # single taxAmount. Normalize them so Purchase Save receives the full total.
    if _dict_value(container, "taxAmount", "totalTax", "totalTaxAmount") is None:
        component_names = ("totalVAT", "totalTCS", "totalTP", "totalOther", "totalETD")
        if any(_dict_value(container, name) is not None for name in component_names):
            container["taxAmount"] = _money(sum(_money(_dict_value(container, name)) for name in component_names))

    return output


class ItemMasterCalculateClient:
    """Madhushala client facade that replaces Calculate inputs with Item Master data."""

    def __init__(
        self,
        inner: Any,
        purchase_items: list[dict[str, Any]],
        item_master: dict[str, dict[str, Any]],
    ) -> None:
        self._inner = inner
        self._purchase_items = purchase_items
        self._item_master = item_master

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def calculate_purchase(self, payload: dict[str, Any]) -> Any:
        actual_request = build_item_master_calculation_request(payload, self._purchase_items, self._item_master)
        # Mutate the original object intentionally: PurchaseOrchestrator keeps
        # this same object for calculationDebug, so debug shows what was really
        # sent to Madhushala rather than the provisional zeroed DTO.
        payload.clear()
        payload.update(actual_request)
        response = await self._inner.calculate_purchase(payload)
        return _augment_calculation_response(response, self._purchase_items, self._item_master)
