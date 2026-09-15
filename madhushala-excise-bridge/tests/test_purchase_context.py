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
