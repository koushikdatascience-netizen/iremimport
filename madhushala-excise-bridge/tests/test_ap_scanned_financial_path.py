"""Keep AP scanned-invoice financial evidence across generic batch normalization."""
from app.modules.document_import.ap_icdc.invoice_upload import verified_document_from_result
from app.modules.document_import.normalizer import normalize_extracted_document
from app.modules.document_import.service import DocumentImportService
import json


def test_ap_verified_document_retains_source_row_amount_and_financials():
    result = {
      "validation": {"status": "PASSED"},
      "header": {"icdc_number": "ICDC08326281144107", "tp_number": "TP08326281144107"},
      "summary": {
        "issue_price": 730822.0, "cess": 4463.89,
        "additional_previlage_fee": 15738.72, "additional_retail_excise_tax": 76968.0,
        "previlage_fee_clubs": 0.0, "icdc_value": 827992.61, "tcs": 16560.0,
        "less_this_invoice_value": 844552.61, "sub_total": 844747.65,
        "amount_credited": 195.04,
      },
      "items": [
          {"sl_no": 1, "brand_number": "C018",
           "brand_name": "British Empire Grand Premium Strong Beer",
           "size_ml": 650, "qty_cases": 10, "qty_bottles": 0,
           "total": 17840.0, "pages": [1]},
      ]
    }
    doc = verified_document_from_result(result)
    rows = normalize_extracted_document(doc, "DOCUMENT_PDF")
    assert rows[0].amount == 17840
    assert rows[0].rawData["invoiceAmount"] == 17840
    assert rows[0].rawData["sourceState"] == "ANDHRA_PRADESH"
    assert rows[0].rawData["invoiceFinancials"]["totalNet"] == 844552.61
    service = DocumentImportService.__new__(DocumentImportService)
    service.get_items = lambda *args: [{"raw_data_json": json.dumps(rows[0].rawData)}]
    fin = service.get_invoice_financials({}, "job")
    assert fin["taxes"]["cess"] == 4463.89
    assert fin["taxes"]["additionalRetailExciseTax"] == 76968.0
    assert fin["taxes"]["tcs"] == 16560.0
    assert fin["totalAmount"] == 730822.0
    assert fin["totalNet"] == 844552.61


def test_failed_ap_reconciliation_never_leaks_unverified_amounts():
    import pytest
    with pytest.raises(ValueError, match="reconciliation"):
        verified_document_from_result({"validation": {"status": "FAILED"}, "items": [
            {"brand_name": "UNKNOWN", "total": 55}
        ]})


def test_reference_six_page_invoice_line_amounts_reconcile_to_printed_issue_price():
    # Transcribed from user's 2026-10-08 41-line AP ICDC PDF, pages 1-5.
    printed = (
        17840, 10530, 10700, 6482, 12872, 133800, 7875, 8045,
        17840, 26560, 7645, 14022, 36005, 20652, 13230, 8817, 8095, 7927,
        9005, 8291, 12927, 12330, 27536, 14402, 13091, 9738, 14402,
        14647, 9679, 10110, 15840, 16812, 15430, 15862, 12934, 13816, 18540,
        39750, 29544, 7049, 20150
    )
    assert len(printed) == 41
    assert sum(printed) == 730822
    assert round(730822 + 4463.89 + 15738.72 + 76968, 2) == 827992.61
    assert round(827992.61 + 16560, 2) == 844552.61
    assert round(844747.65 - 844552.61, 2) == 195.04
