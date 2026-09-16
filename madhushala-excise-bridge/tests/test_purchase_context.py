from __future__ import annotations

import base64
import json

import pytest

from app.main import document_import_html
from app.modules.document_import import purchase_context


def _jwt(payload: dict) -> str:
    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encode({'alg': 'none', 'typ': 'JWT'})}.{encode(payload)}.signature"


def test_document_import_loads_purchase_context_script():
    html = document_import_html()
    assert '<script src="./static/qr-browser-fallback.js"></script>' in html
    assert '<script src="./static/purchase-context.js"></script>' in html


def test_normalize_purchase_master_options_accepts_madhushala_field_names():
    assert purchase_context.normalize_options(
        [{"ledgerCode": "SUP-1", "ledgerName": "HARPREET SINGH"}],
        "supplier",
    ) == [{"code": "SUP-1", "name": "HARPREET SINGH"}]

    assert purchase_context.normalize_options(
        [{"storageCode": "MAIN", "storageName": "Main Store"}],
        "storage",
    ) == [{"code": "MAIN", "name": "Main Store"}]

    assert purchase_context.normalize_options(
        [{"accountCode": "PUR", "accountName": "Purchase Account"}],
        "account",
    ) == [{"code": "PUR", "name": "Purchase Account"}]

    assert purchase_context.normalize_options(
        [{"userCode": "U7", "userName": "Atul Kumar"}],
        "user",
    ) == [{"code": "U7", "name": "Atul Kumar"}]


def test_up_excise_supplier_hint_uses_consignor_not_consignee():
    payload = {
        "tables": [
            [
                ["Consignor DetailsConsignee Details"],
                ["License Type", "FL2", "License Type", "FL4C"],
                ["Unit Name", "HARPREET SINGH", "Unit Name", "Vina Alkohal"],
                ["Licensee Name", "HARPREET SINGH", "Licensee Name", "Atul Kumar Jaiswal"],
            ]
        ]
    }
    assert purchase_context.up_supplier_hint(payload) == "HARPREET SINGH"


@pytest.mark.asyncio
async def test_purchase_context_matches_supplier_and_current_user(monkeypatch):
    class FakeClient:
        def __init__(self, base_url: str, shop_code: str, token: str):
            assert shop_code == "SHOP-A"
            self.token = token

        async def get_purchase_suppliers(self, company_code: str):
            assert company_code == "2"
            return [
                {"ledgerCode": "SUP-1", "ledgerName": "Other Supplier"},
                {"ledgerCode": "SUP-2", "ledgerName": "HARPREET SINGH"},
            ]

        async def get_purchase_storages(self, company_code: str):
            return [{"storeCode": "STORE-1", "storeName": "Main Store"}]

        async def get_purchase_accounts(self, company_code: str):
            return [{"purchaseAccCode": "PUR-1", "purchaseAccName": "Purchase"}]

        async def get_purchase_users(self, company_code: str):
            return [
                {"userCode": "U1", "userName": "Other User"},
                {"userCode": "U7", "userName": "Atul Kumar"},
            ]

    monkeypatch.setattr(purchase_context, "MadhushalaClient", FakeClient)
    session = {
        "shop_code": "SHOP-A",
        "company_code": "2",
        "bill_type": "AI",
        "madhushala_token": _jwt({"userCode": "U7"}),
    }

    result = await purchase_context.build_purchase_context(session, "HARPREET SINGH")

    assert result["defaults"] == {
        "supplierCode": "SUP-2",
        "storeCode": "STORE-1",
        "purchaseAccCode": "PUR-1",
        "userCode": "U7",
    }
    assert result["warnings"] == []
    assert result["options"]["suppliers"][1]["name"] == "HARPREET SINGH"


def test_purchase_context_script_uses_document_date_for_financial_year():
    script = open("app/static/purchase-context.js", encoding="utf-8").read()
    assert "if (month < 4) year -= 1" in script
    assert "purchase-tp-pass-no" in script
    assert "madhushalaPurchaseProfile" in script

@pytest.mark.asyncio
async def test_purchase_save_does_not_block_optional_header_fields(monkeypatch):
    from app.modules.document_import import service as service_module
    from app.modules.document_import.service import DocumentImportService

    class DummyDb:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def execute(self, *args, **kwargs):
            return None

    class FakeClient:
        async def save_purchase(self, payload):
            self.payload = payload
            return {"ok": True}

    async def fake_context(session, supplier_name=""):
        return {"defaults": {}}

    async def fake_items(session, job_id):
        return [{
            "itemCode": "ITEM-1", "itemName": "Item One", "batchNo": "",
            "box": 1, "loose": 0, "qnty": 1, "freeQnty": 0,
            "rate": 10.0, "boxRate": 10.0, "looseRate": 0.0,
            "mrp": 12.0, "itemAmount": 10.0, "discount": 0.0,
            "cgst": 0.0, "sgst": 0.0, "cess": 0.0, "addCess": 0.0,
            "igst": 0.0, "t1Amt": 0.0, "t2Amt": 0.0, "t3Amt": 0.0,
            "t4Amt": 0.0, "etd": 0.0, "cgstInptLdgr": "",
            "sgstInptLdgr": "", "cessInptLdgr": "", "adCessInptLdgr": "",
            "igstInptLdgr": "", "packing": 1, "t1Rate": 0.0,
            "t2Rate": 0.0, "t3Rate": 0.0, "t4Rate": 0.0,
        }]

    service = DocumentImportService(object())
    service.get_job = lambda session, job_id: {
        "id": job_id,
        "source_type": "QR_HTML",
        "invoice_date": "",
        "invoice_number": "",
        "supplier_name": "",
    }
    service._purchase_items_for_job = fake_items
    fake_client = FakeClient()
    service._client_for_session = lambda session: fake_client
    monkeypatch.setattr(service_module, "build_purchase_context", fake_context)
    monkeypatch.setattr(service_module, "conn", lambda: DummyDb())

    result = await service.save_purchase(
        {"shop_code": "SHOP-A", "company_code": "2"},
        "job-12345678",
        {},
    )

    assert result["success"] is True
    assert "docDate" not in fake_client.payload
    assert "supplierCode" not in fake_client.payload
    assert "storeCode" not in fake_client.payload
    assert fake_client.payload["trnDate"]
    assert fake_client.payload["yearCode"]


def test_frontend_does_not_block_purchase_save_on_optional_headers():
    html = open("app/static/index.html", encoding="utf-8").read()
    helper = open("app/static/purchase-context.js", encoding="utf-8").read()
    assert "Fill purchase fields first:" not in html
    assert "if (showFriendlyMissing()) return;" not in helper
