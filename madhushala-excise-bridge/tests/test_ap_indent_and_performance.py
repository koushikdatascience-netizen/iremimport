import numpy as np
from app.modules.document_import.ap_icdc.bar_indent import parse_bar_row, select_bar_columns, validate_bar_items, parse_bar_summary
from app.modules.document_import.ap_icdc.performance import ocr_page

def test_bar_indent_11_column_row():
    row=["9","2897EC707GQQ","C707","KYRA PREMIUM RED WINE","IML","QQ","12/750ml","G","1","5603.00","5603.00"]
    parsed=parse_bar_row(row,2)
    assert parsed["brand_number"]=="C707"
    assert parsed["size_ml"]==750
    assert parsed["bottles_per_case"]==12
    assert parsed["qty_cases"]==1
    assert parsed["total"]==5603.0
    assert parsed["flags"]==[]

def test_bar_indent_does_not_silently_change_amount():
    row=["1","3154BC021CAP","C021","BUDWEISER MAGNUM BEER","Beer","AP","24/500ml","C","10","3510.00","35101.00"]
    parsed=parse_bar_row(row,1)
    assert "manual_check" in parsed["flags"]
    assert validate_bar_items([parsed])["status"] == "FAILED"

def test_continuation_page_columns():
    xs=list(range(12))
    assert select_bar_columns([(1,[1,2,3],[]),(2,xs,[])])==xs

def test_full_ocr_mode_same_image(monkeypatch):
    observed=[]
    monkeypatch.setattr("app.modules.document_import.ap_icdc.performance.ocr_boxes",
                        lambda engine,image: observed.append(image.shape) or [])
    image=np.zeros((100,100,3),dtype=np.uint8)
    result,metrics=ocr_page(None,image,2,4,mode="full")
    assert observed == [(100,100,3)]
    assert metrics["mode"] == "full"

def test_first_and_last_pages_never_cropped(monkeypatch):
    observed=[]
    monkeypatch.setattr("app.modules.document_import.ap_icdc.performance.ocr_boxes",
                        lambda engine,image: observed.append(image.shape) or [])
    image=np.zeros((100,100,3),dtype=np.uint8)
    for p in (1,4):
        _,metrics=ocr_page(None,image,p,4,mode="table")
        assert metrics["mode"]=="full"
    assert observed == [(100,100,3),(100,100,3)]

def test_bar_reconciles_printed_totals():
    items = [parse_bar_row(["1", "P001", "C707", "TEST WINE", "WINE",
         "XX", "12/750ml", "G", "2", "100.00", "200.00"], 1)]
    valid = validate_bar_items(items, expected_total_cases=2, expected_total_amount=200)
    assert valid["status"] == "PASSED"
    assert all(valid["checks"].values())

def test_bar_missing_totals_fails_closed():
    items = [parse_bar_row(["1","P001","C707","TEST WINE","WINE",
         "XX","12/750ml","G","2","100.00","200.00"], 1)]
    assert validate_bar_items(items)["status"] == "FAILED"

def test_bar_summary_label_and_money():
    boxes = [
        {"text":"Total", "x0":100,"x1":180,"y0":500,"y1":522},
        {"text":"113", "x0":1400,"x1":1470,"y0":500,"y1":522},
        {"text":"360631.00", "x0":1800,"x1":1940,"y0":500,"y1":522},
    ]
    assert parse_bar_summary(boxes) == {"total_cases":113, "issue_price":360631.0}
