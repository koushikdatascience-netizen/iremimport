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
    assert '<script src="./static/qr-browser-fallback.js?v=20260920-portal-mapping-v6"></script>' in html
    assert '<script src="./static/purchase-context.js?v=20260920-portal-mapping-v6"></script>' in html


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
async def test_purchase_context_matches_supplier_current_user_and_warms_purchase_bootstrap(monkeypatch):
    async def fake_bootstrap(session):
        assert session["shop_code"] == "SHOP-A"
        assert session["company_code"] == "2"
        return {
            "companies": [{"code": "2", "name": "COM_HIRAPUR_SHOP"}],
            "suppliers": [
                {"ledgerCode": "SUP-1", "ledgerName": "Other Supplier"},
                {"ledgerCode": "SUP-2", "ledgerName": "HARPREET SINGH"},
            ],
            "storages": [{"storeCode": "STORE-1", "storeName": "Main Store"}],
            "accounts": [{"purchaseAccCode": "PUR-1", "purchaseAccName": "Purchase"}],
            "users": [
                {"userCode": "U1", "userName": "Other User"},
                {"userCode": "U7", "userName": "Atul Kumar"},
            ],
            "schemes": [{"schemeCode": "SCH-1", "schemeName": "Regular Purchase"}],
            "taxMode": "ITEMWISE",
            "catalogue": [{"itemCode": "100003"}],
            "taxTags": [{"taxCode": "T1", "inputLedgerCode": "L1"}],
            "warnings": [],
        }

    monkeypatch.setattr(purchase_context.reference_data_service, "purchase_bootstrap", fake_bootstrap)
    session = {
        "shop_code": "SHOP-A",
        "company_code": "2",
        "bill_type": "AI",
        "madhushala_token": _jwt({"userCode": "U7", "yearCode": "2026-27"}),
    }

    result = await purchase_context.build_purchase_context(session, "HARPREET SINGH")

    assert result["defaults"] == {
        "supplierCode": "SUP-2",
        "storeCode": "STORE-1",
        "schemeCode": "SCH-1",
        "purchaseAccCode": "PUR-1",
        "userCode": "U7",
        "yearCode": "",
    }
    assert result["warnings"] == []
    assert result["purchaseTaxMode"] == "ITEMWISE"
    assert result["catalogueCount"] == 1
    assert result["taxTagCount"] == 1
    assert result["jwtContext"]["yearCode"] == "2026-27"
    assert result["options"]["suppliers"][1]["name"] == "HARPREET SINGH"
    assert result["options"]["schemes"][0]["code"] == "SCH-1"


def test_purchase_context_script_matches_current_manual_purchase_requirements():
    script = open("app/static/purchase-context.js", encoding="utf-8").read()
    assert 'id: "purchase-supplier-code"' in script
    assert 'id: "purchase-store-code"' in script
    assert 'id: "purchase-acc-code"' in script
    assert 'id: "purchase-user-code"' in script
    assert 'id: "purchase-scheme-code"' in script
    assert 'key: "schemeCode", optionKey: "schemes", label: "Scheme", prompt: "Select Scheme", persist: true, required: false' in script
    assert 'key: "purchaseAccCode", optionKey: "accounts", label: "Purchase A/c", prompt: "Select Purchase A/c", persist: true, required: true' in script
    assert 'key: "userCode", optionKey: "users", label: "User", prompt: "Select User", persist: true, required: true' in script
    assert '{id: "purchase-tp-pass-no"' not in script
    assert "financialYearCode" not in script
    assert "Madhushala requires:" not in script
    assert "Purchase requires:" in script
    assert "madhushalaPurchaseProfile" in script


def test_jwt_context_is_diagnostic_and_does_not_force_year_into_purchase_default():
    token = _jwt({
        "companyCode": "2",
        "companyName": "COM_HIRAPUR_SHOP",
        "yearCode": "2026-27",
        "userCode": "A00001",
    })
    context = purchase_context.jwt_context(token)
    assert context == {
        "companyCode": "2",
        "companyName": "COM_HIRAPUR_SHOP",
        "yearCode": "2026-27",
        "userCode": "A00001",
    }
