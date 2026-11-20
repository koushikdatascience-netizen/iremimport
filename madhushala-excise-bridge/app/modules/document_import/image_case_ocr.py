"""Read clearly aligned case cells when image extraction omitted quantities."""
from io import BytesIO
from collections import Counter
import re
from threading import Lock

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.modules.document_import.schemas import ExtractedProduct
from app.services.normalizer import parse_ml

_lock = Lock()
_engine = None


def _compact(text):
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def _box(raw):
    points, text, confidence = raw
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return {"text": str(text), "confidence": float(confidence), "x": sum(xs) / len(xs),
            "y": sum(ys) / len(ys), "height": max(ys) - min(ys)}


def recover_cases_from_image(data, document):
    global _engine
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if image.width * image.height > 30_000_000:
            return document, 0
        image = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        output, _ = _engine(image)
        boxes = [_box(row) for row in (output or [])]
        case_headers = [b for b in boxes if _compact(b['text']) == 'CS' and b['confidence'] >= .7]
        bottle_headers = [b for b in boxes if _compact(b['text']) == 'BT' and b['confidence'] >= .7]
        size_headers = [b for b in boxes if _compact(b['text']) == 'SIZE' and b['confidence'] >= .8]
        rate_headers = [b for b in boxes if _compact(b['text']) == 'RATE' and b['confidence'] >= .8]
        if any(len(group) != 1 for group in (case_headers, bottle_headers, size_headers, rate_headers)):
            return document, 0
        case, bottle, size, rate = (g[0] for g in (case_headers, bottle_headers, size_headers, rate_headers))
        if not (size['x'] < rate['x'] < case['x'] < bottle['x']) or max(b['y'] for b in (size, rate, case, bottle)) - min(b['y'] for b in (size, rate, case, bottle)) > image.shape[0] * .03:
            return document, 0
        left = round((rate['x'] + case['x']) / 2 + (bottle['x'] - case['x']) * .17)
        right = round((case['x'] + bottle['x']) / 2) - 2
        rows = [item.model_dump() for item in document.items]
        identities = Counter((_compact(row.get('itemName')), parse_ml(row.get('ml'))) for row in rows)
        updated = 0
        used = set()
        for row in rows:
            if any(row.get(k) not in (None, '', 0, '0') for k in ('box', 'loose', 'quantity')):
                continue
            name = _compact(row.get('itemName'))
            ml = parse_ml(row.get('ml'))
            if len(name) < 8 or not ml or identities[(name, ml)] != 1:
                continue
            names = [b for b in boxes if _compact(b['text']) == name and b['confidence'] >= .8 and b['y'] > size['y'] and b['x'] < size['x']]
            sizes = [b for b in boxes if re.fullmatch(r'\s*\d+\s*M\.?L\.?\s*', b['text'], re.I) and parse_ml(b['text']) == ml and abs(b['x'] - size['x']) < (rate['x'] - size['x']) / 2]
            pairs = [(n, s) for n in names for s in sizes if abs(n['y'] - s['y']) < max(n['height'], s['height'])]
            if len(pairs) != 1:
                continue
            _, anchor = pairs[0]
            if id(anchor) in used:
                continue
            used.add(id(anchor))
            center = anchor['y'] + case['y'] - size['y']
            height = max(8, round(anchor['height'] * .85))
            readings = []
            for scale in (3, 4):
                top = round(center - height / 2)
                if left < 0 or right > image.shape[1] or top < 0 or top + height > image.shape[0] or right <= left:
                    continue
                crop = cv2.resize(image[top:top + height, left:right], None, fx=scale, fy=scale)
                result, _ = _engine(crop, use_det=False, use_cls=False)
                if result and re.fullmatch(r'\d{1,4}', str(result[0][0]).strip()) and float(result[0][1]) >= .8:
                    readings.append((int(result[0][0]), float(result[0][1])))
            if len(readings) == 2 and readings[0][0] == readings[1][0] and readings[0][0] > 0:
                row['box'] = readings[0][0]
                row['imageCaseOcrEvidence'] = {'header': case['text'], 'readings': readings}
                updated += 1
        return document.model_copy(update={'items': [ExtractedProduct.model_validate(row) for row in rows]}), updated
