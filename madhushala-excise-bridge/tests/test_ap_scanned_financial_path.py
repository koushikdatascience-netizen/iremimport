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
