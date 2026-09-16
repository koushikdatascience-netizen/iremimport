from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings
from app.db import init_db
from app.integrations.madhushala.cache import MadhushalaCache, madhushala_cache
from app.integrations.madhushala.client import MadhushalaApiError, MadhushalaClient
from app.integrations.madhushala.masters import MadhushalaMasterService
from app.modules.document_import.purchase_orchestrator import PurchaseOrchestrator


@pytest.mark.asyncio
async def test_memory_cache_round_trip_and_delete():
    cache = MadhushalaCache()
    await cache.set("test:key", {"value": 7}, 60)
    assert await cache.get("test:key") == {"value": 7}
    await cache.delete("test:key")
    assert await cache.get("test:key") is None
    await cache.close()


@pytest.mark.asyncio
async def test_item_catalogue_is_read_through_cached(monkeypatch):
    calls = 0

    async def fake_items(self, company_code, bill_type, search=""):
        nonlocal calls
        calls += 1
        return [{"itemCode": "I-CACHE", "itemName": "Cached Item", "packing": 12}]

    monkeypatch.setattr(MadhushalaClient, "get_dropdown_items", fake_items)
    session = {
        "shop_code": "CACHE-SHOP-UNIQUE",
        "company_code": "CACHE-COMPANY-UNIQUE",
        "bill_type": "AI",
        "madhushala_token": "token",
    }
    service = MadhushalaMasterService(session)
    key = "items:CACHE-SHOP-UNIQUE:CACHE-COMPANY-UNIQUE:AI"
    await madhushala_cache.delete(key)

    first = await service.item_catalogue()
    second = await service.item_catalogue()

    assert first == second
    assert calls == 1
    await madhushala_cache.delete(key)


@pytest.mark.asyncio
async def test_get_retries_but_post_save_path_does_not(monkeypatch):
    client = MadhushalaClient("https://example.invalid", "SHOP", "token")
    MadhushalaClient._circuit.failures = 0
    MadhushalaClient._circuit.opened_until = 0
    get_calls = 0

    async def fake_get_send(method, url, **kwargs):
        nonlocal get_calls
        get_calls += 1
        request = httpx.Request(method, url)
        if get_calls < 2:
            return httpx.Response(503, request=request, text="temporary")
        return httpx.Response(200, request=request, json={"ok": True})

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(client, "_send", fake_get_send)
    monkeypatch.setattr("app.integrations.madhushala.client.asyncio.sleep", no_sleep)
    assert await client._request("GET", "/safe", headers={}) == {"ok": True}
    assert get_calls == 2

    post_calls = 0

    async def fake_post_send(method, url, **kwargs):
        nonlocal post_calls
        post_calls += 1
        return httpx.Response(503, request=httpx.Request(method, url), text="unknown save state")

    monkeypatch.setattr(client, "_send", fake_post_send)
    with pytest.raises(MadhushalaApiError):
        await client._request("POST", "/api/purchase/save", json_body={}, headers={})
    assert post_calls == 1


@pytest.mark.asyncio
async def test_saved_purchase_is_returned_idempotently_without_resubmission(tmp_path):
    original_db = settings.DATABASE_PATH
    object.__setattr__(settings, "DATABASE_PATH", str(tmp_path / "bridge.db"))
    try:
        init_db()

        class Service:
            pass

        orchestrator = PurchaseOrchestrator(Service())
        session = {"shop_code": "SHOP", "company_code": "01"}
        payload = {
            "companyCode": "01",
            "supplierCode": "SUP",
            "docNo": "INV-1",
            "items": [{"itemCode": "I1"}],
        }
        response = {"success": True, "trnNo": "1024"}
        orchestrator._record_transaction(
            "job-saved",
            session,
            payload,
            "PURCHASE_SAVED",
            response=response,
            trn_no="1024",
        )

        result = await orchestrator.save(session, "job-saved", {})

        assert result["success"] is True
        assert result["idempotentReplay"] is True
        assert result["madhushalaResponse"] == response
        assert result["purchasePayload"] == payload
    finally:
        object.__setattr__(settings, "DATABASE_PATH", original_db)
