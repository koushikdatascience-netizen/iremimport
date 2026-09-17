from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.modules.document_import.purchase_contract import load_item_master_details


@pytest.mark.asyncio
async def test_item_master_loader_keeps_session_company_unchanged():
    seen = []

    class Service:
        async def items(self, session, codes):
            seen.append((session["company_code"], list(codes)))
            return {
                "B00003": {
                    "itemCode": "B00003",
                    "packing": 12,
                    "purchaseRate": 100,
                    "mrp": 150,
                }
            }

    session = {"company_code": "2"}
    result = await load_item_master_details(Service(), session, ["B00003"])

    assert session["company_code"] == "2"
    assert seen == [("2", ["B00003"])]
    assert result["B00003"]["itemCode"] == "B00003"


@pytest.mark.asyncio
async def test_item_master_loader_blocks_any_company_mutation():
    class Service:
        async def items(self, session, _codes):
            session["company_code"] = "7"
            return {"B00003": {"itemCode": "B00003"}}

    session = {"company_code": "2"}

    with pytest.raises(HTTPException) as exc_info:
        await load_item_master_details(Service(), session, ["B00003"])

    assert exc_info.value.status_code == 500
    assert "company scope changed" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_item_master_loader_requires_every_mapped_item():
    class Service:
        async def items(self, _session, _codes):
            return {"100010": {"itemCode": "100010"}}

    session = {"company_code": "2"}

    with pytest.raises(HTTPException) as exc_info:
        await load_item_master_details(Service(), session, ["100010", "B00003"])

    assert exc_info.value.status_code == 502
    assert "B00003" in str(exc_info.value.detail)
