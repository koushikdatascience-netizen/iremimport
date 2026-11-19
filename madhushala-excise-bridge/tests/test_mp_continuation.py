import copy
import json
from pathlib import Path

from app.modules.document_import import pdf_extractor as pe

PROFILE = "MADHYA_PRADESH_DELIVERY_CHALLAN"
FULL_TEXT = "Madhya Pradesh Excise Department Delivery Challan\nDemand Id- tFLDR/2026-2027/00420283\nDate- 16/11/2026"


def pages():
    return json.loads((Path(__file__).parent / "fixtures/mp_00420283_rows.json").read_text())


def test_real_mp_headerless_continuations_keep_all_rows():
    data = pages()
    rows, number, date = pe._extract_madhya_pradesh(FULL_TEXT, data)
    assert len(rows) == pe._candidate_row_count(PROFILE, data) == 27
    assert [sum(r.sourcePage == n for r in rows) for n in (1, 2, 3)] == [7, 10, 10]
    assert [r.box for r in rows] == [15, 20, 3, 1, 5, 5, 1, 10, 3, 10, 10, 3, 1, 2, 10, 5, 1, 1, 25, 2, 10, 20, 5, 10, 3, 3, 1]
    assert sum(r.box for r in rows) == 185
    assert number == "tFLDR/2026-2027/00420283"
    assert date == "16/11/2026"
    assert all(r.loose == 0 and r.ml in (180, 375, 750) for r in rows)


def test_missing_continuation_table_requires_fallback(monkeypatch):
    data = pages()
    data[1][2] = []
    data[2][2] = []
    class Page:
        def __init__(self, record): self.record = record
        def get_text(self, mode="text"):
            if mode == "words": return [()] * 50
            return FULL_TEXT + "\n" + self.record[1]
    class Document:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def close(self): pass
        def __len__(self): return len(data)
        def __iter__(self): return iter([Page(r) for r in data])
    monkeypatch.setattr(pe.fitz, "open", lambda *a, **kw: Document())
    monkeypatch.setattr(pe, "_page_tables", lambda page, **kw: page.record[2])
    document, meta = pe.extract_pdf_locally(Path("missing-continuation.pdf"))
    assert len(document.items) == 7
    assert meta["candidateRowCount"] == 27
    assert meta["needsFallback"] is True
    assert meta["completeness"] == 7 / 27


def test_repeated_headers_and_summary_do_not_add_products():
    data = pages()
    header = data[0][2][0][0]
    for record in data[1:]: record[2][0].insert(0, copy.deepcopy(header))
    data.append([4, "Payment summary", [[["1", "Payment", "539262.77"]]]])
    rows, _, _ = pe._extract_madhya_pradesh(FULL_TEXT, data)
    assert len(rows) == 27 and sum(r.box for r in rows) == 185


def test_changed_grid_is_not_read_with_stale_column_positions():
    data = pages()
    data[1][2] = [[r[:-1] for r in data[1][2][0]]]
    rows, _, _ = pe._extract_madhya_pradesh(FULL_TEXT, data)
    assert len(rows) == 17
    assert pe._candidate_row_count(PROFILE, data) == 27
