from __future__ import annotations

import pytest

from app.modules.document_import import purchase_orchestrator


class FakeClient:
    def __init__(self):
        self.calculate_calls = 0
        self.duplicate_calls = 0

    async def calculate_purchase(self, payload):
        self.calculate_calls += 1
        assert payload["items"][0]["itemCode"] == "I001"
        return {
            "grossAmount": 500,
            "taxAmount": 25,
            "netAmount": 525,
            "items": [{
                "itemCode": "I001",
                "qnty": 48,
                "rate": 10.42,
                "mrp": 500,
                "itemAmount": 500,
                "discount": 0,
                "cgst": 0,
                "sgst": 0,
                "cess": 0,
                "addCess": 0,
                "igst": 0,
                "t1Amt": 25,
                "t2Amt": 0,
                "t3Amt": 0,
                "t4Amt": 0,
                "etd": 0,
            }],
        }

    async def check_duplicate_bill(self, **kwargs):
        self.duplicate_calls += 1
        assert kwargs["supplier_code"] == "SUP-1"
        assert kwargs["doc_no"] == "INV-1"
        return {"isDuplicate": False}


class FakeMaster:
    last = None

    def __init__(self, session):
        self.client = FakeClient()
        FakeMaster.last = self

    async def purchase_tax_mode(self):
        return "ITEMWISE"

    async def tax_tags(self):
        return []


class FakeService:
    def get_job(self, session, job_id):
        return {
            "id": job_id,
            "source_type": "QR_HTML",
            "invoice_number": "INV-1",
            "invoice_date": "2026-06-16",
        }

    async def _purchase_items_for_job(self, session, job_id):
        return [{
            "itemCode": "I001",
            "itemName": "Item",
            "batchNo": "",
            "box": 1,
            "loose": 0,
            "qnty": 48,
            "freeQnty": 0,
            "rate": 10.42,
            "boxRate": 500,
            "looseRate": 10.42,
            "mrp": 500,
            "itemAmount": 500,
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
            "packing": 48,
            "t1Rate": 0,
            "t2Rate": 0,
            "t3Rate": 0,
            "t4Rate": 0,
            "cgstInptLdgr": "",
            "sgstInptLdgr": "",
            "cessInptLdgr": "",
            "adCessInptLdgr": "",
            "igstInptLdgr": "",
        }]

    def _build_purchase_calculation_request(self, payload, header):
        return {
            "shopCode": payload["shopCode"],
            "companyCode": payload["companyCode"],
            "schemeCode": payload["schemeCode"],
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
            "items": [{"itemCode": "I001"}],
        }

    def _merge_purchase_calculation(self, payload, response):
        payload["grossAmount"] = response["grossAmount"]
        payload["taxAmount"] = response["taxAmount"]
        payload["netAmount"] = response["netAmount"]
        payload["items"][0].update(response["items"][0])


@pytest.mark.asyncio
async def test_qr_purchase_always_calculates_and_keeps_optional_fields(monkeypatch):
    async def fake_resolve(service, session, job_id, header):
        return {
            **(header or {}),
            "supplierCode": "SUP-1",
            "storeCode": "STORE-1",
            "purchaseAccCode": "PUR-1",
            "userCode": "USR-1",
            "schemeCode": "",
            "tpPassNo": "",
            "taxMode": "ITEMWISE",
        }

    monkeypatch.setattr(purchase_orchestrator, "resolve_required_purchase_header", fake_resolve)
    monkeypatch.setattr(purchase_orchestrator, "MadhushalaMasterService", FakeMaster)

    orchestrator = purchase_orchestrator.PurchaseOrchestrator(FakeService())
    payload, duplicate = await orchestrator.prepare(
        {
            "shop_code": "SHOP-1",
            "company_code": "01",
            "bill_type": "AI",
            "madhushala_token": "",
        },
        "job-qr",
        {"docNo": "INV-1", "docDate": "2026-06-16", "yearCode": "2026-27"},
    )

    assert payload["tpPassNo"] == ""
    assert payload["schemeCode"] == ""
    assert payload["purchaseAccCode"] == "PUR-1"
    assert payload["userCode"] == "USR-1"
    assert payload["grossAmount"] == 500
    assert payload["taxAmount"] == 25
    assert payload["netAmount"] == 525
    assert payload["taxes"] == []
    assert FakeMaster.last.client.calculate_calls == 1
    assert FakeMaster.last.client.duplicate_calls == 1
    assert duplicate == {"isDuplicate": False}


@pytest.mark.asyncio
async def test_billwise_uses_tax_tags_when_calculate_does_not_return_taxes(monkeypatch):
    async def fake_resolve(service, session, job_id, header):
        return {
            **(header or {}),
            "supplierCode": "SUP-1",
            "storeCode": "STORE-1",
            "purchaseAccCode": "PUR-1",
            "userCode": "USR-1",
            "schemeCode": "",
            "tpPassNo": "",
            "taxMode": "BILLWISE",
        }

    class BillwiseMaster(FakeMaster):
        async def purchase_tax_mode(self):
            return "BILLWISE"

        async def tax_tags(self):
            return [{"taxCode": "T1", "inputLedgerCode": "LEDGER-T1", "effect": "-"}]

    monkeypatch.setattr(purchase_orchestrator, "resolve_required_purchase_header", fake_resolve)
    monkeypatch.setattr(purchase_orchestrator, "MadhushalaMasterService", BillwiseMaster)

    orchestrator = purchase_orchestrator.PurchaseOrchestrator(FakeService())
    payload, _ = await orchestrator.prepare(
        {"shop_code": "SHOP-1", "company_code": "01", "bill_type": "AI", "madhushala_token": ""},
        "job-billwise",
        {"docNo": "INV-1", "docDate": "2026-06-16", "yearCode": "2026-27"},
    )

    assert payload["taxMode"] == "BILLWISE"
    assert payload["items"][0]["t1Amt"] == 0
    assert payload["taxes"] == [
        {"ledgerCode": "LEDGER-T1", "amount": 25.0, "sign": "-", "taxCode": "T1"}
    ]
