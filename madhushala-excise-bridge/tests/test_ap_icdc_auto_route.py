"""AP invoice detection may only opt in when invoice identity is verified."""
from app.modules.document_import.ap_icdc.detect_invoice import is_ap_icdc_invoice_pdf


def _pdf_with_text(text):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), text)
    result = doc.tobytes()
    doc.close()
    return result


def test_ap_icdc_invoice_recognized():
    sample = "Government of Andhra Pradesh ICDC123456789 Invoice Cum Delivery Challan"
    assert is_ap_icdc_invoice_pdf(_pdf_with_text(sample))


def test_bar_indent_excluded():
    sample = "Government of Andhra Pradesh ICDC123456789 Invoice Bar Indent"
    assert not is_ap_icdc_invoice_pdf(_pdf_with_text(sample))


def test_telangana_excluded():
    sample = "Government of Telangana ICDC123456789 Invoice Cum Delivery Challan"
    assert not is_ap_icdc_invoice_pdf(_pdf_with_text(sample))


def test_unverified_and_scanned_pdf_not_force_routed():
    assert not is_ap_icdc_invoice_pdf(_pdf_with_text("Generic invoice without state and ICDC"))
    assert not is_ap_icdc_invoice_pdf(b"%PDF-not-a-valid-pdf")
