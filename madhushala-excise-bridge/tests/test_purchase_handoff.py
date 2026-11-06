import json

import pytest

from app.modules.document_import.purchase_handoff import build_purchase_handoff


class FakeService:
    def get_job(self, session, job_id):
        assert session["shop_code"] == "SHOP-1"
        assert job_id == "job-1"
        return {
            "id": job_id,
            "source_type": "DOCUMENT_PDF",
            "invoice_number": "INV-22",
            "invoice_date": "2026-09-21",
            "supplier_name": "Supplier hint only",
        }

    def get_items(self, session, job_id):
        return [
            {
                "id": "row-1",
                "mapped_item_code": "M001",
                "raw_name": "Mapped Whisky",
                "normalized_name": "Mapped Whisky",
                "excise_item_code": "101",
                "box": 2,
                "loose": 3,
                "quantity": 0,
                "ml": 750,
                "packing": 12,
                "rate": 0,
                "mrp": 0,
                "amount": 0,
                "raw_data_json": '{"batchNo":"B-1"}',
            }
        ]


def test_purchase_handoff_needs_only_mapping_not_purchase_masters():
    session = {
        "shop_code": "SHOP-1",
        "company_code": "2",
        "bill_type": "AI",
    }

    result = build_purchase_handoff(FakeService(), session, "job-1")
    purchase = result["purchasePayload"]

    assert purchase["shopCode"] == "SHOP-1"
    assert purchase["companyCode"] == "2"
    assert purchase["docNo"] == "INV-22"
    assert purchase["docDate"] == "2026-09-21"
    assert purchase["supplierName"] == "Supplier hint only"
    assert "supplierCode" not in purchase
    assert "storeCode" not in purchase
    assert "purchaseAccCode" not in purchase
    assert "userCode" not in purchase
    assert purchase["items"][0]["itemCode"] == "M001"
    assert purchase["items"][0]["batchNo"] == "B-1"
    assert purchase["items"][0]["box"] == 2
    assert purchase["items"][0]["loose"] == 3


@pytest.mark.parametrize("raw", [{}, {"batchNo": None}, {"batchNo": ""}, {"batchNo": "   "}])
def test_purchase_handoff_sends_na_for_missing_batch(raw):
    class MissingBatchService(FakeService):
        def get_items(self, session, job_id):
            rows = super().get_items(session, job_id)
            rows[0]["raw_data_json"] = json.dumps(raw)
            return rows

    result = build_purchase_handoff(MissingBatchService(), {"shop_code": "SHOP-1"}, "job-1")
    assert result["purchasePayload"]["items"][0]["batchNo"] == "NA"
