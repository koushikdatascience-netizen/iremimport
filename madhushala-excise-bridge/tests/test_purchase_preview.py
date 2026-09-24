from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.integrations.madhushala.client import MadhushalaApiError
from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter
from app.modules.document_import.purchase_preview import calculate_purchase_preview
from app.services.reference_data_service import reference_data_service


class FakeDocumentService:
    def get_job(self, _session, _job_id):
        return {
            "id": "job-preview-1",
            "invoice_number": "INV-1451",
            "invoice_date": "2026-09-17",
        }


@pytest.fixture
def purchase_items():
    return [
        {
            "itemCode": "100003",
            "itemName": "100 PIPER DELUX 180ML",
            "batchNo": "",
            "box": 0,
            "loose": 6,
            "qnty": 6,
            "freeQnty": 0,
            "rate": 999,
            "boxRate": 999,
            "looseRate": 999,
            "mrp": 999,
            "itemAmount": 999,
            "discount": 0,
            "cgst": 0,
            "sgst": 0,
            "cess": 0,
            "addCess": 0,
            "igst": 0,
            "t1Amt": 0,
            "t2Amt": 0,
            "t3Amt": 0,
            "t4Amt": 0,
            "etd": 0,
            "cgstInptLdgr": "",
            "sgstInptLdgr": "",
            "cessInptLdgr": "",
            "adCessInptLdgr": "",
            "igstInptLdgr": "",
            "packing": 48,
            "t1Rate": 0,
            "t2Rate": 0,
            "t3Rate": 0,
            "t4Rate": 0,
        }
    ]


@pytest.mark.asyncio
async def test_calculate_preview_calls_live_calculate_shape_without_purchase_save(monkeypatch, purchase_items):
    async def fake_purchase_items(self, _session, _job_id):
        return [dict(item) for item in purchase_items]

    monkeypatch.setattr(DocumentPurchaseAdapter, "purchase_items", fake_purchase_items)

    async def fake_tax_mode(_session):
        return "ITEMWISE"

    monkeypatch.setattr(reference_data_service, "tax_mode", fake_tax_mode)

    captured = {}

    class FakeClient:
        async def calculate_purchase(self, payload):
            captured["calculate"] = payload
            return {
                "items": [
                    {
                        "itemCode": "100003",
                        "quantity": 6,
                        "looseRate": 211.8,
                        "mrp": 280,
                        "amount": 1270.8,
                        "discountAmount": 0,
                        "cgst": 0,
                        "sgst": 0,
                        "cess": 0,
                        "addCess": 0,
                        "igst": 0,
                        "t1Amt": 12.5,
                        "t2Amt": 0,
                        "t3Amt": 0,
                        "t4Amt": 0,
                        "etd": 100,
                    }
                ],
                "grossAmount": 1270.8,
                "totalTaxAmount": 112.5,
                "netAmount": 1383,
                "roundOff": 0,
            }

    client = FakeClient()
    monkeypatch.setattr(reference_data_service, "client_for_session", lambda _session: client)

    result = await calculate_purchase_preview(
        FakeDocumentService(),
        {"shop_code": "hedu_test", "company_code": "2"},
        "job-preview-1",
        {
            "yearCode": "",
            "trnDate": "2026-09-17",
            "docDate": "2026-09-17",
            "docNo": "INV-1451",
            "supplierCode": "J00001",
            "storeCode": "S00001",
            "purchaseAccCode": "P00002",
            "userCode": "A00001",
            "schemeCode": "",
        },
    )

    calc = captured["calculate"]
    assert calc["shopCode"] == "hedu_test"
    assert calc["companyCode"] == "2"
    assert calc["items"][0]["itemCode"] == "100003"
    assert calc["items"][0]["loose"] == 6
    assert calc["items"][0]["box"] == 0
    assert calc["items"][0]["mrp"] == 0

    payload = result["purchasePayload"]
    assert result["validated"] is True
    assert payload["yearCode"] == ""
    assert payload["grossAmount"] == 1270.8
    assert payload["taxAmount"] == 112.5
    assert payload["netAmount"] == 1383.0
    assert payload["items"][0]["rate"] == 211.8
    assert payload["items"][0]["mrp"] == 280.0
    assert payload["items"][0]["itemAmount"] == 1270.8
    assert "packing" not in payload["items"][0]
    assert result["calculationDebug"]["requestHeaders"]["Authorization"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_calculate_preview_returns_structured_madhushala_failure(monkeypatch, purchase_items):
    async def fake_purchase_items(self, _session, _job_id):
        return [dict(item) for item in purchase_items]

    monkeypatch.setattr(DocumentPurchaseAdapter, "purchase_items", fake_purchase_items)

    async def fake_tax_mode(_session):
        return "ITEMWISE"

    monkeypatch.setattr(reference_data_service, "tax_mode", fake_tax_mode)

    class FakeClient:
        async def calculate_purchase(self, _payload):
            raise MadhushalaApiError('{"errors":{"items":["Invalid purchase item"]}}', 400)

    monkeypatch.setattr(reference_data_service, "client_for_session", lambda _session: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await calculate_purchase_preview(
            FakeDocumentService(),
            {"shop_code": "hedu_test", "company_code": "2"},
            "job-preview-1",
            {
                "trnDate": "2026-09-17",
                "docDate": "2026-09-17",
                "docNo": "INV-1451",
                "supplierCode": "J00001",
                "storeCode": "S00001",
                "purchaseAccCode": "P00002",
                "userCode": "A00001",
            },
        )

    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    assert detail["stage"] == "CALCULATE"
    assert detail["statusCode"] == 400
    assert detail["requestUrl"] == "/api/purchase/calculate"
    assert detail["requestHeaders"]["Authorization"] == "[REDACTED]"
    assert detail["request"]["items"][0]["itemCode"] == "100003"
    assert detail["response"]["errors"]["items"] == ["Invalid purchase item"]


@pytest.mark.asyncio
async def test_calculate_preview_blocks_partial_item_response(monkeypatch, purchase_items):
    doubled = [dict(purchase_items[0]), {**purchase_items[0], "itemCode": "100004", "itemName": "SECOND ITEM"}]

    async def fake_purchase_items(self, _session, _job_id):
        return [dict(item) for item in doubled]

    monkeypatch.setattr(DocumentPurchaseAdapter, "purchase_items", fake_purchase_items)

    async def fake_tax_mode(_session):
        return "ITEMWISE"

    monkeypatch.setattr(reference_data_service, "tax_mode", fake_tax_mode)

    class FakeClient:
        async def calculate_purchase(self, _payload):
            return {
                "items": [{"itemCode": "100003", "quantity": 6, "looseRate": 211.8}],
                "grossAmount": 1270.8,
                "taxAmount": 0,
                "netAmount": 1270.8,
            }

    monkeypatch.setattr(reference_data_service, "client_for_session", lambda _session: FakeClient())

    with pytest.raises(HTTPException) as exc_info:
        await calculate_purchase_preview(
            FakeDocumentService(),
            {"shop_code": "hedu_test", "company_code": "2"},
            "job-preview-1",
            {
                "trnDate": "2026-09-17",
                "docDate": "2026-09-17",
                "docNo": "INV-1451",
                "supplierCode": "J00001",
                "storeCode": "S00001",
                "purchaseAccCode": "P00002",
                "userCode": "A00001",
            },
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["stage"] == "CALCULATE_NORMALIZE"
    assert exc_info.value.detail["expectedItemCount"] == 2
    assert exc_info.value.detail["calculatedItemCount"] == 1


@pytest.mark.asyncio
async def test_handoff_preview_preserves_box_and_loose_rates(monkeypatch, purchase_items):
    async def fake_purchase_items(self, _session, _job_id):
        item = dict(purchase_items[0])
        item.update({
            "itemCode": "A00056",
            "box": 0,
            "loose": 1,
            "qnty": 1,
            "boxRate": 3145.0,
            "looseRate": 0.0,
            "rate": 0.0,
            "mrp": 250.0,
            "_canonicalQuantityVersion": 2,
        })
        return [item]

    monkeypatch.setattr(DocumentPurchaseAdapter, "purchase_items", fake_purchase_items)

    async def fake_tax_mode(_session):
        return "ITEMWISE"

    monkeypatch.setattr(reference_data_service, "tax_mode", fake_tax_mode)

    class FakeClient:
        async def calculate_purchase(self, payload):
            return {
                "items": [{
                    "itemCode": "A00056",
                    "quantity": 1,
                    "boxRate": 3145.0,
                    "looseRate": 0.0,
                    "mrp": 250.0,
                    "amount": 0.0,
                    "t1Amount": 2.5,
                    "t2Amount": 95.17,
                    "t3Amount": 1.95,
                    "totalAmount": 99.62,
                }],
                "grossAmount": 99.62,
                "netAmount": 100.0,
            }

    monkeypatch.setattr(reference_data_service, "client_for_session", lambda _session: FakeClient())

    result = await calculate_purchase_preview(
        FakeDocumentService(),
        {"shop_code": "WBTEST", "company_code": "2"},
        "job-preview-1",
        {
            "trnDate": "2026-09-24",
            "docDate": "2026-09-24",
            "docNo": "INV-A00056",
            "supplierCode": "J00001",
            "storeCode": "S00001",
            "purchaseAccCode": "P00002",
            "userCode": "A00001",
        },
        preserve_commercial_rates=True,
    )

    item = result["purchasePayload"]["items"][0]
    assert item["boxRate"] == 3145.0
    assert item["looseRate"] == 0.0
