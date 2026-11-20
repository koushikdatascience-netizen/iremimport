"""Verified printed financial evidence for CS/BT commercial invoice photos."""
from collections import Counter
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.modules.document_import import image_case_ocr as ocr
from app.modules.document_import.schemas import ExtractedProduct
from app.services.normalizer import parse_ml


def _money(value):
    text = str(value).strip().replace(',', '')
    if not re.fullmatch(r'\d+(?:\.\d{1,2})?', text):
        return None
    try:
        return Decimal(text).quantize(Decimal('.01'))
    except InvalidOperation:
        return None


def reconcile(values, amounts):
    required = ('grossAmount', 'discount', 'cashDiscount', 'freight', 'tpFees', 'auxCharges',
                'subTotal', 'vat', 'tcsEcSc', 'adjustment', 'roundedOff', 'totalNet')
    if any(values.get(key) is None for key in required) or any(value is None for value in amounts):
        return False
    return (sum(amounts, Decimal(0)) == values['grossAmount']
            and values['grossAmount'] - values['discount'] - values['cashDiscount']
                + values['freight'] + values['tpFees'] + values['auxCharges'] == values['subTotal']
            and values['subTotal'] + values['vat'] + values['tcsEcSc']
                + values['adjustment'] + values['roundedOff'] == values['totalNet'])


def _read_money(engine, image, bounds):
    left, top, right, bottom = (round(v) for v in bounds)
    if min(left, top) < 0 or right > image.shape[1] or bottom > image.shape[0] or right <= left or bottom <= top:
        return None
    readings = []
    for scale in (3, 4):
        crop = cv2.resize(image[top:bottom, left:right], None, fx=scale, fy=scale)
        result, _ = engine(crop, use_det=False, use_cls=False)
        value = _money(result[0][0]) if result and float(result[0][1]) >= .85 else None
        readings.append(value)
    return readings[0] if readings[0] is not None and readings[0] == readings[1] else None


def extract_image_invoice_financials(data, document):
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        if image.width * image.height > 30_000_000:
            return document, {'status': 'unsupported'}
        image = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    with ocr._lock:
        if ocr._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            ocr._engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        engine = ocr._engine
        result, _ = engine(image)
        boxes = []
        for raw in result or []:
            box = ocr._box(raw)
            box['bounds'] = (min(p[0] for p in raw[0]), min(p[1] for p in raw[0]),
                             max(p[0] for p in raw[0]), max(p[1] for p in raw[0]))
            boxes.append(box)

        def labels(text):
            return [b for b in boxes if ocr._compact(b['text']) == text and b['confidence'] >= (.7 if text == 'CS' else .8)]

        # This ruled summary has two rows of six columns, beneath a CS/BT table.
        # Do not apply its meaning to other state invoice layouts.
        if any(len(labels(key)) != 1 for key in ('TCSECSC', 'ROUNDOFF', 'FREIGHT', 'TPFEES', 'AUXCHARGES', 'ADJAMT', 'CS', 'BT', 'SIZE')):
            return document, {'status': 'unsupported'}
        tax_label = labels('TCSECSC')[0]
        totals = [b for b in labels('TOTAL') if abs(b['y'] - tax_label['y']) < tax_label['height']]
        if len(totals) != 2:
            return document, {'status': 'unverified', 'reason': 'summary_totals_ambiguous'}
        totals.sort(key=lambda b: b['x'])
        upper_total = [b for b in labels('TOTAL') if b['x'] < totals[0]['x'] + 15 and tax_label['y'] - 100 < b['y'] < tax_label['y'] - tax_label['height']]
        vat_labels = [b for b in labels('VAT') if abs(b['y'] - tax_label['y']) < tax_label['height'] and b['x'] < tax_label['x']]
        if len(upper_total) != 1 or len(labels('DISCOUNT')) != 1 or len(labels('CD')) != 1 or len(vat_labels) != 1:
            return document, {'status': 'unverified', 'reason': 'summary_labels_missing'}
        top_headers = [upper_total[0], labels('DISCOUNT')[0], labels('CD')[0], labels('FREIGHT')[0], labels('TPFEES')[0], labels('AUXCHARGES')[0]]
        bottom_headers = [totals[0], vat_labels[0], tax_label, labels('ADJAMT')[0], labels('ROUNDOFF')[0], totals[1]]
        values = {}
        for headers, keys in ((top_headers, ('grossAmount', 'discount', 'cashDiscount', 'freight', 'tpFees', 'auxCharges')),
                              (bottom_headers, ('subTotal', 'vat', 'tcsEcSc', 'adjustment', 'roundedOff', 'totalNet'))):
            if any(a['x'] >= b['x'] for a, b in zip(headers, headers[1:])):
                return document, {'status': 'unverified', 'reason': 'summary_columns_ambiguous'}
            for i, (header, key) in enumerate(zip(headers, keys)):
                width = min(abs(header['x'] - other['x']) for other in headers if other is not header)
                candidates = [b for b in boxes if abs(b['x'] - header['x']) < width * .4
                              and header['height'] * .7 < b['y'] - header['y'] < header['height'] * 2.5
                              and _money(b['text']) is not None and b['confidence'] >= .9]
                if len(candidates) == 1:
                    values[key] = _money(candidates[0]['text'])
                else:
                    # Thin printed zeroes may be omitted by text detection.
                    # Read the actual ruled value cell instead of inventing 0.
                    row_gap = abs(tax_label['y'] - top_headers[2]['y'])
                    center_y = header['y'] + row_gap * .5
                    values[key] = _read_money(engine, image, (header['x'] - width * .35, center_y - row_gap * .18,
                                                            header['x'] + width * .35, center_y + row_gap * .18))
                    if values[key] is None:
                        zero = _read_money(engine, image, (header['x'] - width * .12, center_y - row_gap * .18,
                                                          header['x'] + width * .12, center_y + row_gap * .18))
                        if zero == Decimal(0):
                            values[key] = zero

        size = labels('SIZE')[0]
        bottle = labels('BT')[0]
        items = [item.model_dump() for item in document.items]
        identities = Counter((ocr._compact(row.get('itemName')), parse_ml(row.get('ml'))) for row in items)
        amounts = []
        for row in items:
            name, ml = ocr._compact(row.get('itemName')), parse_ml(row.get('ml'))
            names = [b for b in boxes if ocr._compact(b['text']) == name and b['confidence'] >= .8]
            sizes = [b for b in boxes if re.fullmatch(r'\s*\d+\s*M\.?L\.?\s*', b['text'], re.I)
                     and parse_ml(b['text']) == ml and abs(b['x'] - size['x']) < 20]
            pairs = [(n, s) for n in names for s in sizes if abs(n['y'] - s['y']) < max(n['height'], s['height'])]
            if len(pairs) != 1 or identities[(name, ml)] != 1:
                return document, {'status': 'unverified', 'reason': 'item_identity_ambiguous'}
            anchor = pairs[0][1]
            slope = (bottle['y'] - size['y']) / (bottle['x'] - size['x'])
            candidates = [b for b in boxes if b['x'] > bottle['x'] and b['y'] > size['y']
                          and abs(b['y'] - (anchor['y'] + slope * (b['x'] - anchor['x']))) < anchor['height'] * .65]
            if not candidates:
                return document, {'status': 'unverified', 'reason': 'line_amount_missing'}
            candidate = max(candidates, key=lambda b: b['x'])
            left, top, right, bottom = candidate['bounds']
            amount = _read_money(engine, image, (left - 2, top - 2, right + 2, bottom + 2))
            amounts.append(amount)
        if not reconcile(values, amounts):
            return document, {'status': 'unverified', 'reason': 'printed_totals_do_not_reconcile',
                              'values': {key: float(value) if value is not None else None for key, value in values.items()},
                              'lineAmounts': [float(value) if value is not None else None for value in amounts]}
        financials = {key: float(value) for key, value in values.items()}
        financials.update(totalAmount=financials['grossAmount'], taxes={'vat': financials['vat'], 'tcsEcSc': financials['tcsEcSc']},
                          reconciled=True, validation={'status': 'verified', 'method': 'printed_cell_ocr_and_reconciliation'},
                          calculationMappingRequired=[key for key in ('tcsEcSc', 'cashDiscount', 'freight', 'tpFees', 'auxCharges', 'adjustment') if financials[key]],
                          sourceTaxLabel='TCS+EC+SC')
        for row, amount in zip(items, amounts):
            row.update(invoiceAmount=float(amount), sourceState='MAHARASHTRA', sourceDocumentType='COMMERCIAL_IMAGE_INVOICE',
                       sourceFinancialValidation=financials['validation'], invoiceFinancials=financials)
        return document.model_copy(update={'items': [ExtractedProduct.model_validate(row) for row in items],
                                          'sourceState': 'MAHARASHTRA', 'invoiceFinancials': financials}), {'status': 'verified'}
