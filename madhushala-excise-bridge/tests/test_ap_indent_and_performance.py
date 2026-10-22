import numpy as np
from app.modules.document_import.ap_icdc.bar_indent import parse_bar_row, select_bar_columns, validate_bar_items
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
