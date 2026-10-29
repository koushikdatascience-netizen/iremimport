"""Regression based on the actual Haryana Pass Details.html card structure."""
from app.modules.document_import.haryana_pass_cards import haryana_card_payload
from app.modules.document_import.service import DocumentImportService
from app.modules.document_import.normalizer import normalize_extracted_document


def test_real_haryana_card_layout_preserves_four_rows_and_receipt_numbers():
    html = """<!doctype html><html><body>
    <div class="card receipt-card">
      <p><span class="section-title">Permit Number:</span> PN261599433427</p>
      <p><span class="section-title">Pass Number:</span> PS26150809023</p>
    </div>
    <div class="card brand-card">
      <p><span class="section-title">Brand Name:</span> BLENDERS PRIDE ULTRA PREMIUM WHISKY</p>
      <p><span class="section-title">Packing Type:</span> 750(12) ml</p>
      <p><span class="section-title">No. of Cases:</span> 108</p>
      <p><span class="section-title">Loose Bottles:</span> 0</p>
      <p><span class="section-title">Liquor Quantity:</span> 729.000 PL</p>
      <p><span class="section-title">Batch Number:</span> 18FL/2026/00130, 18FL/2026/00133</p>
    </div>
    <div class="card brand-card">
      <p><span class="section-title">Brand Name:</span> SEAGRAMS BLENDERS PRIDE FOUR ELEMENTS PREMIUM WHISKY</p>
      <p><span class="section-title">Packing Type:</span> 750(12) ml</p>
      <p><span class="section-title">No. of Cases:</span> 50</p>
      <p><span class="section-title">Loose Bottles:</span> 0</p>
      <p><span class="section-title">Batch Number:</span> 18FL/2026/00038</p>
    </div>
    <div class="card brand-card">
      <p><span class="section-title">Brand Name:</span> ROYAL STAG PREMIER WHISKY</p>
      <p><span class="section-title">Packing Type:</span> 375(24) ml</p>
      <p><span class="section-title">No. of Cases:</span> 350</p>
      <p><span class="section-title">Loose Bottles:</span> 0</p>
      <p><span class="section-title">Batch Number:</span> 18FL/2026/00174</p>
    </div>
    <div class="card brand-card">
      <p><span class="section-title">Brand Name:</span> ROYAL STAG PREMIER WHISKY</p>
      <p><span class="section-title">Packing Type:</span> 180(48) ml</p>
      <p><span class="section-title">No. of Cases:</span> 200</p>
      <p><span class="section-title">Loose Bottles:</span> 0</p>
      <p><span class="section-title">Batch Number:</span> 18FL/2026/00172</p>
    </div></body></html>"""
    assert "<table" not in html
    payload = haryana_card_payload(html)
    assert payload is not None
    doc = DocumentImportService._qr_document_from_html(None, payload)
    assert doc.invoiceNumber == "PS26150809023"
    assert doc.transportPassNo == "PS26150809023"
    assert len(doc.items) == 4
    assert [(i.itemName, int(i.ml), int(i.box), int(i.loose)) for i in doc.items] == [
        ("BLENDERS PRIDE ULTRA PREMIUM WHISKY", 750, 108, 0),
        ("SEAGRAMS BLENDERS PRIDE FOUR ELEMENTS PREMIUM WHISKY", 750, 50, 0),
        ("ROYAL STAG PREMIER WHISKY", 375, 350, 0),
        ("ROYAL STAG PREMIER WHISKY", 180, 200, 0),
    ]
    assert len(normalize_extracted_document(doc, "QR_HTML")) == 4
    assert doc.items[0].batchNo == "18FL/2026/00130, 18FL/2026/00133"
    assert payload["text"].find("PN261599433427") > 0


def test_card_page_missing_cases_is_not_fabricated():
    html = """<div class="brand-card">
    <p><span>Brand Name:</span> SAMPLE WHISKY</p>
    <p><span>Packing Type:</span> 750(12) ml</p>
    <p><span>Loose Bottles:</span> 0</p>
    </div>"""
    assert haryana_card_payload(html) is None
