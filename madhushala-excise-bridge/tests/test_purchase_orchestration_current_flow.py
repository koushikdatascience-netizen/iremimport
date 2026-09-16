from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager, contextmanager

import pytest

import app.modules.document_import.purchase_adapter as adapter_module
from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter
from app.services.purchase_orchestrator import PurchaseOrchestrator, purchase_orchestrator
from app.services.purchase_transaction_service import purchase_transaction_service
from app.services.reference_data_service import reference_data_service


def _calculation_item(item_code: str = "100003") -> dict:
    return {
        "itemCode": item_code,
        "itemName": "100 PIPER DELUX 180ML",
        "batchNo": "1",
        "box": 1,
        "loose": 0,
        "qnty": 48,
        "freeQnty": 0,
        "rate": 10.42,
        "boxRate": 500.0,
        "looseRate": 10.42,
        "mrp": 280.0,
        "itemAmount": 500.0,
        "discount": 0.0,
        "cgst": 0.0,
        "sgst": 0.0,
        "cess": 0.0,
        "addCess": 0.0,
        "igst": 0.0,
        "t1Amt": 100.0,
        "t2Amt": 0.0,
        "t3Amt": 0.0,
        "t4Amt": 0.0,
        "etd": 10.0,
        "cgstInptLdgr": "",
        "sgstInptLdgr": "",
        "cessInptLdgr": "",
        "adCessInptLdgr": "",
        "igstInptLdgr": "",
        "packing": 48,
        "t1Rate": 1.0,
        "t2Rate": 0.0,
        "t3Rate": 0.0,
        "t4Rate": 0.0,
    }


def test_calculation_request_matches_live_madhushala_contract():
    item = _calculation_item()
    request = purchase_orchestrator.build_calculation_request(
        {
            "shopCode": "hedu_test",
            "companyCode": "2",
            "schemeCode": "",
            "items": [item],
        },
        {"salesTaxRate": 0, "salesTaxIncludingFree": False},
    )

    assert set(request) == {
        "shopCode",
        "companyCode",
        "schemeCode",
        "salesTaxRate",
        "salesTaxIncludingFree",
        "items",
    }
    assert set(request["items"][0]) == {
        "itemCode",
        "box",
        "loose",
        "free",
        "boxRate",
        "looseRate",
        "mrp",
        "discount",
        "cgst",
        "sgst",
        "cess",
        "addCess",
        "igst",
        "t1Amt",
        "t2Amt",
        "t3Amt",
        "t4Amt",
        "etd",
        "packing",
        "t1Rate",
        "t2Rate",
        "t3Rate",
        "t4Rate",
    }
    assert request["items"][0]["packing"] == 48
    assert request["items"][0]["box"] == 1


def test_final_payload_accepts_current_manual_empty_year_code():
    payload = {
        "shopCode": "hedu_test",
        "companyCode": "2",
        "yearCode": "",
        "trnDate": "2026-09-16",
        "docDate": "2026-09-16",
        "docNo": "1451",
        "tpPassNo": "",
        "supplierCode": "J00001",
        "storeCode": "S00001",
        "schemeCode": "",
        "purchaseAccCode": "P00002",
        "userCode": "A00001",
        "billType": "AI",
        "pType": "purchase",
        "taxMode": "ITEMWISE",
        "grossAmount": 39030.19,
        "taxAmount": 1004.17,
        "netAmount": 39030.0,
        "items": [{"itemCode": "100003"}],
        "taxes": [],
    }

    PurchaseOrchestrator._validate_final_payload(payload)


@pytest.mark.asyncio
async def test_orchestrator_mirrors_current_itemwise_save_payload(monkeypatch):
    state: dict[str, object] = {"status": ""}
    events: list[tuple[str, str]] = []

    @asynccontextmanager
    async def fake_lock(_job_id: str):
        yield

    def fake_get(_job_id: str):
        return None if not state.get("status") else dict(state)

    def fake_ensure(**_kwargs):
        state.update({"id": "tx1", "status": "PREPARING"})
        return dict(state)

    def fake_update(_job_id: str, status: str, **fields):
        state.update(fields)
        state["status"] = status
        return dict(state)

    def fake_event(_tx_id, _job_id, stage, status, **_kwargs):
        events.append((stage, status))

    monkeypatch.setattr(purchase_transaction_service, "lock", fake_lock)
    monkeypatch.setattr(purchase_transaction_service, "get", fake_get)
    monkeypatch.setattr(purchase_transaction_service, "ensure", fake_ensure)
    monkeypatch.setattr(purchase_transaction_service, "update", fake_update)
    monkeypatch.setattr(purchase_transaction_service, "record_event", fake_event)

    async def fake_tax_mode(_session):
        return "ITEMWISE"

    monkeypatch.setattr(reference_data_service, "tax_mode", fake_tax_mode)

    captured: dict[str, object] = {}

    class FakeClient:
        async def calculate_purchase(self, request):
            captured["calculate"] = request
            return {
                "items": [{
                    "itemCode": "100003",
                    "qnty": 48,
                    "rate": 10.42,
                    "mrp": 280,
                    "itemAmount": 500,
                    "discount": 0,
                    "cgst": 0,
                    "sgst": 0,
                    "cess": 0,
                    "addCess": 0,
                    "igst": 0,
                    "t1Amt": 100,
                    "t2Amt": 0,
                    "t3Amt": 0,
                    "t4Amt": 0,
                    "etd": 10,
                }],
                "grossAmount": 500,
                "taxAmount": 110,
                "netAmount": 610,
                "discount": 0,
                "salesTaxOnMRP": 0,
                "roundOff": 0,
            }

        async def check_duplicate_bill(self, *_args, **_kwargs):
            return {"isDuplicate": False}

        async def save_purchase(self, payload):
            captured["save"] = json.loads(json.dumps(payload))
            return {"success": True, "trnNo": "1024"}

    result = await purchase_orchestrator.execute(
        session={"shop_code": "hedu_test", "company_code": "2"},
        job_id="job-1",
        job={"invoice_number": "1451", "invoice_date": "2026-09-16"},
        header={
            "yearCode": "",
            "trnDate": "2026-09-16",
            "docDate": "2026-09-16",
            "docNo": "1451",
            "tpPassNo": "",
            "supplierCode": "J00001",
            "storeCode": "S00001",
            "schemeCode": "",
            "purchaseAccCode": "P00002",
            "userCode": "A00001",
            "narration": "",
        },
        items=[_calculation_item()],
        client=FakeClient(),
    )

    saved = captured["save"]
    assert saved["shopCode"] == "hedu_test"
    assert saved["companyCode"] == "2"
    assert saved["yearCode"] == ""
    assert saved["pType"] == "purchase"
    assert saved["billType"] == "AI"
    assert saved["taxMode"] == "ITEMWISE"
    assert saved["tpPassNo"] == ""
    assert saved["schemeCode"] == ""
    assert saved["taxes"] == []
    assert saved["grossAmount"] == 500.0
    assert saved["taxAmount"] == 110.0
    assert saved["netAmount"] == 610.0
    assert "packing" not in saved["items"][0]
    assert "boxRate" not in saved["items"][0]
    assert result["trnNo"] == "1024"
    assert ("CALCULATE", "OK") in events
    assert ("DUPLICATE_CHECK", "OK") in events
    assert ("SAVE", "OK") in events


@pytest.mark.asyncio
async def test_qr_bottle_total_is_translated_to_madhushala_case_loose(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE import_items (
          id TEXT, job_id TEXT, raw_name TEXT, normalized_name TEXT,
          packing INTEGER, quantity REAL, rate REAL, mrp REAL, amount REAL,
          mapped_item_code TEXT, excise_item_code TEXT, raw_data_json TEXT,
          created_at TEXT
        );
        CREATE TABLE mappings (
          shop_code TEXT, excise_item_code TEXT, madhushala_item_code TEXT
        );
        """
    )
    db.execute(
        """
        INSERT INTO import_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "row1",
            "job1",
            "South Bank London Dry Gin",
            "South Bank London Dry Gin",
            1,
            6,
            0,
            0,
            0,
            "100003",
            "",
            json.dumps({"box": 6, "qnty": 6, "No of Bottles Dispatched": 6}),
            "2026-09-16",
        ),
    )
    db.commit()

    @contextmanager
    def fake_conn():
        try:
            yield db
            db.commit()
        finally:
            pass

    monkeypatch.setattr(adapter_module, "conn", fake_conn)

    async def fake_items(_session, _codes):
        return {
            "100003": {
                "itemCode": "100003",
                "itemName": "SOUTH BANK LONDON DRY GIN",
                "packing": 48,
                "purchaseRate": 10,
                "purchaseRateCase": 480,
                "mrp": 280,
                "t1Rate": 0,
            }
        }

    monkeypatch.setattr(reference_data_service, "items", fake_items)

    class FakeDocumentService:
        def get_job(self, _session, _job_id):
            return {"source_type": "QR_HTML"}

    adapter = DocumentPurchaseAdapter(FakeDocumentService())
    result = await adapter.purchase_items({"shop_code": "hedu_test"}, "job1")

    assert len(result) == 1
    assert result[0]["qnty"] == 6
    assert result[0]["box"] == 0
    assert result[0]["loose"] == 6
    assert result[0]["packing"] == 48
    db.close()


@pytest.mark.asyncio
async def test_manual_case_loose_shape_is_preserved_when_it_matches_quantity(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE import_items (
          id TEXT, job_id TEXT, raw_name TEXT, normalized_name TEXT,
          packing INTEGER, quantity REAL, rate REAL, mrp REAL, amount REAL,
          mapped_item_code TEXT, excise_item_code TEXT, raw_data_json TEXT,
          created_at TEXT
        );
        CREATE TABLE mappings (
          shop_code TEXT, excise_item_code TEXT, madhushala_item_code TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO import_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "row1",
            "job1",
            "100 PIPERS 12Y 180ML",
            "100 PIPERS 12Y 180ML",
            48,
            1,
            0,
            0,
            0,
            "100001",
            "",
            json.dumps({"box": 1, "loose": 4, "qnty": 52}),
            "2026-09-16",
        ),
    )
    db.commit()

    @contextmanager
    def fake_conn():
        try:
            yield db
            db.commit()
        finally:
            pass

    monkeypatch.setattr(adapter_module, "conn", fake_conn)

    async def fake_items(_session, _codes):
        return {
            "100001": {
                "itemCode": "100001",
                "itemName": "100 PIPERS 12Y 180ML",
                "packing": 48,
                "purchaseRate": 10,
                "purchaseRateCase": 480,
                "mrp": 280,
                "t1Rate": 0,
            }
        }

    monkeypatch.setattr(reference_data_service, "items", fake_items)

    class FakeDocumentService:
        def get_job(self, _session, _job_id):
            return {"source_type": "QR_HTML"}

    result = await DocumentPurchaseAdapter(FakeDocumentService()).purchase_items(
        {"shop_code": "hedu_test"},
        "job1",
    )

    assert result[0]["box"] == 1
    assert result[0]["loose"] == 4
    assert result[0]["qnty"] == 52
    db.close()
