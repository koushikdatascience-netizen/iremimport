from app.services.purchase_orchestrator import purchase_orchestrator


def test_qr_bottle_count_becomes_loose_and_box_is_zero():
    payload = {
        "shopCode": "hedu_test",
        "companyCode": "2",
        "schemeCode": "",
        "items": [
            {
                "itemCode": "100003",
                "box": 2,
                "loose": 0,
                "qnty": 96,
                "freeQnty": 0,
                "packing": 48,
            }
        ],
    }

    purchase_orchestrator._apply_qr_bottle_quantity({"source_type": "QR_HTML"}, payload)

    assert payload["items"][0]["box"] == 0
    assert payload["items"][0]["loose"] == 96
    assert payload["items"][0]["qnty"] == 96


def test_calculate_payload_uses_full_madhushala_item_master_values():
    payload = {
        "shopCode": "hedu_test",
        "companyCode": "2",
        "schemeCode": "",
        "items": [
            {
                "itemCode": "100003",
                "box": 0,
                "loose": 96,
                "qnty": 96,
                "freeQnty": 0,
                # Deliberately zeroed: production must read the master passed below.
                "packing": 0,
                "looseRate": 0,
                "boxRate": 0,
                "mrp": 0,
                "discount": 0,
                "cgst": 0,
                "sgst": 0,
                "cess": 0,
                "addCess": 0,
                "igst": 0,
                "t1Amt": 0,
                "t2Amt": 0,
                "t3Amt": 0,
                "t4Amt": 0,
                "t1Rate": 0,
                "t2Rate": 0,
                "t3Rate": 0,
                "t4Rate": 0,
                "etd": 0,
            }
        ],
    }
    master = {
        "100003": {
            "itemCode": "100003",
            "packing": 48,
            "purchaseRate": 211.80125,
            "purchaseRateCase": 10166.46,
            "mrp": 540,
            "discount": 0,
            "cgst": 0,
            "sgst": 0,
            "cess": 0,
            "addCess": 0,
            "igst": 0,
            "t1Amt": 100,
            "t2Amt": 0,
            "t3Amt": 100,
            "t4Amt": 100,
            "t1Rate": 0,
            "t2Rate": 2,
            "t3Rate": 0,
            "t4Rate": 0,
            "etd": 11955.6,
        }
    }

    request = purchase_orchestrator.build_calculation_request(payload, {}, master)
    item = request["items"][0]

    assert request["shopCode"] == "hedu_test"
    assert request["companyCode"] == "2"
    assert request["salesTaxRate"] == 0
    assert request["salesTaxIncludingFree"] is False
    assert item == {
        "itemCode": "100003",
        "packing": 48,
        "box": 0,
        "loose": 96,
        "free": 0,
        "looseRate": 211.80125,
        "boxRate": 10166.46,
        "mrp": 540.0,
        "discount": 0.0,
        "cgst": 0.0,
        "sgst": 0.0,
        "cess": 0.0,
        "addCess": 0.0,
        "igst": 0.0,
        "t1Amt": 100.0,
        "t2Amt": 0.0,
        "t3Amt": 100.0,
        "t4Amt": 100.0,
        "t1Rate": 0.0,
        "t2Rate": 2.0,
        "t3Rate": 0.0,
        "t4Rate": 0.0,
        "etd": 11955.6,
    }


def test_non_qr_quantity_shape_is_not_rewritten():
    payload = {
        "items": [
            {
                "itemCode": "100003",
                "box": 2,
                "loose": 3,
                "qnty": 99,
            }
        ]
    }

    purchase_orchestrator._apply_qr_bottle_quantity({"source_type": "DOCUMENT_PDF"}, payload)

    assert payload["items"][0]["box"] == 2
    assert payload["items"][0]["loose"] == 3
