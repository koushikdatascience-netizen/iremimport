from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.modules.document_import import purchase_required


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeDb:
    def __init__(self, raw_rows):
        self.raw_rows = raw_rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        if "SELECT raw_data_json FROM import_items" in sql:
            return FakeCursor(self.raw_rows)
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_resolves_tp_pass_and_unique_purchase_masters(monkeypatch):
    raw = json.dumps({"transportPassNo": "TP-2026-0001"})
    monkeypatch.setattr(purchase_required, "conn", lambda: FakeDb([{"raw_data_json": raw}]))

    async def fake_context(session, supplier_name=""):
        return {
            "defaults": {
                "supplierCode": "SUP-1",
                "storeCode": "STORE-1",
                "schemeCode": "SCH-1",
                "purchaseAccCode": "PUR-1",
                "userCode": "U-1",
            }
        }

    monkeypatch.setattr(purchase_required, "build_purchase_context", fake_context)

    class Service:
        def get_job(self, session, job_id):
            return {"id": job_id, "supplier_name": "HARPREET SINGH", "source_filename": ""}

        def _update_job(self, job_id, **fields):
            raise AssertionError("existing supplier should not need updating")

    resolved = await purchase_required.resolve_required_purchase_header(
        Service(),
        {"shop_code": "SHOP-A"},
        "job-1",
        {},
    )

    assert resolved["tpPassNo"] == "TP-2026-0001"
    assert resolved["supplierCode"] == "SUP-1"
    assert resolved["storeCode"] == "STORE-1"
    assert resolved["schemeCode"] == "SCH-1"
    assert resolved["purchaseAccCode"] == "PUR-1"
    assert resolved["userCode"] == "U-1"
    assert resolved["yearCode"] == ""


@pytest.mark.asyncio
async def test_existing_up_qr_job_recovers_supplier_hint_from_source_page(monkeypatch):
    monkeypatch.setattr(purchase_required, "conn", lambda: FakeDb([]))
    url = (
        "https://cms.upexciseonline.co/transport-pass-tracking/"
        "?tpnum=WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674"
        "&tptype=FG&tpyear=2026"
    )
    html = """
    <html><body><table>
      <tr><th>Consignor Details</th><th></th><th>Consignee Details</th><th></th></tr>
      <tr><td>Unit Name</td><td>HARPREET SINGH</td><td>Unit Name</td><td>Vina Alkohal</td></tr>
    </table></body></html>
    """

    async def fake_fetch(source):
        assert source == url
        return {"text": html}

    seen = {}

    async def fake_context(session, supplier_name=""):
        seen["supplier"] = supplier_name
        return {
            "defaults": {
                "supplierCode": "SUP-HS",
                "storeCode": "STORE-1",
                "schemeCode": "",
                "purchaseAccCode": "PUR-1",
                "userCode": "U-1",
            }
        }

    monkeypatch.setattr(purchase_required, "_fetch_up_transport_page", fake_fetch)
    monkeypatch.setattr(purchase_required, "build_purchase_context", fake_context)

    class Service:
        def get_job(self, session, job_id):
            return {"id": job_id, "supplier_name": "", "source_filename": url}

        def _update_job(self, job_id, **fields):
            seen["persisted"] = fields

    resolved = await purchase_required.resolve_required_purchase_header(
        Service(),
        {"shop_code": "SHOP-A"},
        "job-old",
        {},
    )

    assert resolved["tpPassNo"] == "WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674"
    assert seen["supplier"] == "HARPREET SINGH"
    assert seen["persisted"] == {"supplier_name": "HARPREET SINGH"}
    assert resolved["supplierCode"] == "SUP-HS"
    assert resolved["schemeCode"] == ""
    assert resolved["purchaseAccCode"] == "PUR-1"
    assert resolved["userCode"] == "U-1"


@pytest.mark.asyncio
async def test_tp_pass_scheme_and_year_are_allowed_to_be_empty(monkeypatch):
    monkeypatch.setattr(purchase_required, "conn", lambda: FakeDb([]))

    class Service:
        def get_job(self, session, job_id):
            return {"id": job_id, "supplier_name": "", "source_filename": ""}

        def _update_job(self, job_id, **fields):
            pass

    resolved = await purchase_required.resolve_required_purchase_header(
        Service(),
        {"shop_code": "SHOP-A"},
        "job-1",
        {
            "supplierCode": "SUP-1",
            "storeCode": "STORE-1",
            "purchaseAccCode": "PUR-1",
            "userCode": "U-1",
        },
    )

    assert resolved["tpPassNo"] == ""
    assert resolved["schemeCode"] == ""
    assert resolved["yearCode"] == ""


@pytest.mark.asyncio
async def test_clear_error_when_required_purchase_master_is_unresolved(monkeypatch):
    monkeypatch.setattr(
        purchase_required,
        "conn",
        lambda: FakeDb([{"raw_data_json": json.dumps({"transportPassNo": "TP-1"})}]),
    )

    async def fake_context(session, supplier_name=""):
        return {"defaults": {}}

    monkeypatch.setattr(purchase_required, "build_purchase_context", fake_context)

    class Service:
        def get_job(self, session, job_id):
            return {"id": job_id, "supplier_name": "", "source_filename": ""}

        def _update_job(self, job_id, **fields):
            pass

    with pytest.raises(HTTPException) as exc:
        await purchase_required.resolve_required_purchase_header(
            Service(),
            {"shop_code": "SHOP-A"},
            "job-1",
            {},
        )

    assert exc.value.status_code == 400
    detail = str(exc.value.detail)
    assert "Supplier" in detail
    assert "Store" in detail
    assert "Purchase A/c" in detail
    assert "User" in detail
    assert "Scheme" not in detail
    assert "TP Pass No" not in detail
