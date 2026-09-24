from __future__ import annotations

import copy
import logging
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException


logger = logging.getLogger("madhushala-excise-bridge.purchase-contract")


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


def calculate_source_line_amount(
    *,
    box: Any,
    loose: Any,
    box_rate: Any,
    loose_rate: Any,
    rate: Any = 0,
    qnty: Any = 0,
) -> float:
    """Calculate a local pre-Calculate line amount without mixing case and loose rates.

    This value is only a bridge-side preview/fallback. Madhushala Calculate remains
    authoritative for the final financial values. Quantity identity is preserved:
    cases use boxRate, loose bottles use looseRate, and a loose-only row must never
    fall through to a case/general rate while looseRate is available.
    """
    box_value = _int_value(box)
    loose_value = _int_value(loose)
    box_rate_value = _money(box_rate)
    loose_rate_value = _money(loose_rate)
    rate_value = _money(rate)
    qnty_value = _int_value(qnty)

    if box_value > 0 and box_rate_value > 0:
        return _money(
            (box_rate_value * box_value)
            + (loose_rate_value * loose_value)
        )

    if box_value == 0 and loose_value > 0 and loose_rate_value > 0:
        return _money(loose_rate_value * loose_value)

    if rate_value > 0 and qnty_value > 0:
        return _money(rate_value * qnty_value)

    return 0.0


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


def _commercial_values(
    master: dict[str, Any],
    *,
    exact_purchase_rates: bool = False,
) -> tuple[float, float, float, int]:
    # Madhushala PDF Purchase Calculate contract:
    #   boxRate   <- itemmst.purchaseRateCase
    #   looseRate <- itemmst.purchaseRate
    # with NO bridge-side fallback or rate conversion.
    #
    # Legacy non-PDF flows retain the historical purchaseRateCase fallback when
    # purchaseRate is zero, so this PDF fix does not change QR/other behavior.
    packing = _int_value(_dict_value(master, "packing", "bottlePerCase", "bottlesPerCase", "caseQty"))
    purchase_rate = _money(_dict_value(master, "purchaseRate"))
    box_rate = _money(_dict_value(master, "purchaseRateCase"))
    loose_rate = purchase_rate if exact_purchase_rates else (purchase_rate if purchase_rate else box_rate)
    mrp = _money(_dict_value(master, "salesRate", "mrp", "itemMrp", "mrpPerUnit", "saleRate"))
    return loose_rate, box_rate, mrp, packing



async def load_item_master_details(reference_service: Any, session: dict[str, Any], item_codes: list[str]) -> dict[str, dict[str, Any]]:
    """Load a fresh Purchase dropdown snapshot for Calculate/Save commercial values.

    Madhushala Purchase commercial fields are owned by
    /api/purchase/dropdown/items. Calculate and Save must use the same live
    company-scoped snapshot that mapping/validation sees; falling back to the
    long-lived catalogue cache here can send stale purchaseRateCase/purchaseRate
    values even after the live Purchase dropdown has changed.
    """
    codes = list(dict.fromkeys(str(code or "").strip() for code in item_codes if str(code or "").strip()))
    if not codes:
        return {}

    company_before = str(session.get("company_code") or "").strip()

    fresh_loader = getattr(reference_service, "fresh_catalogue", None)
    catalogue_loader = getattr(reference_service, "catalogue", None)
    items_loader = getattr(reference_service, "items", None)

    # Production Purchase sessions are company-scoped. Lightweight unit/legacy
    # flows can intentionally omit company_code and monkeypatch items(); never
    # force those fixtures through the authenticated live dropdown.
    loader = (fresh_loader if callable(fresh_loader) else catalogue_loader) if company_before else None
    if not callable(loader):
        # Backward-compatible fallback for blank-company test/legacy flows or
        # lightweight reference-service doubles that expose only items().
        # Real production company-scoped requests still use fresh_catalogue().
        if not callable(items_loader):
            raise HTTPException(status_code=500, detail="Madhushala Purchase Item Master loader is not available")
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

    try:
        catalogue = await loader(session)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not load Madhushala Purchase Item Master for company {company_before or '[blank]'}: {exc}",
        ) from exc

    company_after = str(session.get("company_code") or "").strip()
    if company_after != company_before:
        raise HTTPException(
            status_code=500,
            detail="Purchase company scope changed while loading Item Master; request was blocked.",
        )

    by_code: dict[str, dict[str, Any]] = {}
    for row in catalogue or []:
        if not isinstance(row, dict):
            continue
        code = str(_dict_value(row, "itemCode", "code", "value", "id") or "").strip()
        if code and code not in by_code:
            by_code[code] = dict(row)

    for requested_code in codes:
        traced = by_code.get(requested_code) or {}
        logger.info(
            "purchase_master_snapshot itemCode=%s companyCode=%s purchaseRate=%s purchaseRateCase=%s salesRate=%s packing=%s",
            requested_code,
            company_before,
            _dict_value(traced, "purchaseRate"),
            _dict_value(traced, "purchaseRateCase"),
            _dict_value(traced, "salesRate", "mrp"),
            _dict_value(traced, "packing"),
        )

    missing = [code for code in codes if code not in by_code]
    if missing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Mapped Madhushala item(s) {', '.join(missing)} are not available "
                f"in company {company_before or '[blank]'} after a fresh Item Master check. "
                "Refresh and remap those rows for the current company before saving."
            ),
        )

    return {code: by_code[code] for code in codes}


def build_item_master_calculation_request(
    base_request: dict[str, Any],
    purchase_items: list[dict[str, Any]],
    item_master: dict[str, dict[str, Any]],
    *,
    exact_purchase_rates: bool = False,
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
        loose_rate, box_rate, mrp, packing = _commercial_values(
            master,
            exact_purchase_rates=exact_purchase_rates,
        )
        # Quantity identity and Item Master rates are independent in the
        # Madhushala Calculate contract. Always send both commercial rate
        # fields exactly from the Purchase Item Master:
        #   boxRate   <- purchaseRateCase
        #   looseRate <- purchaseRate
        # Box/loose determine quantity only; they must not zero the other rate.

        logger.info(
            "purchase_calculate_rate itemCode=%s exactPurchaseRates=%s masterPurchaseRate=%s masterPurchaseRateCase=%s outgoingLooseRate=%s outgoingBoxRate=%s",
            code,
            exact_purchase_rates,
            _dict_value(master, "purchaseRate"),
            _dict_value(master, "purchaseRateCase"),
            loose_rate,
            box_rate,
        )

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
    *,
    exact_purchase_rates: bool = False,
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
        source_by_code: dict[str, list[dict[str, Any]]] = {}
        for source_item in purchase_items:
            code = str(source_item.get("itemCode") or "").strip()
            source_by_code.setdefault(code, []).append(source_item)

        used_by_code: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(_dict_value(row, "itemCode", "code") or "").strip()
            master = item_master.get(code) or {}
            occurrence = used_by_code.get(code, 0)
            matching = source_by_code.get(code) or []
            source = matching[occurrence] if occurrence < len(matching) else {}
            used_by_code[code] = occurrence + 1
            loose_rate, box_rate, mrp, _ = _commercial_values(
                master,
                exact_purchase_rates=exact_purchase_rates,
            )
            # Preserve both Item Master rates if Madhushala omits either one
            # from the Calculate response. Do not zero rates based on quantity.

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
        *,
        exact_purchase_rates: bool = False,
    ) -> None:
        self._inner = inner
        self._purchase_items = purchase_items
        self._item_master = item_master
        self._exact_purchase_rates = exact_purchase_rates

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def calculate_purchase(self, payload: dict[str, Any]) -> Any:
        actual_request = build_item_master_calculation_request(
            payload,
            self._purchase_items,
            self._item_master,
            exact_purchase_rates=self._exact_purchase_rates,
        )
        payload.clear()
        payload.update(actual_request)
        response = await self._inner.calculate_purchase(payload)
        return _augment_calculation_response(
            response,
            self._purchase_items,
            self._item_master,
            exact_purchase_rates=self._exact_purchase_rates,
        )
