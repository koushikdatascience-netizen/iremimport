"""AP ICDC invoice source money regression; BAR INDENT deliberately excluded."""
import json

from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
from app.modules.document_import.normalizer import normalize_extracted_document
from app.modules.document_import.ap_icdc.invoice_upload import invoice_financials
from app.modules.document_import.service import DocumentImportService


def test_invoice_printed_amount_and_tp_persist():
    doc = ExtractedDocument(
        documentType="invoice", sourceState="ANDHRA_PRADESH",
        sourceDocumentType="AP_ICDC_INVOICE",
        transportPassNo="TP123456789",
        invoiceFinancials=invoice_financials({
            "issue_price": 1000.0, "cess": 30.0, "additional_previlage_fee": 50.0,
            "additional_retail_excise_tax": 60.0, "previlage_fee_clubs": 0.0,
            "icdc_value": 1140.0, "tcs": 11.0,
            "less_this_invoice_value": 1151.0,
        }),
        items=[ExtractedProduct(itemName="AP WHISKY", ml=750, box=2,
                                amount=1000, invoiceAmount=1000)],
    )
    row = normalize_extracted_document(doc, "DOCUMENT_PDF")[0]
    assert row.amount == 1000.0
    assert row.rawData["invoiceAmount"] == 1000.0
    assert row.rawData["transportPassNo"] == "TP123456789"
    service = DocumentImportService.__new__(DocumentImportService)
    service.get_items = lambda session, job_id: [{"raw_data_json": json.dumps(row.rawData)}]
    result = service.get_invoice_financials({}, "job")
    assert result["totalAmount"] == 1000.0
    assert result["totalNet"] == 1151.0
    assert result["icdcValue"] == 1140.0
    assert result["taxes"]["cess"] == 30.0
    assert result["taxes"]["additionalRetailExciseTax"] == 60.0
    assert result["taxes"]["vat"] is None


def test_bar_indent_and_wb_are_not_changed():
    for state, doc_type in (("ANDHRA_PRADESH", "AP_BAR_INDENT"),
                            ("WEST_BENGAL", "WB_INVOICE")):
        doc = ExtractedDocument(
            documentType="invoice", sourceState=state, sourceDocumentType=doc_type,
            items=[ExtractedProduct(itemName="SAMPLE", box=1, amount=44, invoiceAmount=44)])
        row = normalize_extracted_document(doc, "DOCUMENT_PDF")[0]
        assert row.amount is None
        assert "invoiceFinancials" not in row.rawData
