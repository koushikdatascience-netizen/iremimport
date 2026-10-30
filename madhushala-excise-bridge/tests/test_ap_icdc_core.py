from app.modules.document_import.ap_icdc.parse import merge_continuation, parse_cells
from app.modules.document_import.ap_icdc.validate import recover_cases, validate_document

def test_arithmetic_recovery_keeps_review_flag():
    assert recover_cases(3, 2675, 10700) == (4, ["corrected_via_arithmetic", "manual_review"])

def test_does_not_guess_fractional_cases():
    cases, flags = recover_cases(3, 2675, 10001)
    assert cases == 3 and "manual_check" in flags

def test_boundary_name_merge():
    rows = [
        {"sl_no": 27, "brand_number":"C434", "brand_name":"NICOLS BLACK", "pages":[3], "page_open_end":True, "flags":[]},
        {"sl_no":None,"brand_number":"","brand_name":"GOLD RARE PREMIUM","pages":[4],"continuation_position":"page_start"},
    ]
    result=merge_continuation(rows)
    assert len(result)==1
    assert result[0]["brand_name"]=="NICOLS BLACK GOLD RARE PREMIUM"
    assert result[0]["pages"]==[3,4]

def test_not_merge_arbitrary_orphan():
    rows=[{"sl_no": 1,"brand_name":"ABC", "page_open_end":False},
          {"sl_no": None,"brand_number":"","brand_name":"Financial Summary","continuation_position":"page_start"}]
    result=merge_continuation(rows)
    assert len(result)==2
    assert "unmatched_continuation" in result[1]["flags"]

def test_money_and_row_validation():
    item = parse_cells(["1","C021","BUDWEISER MAGNUM BEER","BEER","G","650","4","0","2675 / 222.92","10700"],1)
    assert item["qty_cases"]==4
    assert item["case_rate"]==2675.0
    assert item["total"]==10700.0
    summary={"invoice_qty_total":4,"issue_price":10700,"cess":0,
            "additional_previlage_fee":0,"additional_retail_excise_tax":0,
            "previlage_fee_clubs":0,"icdc_value":10700,"tcs":100,
            "less_this_invoice_value":10800,"sub_total":10850,
            "amount_credited":50}
    result=validate_document([item],summary)
    assert all(result["checks"].values())
    assert result["status"]=="PASSED"

def test_rate_pair_survives_ocr_dropping_the_slash():
    item = parse_cells(["1", "C021", "BUDWEISER MAGNUM BEER", "BEER", "G", "650", "9", "0", "2675 222.92", "24075"], 1)
    assert item["case_rate"] == 2675.0
    assert item["btl_rate"] == 222.92
    assert item["qty_cases"] == 9
    assert "manual_check" not in item["flags"]

def test_zero_missing_not_equal():
    item=parse_cells(["1","C021","Test","BEER","G","650","","0","2675 / 222.92","10700"],1)
    assert "manual_review" in item["flags"]
