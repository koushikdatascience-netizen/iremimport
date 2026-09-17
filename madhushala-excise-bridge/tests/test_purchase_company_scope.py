from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.modules.document_import.purchase_contract import (
    _resolve_purchase_company,
    load_item_master_details,
)


class CompanyService:
    def __init__(self, companies):
        self._companies = companies

    async def companies(self, _session):
        return self._companies


@pytest.mark.asyncio
async def test_single_accessible_company_replaces_stale_default_company():
    session = {"company_code": "2"}
    service = CompanyService([{"companyCode": "7", "companyName": "Accessible Company"}])

    resolved = await _resolve_purchase_company(service, session)

    assert resolved == "7"
    assert session["company_code"] == "7"


@pytest.mark.asyncio
async def test_requested_company_is_kept_when_it_is_accessible():
    session = {"company_code": "2"}
    service = CompanyService([{"companyCode": "2"}, {"companyCode": "7"}])

    resolved = await _resolve_purchase_company(service, session)

    assert resolved == "2"
    assert session["company_code"] == "2"


@pytest.mark.asyncio
async def test_stale_company_is_rejected_when_multiple_companies_are_accessible():
    session = {"company_code": "2"}
    service = CompanyService([{"companyCode": "7"}, {"companyCode": "9"}])

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_purchase_company(service, session)

    assert exc_info.value.status_code == 403
    assert "not within your accessible Madhushala scope" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_missing_company_requires_selection_when_multiple_are_accessible():
    session = {"company_code": ""}
    service = CompanyService([{"companyCode": "7"}, {"companyCode": "9"}])

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_purchase_company(service, session)

    assert exc_info.value.status_code == 409
    assert "Select a company" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_item_master_lookup_uses_resolved_accessible_company():
    captured = []

    class Client:
        async def get_item(self, item_code, company_code):
            captured.append((item_code, company_code))
            return {
                "itemCode": item_code,
                "packing": 12,
                "purchaseRate": 100,
                "mrp": 150,
            }

    client = Client()

    class Service(CompanyService):
        def client_for_session(self, _session):
            return client

    session = {"company_code": "2"}
    service = Service([{"companyCode": "7"}])

    result = await load_item_master_details(service, session, ["100010"])

    assert session["company_code"] == "7"
    assert captured == [("100010", "7")]
    assert result["100010"]["itemCode"] == "100010"
