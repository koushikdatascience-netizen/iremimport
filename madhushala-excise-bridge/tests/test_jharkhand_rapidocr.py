"""Pure parser and fail-closed tests; no OCR runtime/model download required."""
import pytest

from app.modules.document_import.jharkhand_rapidocr import parse_cells, _qty, _cells


def row(serial="1", quantity="15.00"):
    return [serial, "Beer", "GODFATHER THE LEGENDARY STRONG BEER",
            "650 ML", quantity, "11,025.15", "22,441.05",
            "1,122.00", "57.90", "3.00", "12,238.05"]


def test_verified_sample_row():
    parsed = parse_cells([row()])
    assert parsed[0]["serial"] == 1
    assert parsed[0]["box"] == 15
    assert parsed[0]["loose"] == 0
    assert parsed[0]["ml"] == 650
    assert parsed[0]["amount"] == "12238.05"


def test_jharkhand_cases_dot_loose_is_not_decimal():
    assert _qty("17.20") == (17, 20)
    assert _qty("2.04") == (2, 4)


def test_missing_line_fail_closed():
    with pytest.raises(ValueError, match="missing"):
        parse_cells([row("1"), row("3")])


def test_incomplete_columns_fail_closed():
    with pytest.raises(ValueError, match="eleven"):
        parse_cells([row()[:-1]])


def test_missing_price_fail_closed():
    cells = row()
    cells[-1] = ""
    with pytest.raises(ValueError, match="Invalid amount"):
        parse_cells([cells])


def test_grid_detection_failure_is_explicit():
    with pytest.raises(ValueError, match="incomplete"):
        _cells([], [0, 100], [0, 200])
