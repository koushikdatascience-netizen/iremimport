from __future__ import annotations

import pytest

from app.integrations.madhushala.client import MadhushalaApiError
from app.modules.document_import.purchase_contract import load_item_master_details


@pytest.mark.asyncio
async def test_item_master_403_on_dropdown_company_retries_other_company():
    calls = []

    class Client:
        async def get_item(self, item_code, company_code):
            calls.append((item_code, company_code))
            if company_code == "2":
                raise MadhushalaApiError("Company '2' is not within your accessible scope.", 403)
            if company_code == "7":
                return {
                    "itemCode": item_code,
                    "packing": 12,
                    "purchaseRate": 100,
                    "mrp": 150,
                }
            raise AssertionError(company_code)

    class Service:
        async def companies(self, _session):
            return [{"companyCode": "2"}, {"companyCode": "7"}]

        def client_for_session(self, _session):
            return Client()

    session = {"company_code": "2"}
    result = await load_item_master_details(Service(), session, ["B00003"])

    assert session["company_code"] == "7"
    assert result["B00003"]["itemCode"] == "B00003"
    assert calls == [("B00003", "2"), ("B00003", "7")]
