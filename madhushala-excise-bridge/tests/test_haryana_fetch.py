import asyncio
import json
import os

import httpx
import pytest

from app.modules.document_import.haryana_fetch import fetch_haryana_pass, HaryanaFetchError
from app.modules.document_import.service import DocumentImportService
from app.modules.document_import.normalizer import normalize_extracted_document

URL = "https://portal.haryanatax.gov.in/Department/QRGeneration/PassDetails?passheaderid=1010712"
# Synthetic token used only with MockTransport. Live tests obtain the real redirect.
ENC = URL.split("?")[0] + "?enc=mock-token"
HTML = """<div class="card receipt-card">
<p><span class="section-title">Permit Number:</span> PN261599433427</p>
<p><span class="section-title">Pass Number:</span> PS26150809023</p></div>
<div class="card brand-card">
<p><span class="section-title">Brand Name:</span> SAMPLE WHISKY</p>
<p><span class="section-title">Packing Type:</span> 750(12) ml</p>
<p><span class="section-title">No. of Cases:</span> 108</p>
<p><span class="section-title">Loose Bottles:</span> 3</p>
<p><span class="section-title">Liquor Quantity:</span> 729.000 PL</p>
<p><span class="section-title">Batch Number:</span> B/123</p></div>"""

def run(coro):
    return asyncio.run(coro)

async def no_browser(url):
    raise AssertionError("Complete HTTP cards must avoid Chromium")

@pytest.mark.parametrize("url", [URL, ENC])
def test_redirect_and_cookie_session(url):
    def request(req):
        if "passheaderid" in req.url.params:
            return httpx.Response(302, headers={"location": ENC, "set-cookie": "test=ok; Path=/"})
        if url == URL:
            assert req.headers["cookie"] == "test=ok"
        return httpx.Response(200, text=HTML)
    result = run(fetch_haryana_pass(url, transport=httpx.MockTransport(request), renderer=no_browser))
    assert result["finalUrl"] == ENC
    assert result["renderedWithBrowser"] is False
    doc = DocumentImportService._qr_document_from_html(None, result["payload"])
    assert (doc.invoiceNumber, doc.transportPassNo) == ("PN261599433427", "PS26150809023")
    item = normalize_extracted_document(doc, "QR_HTML")[0]
    assert (item.box, item.loose, item.packing, item.ml, item.batchNo) == (108, 3, 12, 750, "B/123")
    assert item.amount is None and item.rate is None
    assert item.rawData["transportPassNo"] == "PS26150809023"
    assert item.rawData["permitNumber"] == "PN261599433427"


def test_delayed_empty_response_retried_then_complete():
    calls = []
    async def request(req):
        calls.append(req)
        await asyncio.sleep(0.01)
        return httpx.Response(200, text="" if len(calls) == 1 else HTML)
    result = run(fetch_haryana_pass(URL, transport=httpx.MockTransport(request), renderer=no_browser))
    assert len(calls) == 2 and result["payload"]["sourceState"] == "HARYANA"


def test_network_timeout_recovers_with_browser():
    def request(req):
        raise httpx.ReadTimeout("mock private URL must not be logged", request=req)
    async def render(url):
        return HTML, ENC
    result = run(fetch_haryana_pass(URL, transport=httpx.MockTransport(request), renderer=render))
    assert result["renderedWithBrowser"] is True


@pytest.mark.parametrize("html,reason", [("", "empty_page"), ("<html>shell</html>", "no_brand_cards"),
                                         ("<p>Access denied</p>", "access_gate"),
                                         (HTML.replace("108</p>", "bad</p>"), "invalid_product_fields"),
                                         (HTML.replace("750(12)", "750(0)"), "invalid_product_fields")])
def test_failures_are_specific_and_never_empty_success(html, reason):
    async def render(url):
        return html, ENC
    with pytest.raises(HaryanaFetchError, match=reason):
        run(fetch_haryana_pass(URL, transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)), renderer=render))


def test_redirect_outside_official_endpoint_is_not_followed():
    seen=[]
    def request(req):
        seen.append(req)
        return httpx.Response(302, headers={"location": "https://example.com/"})
    with pytest.raises(HaryanaFetchError, match="unexpected_redirect"):
        run(fetch_haryana_pass(URL, transport=httpx.MockTransport(request), renderer=no_browser))
    assert len(seen) == 1


def test_shell_uses_browser_cards():
    async def render(url):
        return HTML, ENC
    result = run(fetch_haryana_pass(URL, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="shell")), renderer=render))
    assert result["renderedWithBrowser"] is True


@pytest.mark.skipif(os.environ.get("RUN_HARYANA_BROWSER_TEST") != "1", reason="Production image Chromium smoke")
def test_production_chromium_launches_and_reads_delayed_cards():
    async def check():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True)
            try:
                page = await b.new_page()
                await page.set_content("<body><script>setTimeout(() => document.body.innerHTML = " + json.dumps(HTML) + ", 100)</script></body>")
                await page.wait_for_selector(".brand-card .section-title", timeout=5000)
                assert await page.locator(".brand-card").count() == 1
            finally:
                await b.close()
    run(check())


def test_haryana_identifiers_reach_universal_header(monkeypatch):
    from app.modules.document_import import purchase_required
    from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter
    doc = DocumentImportService._qr_document_from_html(None, __import__(
        'app.modules.document_import.haryana_pass_cards', fromlist=['haryana_card_payload']).haryana_card_payload(HTML))
    item = normalize_extracted_document(doc, 'QR_HTML')[0]
    class Service:
        def get_job(self, *args):
            return {'source_type': 'QR_HTML', 'invoice_number': doc.invoiceNumber}
        def get_items(self, *args):
            return [{'raw_data_json': json.dumps(item.rawData)}]
    def raw_value(job_id, *aliases):
        for alias in aliases:
            if item.rawData.get(alias):
                return str(item.rawData[alias])
        return ''
    monkeypatch.setattr(purchase_required, '_raw_value', raw_value)
    header = run(purchase_required.resolve_required_purchase_header(Service(), {}, 'test', {
        'docNo': 'stale', 'tpPassNo': doc.invoiceNumber,
        'supplierCode': 'S', 'storeCode': 'T', 'purchaseAccCode': 'A', 'userCode': 'U'}))
    assert header['docNo'] == 'PN261599433427'
    assert header['tpPassNo'] == 'PS26150809023'
    assert DocumentPurchaseAdapter(Service()).non_wb_tp_pass({}, 'test') == 'PS26150809023'


def test_service_uses_haryana_fetch_and_persists_all_rows(monkeypatch):
    from app.modules.document_import import haryana_fetch
    from app.modules.document_import.haryana_pass_cards import haryana_card_payload
    async def fetch(url, **kwargs):
        return dict(payload=haryana_card_payload(HTML), finalUrl=ENC, statusCode=200, contentType='text/html')
    monkeypatch.setattr(haryana_fetch, 'fetch_haryana_pass', fetch)
    class Service(DocumentImportService):
        def _create_job(self, *args): return 'job'
        def _update_job(self, job, **fields): self.state = fields
        def _persist_items(self, job, items): self.items = items
        def get_review_items(self, *args): return [{'valid': True} for x in self.items]
        def get_job(self, *args): return {'status': self.state['status']}
    service = Service(None)
    result = run(service.extract_qr_link({}, URL))
    assert result['summary']['detected'] == 1
    assert result['job']['status'] == 'REVIEW_REQUIRED'
    assert result['extractedDocument']['invoiceNumber'] == 'PN261599433427'
    assert service.items[0].rawData['transportPassNo'] == 'PS26150809023'


def test_private_redirect_token_is_not_logged(caplog):
    import logging
    private = URL.split('?')[0] + '?enc=secret-test-value'
    def request(req):
        return httpx.Response(302, headers={'location': private}) if 'passheaderid' in req.url.params else httpx.Response(200, text=HTML)
    with caplog.at_level(logging.INFO):
        run(fetch_haryana_pass(URL, transport=httpx.MockTransport(request), renderer=no_browser))
    assert 'secret-test-value' not in caplog.text
    assert 'haryana_fetch' in caplog.text
