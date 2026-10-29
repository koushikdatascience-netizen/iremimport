"""Bounded retrieval of the verified public Haryana PassDetails endpoint.

Never include URLs, cookies or response text in diagnostics.
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import parse_qs, urlparse

import httpx

from app.modules.document_import.haryana_pass_cards import haryana_card_payload

logger = logging.getLogger("madhushala-excise-bridge.document-import")
_browser_slot = asyncio.Semaphore(1)


class _PrivatePassLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # HTTPX INFO normally logs the complete redirect URL, including enc.
        # Our status/size/timing events replace those entries for this portal.
        return "portal.haryanatax.gov.in" not in record.getMessage().casefold()


logging.getLogger("httpx").addFilter(_PrivatePassLogFilter())


def is_haryana_pass(url: str) -> bool:
    p = urlparse(url)
    return (p.scheme == "https" and p.hostname == "portal.haryanatax.gov.in"
            and p.port in (None, 443) and not p.username and not p.password
            and p.path.rstrip("/").casefold() == "/department/qrgeneration/passdetails")


class HaryanaFetchError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _payload(html: str) -> dict | None:
    payload = haryana_card_payload(html)
    if payload:
        return payload
    if 'brand-card' in html and '<p' in html:
        # A populated but malformed pass must never become a partial import.
        from app.modules.document_import.haryana_pass_cards import _PassCards
        parser = _PassCards()
        parser.feed(html)
        if parser.brands:
            raise HaryanaFetchError("invalid_product_fields")
    return None


def _gate(html: str) -> bool:
    return any(x in html.casefold() for x in
               ("session expired", "please login", "access denied", "captcha", "sign in"))


async def _render(url: str) -> tuple[str, str]:
    from playwright.async_api import async_playwright
    # Bound queue time as well as navigation; at most one browser per API process.
    async with asyncio.timeout(55):
        async with _browser_slot:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, timeout=10000)
                try:
                    page = await browser.new_page()
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    if response and response.status in (401, 403):
                        raise HaryanaFetchError("access_gate")
                    if response and response.status >= 400:
                        raise HaryanaFetchError("upstream_http_error")
                    await page.wait_for_function(
                        "() => [...document.querySelectorAll('.brand-card')].some(c => c.querySelector('p .section-title'))",
                        timeout=10000,
                    )
                    if not is_haryana_pass(page.url):
                        raise HaryanaFetchError("unexpected_redirect")
                    return await page.content(), page.url
                finally:
                    await browser.close()


async def fetch_haryana_pass(url: str, *, job_id: str = "", transport=None, renderer=None) -> dict:
    try:
        async with asyncio.timeout(100):
            return await _fetch_haryana_pass(url, job_id=job_id, transport=transport, renderer=renderer)
    except TimeoutError as exc:
        raise HaryanaFetchError("network_timeout") from exc


async def _fetch_haryana_pass(url: str, *, job_id: str, transport, renderer) -> dict:
    if not is_haryana_pass(url) or not any(parse_qs(urlparse(url).query).get(k) for k in ("enc", "passheaderid")):
        raise HaryanaFetchError("invalid_pass_url")
    started = time.monotonic()
    reason = "empty_page"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), transport=transport) as client:
        for attempt in range(2):
            try:
                # Validate every redirect before following it. Preserve the portal's
                # actual encrypted token and the client's redirect-session cookies.
                current = url
                redirects = 0
                for _ in range(6):
                    response = await client.get(current, headers={"accept": "text/html"})
                    if response.is_redirect:
                        target = str(response.url.join(response.headers.get("location", "")))
                        if not is_haryana_pass(target):
                            raise HaryanaFetchError("unexpected_redirect")
                        current = target
                        redirects += 1
                        continue
                    break
                else:
                    raise HaryanaFetchError("redirect_limit")
                logger.info("haryana_fetch jobId=%s attempt=%s status=%s redirects=%s bytes=%s elapsedMs=%s",
                            job_id, attempt + 1, response.status_code, redirects, len(response.content),
                            round((time.monotonic() - started) * 1000))
                if response.status_code in (401, 403) or _gate(response.text):
                    raise HaryanaFetchError("access_gate")
                if response.status_code >= 400:
                    reason = "upstream_http_error"
                else:
                    payload = _payload(response.text[:2000000])
                    if payload:
                        return dict(payload=payload, finalUrl=str(response.url), statusCode=response.status_code,
                                    contentType=response.headers.get("content-type", ""), renderedWithBrowser=False)
                    reason = "empty_page" if not response.text.strip() else "no_brand_cards"
            except httpx.TimeoutException:
                reason = "network_timeout"
            except httpx.HTTPError:
                reason = "network_error"
            if attempt == 0:
                await asyncio.sleep(0.5)
    try:
        html, final_url = await (renderer or _render)(url)
        if not is_haryana_pass(final_url):
            raise HaryanaFetchError("unexpected_redirect")
        if _gate(html):
            raise HaryanaFetchError("access_gate")
        payload = _payload(html[:2000000])
        if payload:
            logger.info("haryana_fetch jobId=%s browserRows=%s elapsedMs=%s", job_id,
                        len(payload["tables"][0]) - 1, round((time.monotonic() - started) * 1000))
            return dict(payload=payload, finalUrl=final_url, statusCode=200,
                        contentType="text/html", renderedWithBrowser=True)
    except HaryanaFetchError:
        raise
    except Exception as exc:
        logger.warning("haryana_browser jobId=%s errorType=%s priorReason=%s", job_id, type(exc).__name__, reason)
        if reason == "no_brand_cards":
            reason = "browser_timeout" if "Timeout" in type(exc).__name__ else "browser_unavailable"
    raise HaryanaFetchError(reason)
