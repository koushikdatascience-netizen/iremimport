from __future__ import annotations

from app.modules.document_import.purchase_contract import build_item_master_calculation_request


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


def test_missing_rate_and_mrp_are_passed_through_without_blocking():
    request = build_item_master_calculation_request(
        {
            "shopCode": "hedu_test2",
            "companyCode": "2",
            "schemeCode": "",
            "salesTaxRate": 0,
            "salesTaxIncludingFree": False,
        },
        [{"itemCode": "B00003", "qnty": 2, "freeQnty": 0}],
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
    )
    row = request["items"][0]
    assert row["box"] == 0
    assert row["loose"] == 2
    assert row["looseRate"] == 0.0
    assert row["boxRate"] == 0.0
    assert row["mrp"] == 0.0
    assert row["etd"] == 7666.65
