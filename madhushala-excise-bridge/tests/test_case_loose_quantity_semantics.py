from __future__ import annotations

from app.modules.document_import.normalizer import normalize_extracted_document
from app.modules.document_import.quantity import case_embeds_loose, resolve_case_loose
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct


def test_missing_case_uses_bottles_as_loose():
    assert resolve_case_loose(None, "24") == (0, 24)
    assert resolve_case_loose("", 24) == (0, 24)
    assert resolve_case_loose("0", "24") == (0, 24)


def test_integer_case_keeps_separate_bottle_quantity():
    assert resolve_case_loose("15", "8") == (15, 8)
    assert resolve_case_loose(15, 8) == (15, 8)
    assert resolve_case_loose("15", None) == (15, 0)


def test_dot_case_encodes_box_and_loose_and_ignores_bottles():
    assert case_embeds_loose("15.78") is True
    assert resolve_case_loose("15.78", "30") == (15, 78)
    assert resolve_case_loose("7.5", "100") == (7, 5)


def test_dash_case_encodes_box_and_loose_and_ignores_bottles():
    assert case_embeds_loose("15-20") is True
    assert case_embeds_loose("3 - 0") is True
    assert resolve_case_loose("15-20", "50") == (15, 20)
    assert resolve_case_loose("3 - 0", "144") == (3, 0)


def test_malformed_case_is_not_silently_guessed():
    assert resolve_case_loose("15-20-3", "99") == (0, 0)
    assert resolve_case_loose("15/", "99") == (0, 0)


def test_pdf_normalizer_honors_compound_case_over_separate_bottles():
    document = ExtractedDocument(
        documentType="invoice",
        items=[
            ExtractedProduct(
                itemName="TEST WHISKY 750 ML",
                brand="TEST WHISKY",
                ml=750,
                box="15.78",
                loose="30",
            )
        ],
    )

    rows = normalize_extracted_document(document, "DOCUMENT_PDF")

    assert len(rows) == 1
    assert rows[0].box == 15
    assert rows[0].loose == 78
    assert rows[0].rawData["canonicalBox"] == 15
    assert rows[0].rawData["canonicalLoose"] == 78


def test_pdf_normalizer_keeps_bottles_when_case_is_clean_integer():
    document = ExtractedDocument(
        documentType="invoice",
        items=[
            ExtractedProduct(
                itemName="TEST WHISKY 750 ML",
                brand="TEST WHISKY",
                ml=750,
                box="15",
                loose="30",
            )
        ],
    )

    rows = normalize_extracted_document(document, "DOCUMENT_PDF")

    assert len(rows) == 1
    assert rows[0].box == 15
    assert rows[0].loose == 30


def test_pdf_normalizer_uses_bottles_when_case_is_missing():
    document = ExtractedDocument(
        documentType="invoice",
        items=[
            ExtractedProduct(
                itemName="TEST WHISKY 750 ML",
                brand="TEST WHISKY",
                ml=750,
                box=None,
                loose="30",
            )
        ],
    )

    rows = normalize_extracted_document(document, "DOCUMENT_PDF")

    assert len(rows) == 1
    assert rows[0].box is None
    assert rows[0].loose == 30


def test_calculate_request_preserves_canonical_box_and_loose():
    from app.services.purchase_orchestrator import purchase_orchestrator

    request = purchase_orchestrator.build_calculation_request(
        {
            "shopCode": "SHOP_A",
            "companyCode": "2",
            "schemeCode": "",
            "items": [
                {
                    "itemCode": "ITEM1",
                    "box": 15,
                    "loose": 78,
                    "qnty": 15 * 12 + 78,
                    "_canonicalQuantityVersion": 2,
                }
            ],
        },
        {},
    )

    assert request["items"][0]["box"] == 15
    assert request["items"][0]["loose"] == 78



def test_pdf_calculate_rates_follow_loose_only_quantity():
    from app.modules.document_import.purchase_contract import build_item_master_calculation_request

    request = build_item_master_calculation_request(
        {
            "shopCode": "SHOP_A",
            "companyCode": "2",
            "schemeCode": "",
        },
        [
            {
                "itemCode": "A00056",
                "box": 0,
                "loose": 1,
                "_canonicalQuantityVersion": 2,
            }
        ],
        {
            "A00056": {
                "purchaseRateCase": 3415.2,
                "purchaseRate": 142.3,
                "packing": 24,
                "salesRate": 250,
            }
        },
        exact_purchase_rates=True,
    )

    item = request["items"][0]
    assert item["box"] == 0
    assert item["loose"] == 1
    assert item["boxRate"] == 0
    assert item["looseRate"] == 142.3


def test_pdf_calculate_rates_follow_box_only_quantity():
    from app.modules.document_import.purchase_contract import build_item_master_calculation_request

    request = build_item_master_calculation_request(
        {
            "shopCode": "SHOP_A",
            "companyCode": "2",
            "schemeCode": "",
        },
        [
            {
                "itemCode": "ITEM1",
                "box": 1,
                "loose": 0,
                "_canonicalQuantityVersion": 2,
            }
        ],
        {
            "ITEM1": {
                "purchaseRateCase": 3415.2,
                "purchaseRate": 142.3,
                "packing": 24,
            }
        },
        exact_purchase_rates=True,
    )

    item = request["items"][0]
    assert item["box"] == 1
    assert item["loose"] == 0
    assert item["boxRate"] == 3415.2
    assert item["looseRate"] == 0


def test_pdf_calculate_rates_keep_both_for_mixed_quantity():
    from app.modules.document_import.purchase_contract import build_item_master_calculation_request

    request = build_item_master_calculation_request(
        {
            "shopCode": "SHOP_A",
            "companyCode": "2",
            "schemeCode": "",
        },
        [
            {
                "itemCode": "ITEM1",
                "box": 1,
                "loose": 3,
                "_canonicalQuantityVersion": 2,
            }
        ],
        {
            "ITEM1": {
                "purchaseRateCase": 3415.2,
                "purchaseRate": 142.3,
                "packing": 24,
            }
        },
        exact_purchase_rates=True,
    )

    item = request["items"][0]
    assert item["boxRate"] == 3415.2
    assert item["looseRate"] == 142.3
