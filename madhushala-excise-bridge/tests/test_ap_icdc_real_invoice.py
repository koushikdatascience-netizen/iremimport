"""Replay sanitized OCR geometry from the real six-page invoice, not invented rows.

Set AP_ICDC_REAL_PDF to run the original private PDF through the full OCR engine.
The production diagnostic and final API verification also run that actual PDF.
"""
import json
import os
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path(__file__).parent / 'fixtures' / 'ap_icdc_41_production_ocr.json'
AMOUNTS = [17840,10530,10700,6482,12872,133800,7875,8045,17840,26560,7645,
           14022,36005,20652,13230,8817,8095,7927,9005,8291,12927,12330,27536,
           14402,13091,9738,14402,14647,9679,10110,15840,16812,15430,15862,
           12934,13816,18540,39750,29544,7049,20150]
FINANCIALS = dict(issue_price='730822.00',cess='4463.89',additional_previlage_fee='15738.72',
                 additional_retail_excise_tax='76968.00',previlage_fee_clubs='0.00',
                 icdc_value='827992.61',tcs='16560.00',challan_amount='844548.00',
                 previous_credit_leftover='199.65',sub_total='844747.65',
                 less_this_invoice_value='844552.61',amount_credited='195.04')


def assert_invoice(result):
    assert len(result['items']) == 41
    assert [i['sl_no'] for i in result['items']] == list(range(1,42))
    assert [Decimal(str(i['total'])) for i in result['items']] == list(map(Decimal, AMOUNTS))
    assert sum(Decimal(str(i['total'])) for i in result['items']) == Decimal('730822.00')
    assert sum(i['qty_cases'] for i in result['items']) == 190
    assert all(i['qty_bottles'] == 0 for i in result['items'])
    assert result['header']['icdc_number'] == 'ICDC08326281144107'
    assert result['header']['tp_number'] == 'TP08326281144107'
    assert all(Decimal(str(result['summary'][key])) == Decimal(value) for key,value in FINANCIALS.items())
    assert all(result['validation']['checks'].values())
    assert not result['validation']['row_arithmetic_errors']
    assert result['items'][26]['pages'] == [3,4]
    assert result['items'][36]['pages'] == [4,5]


def replay_invoice(monkeypatch, reuse_first=False):
    from app.modules.document_import.ap_icdc import main
    captured = json.loads(FIXTURE.read_text())
    pages = captured['pages']
    grids = iter(pages); panels = iter(pages)
    monkeypatch.setattr(main,'render_pages',lambda *a,**kw:((p['page'],np.zeros((p['height'],p['width'],3),dtype=np.uint8)) for p in pages))
    monkeypatch.setattr(main,'grid',lambda image: (lambda p:(p['xs'],p['ys']))(next(grids)))
    monkeypatch.setattr(main,'ocr_page',lambda engine,image,page,count,**kw:(pages[page-1]['boxes'],{'mode':'full'}))
    monkeypatch.setattr(main,'summary_panel_lines',lambda image:next(panels)['summary_panel_lines'])
    monkeypatch.setattr(main,'reread_serial',lambda *a,**kw: captured['serialCellOcr'])
    class Dummy:
        def __len__(self): return 6
        def __enter__(self): return self
        def __exit__(self,*a): pass
    monkeypatch.setattr('fitz.open',lambda *a,**kw:Dummy())
    first = {**pages[0], 'dpi':250} if reuse_first else None
    from unittest.mock import Mock
    original = main.ocr_page
    monkeypatch.setattr(main,'ocr_page',Mock(side_effect=original))
    return main.extract_pdf('captured-real-invoice.pdf',engine=object(),first_page=first)


def test_real_six_page_geometry_all_rows_and_every_printed_financial(monkeypatch):
    result = replay_invoice(monkeypatch)
    assert_invoice(result)
    assert result['items'][0]['audit']['serialCellOcr']['text'] == '1'


def test_missing_serial_is_not_filled_from_expected_sequence(monkeypatch):
    result = replay_invoice(monkeypatch)
    result['items'][0]['sl_no'] = None
    from app.modules.document_import.ap_icdc.validate import validate_document
    assert validate_document(result['items'],result['summary'])['status'] == 'FAILED'


def test_missing_or_wrong_printed_challan_cannot_pass(monkeypatch):
    result = replay_invoice(monkeypatch)
    from app.modules.document_import.ap_icdc.validate import validate_document
    for value in (None,844549):
        result['summary']['challan_amount'] = value
        validation = validate_document(result['items'],result['summary'])
        assert validation['checks']['funds_subtotal'] is False
        assert validation['status'] == 'FAILED'


@pytest.mark.skipif(not os.environ.get('AP_ICDC_REAL_PDF'),reason='Private real six-page invoice supplied separately')
def test_original_six_page_pdf_with_actual_ocr():
    import hashlib
    from app.modules.document_import.ap_icdc.main import extract_pdf
    path = Path(os.environ['AP_ICDC_REAL_PDF'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == json.loads(FIXTURE.read_text())['sourceSha256']
    assert_invoice(extract_pdf(path))


def test_first_full_resolution_identification_page_is_not_ocred_twice(monkeypatch):
    from app.modules.document_import.ap_icdc import main
    result = replay_invoice(monkeypatch,reuse_first=True)
    assert_invoice(result)
    assert main.ocr_page.call_count == 5
    assert [call.args[2] for call in main.ocr_page.call_args_list] == [2,3,4,5,6]
    assert result['diagnostics']['ocr_modes'][0] == 'full_reused_identification'
