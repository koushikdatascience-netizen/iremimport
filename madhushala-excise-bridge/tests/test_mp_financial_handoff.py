import json
from pathlib import Path

import pytest

from app.modules.document_import import pdf_extractor as pe
from app.modules.document_import.mp_financials import extract_mp_financials
from test_document_import import auth, client, create_session  # noqa: F401


ROOT = Path(__file__).parent / "fixtures"
TEXT = json.loads((ROOT / "mp_financial_text.json").read_text())


def financial_text():
    return [TEXT["productText"], TEXT["tableTotal"], TEXT["payment"]]


def test_synthetic_mp_printed_amounts_reconcile_without_counting_duty_twice():
    result = extract_mp_financials(financial_text(), 27, 27)
    assert result["reconciled"] is True
    assert result["taxes"] == {"vat": "54.00", "incomeTax": "6.48"}
    assert result["totalAmount"] == result["subTotal"] == "270.00"
    assert result["totalNet"] == "330.48"
    assert result["dutyAmount"] == "135.00"
    assert result["roundedOff"] is None
    assert result["calculationMappingRequired"] == ["incomeTax"]
    assert "tcs" not in result["taxes"]


@pytest.mark.parametrize("change", ["missing_tax", "missing_manufacturing", "missing_vat", "wrong_net", "wrong_vat", "missing_rows", "conflicting_footer"])
def test_invalid_mp_financials_require_review(change):
    texts = financial_text()
    if change == "missing_tax": texts[2] = texts[2].replace("Income Tax : \n6.48", "")
    elif change == "missing_manufacturing": texts[2] = texts[2].replace("Manufacturing Amount :\n270.00", "")
    elif change == "missing_vat": texts[2] = texts[2].replace("VAT Amount :\n54.00", "")
    elif change == "wrong_net": texts[2] = texts[2].replace("330.48", "331.48")
    elif change == "wrong_vat": texts[2] = texts[2].replace("54.00", "55.00")
    elif change == "missing_rows": texts[0] = ""
    else: texts.append(texts[2].replace("330.48", "331.48"))
    # Exercise missing values with arbitrary label whitespace.
    if change == "missing_tax":
        import re
        texts[2] = re.sub(r"Income\s+Tax\s*:\s*6\.48", "", texts[2])
    result = extract_mp_financials(texts, 27, 27)
    assert result["reconciled"] is False
    assert result["validation"]["status"] == "review_required"


def test_no_payment_footer_keeps_older_mp_documents_compatible():
    assert extract_mp_financials([TEXT["productText"]], 27, 27) is None


def install_mp(monkeypatch, invalid=False):
    header = ["Sl", "Label Name", "Batch", "Capacity", "Quantity in Cases", "Duty", "Mfg Amount", "VAT"]
    products = [[str(n), f"Synthetic Product {n}", "", "750 (Glass Bottle)", "1", "5.00", "10.00", "2.00"] for n in range(1, 28)]
    table_pages = [[1, "", [[header, *products[:7]]]], [2, "", [products[7:17]]], [3, "", [products[17:]]]]
    texts = financial_text()
    if invalid: texts[2] = texts[2].replace("330.48", "331.48")
    class Page:
        def __init__(self, index): self.index = index
        def get_text(self, mode="text"):
            if mode == "words": return [()] * 50
            prefix = "Madhya Pradesh Excise Department\nDelivery Challan\nDemand Id- MP-TEST\nDate- 16/11/2026\n" if self.index == 0 else ""
            return prefix + ("\n".join(texts) if self.index == 0 else "")
    class Document:
        def __len__(self): return 4
        def __iter__(self): return iter([Page(n) for n in range(4)])
        def close(self): pass
    monkeypatch.setattr(pe.fitz, "open", lambda *a, **kw: Document())
    monkeypatch.setattr(pe, "_page_tables", lambda page, **kw: table_pages[page.index][2] if page.index < 3 else [])


@pytest.mark.parametrize("invalid", [False, True])
def test_mp_upload_persistence_review_and_handoff(client, monkeypatch, invalid):
    from app.db import conn
    from app.modules.document_import import purchase_required
    from app.services.reference_data_service import reference_data_service
    install_mp(monkeypatch, invalid)
    async def context(*a, **kw): return {"defaults": {}}
    async def catalogue(*a, **kw):
        return [{"itemCode": f"MP{i}", "packing": 12, "purchaseRate": 1, "purchaseRateCase": 12} for i in range(27)]
    monkeypatch.setattr(purchase_required, "build_purchase_context", context)
    monkeypatch.setattr(reference_data_service, "fresh_catalogue", catalogue)
    session = create_session(client)
    response = client.post("/api/v1/document-import/upload/batch/pdf", headers=auth(session), files=[("files", ("mp.pdf", b"%PDF-test", "application/pdf"))])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["detected"] == 27
    assert body["summary"]["needsAttention"] == (27 if invalid else 0)
    job = body["job"]["id"]
    rows = client.get(f"/api/v1/document-import/jobs/{job}/items", headers=auth(session)).json()["items"]
    assert all(json.loads(r["raw_data_json"])["sourceState"] == "MADHYA_PRADESH" for r in rows)
    with conn() as db:
        for i, row in enumerate(rows):
            db.execute("UPDATE import_items SET mapped_item_code=?,mapping_status='MAPPED' WHERE id=?", (f"MP{i}", row["id"]))
    result = client.get(f"/api/v1/document-import/jobs/{job}/purchase/handoff", headers=auth(session))
    if invalid:
        assert result.status_code == 409, result.text
        return
    assert result.status_code == 200, result.text
    purchase = result.json()["purchasePayload"]
    assert len(purchase["items"]) == 27
    assert sum(row["box"] for row in purchase["items"]) == 27
    assert purchase["totalAmount"] == "270.00"
    assert purchase["totalNet"] == "330.48"
    assert purchase["taxes"]["vat"] == "54.00"
    assert purchase["taxes"]["incomeTax"] == "6.48"
    assert purchase["taxes"]["tcs"] is None
    assert purchase["sourceFinancials"]["dutyAmount"] == "135.00"
    assert purchase["calculationMappingRequired"] == ["incomeTax"]
    assert all(row["amount"] is None for row in purchase["items"])
