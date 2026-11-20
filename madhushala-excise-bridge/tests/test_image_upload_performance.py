import asyncio
from threading import Event

import pytest

from app.modules.document_import.schemas import ExtractedDocument


@pytest.mark.parametrize('local_cases', [0, 5])
def test_image_upload_overlaps_primary_ocr_and_retains_fallback(monkeypatch, local_cases):
    from app.modules.document_import import image_case_ocr, image_invoice_financials, service
    prepared = Event()
    fallback_calls = []
    context = object()
    document = ExtractedDocument(items=[{'itemName': 'PRODUCT ONE BEER', 'ml': 500}])

    class Client:
        async def extract_products(self, *args):
            assert await asyncio.to_thread(prepared.wait, 2), 'OCR must overlap primary extraction'
            return document.model_copy(deep=True)

        async def extract_image_quantities(self, *args):
            fallback_calls.append(True)
            return {'items': [{'itemName': 'PRODUCT ONE BEER', 'ml': 500, 'caseHeader': 'CS', 'caseCell': '5'}]}

    def prepare(data):
        prepared.set()
        return context

    def recover(data, doc, *, ocr_context):
        assert ocr_context is context
        doc.items[0].box = local_cases or None
        return doc, int(bool(local_cases))

    def financials(data, doc, *, ocr_context):
        assert ocr_context is context
        assert doc.items[0].box == 5
        return doc, {'status': 'unsupported'}

    monkeypatch.setattr(service, 'LlamaCloudClient', Client)
    monkeypatch.setattr(image_case_ocr, 'prepare_image_ocr', prepare)
    monkeypatch.setattr(image_case_ocr, 'recover_cases_from_image', recover)
    monkeypatch.setattr(image_invoice_financials, 'extract_image_invoice_financials', financials)
    result, meta = asyncio.run(service.DocumentImportService(None)._extract_upload_part(b'image', 'jpg', 'photo.jpg', 1))
    assert result.items[0].box == 5
    assert fallback_calls == ([] if local_cases else [True])
    assert meta['imageFinancialValidation']['status'] == 'unsupported'
