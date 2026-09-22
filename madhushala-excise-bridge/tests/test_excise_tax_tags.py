from __future__ import annotations

from app.services.excise_tax_tags import apply_excise_tax_tags


def test_tax_tags_map_labels_to_raw_excise_values():
    payload = {
        "itemName": "Aberfeldy 750ML",
        "bottlesPerCase": "6",
        "specialPurposeFee": "234.71",
        "roundOffGovt": "9.98",
        "retailerMargin": "243.06",
        "t1": "",
        "t2": "",
        "t3": "",
        "t4": "",
    }
    tags = [
        {"taxCode": "DISC", "taxLabel": "DISCOUNT", "itemType": "ALL"},
        {"taxCode": "T4", "taxLabel": "OTHERS", "itemType": "AI"},
        {"taxCode": "T2", "taxLabel": "ROUNDING OFF", "itemType": "AI"},
        {"taxCode": "T1", "taxLabel": "SP FEE", "itemType": "AI"},
        {"taxCode": "T3", "taxLabel": "TCS", "itemType": "AI"},
    ]

    result = apply_excise_tax_tags(payload, tags)

    assert result["t1"] == "234.71"
    assert result["t2"] == "9.98"
    assert result["t3"] == ""
    assert result["t4"] == ""


def test_tax_tag_order_is_dynamic_not_hard_coded():
    payload = {
        "bottlesPerCase": "10",
        "specialPurposeFee": "2.50",
        "roundOffGovt": "1.25",
    }
    tags = [
        {"taxCode": "T1", "taxLabel": "ROUND OFF", "itemType": "AI"},
        {"taxCode": "T2", "taxLabel": "SPECIAL PURPOSE FEE", "itemType": "AI"},
    ]

    result = apply_excise_tax_tags(payload, tags)

    assert result["t1"] == "1.25"
    assert result["t2"] == "2.50"


def test_missing_excise_value_stays_blank_not_zero():
    payload = {"bottlesPerCase": "12", "specialPurposeFee": "5"}
    tags = [
        {"taxCode": "T3", "taxLabel": "TCS", "itemType": "AI"},
        {"taxCode": "T4", "taxLabel": "OTHERS", "itemType": "AI"},
    ]

    result = apply_excise_tax_tags(payload, tags)

    assert result["t3"] == ""
    assert result["t4"] == ""


def test_missing_bottles_per_case_does_not_blank_available_tax_value():
    payload = {
        "specialPurposeFee": "5.75",
        "retailerMargin": "12.40",
    }
    tags = [
        {"taxCode": "T1", "taxLabel": "SP FEE", "itemType": "AI"},
        {"taxCode": "T2", "taxLabel": "RETAILER MARGIN", "itemType": "AI"},
    ]

    result = apply_excise_tax_tags(payload, tags)

    assert result["t1"] == "5.75"
    assert result["t2"] == "12.40"


def test_ai_tax_tag_wins_over_generic_all():
    payload = {
        "bottlesPerCase": "2",
        "specialPurposeFee": "3",
        "roundOffGovt": "4",
    }
    tags = [
        {"taxCode": "T1", "taxLabel": "ROUND OFF", "itemType": "ALL"},
        {"taxCode": "T1", "taxLabel": "SP FEE", "itemType": "AI"},
    ]

    result = apply_excise_tax_tags(payload, tags)

    assert result["t1"] == "3"
