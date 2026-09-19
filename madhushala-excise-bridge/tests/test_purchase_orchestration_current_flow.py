from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager, contextmanager

import pytest
from fastapi import HTTPException

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
        {"salesTaxRate": 12.5, "salesTaxIncludingFree": True},
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
    calc_item = request["items"][0]
    assert calc_item["itemCode"] == "100003"
    assert calc_item["loose"] == 48
    for field in purchase_orchestrator.CALCULATION_ZERO_ITEM_FIELDS:
        assert calc_item[field] == 0
    assert request["salesTaxRate"] == 0
    assert request["salesTaxIncludingFree"] is False


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


def test_calculation_response_aliases_become_purchase_values():
    payload = {
        "items": [{
            "itemCode": "100003",
            "qnty": 6,
            "rate": 999,
            "mrp": 999,
            "itemAmount": 999,
            "discount": 999,
        }],
        "grossAmount": 0,
        "taxAmount": 0,
        "netAmount": 0,
        "discount": 0,
        "salesTaxOnMRP": 0,
        "roundOff": 0,
    }
    purchase_orchestrator._reset_calculation_owned_values(payload)
    purchase_orchestrator.merge_calculation(
        payload,
        {
            "items": [{
                "itemCode": "100003",
                "quantity": 6,
                "looseRate": 211.8,
                "mrp": 280,
                "amount": 1270.8,
                "discountAmount": 5.5,
                "t1Amount": 12.5,
                "etdAmount": 100,
            }],
            "grossAmount": 1270.8,
            "totalTaxAmount": 112.5,
            "netAmount": 1383,
            "totalDiscount": 5.5,
            "roundingMinus": 0.3,
            "roundingPlus": 0,
        },
    )

    item = payload["items"][0]
    assert item["qnty"] == 6
    assert item["rate"] == 211.8
    assert item["mrp"] == 280.0
    assert item["itemAmount"] == 1270.8
    assert item["discount"] == 5.5
    assert item["t1Amt"] == 12.5
    assert item["etd"] == 100.0
    assert payload["grossAmount"] == 1270.8
    assert payload["taxAmount"] == 112.5
    assert payload["netAmount"] == 1383.0
    assert payload["discount"] == 5.5
    assert payload["roundOff"] == -0.3


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
                    "quantity": 48,
                    "looseRate": 10.42,
                    "mrp": 280,
                    "amount": 500,
                    "discountAmount": 0,
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
                "totalDiscount": 0,
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

    calc = captured["calculate"]
    assert calc["items"][0]["itemCode"] == "100003"
    assert calc["items"][0]["loose"] == 48
    assert calc["items"][0]["box"] == 0
    assert calc["items"][0]["boxRate"] == 0
    assert calc["items"][0]["mrp"] == 0
    assert calc["salesTaxRate"] == 0
    assert calc["salesTaxIncludingFree"] is False

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
    assert saved["items"][0]["qnty"] == 48
    assert saved["items"][0]["rate"] == 10.42
    assert saved["items"][0]["mrp"] == 280.0
    assert saved["items"][0]["itemAmount"] == 500.0
    assert "packing" not in saved["items"][0]
    assert "boxRate" not in saved["items"][0]
    assert result["trnNo"] == "1024"
    assert result["calculationDebug"]["request"] == captured["calculate"]
    assert result["calculationDebug"]["response"]["grossAmount"] == 500
    assert result["calculationDebug"]["requestHeaders"]["Authorization"] == "[REDACTED]"
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


@pytest.mark.asyncio
async def test_pdf_purchase_uses_persisted_normalized_quantity_when_raw_row_has_no_quantity(monkeypatch):
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
            "row-pdf-1",
            "job-pdf-1",
            "100 PIPER DELUX 180ML",
            "100 PIPER DELUX 180ML",
            None,
            18,
            None,
            None,
            None,
            "100003",
            "",
            json.dumps({"itemName": "100 PIPER DELUX 180ML", "ml": 180}),
            "2026-09-18",
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
                "itemName": "100 PIPER DELUX 180ML",
                "packing": 48,
                "purchaseRate": 211.8,
                "purchaseRateCase": 0,
                "salesRate": 540,
                "etd": 11955.6,
            }
        }

    monkeypatch.setattr(reference_data_service, "items", fake_items)

    class FakeDocumentService:
        def get_job(self, _session, _job_id):
            return {"source_type": "DOCUMENT_PDF"}

    result = await DocumentPurchaseAdapter(FakeDocumentService()).purchase_items(
        {"shop_code": "hedu_test2"},
        "job-pdf-1",
    )

    assert result[0]["qnty"] == 18
    assert result[0]["box"] == 0
    assert result[0]["loose"] == 18
    assert result[0]["packing"] == 48
    assert result[0]["rate"] == 211.8
    assert result[0]["mrp"] == 540.0
    assert result[0]["etd"] == 11955.6
    db.close()


@pytest.mark.asyncio
async def test_pdf_purchase_blocks_save_when_extracted_quantity_is_missing(monkeypatch):
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
            "row-pdf-0",
            "job-pdf-0",
            "ZERO QTY ITEM 750ML",
            "ZERO QTY ITEM 750ML",
            None,
            None,
            None,
            None,
            None,
            "Z00001",
            "",
            json.dumps({"itemName": "ZERO QTY ITEM 750ML", "ml": 750}),
            "2026-09-18",
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
            "Z00001": {
                "itemCode": "Z00001",
                "itemName": "ZERO QTY ITEM 750ML",
                "packing": 12,
                "purchaseRate": 100,
                "salesRate": 500,
            }
        }

    monkeypatch.setattr(reference_data_service, "items", fake_items)

    class FakeDocumentService:
        def get_job(self, _session, _job_id):
            return {"source_type": "DOCUMENT_PDF"}

    with pytest.raises(HTTPException) as exc_info:
        await DocumentPurchaseAdapter(FakeDocumentService()).purchase_items(
            {"shop_code": "hedu_test2"},
            "job-pdf-0",
        )

    assert exc_info.value.status_code == 422
    assert "positive Box/Cases or Loose/Bottles quantity" in str(exc_info.value.detail)
    db.close()


@pytest.mark.asyncio
async def test_pdf_purchase_recovers_nested_physical_qty_and_keeps_extracted_name_without_master_discount(monkeypatch):
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
            "row-physical",
            "job-physical",
            "SIGNATURE 180",
            "SIGNATURE 180",
            None,
            None,
            None,
            None,
            None,
            "S00017",
            "",
            json.dumps({
                "brand": "SIGNATURE 180",
                "ml": 180,
                "rawPdfRow": {
                    "Brand Name": "SIGNATURE 180",
                    "Physical Qty": "24",
                },
            }),
            "2026-09-18",
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
            "S00017": {
                "itemCode": "S00017",
                "itemName": "SIGNATURE RARE AGED 180ML",
                "packing": 48,
                "purchaseRate": 87.39,
                "purchaseRateCase": 0,
                "salesRate": 290,
                "purchaseDiscountAmount": 999,
                "etd": 7406.64,
            }
        }

    monkeypatch.setattr(reference_data_service, "items", fake_items)

    class FakeDocumentService:
        def get_job(self, _session, _job_id):
            return {"source_type": "DOCUMENT_PDF"}

    result = await DocumentPurchaseAdapter(FakeDocumentService()).purchase_items(
        {"shop_code": "hedu_test2"},
        "job-physical",
    )

    item = result[0]
    assert item["itemName"] == "SIGNATURE 180"
    assert item["itemCode"] == "S00017"
    assert item["box"] == 0
    assert item["loose"] == 24
    assert item["qnty"] == 24
    assert item["packing"] == 48
    assert item["rate"] == 87.39
    assert item["mrp"] == 290.0
    assert item["etd"] == 7406.64
    assert item["discount"] == 0.0
    db.close()


@pytest.mark.asyncio
async def test_pdf_purchase_self_heals_generic_raw_quantity_and_always_sends_it_as_loose(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE import_items (
          id TEXT, job_id TEXT, raw_name TEXT, normalized_name TEXT,
          packing INTEGER, quantity REAL, rate REAL, mrp REAL, amount REAL,
          mapped_item_code TEXT, excise_item_code TEXT, raw_data_json TEXT,
          created_at TEXT, updated_at TEXT
        );
        CREATE TABLE mappings (
          shop_code TEXT, excise_item_code TEXT, madhushala_item_code TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO import_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "row-generic",
            "job-generic",
            "ROYAL STAG DELUXE WHISKY",
            "royal stag deluxe whisky",
            None,
            None,
            None,
            None,
            None,
            "R00001",
            "",
            json.dumps({
                "brand": "ROYAL STAG DELUXE WHISKY",
                "sourceRow": {
                    "Physical Stock Bottle Count": "30",
                    "MRP": "9999",
                    "Bottle Packing Per Case": "48",
                },
            }),
            "2026-09-18",
            "2026-09-18",
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
            "R00001": {
                "itemCode": "R00001",
                "itemName": "ROYAL STAG 750ML",
                "packing": 12,
                "purchaseRate": 200,
                "purchaseRateCase": 0,
                "salesRate": 800,
                "purchaseDiscountAmount": 25,
            }
        }

    monkeypatch.setattr(reference_data_service, "items", fake_items)

    class FakeDocumentService:
        def get_job(self, _session, _job_id):
            return {"source_type": "DOCUMENT_PDF"}

    result = await DocumentPurchaseAdapter(FakeDocumentService()).purchase_items(
        {"shop_code": "hedu_test2"},
        "job-generic",
    )

    item = result[0]
    assert item["itemName"] == "ROYAL STAG DELUXE WHISKY"
    assert item["box"] == 0
    assert item["loose"] == 30
    assert item["qnty"] == 30
    assert item["packing"] == 12
    assert item["rate"] == 200.0
    assert item["mrp"] == 800.0
    assert item["discount"] == 0.0

    healed = db.execute(
        "SELECT quantity FROM import_items WHERE id='row-generic'"
    ).fetchone()
    assert healed["quantity"] == 30.0
    db.close()


@pytest.mark.asyncio
async def test_existing_page2_job_recovers_shifted_packing_as_loose_quantity(monkeypatch):
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
            "row-page2",
            "job-page2",
            "SIGNATURE PREMIER GRAIN WHISKY RESERVE SELECTION",
            "signature premier grain whisky reserve selection",
            None,
            None,
            None,
            None,
            None,
            "SIG375",
            "",
            json.dumps({
                "itemName": "SIGNATURE PREMIER GRAIN WHISKY RESERVE SELECTION",
                "brand": "SIGNATURE",
                "ml": "375 ML",
                "packing": 3,
                "quantity": None,
                "box": None,
                "loose": None,
                "rate": None,
                "boxRate": None,
                "looseRate": None,
                "mrp": None,
                "discount": None,
                "amount": 8464.74,
                "sourcePage": 2,
            }),
            "2026-09-18",
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
            "SIG375": {
                "itemCode": "SIG375",
                "itemName": "SIGNATURE 375ML",
                "packing": 24,
                "purchaseRate": 100,
                "purchaseRateCase": 0,
                "salesRate": 500,
                "purchaseDiscountAmount": 50,
            }
        }

    monkeypatch.setattr(reference_data_service, "items", fake_items)

    class FakeDocumentService:
        def get_job(self, _session, _job_id):
            return {"source_type": "DOCUMENT_PDF"}

    result = await DocumentPurchaseAdapter(FakeDocumentService()).purchase_items(
        {"shop_code": "hedu_test2"},
        "job-page2",
    )

    item = result[0]
    assert item["box"] == 0
    assert item["loose"] == 3
    assert item["qnty"] == 3
    assert item["packing"] == 24
    assert item["discount"] == 0.0
    healed = db.execute("SELECT quantity FROM import_items WHERE id='row-page2'").fetchone()
    assert healed["quantity"] == 3.0
    db.close()


@pytest.mark.asyncio
async def test_document_purchase_uses_reviewed_cases_and_loose_with_item_master_packing(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE import_items (
          id TEXT, job_id TEXT, raw_name TEXT, normalized_name TEXT,
          packing INTEGER, quantity REAL, box INTEGER, loose INTEGER,
          rate REAL, mrp REAL, amount REAL,
          mapped_item_code TEXT, excise_item_code TEXT, raw_data_json TEXT,
          created_at TEXT
        );
        CREATE TABLE mappings (
          shop_code TEXT, excise_item_code TEXT, madhushala_item_code TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO import_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "row-canonical",
            "job-canonical",
            "SEAGRAMS IMPERIAL BLUE CLASSIC GRAIN WHISKY",
            "seagrams imperial blue classic grain whisky",
            None,
            54,
            7,
            47,
            None,
            None,
            None,
            "0258",
            "",
            json.dumps({
                "itemName": "SEAGRAMS IMPERIAL BLUE CLASSIC GRAIN WHISKY",
                "brand": "SEAGRAMS IMPERIAL BLUE CLASSIC GRAIN WHISKY",
                "ml": 180,
                "box": 7,
                "loose": 47,
                "canonicalBox": 7,
                "canonicalLoose": 47,
                "canonicalQuantityVersion": 2,
            }),
            "2026-09-18",
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
            "0258": {
                "itemCode": "0258",
                "itemName": "IMPERIAL BLUE 180ML",
                "packing": 48,
                "purchaseRate": 125.08,
                "purchaseRateCase": 6004,
                "salesRate": 300,
                "purchaseDiscountAmount": 99,
            }
        }

    monkeypatch.setattr(reference_data_service, "items", fake_items)

    class FakeDocumentService:
        def get_job(self, _session, _job_id):
            return {"source_type": "DOCUMENT_PDF"}

    item = (
        await DocumentPurchaseAdapter(FakeDocumentService()).purchase_items(
            {"shop_code": "hedu_test2"},
            "job-canonical",
        )
    )[0]

    assert item["box"] == 7
    assert item["loose"] == 47
    assert item["packing"] == 48
    assert item["qnty"] == (7 * 48) + 47
    assert item["itemName"] == "SEAGRAMS IMPERIAL BLUE CLASSIC GRAIN WHISKY"
    assert item["discount"] == 0.0
    db.close()


def test_calculate_coverage_blocks_partial_item_list():
    from fastapi import HTTPException
    from app.services.purchase_orchestrator import PurchaseOrchestrator

    expected = [
        {"itemCode": "100001"},
        {"itemCode": "100002"},
        {"itemCode": "100003"},
        {"itemCode": "100004"},
        {"itemCode": "100005"},
    ]
    response = {
        "items": [
            {"itemCode": "100001"},
            {"itemCode": "100002"},
            {"itemCode": "100003"},
            {"itemCode": "100004"},
        ]
    }

    with pytest.raises(HTTPException) as exc_info:
        PurchaseOrchestrator._validate_calculation_coverage(expected, response)

    assert exc_info.value.status_code == 502
    assert "Expected 5 item(s) but received 4" in str(exc_info.value.detail)
    assert "Purchase was not saved" in str(exc_info.value.detail)


def test_calculate_coverage_blocks_item_code_mismatch_even_when_count_matches():
    from fastapi import HTTPException
    from app.services.purchase_orchestrator import PurchaseOrchestrator

    expected = [
        {"itemCode": "100001"},
        {"itemCode": "100002"},
    ]
    response = {
        "items": [
            {"itemCode": "100001"},
            {"itemCode": "999999"},
        ]
    }

    with pytest.raises(HTTPException) as exc_info:
        PurchaseOrchestrator._validate_calculation_coverage(expected, response)

    assert exc_info.value.status_code == 502
    assert "different item codes" in str(exc_info.value.detail)
