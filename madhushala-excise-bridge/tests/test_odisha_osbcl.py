"""Odisha OSBCL deterministic extraction unit regression tests."""
from decimal import Decimal
import pytest
from app.modules.document_import.odisha_osbcl import recognized, _financials, _money, _count

def test_osbcl_profile_is_specific():
    assert recognized("Odisha State Beverages Corporation Ltd\nDELIVERY CHALLAN CUM INVOICE")
    assert not recognized("West Bengal State Beverages Corporation Ltd\nDELIVERY CHALLAN CUM INVOICE")

def test_indian_grouping_and_financial_reconciliation():
    summary = """Sub Total
13,99,646.23
Sub Total
13,99,646.23
VAT
4,89,876.18
TCS
37,790.00
Total
19,27,312.41
"""
    values=_financials(summary)
    assert values["subtotal"] + values["vat"] + values["tcs"] == values["total"]
    assert _money("1,19,567.40") == Decimal("119567.40")

def test_conflicting_subtotal_blocks_purchase():
    with pytest.raises(ValueError,match="conflicting"):
        _financials("Sub Total\n10.00\nSub Total\n12.00\nVAT\n2.00\nTCS\n1.00\nTotal\n13.00")

def test_case_count_rejects_decimal_fraction():
    with pytest.raises(ValueError):
        _count("2.5")
