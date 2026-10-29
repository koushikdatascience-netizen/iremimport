"""Short-lived reuse of immutable, verified source facts, never Purchase inputs.

Scope includes the authenticated session, company, shop, source SHA and running
code revision. No government pages, credentials, mappings or master data enter
this cache. Every hit is cloned and passes the current source validation again.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict

from app.modules.document_import.schemas import ExtractedDocument
from app.services.cache_service import cache_service

TTL_SECONDS = 900
MAX_ENTRIES = 16
MAX_BYTES = 1_000_000
_recent: OrderedDict[str, None] = OrderedDict()


def _key(data: bytes, session: dict) -> str | None:
    scope = [session.get(k) for k in ("shop_code", "company_code", "session_id")]
    if not data.startswith(b"%PDF") or not all(scope):
        return None
    tenant = hashlib.sha256(json.dumps(scope).encode()).hexdigest()
    content = hashlib.sha256(data).hexdigest()
    return f"verified-source-v1:{os.getenv('APP_GIT_SHA', 'development')}:{tenant}:{content}"


def _validated(entry: dict) -> dict | None:
    try:
        if entry.get("kind") == "AP_ICDC_INVOICE":
            from app.modules.document_import.ap_icdc.validate import validate_document
            from app.modules.document_import.ap_icdc.invoice_upload import verified_document_from_result
            result = entry["result"]
            result["validation"] = validate_document(result["items"], result["summary"])
            doc = verified_document_from_result(result)
            if not doc.invoiceNumber or not doc.transportPassNo:
                return None
            entry["document"] = doc.model_dump()
            return entry
        if entry.get("kind") == "JHARKHAND":
            from app.modules.document_import.jharkhand_financials import require_verified_financials, money
            doc = ExtractedDocument.model_validate(entry["document"])
            validation = getattr(doc, "sourceFinancialValidation", {}) or {}
            if (str(getattr(doc, "sourceState", "")) != "JHARKHAND"
                    or validation.get("status") != "verified"
                    or validation.get("errors")
                    or not doc.items or not doc.invoiceNumber or not doc.transportPassNo):
                return None
            if any(getattr(i, "sourceFinancialValidation", {}).get("status") != "verified" for i in doc.items):
                return None
            amounts = [money(getattr(i, "invoiceAmount", None)) for i in doc.items]
            financials = getattr(doc, "invoiceFinancials", {})
            amount_sum = sum(amounts) if all(a is not None for a in amounts) else None
            serials = [getattr(i, "sourceRowNumber", None) for i in doc.items]
            serials = [int(value) if str(value or "").isdigit() else None for value in serials]
            required = ("invoiceAmount", "grandTotal", "vat", "aed", "vatOnAed")
            printed = {name: money(financials.get(name)) for name in required}
            if (amount_sum is None or amount_sum != printed["invoiceAmount"]
                    or any(value is None for value in printed.values())
                    or serials != list(range(1, len(doc.items) + 1))
                    or validation.get("lineCount") != len(doc.items)
                    or money(validation.get("lineAmountSum")) != amount_sum
                    or money(validation.get("printedAmount")) != amount_sum):
                return None
            require_verified_financials([{"raw_data_json": json.dumps(i.model_dump())} for i in doc.items])
            return entry
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    except Exception:
        # A failed validation/cache read must always return to real extraction.
        return None
    return None


async def get_verified(data: bytes, session: dict) -> dict | None:
    key = _key(data, session)
    if key is None:
        return None
    try:
        entry = await cache_service.get_json(key)
        if not isinstance(entry, dict):
            return None
        # Detached JSON prevents a caller/review edit from mutating the cache.
        return _validated(json.loads(json.dumps(entry)))
    except Exception:
        return None


async def put_verified(data: bytes, session: dict, *, result=None, document=None) -> None:
    key = _key(data, session)
    if key is None:
        return
    if result is not None:
        # Discard temporary filenames and private driver/retailer header fields.
        result = json.loads(json.dumps(result))
        result["header"] = {k: result.get("header", {}).get(k)
                            for k in ("icdc_number", "tp_number", "invoice_date")}
        result.pop("source", None)
        entry = {"kind": "AP_ICDC_INVOICE", "result": result}
    elif document is not None:
        entry = {"kind": "JHARKHAND", "document": document.model_dump()}
    else:
        return
    entry = _validated(entry)
    if entry is None or len(json.dumps(entry)) > MAX_BYTES:
        return
    try:
        await cache_service.set_json(key, entry, TTL_SECONDS)
        _recent[key] = None
        _recent.move_to_end(key)
        while len(_recent) > MAX_ENTRIES:
            oldest, _ = _recent.popitem(last=False)
            await cache_service.delete(oldest)
    except Exception:
        pass
