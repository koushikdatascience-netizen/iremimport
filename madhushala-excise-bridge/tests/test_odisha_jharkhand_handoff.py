"""Guard non-WB PDF handoff without altering West Bengal behavior."""
import pytest
from app.modules.document_import.odisha_osbcl import recognized, _financials
from app.modules.document_import.pdf_extractor import _jharkhand_permit_number, _extract_jharkhand


def test_jharkhand_permit_number_separate_from_bill_number():
    text = """Government Of Jharkhand
Issued Date 01/09/2026 Form-T(Transport)
Excise Permit No: DHA-2026-2027/6267
PASS FOR TRANSPORT WITHIN THE PROVINCE
"""
    assert _jharkhand_permit_number(text) == "DHA-2026-2027/6267"
    items, bill, dt = _extract_jharkhand(text, [])
    assert items == []
    assert bill is None


def test_missing_jharkhand_permit_never_guessed():
    assert _jharkhand_permit_number("Government Of Jharkhand") is None


def test_odisha_source_totals_are_distinct_from_purchase_calculate():
    totals = _financials("""Sub Total
13,99,646.23
VAT
4,89,876.18
TCS
37,790.00
Total
19,27,312.41
""")
    assert totals["subtotal"] + totals["vat"] + totals["tcs"] == totals["total"]
    assert recognized("Odisha State Beverages Corporation Ltd DELIVERY CHALLAN CUM INVOICE")


def test_conflicting_odisha_printed_totals_fail_closed():
    with pytest.raises(ValueError):
        _financials("""Sub Total
100.00
Sub Total
101.00
VAT
20.00
TCS
10.00
Total
140.00
""")

def test_odisha_token_prefill_only_for_odisha():
    import json
    from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter
    class Service:
        def get_items(self, session, job_id):
            return [
                {"raw_data_json": json.dumps({"sourceState": "ODISHA", "transportPassNo": "OD-BAL(T)/69/2026-2027"})},
                {"raw_data_json": json.dumps({"sourceState": "WEST_BENGAL", "transportPassNo": "WB-UNCHANGED"})},
            ]
    assert DocumentPurchaseAdapter(Service()).odisha_tp_pass({}, "job") == "OD-BAL(T)/69/2026-2027"


def test_west_bengal_token_is_not_overridden():
    import json
    from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter
    class Service:
        def get_items(self, session, job_id):
            return [{"raw_data_json": json.dumps({"sourceState": "WEST_BENGAL", "transportPassNo": "WB-UNCHANGED"})}]
    assert DocumentPurchaseAdapter(Service()).odisha_tp_pass({}, "job") == ""


def test_jharkhand_permit_prefill_and_manual_override():
    import json
    from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter
    class Service:
        def get_items(self, session, job_id):
            return [{"raw_data_json": json.dumps({
                "sourceState": "JHARKHAND", "transportPassNo": "DHA-2026-2027/6267"
            })}]
    adapter = DocumentPurchaseAdapter(Service())
    assert adapter.non_wb_tp_pass({}, "job") == "DHA-2026-2027/6267"


def test_non_wb_conflicting_permits_rejected():
    import json
    from fastapi import HTTPException
    from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter
    class Service:
        def get_items(self, session, job_id):
            return [{"raw_data_json": json.dumps({
                "sourceState": "JHARKHAND", "transportPassNo": pass_no
            })} for pass_no in ("DHA-2026-2027/6267", "DHA-2026-2027/6268")]
    with pytest.raises(HTTPException) as exc:
        DocumentPurchaseAdapter(Service()).non_wb_tp_pass({}, "job")
    assert exc.value.status_code == 422


def test_non_wb_prefill_ignores_west_bengal():
    import json
    from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter
    class Service:
        def get_items(self, session, job_id):
            return [{"raw_data_json": json.dumps({
                "sourceState": "WEST_BENGAL", "transportPassNo": "WB-123"
            })}]
    assert DocumentPurchaseAdapter(Service()).non_wb_tp_pass({}, "job") == ""


def test_jharkhand_permit_survives_item_normalization():
    from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
    from app.modules.document_import.normalizer import normalize_extracted_document
    doc = ExtractedDocument(
        documentType="invoice",
        sourceState="JHARKHAND",
        transportPassNo="DHA-2026-2027/6267",
        items=[ExtractedProduct(itemName="TEST BRAND 750 ML", brand="TEST BRAND", ml=750, box=2, loose=0)],
    )
    normalized = normalize_extracted_document(doc, "DOCUMENT_PDF")
    assert len(normalized) == 1
    assert normalized[0].rawData["transportPassNo"] == "DHA-2026-2027/6267"
    assert normalized[0].rawData["sourceState"] == "JHARKHAND"


def test_wb_document_normalization_does_not_inject_permit():
    from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
    from app.modules.document_import.normalizer import normalize_extracted_document
    doc = ExtractedDocument(
        documentType="invoice",
        sourceState="WEST_BENGAL",
        transportPassNo="WB-SAMPLE",
        items=[ExtractedProduct(itemName="TEST BRAND 750 ML", brand="TEST BRAND", ml=750, box=2, loose=0)],
    )
    normalized = normalize_extracted_document(doc, "DOCUMENT_PDF")
    assert "transportPassNo" not in normalized[0].rawData
