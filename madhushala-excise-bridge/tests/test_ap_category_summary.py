import pytest
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
from app.modules.document_import.ap_icdc.category_summary import strip_ap_category_totals

def _doc(items, invoice_number=None):
    return ExtractedDocument(documentType="invoice", invoiceNumber=invoice_number, items=items)

def _product(name, boxes, ml=750):
    return ExtractedProduct(itemName=name, brand=name, box=boxes, ml=ml)

def test_ap_indent_29_products_only_after_filter():
    products = [_product(f"BRAND {i:02d}", 1) for i in range(1, 29)]
    products.append(_product("BRAND 29", 142))
    doc = _doc(products + [_product("IMFL/FL", 37, None), _product("Beer", 133, None)])
    actual, audit = strip_ap_category_totals(doc, "INDENT2.pdf")
    assert len(actual.items)==29
    assert audit["total_cases"]==170
    assert audit["filtered_product_rows"]==29
    assert [x.itemName for x in actual.items]==[x.itemName for x in products]

def test_other_state_never_filtered():
    doc=_doc([_product("IMFL/FL",37),_product("Beer",133),_product("Sample",170)])
    result, audit=strip_ap_category_totals(doc,"WEST_BENGAL.pdf")
    assert len(result.items)==3 and not audit["apSummaryFiltered"]

def test_ap_mismatch_blocks_saving():
    doc=_doc([_product("ABC",5),_product("IMFL/FL",37),_product("Beer",133)],"ICDC12345678")
    with pytest.raises(ValueError,match="quantity-summary mismatch"):
        strip_ap_category_totals(doc,"invoice.pdf")

def test_unpaired_category_fails_closed():
    doc=_doc([_product("ABC",5),_product("Beer",133)],"ICDC12345678")
    with pytest.raises(ValueError,match="Ambiguous"):
        strip_ap_category_totals(doc,"invoice.pdf")
