"""Replay sanitized glyphs/cell boundaries captured from eight real WB PDFs.

No customer/address blocks or PDFs are committed. Set WB_REAL_PDF_DIR to also
exercise the original files with the installed PyMuPDF table finder.
"""
import copy
import gzip
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from app.modules.document_import import pdf_extractor as pe
from app.modules.document_import.service import DocumentImportService
from test_document_import import client, create_session, auth  # noqa: F401

FIXTURE = Path(__file__).parent / "fixtures/wb_real_pdf_geometry.json.gz"
CAPTURES = json.loads(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8"))
INVOICE_1 = "BEVCOInvoice_ (1)"
INVOICE_18 = "BEVCOInvoice_ (18)"
TP_1 = "BEVCOTP (1)"
TP_3 = "BEVCOTP (3)"
LEGACY_INVOICE = "BEVCOInvoice_2026-2027-W-2024-015-01-008033"
AMOUNTS = {
    INVOICE_1: ["2613.90", "8623.71", "5529.96", "10677.12", "4678.53", "10944.84",
                "8424.12", "7148.88", "7119.12", "6916.80", "4776.27", "7658.64"],
    INVOICE_18: ["31244.80", "259341.80", "24873.60"],
    "3": ["447.30", "3136.68", "1192.85", "250.05", "1696.82", "7657.68", "6782.94", "16848.96",
          "16965.60", "6767.52", "33693.00", "63880.56", "7607.04", "164172.60", "10678.56",
          "14238.24", "14297.76", "11503.20", "13833.60"],
    LEGACY_INVOICE: ["14804.40"],
}
FINANCIALS = {
    INVOICE_1: ["85111.89", "8544.82", "715.64", "1887.45", "96259.80", "0.20", "96260.00"],
    INVOICE_18: ["315460.20", "4667.00", "2083.88", "6444.22", "328655.30", "0.30", "328655.00"],
    "3": ["395650.96", "35397.71", "3686.55", "8694.70", "443429.92", "0.08", "443430.00"],
    LEGACY_INVOICE: ["14804.40", "0.00", "359.40", "303.28", "15467.08", "0.08", "15467.00"],
}


class CapturedPage:
    def __init__(self, record):
        self.record = record
        self.number = record["number"]
        self.rect = fitz.Rect(0, 0, record["width"], record["height"])
        self.calls = []

    def get_text(self, mode="text"):
        if mode == "rawdict":
            return self.record["rawdict"]
        if mode == "words":
            return [()] * len(self.record["text"].split())
        return self.record["text"]

    def find_tables(self, **kwargs):
        self.calls.append(kwargs)
        key = "ruled_tables" if kwargs.get("use_layout") is False else "layout_tables"
        tables = []
        for captured in self.record[key]:
            tables.append(SimpleNamespace(
                rows=[SimpleNamespace(cells=row) for row in captured["cells"]],
                extract=lambda captured=captured: captured["extract"],
            ))
        return SimpleNamespace(tables=tables)


class CapturedDocument:
    def __init__(self, name, data):
        self.name = name
        self.pages = [CapturedPage(page) for page in data[name]["pages"]]

    def __len__(self): return len(self.pages)
    def __getitem__(self, key): return self.pages[key]
    def __iter__(self): return iter(self.pages)
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def close(self): pass


def install_captures(monkeypatch, data=None):
    data = data or CAPTURES
    opened = []
    def open_capture(path=None, *, stream=None, **kwargs):
        name = Path(path).stem if path else ""
        if name not in data:
            marker = stream if stream is not None else Path(path).read_bytes()
            name = marker.decode().splitlines()[-1]
        doc = CapturedDocument(name, data)
        opened.append(doc)
        return doc
    monkeypatch.setattr(pe.fitz, "open", open_capture)
    return opened


def assert_document(name, document, meta):
    assert document is not None and meta["usable"]
    assert meta["needsFallback"] is False
    if name in AMOUNTS:
        assert [Decimal(str(item.invoiceAmount)) for item in document.items] == list(map(Decimal, AMOUNTS[name]))
        f = document.invoiceFinancials
        actual = [f["itemSubtotal"], f["taxes"]["specialPurposeFee"], f["taxes"]["roundOffValueToGovt"],
                  f["taxes"]["tcs"], f["totalAmount"], f["roundedOff"], f["totalNet"]]
        assert list(map(lambda x: Decimal(str(x)), actual)) == list(map(Decimal, FINANCIALS[name]))
        assert f["reconciled"] is True
        assert sum(Decimal(str(item.invoiceAmount)) for item in document.items) == Decimal(FINANCIALS[name][0])
        assert len(document.items) == meta["candidateRowCount"] == len(AMOUNTS[name])
        if name == INVOICE_1:
            assert (document.items[0].box, document.items[0].loose) == (0, 5)
            assert document.items[5].ml == 750 and "Smirnoff Mirchi Mango" in document.items[5].itemName
            assert sum(i.box for i in document.items) == 7
            assert sum(i.loose for i in document.items) == 20
        elif name == INVOICE_18:
            assert [(i.ml, i.box, i.loose) for i in document.items] == [(375, 10, 0), (600, 83, 0), (500, 8, 0)]
        else:
            assert (sum(i.box for i in document.items), sum(i.loose for i in document.items)) == ((42, 12) if name == "3" else (5, 0))
    else:
        assert meta["totalValidation"]["status"] == "passed"
        if name in {TP_1, "4"}:
            # Exact known-good rows, including names, whitespace, batch data
            # and raw quantities, captured before the WB changes.
            assert [item.model_dump() for item in document.items] == CAPTURES[name]["previousItems"]
        elif name == TP_3:
            assert [(i.ml, i.box, i.loose) for i in document.items] == [(375, 10, 0), (500, 8, 0), (600, 79, 0), (600, 4, 0)]
            assert [int(i.rawPdfRow["In Bottles"]) for i in document.items] == [320, 192, 1580, 80]
            assert len({i.batchNo for i in document.items}) == 4
            assert sum(i.box for i in document.items) == 101
        else:
            assert name == "2"
            assert [(i.itemName, i.ml, i.box, i.loose) for i in document.items] == [("TOP 10 PREMIUM WHISKY [Pet Bottle]", 750, 5, 0)]
            assert document.items[0].rawPdfRow["In Bottles"] == "60"


@pytest.mark.parametrize("name", list(CAPTURES))
def test_real_geometry_output_and_scoped_table_selection(monkeypatch, name):
    opened = install_captures(monkeypatch)
    document, meta = pe.extract_pdf_locally(Path(name + ".pdf"))
    assert_document(name, document, meta)
    calls = [call for page in opened[-1] for call in page.calls]
    assert calls
    assert all(call == ({} if name in {TP_1, "4"} else {"use_layout": False}) for call in calls)
    active = CAPTURES[name]["activePageCount"]
    assert all(not page.calls for page in opened[-1].pages[active:])


def test_continuation_maps_its_own_seven_columns(monkeypatch):
    install_captures(monkeypatch)
    doc = CapturedDocument(INVOICE_1, CAPTURES)
    pages = [(i+1, page.get_text(), pe._page_tables(page, horizontal_only=True)) for i, page in enumerate(doc.pages[:2])]
    assert len(pages[1][2][0][0]) == 7  # Actual faulty layout-grid shape.
    products, _, _ = pe._extract_west_bengal_bevco_invoice("", pages)
    f = pe._west_bengal_bevco_financials(pages, products)
    assert f["totalNet"] == 96260 and f["roundedOff"] == .2 and f["reconciled"]


def test_missing_wrapped_product_cannot_pass_completeness(monkeypatch):
    data = copy.deepcopy(CAPTURES)
    record = data[INVOICE_1]["pages"][0]
    name_cell = fitz.Rect(record["ruled_tables"][0]["cells"][6][0])
    for block in record["rawdict"]["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                span["chars"] = [c for c in span["chars"] if not name_cell.contains((fitz.Rect(c["bbox"]).tl + fitz.Rect(c["bbox"]).br)/2)]
    install_captures(monkeypatch, data)
    document, meta = pe.extract_pdf_locally(Path(INVOICE_1 + ".pdf"))
    assert len(document.items) == 11
    assert meta["candidateRowCount"] == 12
    assert meta["needsFallback"] and not document.invoiceFinancials["reconciled"]


def test_products_can_continue_before_buyer_copy_without_duplication(monkeypatch):
    install_captures(monkeypatch)
    doc = CapturedDocument(INVOICE_1, CAPTURES)
    first = pe._page_tables(doc[0], horizontal_only=True, ruled_only=True)[0]
    last = pe._page_tables(doc[1], horizontal_only=True, ruled_only=True)[0]
    # Reflow real captured rows over two Office pages. No invoice values are
    # invented; this exercises a product continuation as well as its summary.
    pages = [(1, "Office Copy", [[first[0], *first[1:6]]]),
             (2, "Office Copy", [[first[0], *first[6:], *last[1:]]]),
             (3, "Buyer's Copy", [first])]
    products, _, _ = pe._extract_west_bengal_bevco_invoice("", pages)
    assert len(products) == 12
    assert [p.sourcePage for p in products] == [1] * 5 + [2] * 7
    assert pe._candidate_row_count("WEST_BENGAL_BEVCO_INVOICE", pages) == 12
    assert pe._west_bengal_bevco_financials(pages, products)["reconciled"]


def test_ruled_reader_supports_older_finder_signature(monkeypatch):
    page = CapturedPage(CAPTURES[INVOICE_18]["pages"][0])
    original = page.find_tables
    monkeypatch.setattr(page, "find_tables", lambda: original())
    tables = pe._page_tables(page, horizontal_only=True, ruled_only=True)
    products, _, _ = pe._extract_west_bengal_bevco_invoice("", [(1, "Office Copy", tables)])
    f = pe._west_bengal_bevco_financials([(1, "Office Copy", tables)], products)
    assert f["reconciled"] and f["totalNet"] == 328655


@pytest.mark.parametrize("invoice,tp", [(INVOICE_1, TP_1), (INVOICE_18, TP_3), ("3", "4"), (LEGACY_INVOICE, "2")])
def test_real_pair_keeps_tp_batches_and_exact_money(monkeypatch, invoice, tp):
    install_captures(monkeypatch)
    inv, _ = pe.extract_pdf_locally(Path(invoice + ".pdf"))
    permit, _ = pe.extract_pdf_locally(Path(tp + ".pdf"))
    assert inv.invoiceNumber == permit.invoiceNumber
    rows, f = DocumentImportService(None)._enrich_primary_items_with_invoice(permit.items, inv, invoice_filename=invoice+".pdf")
    assert len(rows) == len(permit.items)
    assert [(r.itemName, r.ml, r.box, r.loose, r.batchNo) for r in rows] == [(r.itemName, r.ml, r.box, r.loose, r.batchNo) for r in permit.items]
    assert sum(Decimal(str(r.invoiceAmount)) for r in rows) == Decimal(FINANCIALS[invoice][0])
    if tp == TP_3:
        assert [Decimal(str(r.invoiceAmount)) for r in rows] == list(map(Decimal, ["31244.80", "24873.60", "246843.40", "12498.40"]))
    assert f["reconciled"]


@pytest.mark.skipif(not os.getenv("WB_REAL_PDF_DIR"), reason="Private original PDFs supplied separately")
@pytest.mark.parametrize("name", list(CAPTURES))
def test_original_pdf_with_actual_table_finder(name):
    path = Path(os.environ["WB_REAL_PDF_DIR"]) / (name + ".pdf")
    if not path.exists():
        path = Path(os.environ["WB_REAL_PDF_DIR"]) / "madhushala-excise-bridge" / (name + ".pdf")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == CAPTURES[name]["sourceSha256"]
    assert_document(name, *pe.extract_pdf_locally(path))


@pytest.mark.parametrize("invoice,tp,count", [(INVOICE_1, TP_1, 12), (INVOICE_18, TP_3, 4), ("3", "4", 19), (LEGACY_INVOICE, "2", 1)])
@pytest.mark.parametrize("original", [False, pytest.param(True, marks=pytest.mark.skipif(not os.getenv("WB_REAL_PDF_DIR"), reason="Private original PDFs supplied separately"))])
def test_pair_upload_persists_financials_and_handoff(client, monkeypatch, invoice, tp, count, original):
    from app.db import conn
    from app.modules.document_import import purchase_required
    from app.modules.document_import.llama_client import LlamaCloudClient
    from app.services.reference_data_service import reference_data_service
    if not original:
        install_captures(monkeypatch)
    def source(name):
        if not original:
            return b"%PDF-captured-real-geometry\n" + name.encode()
        path = Path(os.environ["WB_REAL_PDF_DIR"]) / (name + ".pdf")
        if not path.exists():
            path = path.parent / "madhushala-excise-bridge" / path.name
        return path.read_bytes()
    async def forbidden(*a, **kw): raise AssertionError("Readable verified WB must not call cloud extraction")
    monkeypatch.setattr(LlamaCloudClient, "extract_scanned_pdf_pages", forbidden)
    async def context(*a, **kw): return {"defaults": {}}
    async def catalogue(session):
        return [{"itemCode": f"WB{i}", "itemName": f"WB{i}", "packing": 12,
                 "purchaseRate": 1, "purchaseRateCase": 12, "salesRate": 2} for i in range(count)]
    monkeypatch.setattr(purchase_required, "build_purchase_context", context)
    monkeypatch.setattr(reference_data_service, "fresh_catalogue", catalogue)
    session = create_session(client)
    response = client.post("/api/v1/document-import/upload/batch/pdf", headers=auth(session),
                           files=[("files", (name+".pdf", source(name), "application/pdf")) for name in (tp, invoice)])
    assert response.status_code == 200, response.text
    body = response.json(); job = body["job"]["id"]
    assert body["summary"]["detected"] == count
    persisted = client.get(f"/api/v1/document-import/jobs/{job}/items", headers=auth(session)).json()["items"]
    assert len(persisted) == count
    assert sum(Decimal(str(json.loads(r["raw_data_json"])["invoiceAmount"])) for r in persisted) == Decimal(FINANCIALS[invoice][0])
    with conn() as db:
        for i, row in enumerate(persisted):
            db.execute("UPDATE import_items SET mapped_item_code=?,mapping_status='MAPPED' WHERE id=?", (f"WB{i}", row["id"]))
    response = client.get(f"/api/v1/document-import/jobs/{job}/purchase/handoff", headers=auth(session))
    assert response.status_code == 200, response.text
    payload = response.json()["purchasePayload"]
    assert len(payload["items"]) == count
    assert Decimal(str(payload["totalNet"])) == Decimal(FINANCIALS[invoice][-1])
    assert payload["taxes"]["tcs"] == float(FINANCIALS[invoice][3])
    assert payload["roundedOff"] == float(FINANCIALS[invoice][-2])
    assert sum(Decimal(str(i["amount"])) for i in payload["items"]) == Decimal(FINANCIALS[invoice][0])
