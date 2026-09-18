from __future__ import annotations

import pytest

from app.services.reference_data_service import MadhushalaReferenceDataService


@pytest.mark.asyncio
async def test_purchase_item_lookup_prefers_full_item_master_over_dropdown_summary(monkeypatch):
    service = MadhushalaReferenceDataService()
    session = {
        "shop_code": "hedu_test2",
        "company_code": "2",
        "bill_type": "AI",
        "madhushala_token": "token",
    }

    dropdown = {
        "itemCode": "100003",
        "packing": 48,
        "purchaseRate": 0,
        "purchaseRateCase": 0,
        "mrp": 0,
        "etd": 11955.6,
    }
    detail = {
        "itemCode": "100003",
        "packing": 48,
        "purchaseRate": 10.42,
        "purchaseRateCase": 500,
        "mrp": 280,
        "etd": 11955.6,
    }

    class Client:
        async def get_item(self, item_code, company_code):
            assert item_code == "100003"
            assert company_code == "2"
            return detail

    monkeypatch.setattr(service, "client_for_session", lambda _session: Client())
    async def _set_json(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.reference_data_service.cache_service.set_json",
        _set_json,
    )

    result = await service.item(session, "100003", [dropdown])

    assert result["purchaseRate"] == 10.42
    assert result["purchaseRateCase"] == 500
    assert result["mrp"] == 280
    assert result["packing"] == 48
