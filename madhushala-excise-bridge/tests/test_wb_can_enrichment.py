import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
from app.modules.document_import.service import DocumentImportService


def pair():
    fixture = json.loads((Path(__file__).parent / "fixtures/wb_can_enrichment.json").read_text())
    tp = [ExtractedProduct.model_validate(x) for x in fixture["tp"]]
    invoice = ExtractedDocument(documentType="invoice", items=fixture["invoice"], invoiceFinancials=fixture["financials"])
    return tp, invoice


def test_real_can_pair_enriches_tp_without_creating_invoice_rows():
    tp, invoice = pair()
    result, financials = DocumentImportService(None)._enrich_primary_items_with_invoice(tp, invoice, invoice_filename="invoice.pdf")
    assert len(result) == len(tp) == 9
    assert [(x.itemName, x.ml, x.box, x.loose, x.batchNo) for x in result] == [(x.itemName, x.ml, x.box, x.loose, x.batchNo) for x in tp]
    assert sum(x.box for x in result) == 17
    assert sum(Decimal(str(x.invoiceAmount)) for x in result) == Decimal("78571.68")
    assert financials["totalNet"] == 87293


@pytest.mark.parametrize("change", ["ml", "quantity", "bottles", "product", "ambiguous"])
def test_packaging_tolerance_does_not_relax_financial_guards(change):
    tp, invoice = pair()
    can = next(x for x in tp if "[Can]" in x.itemName)
    if change == "ml": can.ml = 650
    elif change == "quantity": can.box += 1
    elif change == "bottles": can.rawPdfRow["In Bottles"] = "119"
    elif change == "product": can.itemName = can.itemName.replace("FIFA", "OTHER")
    else: invoice.items.append(invoice.items[-1].model_copy(deep=True))
    with pytest.raises(HTTPException) as exc:
        DocumentImportService(None)._enrich_primary_items_with_invoice(tp, invoice, invoice_filename="invoice.pdf")
    assert exc.value.status_code == 422


def test_unbracketed_and_unknown_packaging_words_remain_product_identity():
    key = DocumentImportService._invoice_match_name
    assert key("Brand [Can] 500 Ml.") == key("Brand 500 Ml.")
    assert key("Brand Can 500 Ml.") != key("Brand 500 Ml.")
    assert key("Brand [Reserve] 500 Ml.") != key("Brand 500 Ml.")
