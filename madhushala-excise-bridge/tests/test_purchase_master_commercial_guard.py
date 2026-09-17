from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.modules.document_import.purchase_contract import (
    build_item_master_calculation_request,
    validate_item_master_commercials,
)


def _full_master():
    return {
        "itemCode": "100003",
        "packing": 48,
        "purchaseRate": 10.42,
        "purchaseRateCase": 500.0,
        "mrp": 280.0,
        "etd": 11955.6,
        "t1Rate": 0,
        "t2Rate": 2,
        "t3Rate": 0,
        "t4Rate": 0,
    }


def test_calculate_uses_extracted_quantity_only_and_keeps_master_values_exact():
    request = build_item_master_calculation_request(
        {
            "shopCode": "hedu_test2",
            "companyCode": "2",
            "schemeCode": "",
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
        },
        [{"itemCode": "100003", "qnty": 6, "freeQnty": 0}],
        {"100003": _full_master()},
    )
    row = request["items"][0]
    assert row["box"] == 0
    assert row["loose"] == 6
    assert row["packing"] == 48
    assert row["looseRate"] == 10.42
    assert row["boxRate"] == 500.0
    assert row["mrp"] == 280.0
    assert row["etd"] == 11955.6


def test_missing_rate_and_mrp_is_rejected_before_calculate_or_save():
    with pytest.raises(HTTPException) as exc_info:
        validate_item_master_commercials(
            {
                "B00003": {
                    "itemCode": "B00003",
                    "packing": 12,
                    "purchaseRate": 0,
                    "purchaseRateCase": 0,
                    "mrp": 0,
                    "etd": 7666.65,
                }
            },
            ["B00003"],
        )
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["stage"] == "ITEM_MASTER"
    assert detail["itemCode"] == "B00003"
    assert "purchase rate" in detail["message"].lower()
    assert "mrp" in detail["message"].lower()
