"""Odisha OSBCL deterministic extraction unit regression tests."""
from decimal import Decimal
import pytest
from app.modules.document_import.odisha_osbcl import recognized, _financials, _money, _count, _osbcl_rows

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

def test_merged_continuation_grid_preserves_product_and_printed_subtotal():
    rows = [
        ["SlProduct\nNo.Category", "", "Item Name", "Pack\nSize", "Rate /\nCase",
         "Qty\n(In\nCases)", "", "Qty\n(in loose\nunits)", "MGER\nValue\n(In Rs.)", "Amount\n(In Rs.)"],
        ["77WHISKY (IMFL)", "Seagram's Royal Stag Superior Whisky\n(180)", "", "180ML",
         "6,922.32", "19", "", "0", "92,989.99", "1,31,524.08"],
        ["", "", "", "", "Sub Total", "200", "", "0", "9,77,123.58", "13,80,137.70"],
        ["", "", "", "", "", "Sub Total", "", "", "", "13,80,137.70"],
    ]
    canonical = _osbcl_rows(rows)
    assert canonical[0] == ["77", "WHISKY (IMFL)", "Seagram's Royal Stag Superior Whisky\n(180)",
                            "180ML", "6,922.32", "19", "0", "92,989.99", "1,31,524.08"]
    assert canonical[1][4:] == ["Sub Total", "200", "0", "9,77,123.58", "13,80,137.70"]
    assert len(canonical) == 2

def test_malformed_continuation_grid_fails_closed():
    rows = [
        ["SlProduct\nNo.Category", "", "Item Name", "Pack\nSize", "Rate /\nCase",
         "Qty\n(In\nCases)", "", "Qty\n(in loose\nunits)", "MGER\nValue\n(In Rs.)", "Amount\n(In Rs.)"],
        ["77WHISKY (IMFL)", "Whisky", "unexpected", "180ML", "10.00", "1", "", "0", "10.00", "10.00"],
    ]
    with pytest.raises(ValueError, match="unexpected column layout"):
        _osbcl_rows(rows)
