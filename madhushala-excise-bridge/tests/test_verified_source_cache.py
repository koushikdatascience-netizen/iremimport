import json

import pytest

from app.modules.document_import import verified_cache


@pytest.mark.asyncio
async def test_ap_cache_revalidates_source_before_serving(monkeypatch):
    stored = {}

    async def set_json(key, value, ttl):
        assert ttl == verified_cache.TTL_SECONDS
        stored[key] = json.loads(json.dumps(value))

    async def get_json(key):
        return json.loads(json.dumps(stored[key])) if key in stored else None

    async def delete(key):
        stored.pop(key, None)

    monkeypatch.setattr(verified_cache.cache_service, "set_json", set_json)
    monkeypatch.setattr(verified_cache.cache_service, "get_json", get_json)
    monkeypatch.setattr(verified_cache.cache_service, "delete", delete)
    monkeypatch.setenv("APP_GIT_SHA", "test-revision")
    session = {"shop_code": "shop-a", "company_code": "company-a", "session_id": "session-a"}
    source = b"%PDF verified source invoice"
    result = {
        "header": {"icdc_number": "ICDC-1", "tp_number": "TP-1", "invoice_date": "01/01/2026",
                   "retailer_name": "private header data"},
        "items": [{"sl_no": 1, "brand_name": "source row", "size_ml": 750, "qty_cases": 2,
                    "qty_bottles": 0, "case_rate": "100.00", "btl_rate": "10.00",
                    "total": "200.00", "brand_number": "B-1", "pages": [1]}],
        "summary": {"invoice_qty_total": 2, "issue_price": "200.00", "cess": "0.00",
                    "additional_previlage_fee": "0.00", "additional_retail_excise_tax": "0.00",
                    "previlage_fee_clubs": "0.00", "icdc_value": "200.00", "tcs": "0.00",
                    "less_this_invoice_value": "200.00", "sub_total": "200.00",
                    "amount_credited": "0.00", "challan_amount": "200.00",
                    "previous_credit_leftover": "0.00"},
        "diagnostics": {"ocr_elapsed_seconds": 42},
        "source": "temporary path",
    }

    await verified_cache.put_verified(source, session, result=result)
    hit = await verified_cache.get_verified(source, session)
    assert hit and hit["kind"] == "AP_ICDC_INVOICE"
    assert hit["result"]["validation"]["status"] == "PASSED"
    assert hit["document"]["items"][0]["invoiceAmount"] == "200.00"
    assert "retailer_name" not in hit["result"]["header"]
    assert "source" not in hit["result"]
    assert await verified_cache.get_verified(source, {**session, "session_id": "other-session"}) is None

    stored[next(iter(stored))]["result"]["items"][0]["total"] = "999.00"
    assert await verified_cache.get_verified(source, session) is None


@pytest.mark.asyncio
async def test_verified_cache_requires_session_scope_and_pdf_signature(monkeypatch):
    called = False

    async def set_json(*args):
        nonlocal called
        called = True

    monkeypatch.setattr(verified_cache.cache_service, "set_json", set_json)
    await verified_cache.put_verified(b"not a pdf", {}, result={})
    assert not called

