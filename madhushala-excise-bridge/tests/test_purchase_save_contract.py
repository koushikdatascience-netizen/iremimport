from __future__ import annotations

import pytest

from app.integrations.madhushala.client import MadhushalaApiError, MadhushalaClient


def test_purchase_datetime_normalizes_date_only_and_up_excise_timestamp():
    assert MadhushalaClient._purchase_datetime("2026-06-16") == "2026-06-16T00:00:00"
    assert MadhushalaClient._purchase_datetime("2026-06-16 13:39:17") == "2026-06-16T13:39:17"
    assert MadhushalaClient._purchase_datetime("16-Jun-2026 15:45:21") == "2026-06-16T15:45:21"


def test_purchase_datetime_rejects_unparseable_value():
    with pytest.raises(MadhushalaApiError):
        MadhushalaClient._purchase_datetime("not-a-date")


@pytest.mark.asyncio
async def test_save_purchase_sends_direct_purchase_request_with_datetime_fields(monkeypatch):
    client = MadhushalaClient("https://example.test", "SHOP", "token")
    captured = {}

    async def fake_request(method, path, *, params=None, json_body=None, headers=None):
        captured.update(
            {
                "method": method,
                "path": path,
                "params": params,
                "json_body": json_body,
                "headers": headers,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(client, "_request", fake_request)

    payload = {
        "shopCode": "SHOP",
        "companyCode": "2",
        "trnDate": "2026-09-16",
        "docDate": "2026-06-16 13:39:17",
        "docNo": "RETAIL995782-20260616133917326",
        "items": [{"itemCode": "M001", "qnty": 1}],
    }

    response = await client.save_purchase(payload)

    assert response == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/purchase/save"
    assert captured["json_body"]["trnDate"] == "2026-09-16T00:00:00"
    assert captured["json_body"]["docDate"] == "2026-06-16T13:39:17"
    assert "request" not in captured["json_body"]
    assert captured["json_body"]["items"][0]["itemCode"] == "M001"
