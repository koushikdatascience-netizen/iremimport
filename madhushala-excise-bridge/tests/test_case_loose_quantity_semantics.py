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
