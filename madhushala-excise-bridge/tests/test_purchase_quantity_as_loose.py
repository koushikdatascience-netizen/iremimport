from __future__ import annotations

import json
import sqlite3

import pytest

from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter


class _Service:
    def get_job(self, session, job_id):
        return {
            "id": job_id,
            "source_type": "QR_HTML",
            "invoice_date": "2026-06-16",
            "invoice_number": "INV-1",
        }


@pytest.mark.asyncio
async def test_extracted_bottle_quantity_stays_in_loose(monkeypatch):
    adapter = DocumentPurchaseAdapter(_Service())

    row = {
        "id": "1",
        "mapped_item_code": "100003",
        "excise_item_code": "E1",
        "raw_name": "Test Item",
        "normalized_name": "Test Item",
        "raw_data_json": json.dumps({"No of Bottles Dispatched": 18}),
        "packing": 12,
        "quantity": 18,
        "created_at": "2026-09-18T00:00:00",
    }

    class _Cursor:
        def fetchall(self):
            return [row]

    class _Db:
        def execute(self, *args, **kwargs):
            return _Cursor()

    class _Conn:
        def __enter__(self):
            return _Db()

        def __exit__(self, exc_type, exc, tb):
            return False

    async def _load_master(*args, **kwargs):
        return {
            "100003": {
                "itemCode": "100003",
                "packing": 12,
                "purchaseRate": 0,
                "purchaseRateCase": 0,
                "mrp": 0,
            }
        }

    monkeypatch.setattr("app.modules.document_import.purchase_adapter.conn", lambda: _Conn())
    monkeypatch.setattr(
        "app.modules.document_import.purchase_adapter.load_item_master_details",
        _load_master,
    )

    items = await adapter.purchase_items(
        {"shop_code": "hedu_test2", "company_code": "2"},
        "job-1",
    )

    assert len(items) == 1
    assert items[0]["box"] == 0
    assert items[0]["loose"] == 18
    assert items[0]["qnty"] == 18


@pytest.mark.asyncio
async def test_qr_invalid_box_shape_is_normalized_to_loose(monkeypatch):
    adapter = DocumentPurchaseAdapter(_Service())

    row = {
        "id": "2",
        "mapped_item_code": "100003",
        "excise_item_code": "E2",
        "raw_name": "Test Item 2",
        "normalized_name": "Test Item 2",
        "raw_data_json": json.dumps({"box": 18, "qnty": 18}),
        "packing": 12,
        "quantity": 18,
        "created_at": "2026-09-18T00:00:01",
    }

    class _Cursor:
        def fetchall(self):
            return [row]

    class _Db:
        def execute(self, *args, **kwargs):
            return _Cursor()

    class _Conn:
        def __enter__(self):
            return _Db()

        def __exit__(self, exc_type, exc, tb):
            return False

    async def _load_master(*args, **kwargs):
        return {
            "100003": {
                "itemCode": "100003",
                "packing": 12,
                "purchaseRate": 0,
                "purchaseRateCase": 0,
                "mrp": 0,
            }
        }

    monkeypatch.setattr("app.modules.document_import.purchase_adapter.conn", lambda: _Conn())
    monkeypatch.setattr(
        "app.modules.document_import.purchase_adapter.load_item_master_details",
        _load_master,
    )

    items = await adapter.purchase_items(
        {"shop_code": "hedu_test2", "company_code": "2"},
        "job-2",
    )

    assert items[0]["box"] == 0
    assert items[0]["loose"] == 18
    assert items[0]["qnty"] == 18
