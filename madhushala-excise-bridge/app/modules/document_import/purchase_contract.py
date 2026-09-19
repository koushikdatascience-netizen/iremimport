from __future__ import annotations

import copy
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException


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


def _commercial_values(master: dict[str, Any]) -> tuple[float, float, float, int]:
    # Match the real Madhushala Purchase screen contract.
    #
    # boxRate   <- itemmst.purchaseRateCase
    # looseRate <- itemmst.purchaseRate when non-zero; otherwise use
    #              itemmst.purchaseRateCase exactly as supplied.
    # mrp       <- itemmst.salesRate (legacy aliases retained for compatibility)
    packing = _int_value(_dict_value(master, "packing", "bottlePerCase", "bottlesPerCase", "caseQty"))
    purchase_rate = _money(_dict_value(master, "purchaseRate"))
    box_rate = _money(_dict_value(master, "purchaseRateCase"))
    loose_rate = purchase_rate if purchase_rate else box_rate
    mrp = _money(_dict_value(master, "salesRate", "mrp", "itemMrp", "mrpPerUnit", "saleRate"))
    return loose_rate, box_rate, mrp, packing



async def load_item_master_details(reference_service: Any, session: dict[str, Any], item_codes: list[str]) -> dict[str, dict[str, Any]]:
    """Load mapped purchase items without changing the integration company scope."""
    codes = list(dict.fromkeys(str(code or "").strip() for code in item_codes if str(code or "").strip()))
    if not codes:
        return {}

    company_before = str(session.get("company_code") or "").strip()

    catalogue_loader = getattr(reference_service, "catalogue", None)
    if company_before and callable(catalogue_loader):
        try:
            catalogue = await catalogue_loader(session)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not verify Madhushala Item Master for company {company_before or '[blank]'}: {exc}",
            ) from exc

        available_codes = {
            str(_dict_value(row, "itemCode", "code", "value", "id") or "").strip()
            for row in (catalogue or [])
            if isinstance(row, dict)
        }
        invalid_codes = [code for code in codes if code not in available_codes]

        if invalid_codes:
            # Mapping selection is validated against Madhushala's live purchase
            # dropdown. A cached catalogue can lag behind that live result and
            # falsely report a company mismatch. Re-check the exact same live
            # company-scoped source once before rejecting the mapping.
            fresh_loader = getattr(reference_service, "fresh_catalogue", None)
            if callable(fresh_loader):
                try:
                    fresh_catalogue = await fresh_loader(session)
                except Exception:
                    fresh_catalogue = []
                fresh_codes = {
                    str(_dict_value(row, "itemCode", "code", "value", "id") or "").strip()
                    for row in (fresh_catalogue or [])
                    if isinstance(row, dict)
                }
                invalid_codes = [code for code in codes if code not in fresh_codes]

        if invalid_codes:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Mapped Madhushala item(s) {', '.join(invalid_codes)} are not available "
                    f"in company {company_before or '[blank]'} after a fresh Item Master check. "
                    "Refresh and remap those rows for the current company before saving."
                ),
            )

    items_loader = getattr(reference_service, "items", None)
    if not callable(items_loader):
        raise HTTPException(status_code=500, detail="Madhushala Item Master loader is not available")

    try:
        loaded = await items_loader(session, codes)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not load Madhushala Item Master for company {company_before or '[blank]'}: {exc}",
        ) from exc

    company_after = str(session.get("company_code") or "").strip()
    if company_after != company_before:
        raise HTTPException(
            status_code=500,
            detail="Purchase company scope changed while loading Item Master; request was blocked.",
        )

    result: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for code in codes:
        detail = _unwrap_item((loaded or {}).get(code) if isinstance(loaded, dict) else None)
        if detail:
            result[code] = detail
        else:
            missing.append(code)

    if missing:
        raise HTTPException(
            status_code=502,
            detail=f"Madhushala Item Master returned no detail for item(s): {', '.join(missing)}",
        )

    return result


def build_item_master_calculation_request(
    base_request: dict[str, Any],
    purchase_items: list[dict[str, Any]],
    item_master: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build Calculate request with reviewed box/loose; all commercial values come from Item Master."""
    request_items: list[dict[str, Any]] = []

    for item in purchase_items:
        code = str(item.get("itemCode") or "").strip()
        master = item_master.get(code) or {}
        canonical_v2 = _int_value(item.get("_canonicalQuantityVersion")) >= 2
        if canonical_v2:
            box = _int_value(item.get("box"))
            loose = _int_value(item.get("loose"))
        else:
            box = 0
            loose = _int_value(
                item.get("qnty")
                if item.get("qnty") not in (None, "")
                else item.get("loose") if item.get("loose") not in (None, "") else item.get("quantity")
            )
        loose_rate, box_rate, mrp, packing = _commercial_values(master)

        request_items.append(
            {
                "itemCode": code,
                "box": box,
                "loose": loose,
                "free": _int_value(item.get("freeQnty")),
                "boxRate": box_rate,
                "looseRate": loose_rate,
                "mrp": mrp,
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
    """Keep Calculate authoritative while preserving exact Item Master values it omits."""
    if not isinstance(response, dict):
        return response

    output = copy.deepcopy(response)
    container = _response_container(output)
    if not isinstance(container, dict):
        return output

    rows = container.get("items") or container.get("Items") or container.get("itemDetails")
    if isinstance(rows, list):
        source_by_code = {str(item.get("itemCode") or "").strip(): item for item in purchase_items}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(_dict_value(row, "itemCode", "code") or "").strip()
            master = item_master.get(code) or {}
            source = source_by_code.get(code) or {}
            loose_rate, box_rate, mrp, _ = _commercial_values(master)

            if _dict_value(row, "quantity", "qnty", "qty") is None:
                row["quantity"] = _int_value(source.get("qnty"))
            if _dict_value(row, "looseRate", "rate", "unitRate") is None:
                row["looseRate"] = loose_rate
            if _dict_value(row, "boxRate", "caseRate") is None:
                row["boxRate"] = box_rate
            if _dict_value(row, "mrp", "itemMrp") is None:
                row["mrp"] = mrp

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
        payload.clear()
        payload.update(actual_request)
        response = await self._inner.calculate_purchase(payload)
        return _augment_calculation_response(response, self._purchase_items, self._item_master)
