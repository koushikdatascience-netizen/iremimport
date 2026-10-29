"""AP source financial evidence must survive normalization and reach handoff."""
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
from app.modules.document_import.normalizer import normalize_extracted_document
from app.modules.document_import.service import DocumentImportService


def test_ap_bar_indent_printed_amount_is_preserved():
    doc = ExtractedDocument(
        documentType="stock_list", sourceState="ANDHRA_PRADESH",
        invoiceFinancials={"totalAmount": 1200.0, "totalNet": None,
                           "taxes": {"vat": None, "tcs": None}},
        items=[ExtractedProduct(itemName="AP BRAND", ml=750, box=3, loose=0,
                                printedAmount=1200.0, sourceState="ANDHRA_PRADESH")],
    )
    normalized = normalize_extracted_document(doc, "DOCUMENT_PDF")
    assert len(normalized) == 1
    assert normalized[0].amount == 1200.0
    assert normalized[0].rawData["invoiceAmount"] == 1200.0
    assert normalized[0].rawData["invoiceFinancials"]["totalAmount"] == 1200.0


def test_ap_invoice_named_taxes_and_totals_are_handoff_evidence():
    raw = {"sourceState": "ANDHRA_PRADESH",
           "invoiceFinancials": {
               "totalAmount": 2500.0, "totalNet": 2800.0,
               "taxes": {"vat": 100.0, "cess": 25.0, "tcs": 5.0,
                         "additionalPrivilegeFee": 70.0,
                         "additionalRetailExciseTax": 100.0}}}
    service = DocumentImportService.__new__(DocumentImportService)
    service.get_items = lambda session, job_id: [{"raw_data_json": __import__("json").dumps(raw)}]
    financials = service.get_invoice_financials({}, "ap-job")
    assert financials["totalAmount"] == 2500.0
    assert financials["totalNet"] == 2800.0
    assert financials["taxes"]["vat"] == 100.0
    assert financials["taxes"]["additionalRetailExciseTax"] == 100.0


def test_non_ap_pdf_amount_remains_unchanged():
    doc = ExtractedDocument(documentType="invoice", sourceState="WEST_BENGAL",
         items=[ExtractedProduct(itemName="WB BRAND", box=2, amount=220.0)])
    item = normalize_extracted_document(doc, "DOCUMENT_PDF")[0]
    assert item.amount is None
    assert "invoiceAmount" not in item.rawData
