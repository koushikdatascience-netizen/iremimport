"""AP routing safety and safe diagnostics."""
from app.modules.document_import.ap_icdc.detect_invoice import is_scanned_ap_icdc_pdf
from app.modules.document_import.ap_icdc.invoice_upload import validation_failure_detail


def test_other_state_files_do_not_incur_scanned_ap_ocr():
    assert is_scanned_ap_icdc_pdf(b"%PDF-not-readable", "odisha.pdf") is False
    assert is_scanned_ap_icdc_pdf(b"%PDF-not-readable", "haryana.pdf") is False
    assert is_scanned_ap_icdc_pdf(b"%PDF-not-readable", "jharkhand.pdf") is False


def test_ap_reconciliation_details_reveal_exact_failed_checks_only():
    result = {"validation": {"status": "FAILED", "checks": {
        "row_arithmetic": True, "case_quantity": False, "icdc_value": False
    }}, "items": [{}, {}], "diagnostics": {
        "page_count": 6, "grid_columns_detected": False,
        "ocr_elapsed_seconds": 74.3
    }}
    summary = validation_failure_detail(result)
    assert summary["failedChecks"] == ["case_quantity", "icdc_value"]
    assert summary["ocrRowCount"] == 2
    assert summary["pageCount"] == 6
    assert summary["gridColumnsDetected"] is False
    assert "url" not in summary and "text" not in summary
