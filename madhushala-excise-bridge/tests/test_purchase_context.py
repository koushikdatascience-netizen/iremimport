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
        [{"schemeCode": "SCH-1", "schemeName": "Regular Purchase"}],
        "scheme",
    ) == [{"code": "SCH-1", "name": "Regular Purchase"}]
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
        "tables": [[
            ["Consignor DetailsConsignee Details"],
            ["License Type", "FL2", "License Type", "FL4C"],
            ["Unit Name", "HARPREET SINGH", "Unit Name", "Vina Alkohal"],
            ["Licensee Name", "HARPREET SINGH", "Licensee Name", "Atul Kumar Jaiswal"],
        ]]
    }
    assert purchase_context.up_supplier_hint(payload) == "HARPREET SINGH"


@pytest.mark.asyncio
async def test_purchase_context_matches_supplier_current_user_scheme_and_tax_mode(monkeypatch):
    class FakeMaster:
        def __init__(self, session):
            assert session["shop_code"] == "SHOP-A"
            self.shop_code = "SHOP-A"
            self.company_code = "2"
            self.bill_type = "AI"

        async def suppliers(self):
            return [
                {"ledgerCode": "SUP-1", "ledgerName": "Other Supplier"},
                {"ledgerCode": "SUP-2", "ledgerName": "HARPREET SINGH"},
            ]

        async def storages(self):
            return [{"storeCode": "STORE-1", "storeName": "Main Store"}]

        async def accounts(self):
            return [{"purchaseAccCode": "PUR-1", "purchaseAccName": "Purchase"}]

        async def users(self):
            return [
                {"userCode": "U1", "userName": "Other User"},
                {"userCode": "U7", "userName": "Atul Kumar"},
            ]

        async def schemes(self):
            return [{"schemeCode": "SCH-1", "schemeName": "Regular Purchase"}]

        async def purchase_tax_mode(self):
            return "BILLWISE"

    monkeypatch.setattr(purchase_context, "MadhushalaMasterService", FakeMaster)
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
        "schemeCode": "SCH-1",
        "purchaseAccCode": "PUR-1",
        "userCode": "U7",
        "taxMode": "BILLWISE",
    }
    assert result["taxMode"] == "BILLWISE"
    assert result["warnings"] == []
    assert result["options"]["suppliers"][1]["name"] == "HARPREET SINGH"
    assert result["options"]["schemes"][0]["code"] == "SCH-1"


def test_purchase_context_script_uses_document_date_for_financial_year():
    script = open("app/static/purchase-context.js", encoding="utf-8").read()
    assert "if (month < 4) year -= 1" in script
    assert "purchase-tp-pass-no" in script
    assert "purchase-scheme-code" in script
    assert "madhushalaPurchaseProfile" in script


def test_frontend_required_fields_match_purchase_contract():
    helper = open("app/static/purchase-context.js", encoding="utf-8").read()
    assert '{id: "purchase-supplier-code", key: "supplierCode", optionKey: "suppliers", label: "Supplier", prompt: "Select Supplier", persist: false, required: true}' in helper
    assert '{id: "purchase-store-code", key: "storeCode", optionKey: "storages", label: "Store", prompt: "Select Store", persist: true, required: true}' in helper
    assert '{id: "purchase-scheme-code", key: "schemeCode", optionKey: "schemes", label: "Scheme", prompt: "Select Scheme", persist: true, required: false}' in helper
    assert '{id: "purchase-acc-code", key: "purchaseAccCode", optionKey: "accounts", label: "Purchase A/c", prompt: "Select Purchase A/c", persist: true, required: true}' in helper
    assert '{id: "purchase-user-code", key: "userCode", optionKey: "users", label: "User", prompt: "Select User", persist: true, required: true}' in helper
    required_fn = helper.split("function requiredPurchaseFields()", 1)[1].split("function currentMissingFields()", 1)[0]
    assert "purchase-tp-pass-no" not in required_fn
    assert "Madhushala requires:" in helper
