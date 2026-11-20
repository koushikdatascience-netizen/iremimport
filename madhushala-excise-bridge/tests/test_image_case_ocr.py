from io import BytesIO

from PIL import Image

from app.modules.document_import import image_case_ocr
from app.modules.document_import.schemas import ExtractedDocument
from app.modules.document_import.image_invoice_financials import extract_image_invoice_financials


def box(text, x, y):
    return [[[x-5, y-5], [x+5, y-5], [x+5, y+5], [x-5, y+5]], text, .99]


def test_ocr_requires_unique_anchored_case_cells(monkeypatch):
    boxes = [box('SIZE', 100, 50), box('RATE', 150, 50), box('CS', 200, 50), box('BT', 250, 50),
             box('PRODUCT ONE BEER', 30, 80), box('500ml', 100, 80)]
    def engine(image, **kwargs):
        return ([['5', .95]] if kwargs else boxes), None
    monkeypatch.setattr(image_case_ocr, '_engine', engine)
    source = BytesIO()
    Image.new('RGB', (300, 200), 'white').save(source, 'PNG')
    doc = ExtractedDocument(items=[{'itemName': 'PRODUCT ONE BEER', 'ml': 500}])
    result, recovered = image_case_ocr.recover_cases_from_image(source.getvalue(), doc)
    assert recovered == 1 and result.items[0].box == 5
    duplicate = doc.model_copy(update={'items': doc.items * 2})
    result, recovered = image_case_ocr.recover_cases_from_image(source.getvalue(), duplicate)
    assert recovered == 0 and all(item.box is None for item in result.items)
    boxes.append(box('CS', 210, 50))
    assert image_case_ocr.recover_cases_from_image(source.getvalue(), doc)[1] == 0


def test_shared_evidence_avoids_repeating_full_page_ocr(monkeypatch):
    boxes = [box('SIZE', 100, 50), box('RATE', 150, 50), box('CS', 200, 50), box('BT', 250, 50),
             box('PRODUCT ONE BEER', 30, 80), box('500ml', 100, 80)]
    full_page_calls = []
    def engine(image, **kwargs):
        if not kwargs:
            full_page_calls.append(True)
        return ([['5', .95]] if kwargs else boxes), None
    monkeypatch.setattr(image_case_ocr, '_engine', engine)
    source = BytesIO()
    Image.new('RGB', (300, 200), 'white').save(source, 'PNG')
    data = source.getvalue()
    context = image_case_ocr.prepare_image_ocr(data)
    doc = ExtractedDocument(items=[{'itemName': 'PRODUCT ONE BEER', 'ml': 500}])
    result, recovered = image_case_ocr.recover_cases_from_image(data, doc, ocr_context=context)
    result, financials = extract_image_invoice_financials(data, result, ocr_context=context)
    assert full_page_calls == [True]
    assert recovered == 1 and result.items[0].box == 5
    assert financials['status'] == 'unsupported'
