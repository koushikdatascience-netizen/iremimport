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


def test_jharkhand_invoice_permit_pair_does_not_duplicate_purchase_items():
    from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
    from app.modules.document_import.jharkhand_pair import join_verified_pair
    row = ExtractedProduct(itemName="SEAGRAMS ROYAL STAG WHISKY", ml=750, box=2, loose=0)
    invoice = ExtractedDocument(
        sourceState="JHARKHAND", invoiceNumber="003_COM_DHN_26-27/6267",
        items=[row], documentType="invoice",
    )
    permit = ExtractedDocument(
        sourceState="JHARKHAND", transportPassNo="DHA-2026-2027/6267",
        items=[row], documentType="invoice",
    )
    joined = join_verified_pair(invoice, permit)
    assert len(joined.items) == 1
    assert joined.invoiceNumber == invoice.invoiceNumber
    assert joined.transportPassNo == permit.transportPassNo
    assert joined.jharkhandPairValidated is True


def test_jharkhand_pair_rejects_mismatched_cases():
    from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
    from app.modules.document_import.jharkhand_pair import join_verified_pair
    invoice = ExtractedDocument(
        sourceState="JHARKHAND", invoiceNumber="BILL1",
        items=[ExtractedProduct(itemName="TEST WHISKY", ml=750, box=2)],
    )
    permit = ExtractedDocument(
        sourceState="JHARKHAND", transportPassNo="DHA-2026-2027/6267",
        items=[ExtractedProduct(itemName="TEST WHISKY", ml=750, box=3)],
    )
    with pytest.raises(ValueError, match="disagree"):
        join_verified_pair(invoice, permit)


def test_odisha_printed_financials_survive_handoff_lookup():
    import json
    from app.modules.document_import.service import DocumentImportService
    from unittest.mock import Mock
    raw = {
        "sourceState": "ODISHA",
        "invoiceAmount": "106981.40",
        "transportPassNo": "OD-BAL(T)/100/2026-2027",
        "invoiceFinancials": {
            "subtotal": "1282438.87", "grossAmount": "1282438.87",
            "vat": "448853.60", "tcs": "34626.00",
            "grandTotal": "1765918.47", "netAmount": "1765918.47",
            "taxes": {"vat": "448853.60", "tcs": "34626.00"},
            "roundOff": None,
        },
    }
    service = Mock(spec=DocumentImportService)
    service.get_items.return_value = [{"raw_data_json": json.dumps(raw)}]
    result = DocumentImportService.get_invoice_financials(service, {}, "job")
    assert result["totalAmount"] == "1282438.87"
    assert result["totalNet"] == "1765918.47"
    assert result["taxes"]["vat"] == "448853.60"
    assert result["taxes"]["tcs"] == "34626.00"


@pytest.mark.asyncio
async def test_odisha_tp_number_beats_stale_invoice_duplicate(monkeypatch):
    from app.modules.document_import import purchase_required
    from unittest.mock import Mock
    job = {"source_type": "DOCUMENT_PDF", "invoice_number": "INV(BAL)/2026-2027/19232",
           "invoice_date": "22/10/2026", "supplier_name": "Odisha State Beverages"}
    svc = Mock()
    svc.get_job.return_value = job
    monkeypatch.setattr(purchase_required, "_raw_value",
                        lambda jid, *names: "ODISHA" if "sourceState" in names else
                        "OD-BAL(T)/100/2026-2027" if "transportPassNo" in names else "")
    got = await purchase_required.resolve_required_purchase_header(
        svc, {}, "job", {"tpPassNo": job["invoice_number"],
                         "supplierCode": "S", "storeCode": "T",
                         "purchaseAccCode": "P", "userCode": "U"}, strict=False)
    assert got["tpPassNo"] == "OD-BAL(T)/100/2026-2027"
    assert got["docNo"] == job["invoice_number"]


@pytest.mark.asyncio
async def test_non_odisha_tp_override_not_applied(monkeypatch):
    from app.modules.document_import import purchase_required
    from unittest.mock import Mock
    job = {"source_type": "DOCUMENT_PDF", "invoice_number": "WB-INVOICE",
           "invoice_date": "22/10/2026"}
    svc = Mock()
    svc.get_job.return_value = job
    monkeypatch.setattr(purchase_required, "_raw_value",
                        lambda jid, *names: "WEST_BENGAL" if "sourceState" in names else "")
    got = await purchase_required.resolve_required_purchase_header(
        svc, {}, "job", {"tpPassNo": "WB-TP",
                         "supplierCode": "S", "storeCode": "T",
                         "purchaseAccCode": "P", "userCode": "U"}, strict=False)
    assert got["tpPassNo"] == "WB-TP"


def test_jharkhand_financial_source_extension():
    import json
    from unittest.mock import Mock
    from app.modules.document_import.service import DocumentImportService
    raw = {"sourceState": "JHARKHAND", "invoiceAmount": "12305.05",
           "transportPassNo": "DHA-2026-2027/6267",
           "invoiceFinancials": {"invoiceAmount": "282597.87", "grandTotal": "288249.83",
                                 "vat": "26038.93", "aed": "8544.25", "vatOnAed": "406.07"}}
    service = Mock(spec=DocumentImportService)
    service.get_items.return_value = [{"raw_data_json": json.dumps(raw)}]
    values = DocumentImportService.get_invoice_financials(service, {}, "test")
    assert values["totalAmount"] == "282597.87"
    assert values["totalNet"] == "288249.83"
    assert values["taxes"]["vat"] == "26038.93"
    assert values["taxes"]["aed"] == "8544.25"
    assert values["taxes"]["vatOnAed"] == "406.07"


@pytest.mark.asyncio
async def test_jharkhand_distinct_permit_replaces_duplicated_bill(monkeypatch):
    from unittest.mock import Mock
    from app.modules.document_import import purchase_required
    invoice = "003_COM_DHN_25-26/2026-2027/6267/61/6024"
    service = Mock()
    service.get_job.return_value = {"source_type": "DOCUMENT_PDF",
                                    "invoice_number": invoice,
                                    "invoice_date": "01/09/2026"}
    monkeypatch.setattr(purchase_required, "_raw_value",
        lambda jid, *aliases: "JHARKHAND" if "sourceState" in aliases else
        "DHA-2026-2027/6267" if "transportPassNo" in aliases else "")
    result = await purchase_required.resolve_required_purchase_header(
        service, {}, "job",
        {"tpPassNo": invoice, "supplierCode": "S", "storeCode": "T",
         "purchaseAccCode": "P", "userCode": "U"}, strict=False)
    assert result["docNo"] == invoice
    assert result["tpPassNo"] == "DHA-2026-2027/6267"
