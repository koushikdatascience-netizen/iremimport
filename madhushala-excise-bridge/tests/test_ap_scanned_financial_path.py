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


def test_wrapped_source_charges_read_from_separate_ruled_panel_cells():
    from app.modules.document_import.ap_icdc.main import panel_summary_from_boxes
    def b(text, x, y):
        return {"text": text, "x0": x, "x1": x+65, "y0": y, "y1": y+12}
    # OCR boxes may split AP summary labels across three separate lines.
    boxes = [
        b("Issue Price", 840, 120), b("730,822.00", 1050, 120),
        b("CESS", 840, 170), b("4,463.89", 1050, 170),
        b("Additional", 840, 210), b("Previlage Fee", 840, 230),
        b("15,738.72", 1050, 235),
        b("Additional", 840, 260), b("Retail Excise", 840, 280),
        b("Tax", 840, 300), b("76,968.00", 1050, 285),
        b("Previlage Fee", 840, 330), b("for Clubs", 840, 350),
        b("0.00", 1050, 350),
        b("ICDC Value", 840, 390), b("827,992.61", 1050, 390),
        b("TCS", 840, 430), b("16560", 1050, 430),
        b("Sub Total", 840, 470), b("844,747.65", 1050, 470),
        b("(-)Less this", 840, 520), b("844,552.61", 1050, 520),
    ]
    bands = [110, 155, 195, 250, 315, 380, 420, 460, 500, 550]
    found = panel_summary_from_boxes(boxes, 1200, bands, 600)
    assert found["issue_price"] == 730822
    assert found["cess"] == 4463.89
    assert found["additional_previlage_fee"] == 15738.72
    assert found["additional_retail_excise_tax"] == 76968
    assert found["previlage_fee_clubs"] == 0
    assert found["icdc_value"] == 827992.61
    assert found["tcs"] == 16560
    assert found["sub_total"] == 844747.65
    assert found["less_this_invoice_value"] == 844552.61


def test_three_printed_quantity_cells_are_reconciled_without_guessing():
    from app.modules.document_import.ap_icdc.main import invoice_qty_from_boxes
    def b(text, x, y):
        return {"text": text, "x0": x, "x1": x+55, "y0": y, "y1": y+16}
    boxes = [b("Total Cartons:", 250, 123), b("58/0", 390, 130),
             b("132/0", 540, 130), b("190/0", 690, 130)]
    assert invoice_qty_from_boxes(boxes, 1200) == (58, 132, 190)
    boxes[-1]["text"] = "191/0"
    assert invoice_qty_from_boxes(boxes, 1200) is None
