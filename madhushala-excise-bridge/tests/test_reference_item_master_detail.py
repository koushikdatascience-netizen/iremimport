from __future__ import annotations

import pytest

from app.services.reference_data_service import MadhushalaReferenceDataService


@pytest.mark.asyncio
async def test_purchase_item_lookup_uses_purchase_dropdown_as_source_of_truth(monkeypatch):
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
        "purchaseRateCase": 500,
        "salesRate": 280,
        "etd": 11955.6,
    }

    async def _set_json(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.reference_data_service.cache_service.set_json",
        _set_json,
    )

    def _fail_if_generic_item_client_is_requested(*args, **kwargs):
        raise AssertionError("/api/items/{itemCode} must not be used for Purchase values")

    monkeypatch.setattr(service, "client_for_session", _fail_if_generic_item_client_is_requested)

    result = await service.item(session, "100003", [dropdown])

    assert result == dropdown
    assert result["purchaseRate"] == 0
    assert result["purchaseRateCase"] == 500
    assert result["salesRate"] == 280
    assert result["packing"] == 48
