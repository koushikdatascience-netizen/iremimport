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
