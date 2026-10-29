import asyncio
import threading
import time

import fitz
import pytest

from app.modules.document_import.llama_client import LlamaCloudClient
from app.modules.document_import.schemas import ExtractedDocument


@pytest.mark.asyncio
async def test_jharkhand_financial_pass_overlaps_slow_product_page_without_exceeding_limit(monkeypatch, tmp_path):
    source = fitz.open()
    source.new_page(); source.new_page()
    path = tmp_path / 'two-pages.pdf'
    source.save(path); source.close()
    financial_started = asyncio.Event()
    active = peak = 0
    async def products(self, path, filename, **kwargs):
        nonlocal active, peak
        number = int(filename.rsplit('-', 1)[1].split('.')[0])
        active += 1; peak = max(peak, active)
        try:
            if number == 2:
                await asyncio.wait_for(financial_started.wait(), 2)
            return ExtractedDocument(documentProfile='JHARKHAND_STATE_BEVERAGES', items=[])
        finally:
            active -= 1
    async def financials(self, path, number):
        nonlocal active, peak
        active += 1; peak = max(peak, active)
        financial_started.set()
        await asyncio.sleep(0)
        active -= 1
        return {'items': []}
    monkeypatch.setattr(LlamaCloudClient, 'extract_products', products)
    monkeypatch.setattr(LlamaCloudClient, 'extract_jharkhand_financial_page', financials)
    result = await object.__new__(LlamaCloudClient).extract_scanned_pdf_pages(path, 'scan.pdf')
    assert financial_started.is_set() and peak <= 3
    assert result.sourceFinancialValidation['status'] == 'review_required'


def test_ap_ocr_engine_reused_and_serialized_without_changing_resolution(monkeypatch):
    import rapidocr_onnxruntime
    from app.modules.document_import.ap_icdc import main
    created = []; calls = []
    active = peak = 0
    def factory(**kwargs):
        created.append(kwargs)
        return object()
    def extract(path, dpi, threads, mode, kind, engine):
        nonlocal active, peak
        active += 1; peak = max(peak, active)
        time.sleep(0.02)
        calls.append((dpi, mode, kind, engine))
        active -= 1
        return {'validation': {'status': 'PASSED'}}
    monkeypatch.setattr(rapidocr_onnxruntime, 'RapidOCR', factory)
    monkeypatch.setattr(main, '_ocr_engine', None)
    monkeypatch.setattr(main, '_extract_pdf', extract)
    workers = [threading.Thread(target=main.extract_pdf, args=('test.pdf',)) for _ in range(3)]
    for t in workers: t.start()
    for t in workers: t.join()
    assert len(created) == 1 and peak == 1
    assert all(c[:3] == (250, 'full', 'icdc') for c in calls)
    assert all(c[3] is calls[0][3] for c in calls)


@pytest.mark.asyncio
async def test_local_pdf_extraction_does_not_block_other_requests(monkeypatch):
    from app.modules.document_import import service
    main_thread = threading.get_ident()
    observed = []
    def extract(path):
        observed.append(threading.get_ident())
        return ExtractedDocument(items=[]), {'needsFallback': False}
    monkeypatch.setattr(service, 'extract_pdf_locally', extract)
    await service.DocumentImportService(None)._extract_upload_part(b'%PDF test', 'pdf', 'odisha.pdf', 1)
    assert observed and observed[0] != main_thread

@pytest.mark.asyncio
async def test_financial_second_pass_reuses_identical_uploaded_page(monkeypatch, tmp_path):
    path = tmp_path / 'page.pdf'
    path.write_bytes(b'%PDF identical-page')
    client = object.__new__(LlamaCloudClient)
    client._page_uploads = {}
    uploaded = []
    async def request(method, endpoint, **kwargs):
        uploaded.append(kwargs['files']['file'][1])
        return {'id': 'uploaded-page-1'}
    monkeypatch.setattr(client, '_request', request)
    first = await client.upload_file(path, 'products-page-1.pdf')
    second = await client.upload_file(path, 'financial-page-1.pdf')
    assert first == second == 'uploaded-page-1'
    assert uploaded == [b'%PDF identical-page']


@pytest.mark.asyncio
async def test_failed_page_cancels_requests_before_removing_page_files(monkeypatch, tmp_path):
    source = fitz.open(); source.new_page(); source.new_page()
    path = tmp_path / 'failure.pdf'; source.save(path); source.close()
    started = asyncio.Event(); cancelled = []
    async def products(self, page_path, filename, **kwargs):
        if 'page-1' in filename:
            await started.wait()
            raise ValueError('controlled failure')
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.append(page_path.exists())
    monkeypatch.setattr(LlamaCloudClient, 'extract_products', products)
    client = object.__new__(LlamaCloudClient)
    with pytest.raises(ValueError, match='controlled failure'):
        await client.extract_scanned_pdf_pages(path, 'test.pdf')
    assert cancelled == [True]
    assert client._page_http_client is None and client._page_uploads is None
