"""Haryana Form L-34 QR HTML parsing regressions (no network calls)."""
from app.modules.document_import.service import DocumentImportService, _ReadableHtmlParser, _table_dict_rows


def test_haryana_flat_html_rows_and_l34_quantities():
    html = """
      <h1>Excise and Taxation Department Government of Haryana FORM No. L-34</h1>
      <p>Pass No: PS26150809023</p>
      <p>Permit Number: PN261599433427</p>
      <table>
        <tr><th>Brand Name</th><th>Pack Type</th><th>Number of Cases</th>
            <th>Number of Loose Bottle</th><th>Quantity (PL/BL)</th>
            <th>Batch Number</th></tr>
        <tr><td>BLENDERS PRIDE ULTRA PREMIUM WHISKY</td><td>750(12)</td>
            <td>108</td><td>0</td><td>729.000</td><td>18FL/2026/00130</td></tr>
        <tr><td>ROYAL STAG PREMIER WHISKY</td><td>375(24)</td>
            <td>350</td><td>0</td><td>2362.500</td><td>18FL/2026/00174</td></tr>
        <tr><td>Total</td><td></td><td>458</td><td>0</td><td>3091.500</td><td></td></tr>
      </table>"""
    parser = _ReadableHtmlParser()
    parser.feed(html)
    payload = parser.payload()
    assert len(_table_dict_rows(payload["tables"])) == 2
    result = DocumentImportService._qr_document_from_html(None, payload)
    assert len(result.items) == 2
    assert result.invoiceNumber == "PS26150809023"
    assert result.transportPassNo == "PS26150809023"
    assert [(int(x.box), int(x.loose), int(x.ml)) for x in result.items] == [
        (108, 0, 750), (350, 0, 375)]
    assert result.items[0].batchNo == "18FL/2026/00130"
    assert result.items[1].batchNo == "18FL/2026/00174"


def test_older_grouped_qr_tables_still_supported():
    tables = [[
        ["Brand", "Packaging Size", "Cases Dispatched"],
        ["Example Spirit", "180 ML", "2"],
    ]]
    rows = _table_dict_rows(tables)
    assert len(rows) == 1
    assert rows[0]["Brand"] == "Example Spirit"


def test_haryana_product_rows_beyond_legacy_fifty_row_cutoff():
    noise = "".join("<tr><td>Document metadata</td><td>Entry</td></tr>" for _ in range(55))
    html = ("<table>" + noise +
            "<tr><th>Brand Name</th><th>Pack Type</th><th>Number of Cases</th></tr>"
            "<tr><td>ROYAL STAG WHISKY</td><td>750(12)</td><td>20</td></tr></table>")
    parser = _ReadableHtmlParser()
    parser.feed(html)
    payload = parser.payload()
    assert len(payload["tables"]) > 50
    doc = DocumentImportService._qr_document_from_html(None, payload)
    assert len(doc.items) == 1
    assert int(doc.items[0].box) == 20
