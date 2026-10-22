"""Haryana excise pass QR: strict portal routing, no speculative item quantities.

PassDetails is a link to a government page, not an embedded product list.
Only actual recognized item rows may enter the shared mapping/purchase flow.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import HTTPException

from app.modules.document_import.normalizer import normalize_extracted_document
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
from app.modules.document_import.service import _ReadableHtmlParser, _clean_cell, _table_dict_rows, _first_present

ALLOWED_HOST = "portal.haryanatax.gov.in"
ALLOWED_PATH = "/department/qrgeneration/passdetails"
ID_RE = re.compile(r"^[1-9][0-9]{0,17}$")
INT_RE = re.compile(r"^[0-9]+$")


def pass_id_from_url(url: str) -> str | None:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() != "https" or parsed.hostname != ALLOWED_HOST:
        return None
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        return None
    if parsed.path.rstrip("/").casefold() != ALLOWED_PATH:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(k.casefold() for k in query) != {"passheaderid"}:
        return None
    values = next(iter(query.values()), [])
    if len(values) != 1 or not ID_RE.fullmatch(values[0]):
        return None
    return values[0]


def is_haryana_pass_url(url: str) -> bool:
    return pass_id_from_url(url) is not None


def _integer(text):
    raw = _clean_cell(text).replace(",", "")
    return int(raw) if INT_RE.fullmatch(raw) else None


def _size_ml(*values):
    for raw in values:
        value = _clean_cell(raw)
        match = re.search(r"(?<!\d)(\d{2,4})\s*m\.?l\.?\b", value, re.I)
        if match:
            return int(match.group(1))
        if INT_RE.fullmatch(value) and 30 <= int(value) <= 5000:
            return int(value)
    return None


def parse_pass_html(html: str, pass_id: str) -> ExtractedDocument:
    parser = _ReadableHtmlParser()
    parser.feed(html[:1_000_000])
    payload = parser.payload()
    items = []
    for index, row in enumerate(_table_dict_rows([payload.get("tables") or []]), 1):
        name = _clean_cell(_first_present(row, "Brand Name", "Item Name", "Product Name", "Description of Goods", "Description"))
        if not name or name.casefold().startswith(("total", "sub total")):
            continue
        ml = _size_ml(_first_present(row, "Size", "Size (ML)", "Bottle Size", "Packing Size"), name)
        cases = _integer(_first_present(row, "Cases", "Case Qty", "No of Cases", "Qty (Cases)", "Quantity (Cases)"))
        bottles = _integer(_first_present(row, "Loose Bottles", "Qty (Bottles)", "No of Bottles", "Bottle Qty"))
        # The Haryana form's quantity unit is not yet verified. Never assume
        # 'Qty' means cases or bottles, nor infer packing from an amount.
        if ml is None or (cases is None and bottles is None):
            continue
        if (cases or 0) + (bottles or 0) == 0:
            continue
        items.append(ExtractedProduct(
            itemName=name, brand=name, ml=ml, box=cases or 0, loose=bottles or 0,
            confidence=1.0, sourceState="HARYANA", sourcePassId=pass_id,
            sourceRow=index, rawQrRow=row, quantitySemantics="explicit_cases_and_bottles",
        ))
    return ExtractedDocument(
        documentType="transport_pass", supplierName=None,
        invoiceNumber=None, invoiceDate=None, items=items,
        extractionEngine="haryana-qr-html",
        sourceState="HARYANA",
    )


async def extract_haryana_pass(service, session, url: str):
    pass_id = pass_id_from_url(url)
    if pass_id is None:
        raise HTTPException(status_code=400, detail="Unsupported Haryana QR PassDetails URL")
    canonical_url = f"https://{ALLOWED_HOST}/Department/QRGeneration/PassDetails?passheaderid={pass_id}"
    job_id = service._create_job(session, "QR_HTML", canonical_url[:240])
    service._update_job(job_id, status="FETCHING_QR_LINK")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = await client.get(canonical_url, headers={"Accept": "text/html"})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        service._update_job(job_id, status="FAILED", error="Haryana pass portal unavailable")
        raise HTTPException(status_code=502, detail="Haryana pass portal unavailable; no purchase job created") from exc

    # No redirects to non-allowlisted destinations and no error-page parsing.
    if not response.headers.get("content-type", "").lower().startswith("text/html"):
        service._update_job(job_id, status="FAILED", error="Unexpected Haryana portal response")
        raise HTTPException(status_code=422, detail="Haryana portal did not return an HTML pass")
    extracted = parse_pass_html(response.text, pass_id)
    normalized = normalize_extracted_document(extracted, "QR_HTML")
    if not normalized:
        service._update_job(job_id, status="FAILED", error="No verified Haryana item rows")
        raise HTTPException(status_code=422, detail="Haryana QR opened, but item columns/units could not be verified. Mapping and Purchase Save are blocked.")
    service._persist_items(job_id, normalized)
    service._update_job(job_id, status="REVIEW_REQUIRED", document_type="transport_pass",
                        extracted_count=len(normalized), mapped_count=0, error=None)
    rows = service.get_review_items(session, job_id)
    return {"source":"QR_HTML","provider":"HARYANA_EXCISE",
            "passHeaderId":pass_id, "url":canonical_url,
            "job":service.get_job(session, job_id),
            "summary":{"detected":len(rows), "recognized":0, "needMapping":len(rows),
                       "valid":sum(bool(x["valid"]) for x in rows),
                       "needsAttention":sum(not x["valid"] for x in rows),
                       "reviewRequired":True},
            "extractedDocument":extracted.model_dump(),
            "normalizedItems":[x.model_dump() for x in normalized],
            "reviewItems":rows,
            "extracted":{"passHeaderId":pass_id, "tableRows":len(rows)}}
