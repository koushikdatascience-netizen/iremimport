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


def test_column_anchor_segmentation_without_detectable_grid():
    from app.modules.document_import.jharkhand_rapidocr import column_anchored_rows
    width, height = 1000, 1200
    # Synthetic OCR boxes in known Jharkhand column centers, two source rows.
    centers = [48, 90, 220, 365, 410, 475, 557, 630, 700, 760, 850]
    texts = [
        ["1", "Beer", "GODFATHER STRONG BEER", "650 ML", "15.00", "11025.15",
         "22441.05", "1122.00", "57.90", "3.00", "12238.05"],
        ["2", "Beer", "KINGFISHER STRONG", "500 ML", "20.00", "11025.15",
         "22441.05", "1122.00", "57.90", "3.00", "12238.05"],
    ]
    boxes = []
    for y, source in [(400, texts[0]), (440, texts[1])]:
        for x, value in zip(centers, source):
            boxes.append({"x0": x-3, "x1": x+3, "y0": y-4, "y1": y+4,
                          "text": value, "confidence": 0.99})
    rows = column_anchored_rows(boxes, width, height)
    assert len(rows) == 2
    assert rows[0][2] == "GODFATHER STRONG BEER"
    assert rows[1][4] == "20.00"


def test_column_anchor_rejects_missing_serial():
    from app.modules.document_import.jharkhand_rapidocr import column_anchored_rows
    boxes = [{"x0": 45, "x1": 50, "y0": y, "y1": y+6,
              "text": str(n), "confidence": .99} for n, y in [(1, 400), (3, 440)]]
    with pytest.raises(ValueError, match="missing or out of order"):
        column_anchored_rows(boxes, 1000, 1200)
