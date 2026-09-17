from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import HTTPException

from app.integrations.madhushala.client import MadhushalaApiError
from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter
from app.services.purchase_orchestrator import purchase_orchestrator
from app.services.reference_data_service import reference_data_service


_CALCULATION_HELPER_KEYS = (
    "packing",
    "boxRate",
    "looseRate",
    "t1Rate",
    "t2Rate",
    "t3Rate",
    "t4Rate",
)


def _error_body(message: str) -> Any:
    text = str(message or "").strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except Exception:
        return text


def _calculate_error(exc: Exception, request_payload: dict[str, Any]) -> HTTPException:
    status_code = 502
    response_body: Any = str(exc)
    if isinstance(exc, MadhushalaApiError):
        if exc.status_code and exc.status_code >= 400:
            status_code = exc.status_code
        response_body = _error_body(str(exc))
    return HTTPException(
        status_code=status_code,
        detail={
            "stage": "CALCULATE",
            "message": "Madhushala purchase calculation failed.",
            "statusCode": status_code,
            "requestUrl": "/api/purchase/calculate",
            "requestHeaders": {
                "accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "[REDACTED]",
            },
            "request": request_payload,
            "response": response_body,
        },
    )


def _validate_calculated_items(
    source_items: list[dict[str, Any]],
    calculation: Any,
    request_payload: dict[str, Any],
) -> None:
    calculated = purchase_orchestrator._calculated_items(calculation)
    if len(calculated) < len(source_items):
        raise HTTPException(
            status_code=502,
            detail={
                "stage": "CALCULATE_NORMALIZE",
                "message": (
                    "Madhushala Calculate returned fewer item rows than were sent. "
                    "Purchase Save has been blocked to prevent zero/partial financial values."
                ),
                "statusCode": 502,
                "requestUrl": "/api/purchase/calculate",
                "request": request_payload,
                "response": calculation,
                "expectedItemCount": len(source_items),
                "calculatedItemCount": len(calculated),
            },
        )


async def calculate_purchase_preview(
    service: Any,
    session: dict[str, Any],
    job_id: str,
    header: dict[str, Any],
) -> dict[str, Any]:
    """Run the live Madhushala Calculate step without saving a purchase.

    This intentionally has no purchase-save side effect. It loads full Item
    Master detail for every mapped item, uses the extracted bottle quantity as
    Calculate ``loose``, and exposes the exact redacted request/response.
    """

    job = service.get_job(session, job_id)
    adapter = DocumentPurchaseAdapter(service)
    items = await adapter.purchase_items(session, job_id)
    client = adapter.calculation_client(session, items)

    try:
        tax_mode = await reference_data_service.tax_mode(session)
    except Exception:
        tax_mode = str(header.get("taxMode") or "ITEMWISE").strip().upper() or "ITEMWISE"
    if tax_mode not in {"ITEMWISE", "BILLWISE"}:
        tax_mode = "ITEMWISE"

    payload: dict[str, Any] = {
        "shopCode": str(session.get("shop_code") or "").strip(),
        "companyCode": str(session.get("company_code") or "").strip(),
        "yearCode": str(header.get("yearCode") or "").strip(),
        "trnDate": str(header.get("trnDate") or date.today().isoformat()).strip(),
        "docDate": str(header.get("docDate") or job.get("invoice_date") or "").strip(),
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
    purchase_orchestrator._reset_calculation_owned_values(payload)

    calc_request = purchase_orchestrator.build_calculation_request(payload, header)
    try:
        calculation = await client.calculate_purchase(calc_request)
    except Exception as exc:
        raise _calculate_error(exc, calc_request) from exc

    _validate_calculated_items(items, calculation, calc_request)
    purchase_orchestrator.merge_calculation(payload, calculation)

    if tax_mode == "BILLWISE":
        payload["taxes"] = await purchase_orchestrator._build_billwise_taxes(session, payload)
        for item in payload["items"]:
            for _, key in purchase_orchestrator.TAX_ITEM_FIELDS:
                item[key] = 0
    else:
        payload["taxes"] = []

    for item in payload["items"]:
        for helper_key in _CALCULATION_HELPER_KEYS:
            item.pop(helper_key, None)

    purchase_orchestrator._validate_final_payload(payload)

    return {
        "success": True,
        "validated": True,
        "jobId": job_id,
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
    }