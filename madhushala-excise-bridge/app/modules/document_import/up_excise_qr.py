from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import HTTPException

from app.integrations.madhushala.client import MadhushalaApiError
from app.modules.document_import.normalizer import normalize_extracted_document
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct


UP_EXCISE_HOSTS = {"cms.upexciseonline.co"}
UP_TRANSPORT_PATH_SUFFIX = "/transport-pass-tracking"


class _UpExciseHtmlParser(HTMLParser):
    """Extract readable text while preserving the page's table boundaries."""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self.tables: list[list[list[str]]] = []
        self.loose_rows: list[list[str]] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        if tag == "title":
            self._in_title = True
        elif tag == "table":
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = []
        elif tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"}:
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "title":
            self._in_title = False
            return

        if tag in {"td", "th"} and self._current_cell is not None:
            value = " ".join("".join(self._current_cell).split())
            if self._current_row is not None:
                self._current_row.append(value)
            self._current_cell = None
            return

        if tag == "tr" and self._current_row is not None:
            if any(self._current_row):
                if self._current_table is not None:
                    self._current_table.append(self._current_row)
                else:
                    self.loose_rows.append(self._current_row)
            self._current_row = None
            return

        if tag == "table" and self._current_table is not None:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if not clean:
            return
        if self._in_title:
            self.title = f"{self.title} {clean}".strip()
        if self._current_cell is not None:
            self._current_cell.append(data)
        self.text_parts.append(clean)

    def payload(self) -> dict[str, Any]:
        tables = list(self.tables)
        if self._current_table:
            tables.append(self._current_table)
        if self.loose_rows:
            tables.append(self.loose_rows)
        return {
            "title": self.title,
            "text": " ".join(self.text_parts)[:50000],
            "tables": tables[:100],
        }


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _clean(value).casefold())


def _first_present(row: dict[str, Any], *aliases: str) -> Any:
    normalized = {_key(name): value for name, value in row.items()}
    for alias in aliases:
        value = normalized.get(_key(alias))
        if value not in (None, ""):
            return value
    return None


def _int_value(value: Any) -> int:
    match = re.search(r"-?\d+(?:\.\d+)?", _clean(value).replace(",", ""))
    if not match:
        return 0
    try:
        return max(0, int(float(match.group(0))))
    except ValueError:
        return 0


def _parse_ml(*values: Any) -> int | None:
    for value in values:
        text = _clean(value)
        match = re.search(r"(\d{2,5})\s*m\.?l\.?\b", text, re.IGNORECASE)
        if match:
            return _int_value(match.group(1)) or None
        parsed = _int_value(text)
        if 30 <= parsed <= 5000:
            return parsed
    return None


def _normalize_date(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text

    months = {
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "may": "05",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    named = re.fullmatch(r"(\d{1,2})[-/ ]([A-Za-z]{3,})[-/ ](\d{4})", text)
    if named:
        month = months.get(named.group(2)[:3].casefold())
        if month:
            return f"{named.group(3)}-{month}-{int(named.group(1)):02d}"

    numeric = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text)
    if numeric:
        return f"{numeric.group(3)}-{int(numeric.group(2)):02d}-{int(numeric.group(1)):02d}"
    return text


def _coerce_tables(raw_tables: Any) -> list[list[list[str]]]:
    if not isinstance(raw_tables, list) or not raw_tables:
        return []

    # Compatibility with the old parser, which returned one flat list of rows.
    if all(
        isinstance(row, list) and not any(isinstance(cell, list) for cell in row)
        for row in raw_tables
    ):
        return [[[_clean(cell) for cell in row] for row in raw_tables]]

    tables: list[list[list[str]]] = []
    for table in raw_tables:
        if not isinstance(table, list):
            continue
        rows = [
            [_clean(cell) for cell in row]
            for row in table
            if isinstance(row, list)
        ]
        if rows:
            tables.append(rows)
    return tables


def parse_up_transport_url(url: str) -> dict[str, str]:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").casefold()
    path = (parsed.path or "").rstrip("/").casefold()
    if host not in UP_EXCISE_HOSTS or not path.endswith(UP_TRANSPORT_PATH_SUFFIX):
        return {}

    query = parse_qs(parsed.query, keep_blank_values=True)

    def first(name: str) -> str:
        values = query.get(name) or []
        return _clean(values[0]) if values else ""

    tpnum = first("tpnum")
    tptype = first("tptype")
    tpyear = first("tpyear")
    if not (tpnum and tptype and tpyear):
        return {}

    return {
        "transportPassNo": tpnum,
        "transportPassType": tptype.upper(),
        "transportPassYear": tpyear,
    }


def is_up_transport_pass_url(url: str) -> bool:
    return bool(parse_up_transport_url(url))


def _table_metadata(payload: dict[str, Any]) -> dict[str, str]:
    """Read indent metadata from the structured Indent & Dispatch table."""
    meta: dict[str, str] = {}
    for table in _coerce_tables(payload.get("tables")):
        for row_index, row in enumerate(table):
            keys = [_key(cell) for cell in row]
            if "indentno" not in keys and "indentdate" not in keys:
                continue

            data_row = next(
                (
                    candidate
                    for candidate in table[row_index + 1 :]
                    if any(_clean(cell) for cell in candidate)
                    and [_key(cell) for cell in candidate] != keys
                ),
                None,
            )
            if not data_row:
                continue

            for column, header in enumerate(keys):
                if column >= len(data_row):
                    continue
                value = _clean(data_row[column])
                if not value:
                    continue
                if header in {"indentno", "indentnumber"}:
                    meta["indentNo"] = value
                elif header in {"indentdate", "date"}:
                    meta["indentDate"] = _normalize_date(value)

            if meta:
                return meta
    return meta


def _text_metadata(payload: dict[str, Any]) -> dict[str, str]:
    """Fallback only for pages that expose labelled values outside tables."""
    text = _clean(payload.get("text"))
    meta: dict[str, str] = {}
    patterns = {
        # Require a separator so a header sequence like
        # "Indent No. Indent Date" cannot be mistaken for a value.
        "indentNo": r"Indent\s*No\.?\s*[:\-]\s*([A-Za-z0-9_./\-]+)",
        "indentDate": r"Indent\s*Date\s*[:\-]\s*(\d{1,2}[-/]\w{3,}[-/]\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}-\d{2}-\d{2})",
        "invoiceNo": r"Invoice\s*No\.?\s*[:\-]\s*([A-Za-z0-9_./\-]+)",
        "invoiceDate": r"Dated\s*[:\-]\s*(\d{1,2}[-/]\w{3,}[-/]\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4}|\d{4}-\d{2}-\d{2})",
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        value = _clean(match.group(1))
        meta[name] = _normalize_date(value) if name.endswith("Date") else value
    return meta


def _metadata(payload: dict[str, Any]) -> dict[str, str]:
    structured = _table_metadata(payload)
    fallback = _text_metadata(payload)
    return {**fallback, **structured}



def _looks_like_product_row(row: dict[str, Any]) -> bool:
    keys = {_key(name) for name in row.keys()}
    has_name = bool(
        keys
        & {
            "brand",
            "description",
            "descriptionofgoods",
            "itemname",
            "productname",
            "brandname",
        }
    )
    has_shape = any(
        token in key
        for key in keys
        for token in (
            "packagingsize",
            "packagingtype",
            "case",
            "bottle",
            "quantity",
            "amount",
            "rate",
            "mrp",
            "bulk",
        )
    )
    return has_name and has_shape


def _iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_dicts(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _json_candidates(text: str) -> Iterable[Any]:
    decoder = json.JSONDecoder()
    clean = str(text or "")[:50000]
    for index, char in enumerate(clean):
        if char not in "[{":
            continue
        nearby = clean[index : index + 1200].casefold()
        if not any(marker in nearby for marker in ("brand", "packaging", "bottle", "case", "quantity")):
            continue
        try:
            value, _ = decoder.raw_decode(clean[index:])
        except Exception:
            continue
        yield value


def _embedded_product_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for candidate in _json_candidates(str(payload.get("text") or "")):
        for row in _iter_dicts(candidate):
            if not _looks_like_product_row(row):
                continue
            clean_row = {str(key): _clean(value) for key, value in row.items() if _clean(value)}
            signature = tuple(sorted(clean_row.items()))
            if signature in seen:
                continue
            seen.add(signature)
            found.append(clean_row)
    return found


def _product_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for table in _coerce_tables(payload.get("tables")):
        header: list[str] | None = None
        for raw_row in table:
            cells = [_clean(cell) for cell in raw_row]
            keys = [_key(cell) for cell in cells]
            has_name = any(
                key in {"brand", "description", "descriptionofgoods", "itemname", "productname"}
                for key in keys
            )
            has_product_shape = has_name and any(
                token in key
                for key in keys
                for token in (
                    "packagingsize",
                    "packagingtype",
                    "case",
                    "bottle",
                    "quantity",
                    "amount",
                    "rate",
                    "mrp",
                )
            )
            if has_product_shape:
                header = cells
                continue
            if not header or len(cells) < 3:
                continue

            values = cells[: len(header)]
            values.extend([""] * (len(header) - len(values)))
            row = {
                header[index] or f"column{index + 1}": values[index]
                for index in range(len(header))
            }
            name = _first_present(
                row,
                "Brand",
                "Description",
                "Description of Goods",
                "Item Name",
                "Product Name",
            )
            if not _clean(name):
                continue
            if _key(name).startswith(("total", "tcspayable", "roundoff")):
                continue
            rows.append(row)
    if rows:
        return rows
    return _embedded_product_rows(payload)


def _document_from_payload(
    payload: dict[str, Any],
    transport_meta: dict[str, str],
) -> ExtractedDocument:
    meta = {**_metadata(payload), **transport_meta}
    items: list[ExtractedProduct] = []

    for index, row in enumerate(_product_rows(payload), start=1):
        name = _clean(
            _first_present(
                row,
                "Brand",
                "Description",
                "Description of Goods",
                "Item Name",
                "Product Name",
            )
        )
        package_size = _first_present(
            row,
            "Packaging Size",
            "Packing Size",
            "Size",
            "ML",
            "Measure ML",
        )
        package_type = _clean(
            _first_present(row, "Packaging Type", "Package Type", "Packing Type")
        )
        ml = _parse_ml(package_size, name)

        boxes = _first_present(
            row,
            "No of Cases/ Monocartons Dispatched",
            "No of Cases / Monocartons Dispatched",
            "No of Cases / Mono Cartons Dispatched",
            "No of Cases Dispatched",
            "Cases Dispatched",
            "No of Cases/ Monocartons Requested",
            "No of Cases / Monocartons Requested",
            "No of Cases / Mono Cartons Requested",
            "No of Cases Requested",
            "Quantity",
            "Qty",
        )
        bottles = _first_present(
            row,
            "No of Bottles Dispatched",
            "Bottles Dispatched",
            "No of Bottles Requested",
            "Bottles Requested",
        )
        box_count = _int_value(boxes)
        bottle_count = _int_value(bottles)
        packing = (
            bottle_count // box_count
            if box_count and bottle_count and bottle_count % box_count == 0
            else None
        )
        bulk_litres = _first_present(
            row,
            "BULK LITRES",
            "Bulk Litres",
            "No of Bulk Litre",
            "Total Bulk Litres",
        )

        extracted_quantity = bottle_count or box_count or boxes
        extracted_box = 0 if bottle_count else (box_count or boxes)
        extracted_loose = bottle_count or None

        raw = {
            **meta,
            **row,
            "packageType": package_type,
            "measureMl": ml,
            "packagingSize": _clean(package_size),
            "box": extracted_box,
            "loose": extracted_loose,
            "quantity": extracted_quantity,
            "qnty": bottle_count or bottles,
            "bulkLitres": _clean(bulk_litres),
            "liquorType": _clean(_first_present(row, "Liquor Type")),
            "liquorSubType": _clean(
                _first_present(row, "Liquor Sub Type", "Liquor Subtype")
            ),
            "sourceRow": index,
        }
        excluded = {
            "itemName",
            "brand",
            "ml",
            "packing",
            "quantity",
            "box",
            "confidence",
        }
        extras = {key: value for key, value in raw.items() if key not in excluded}

        items.append(
            ExtractedProduct(
                itemName=name,
                brand=name,
                ml=ml,
                packing=packing,
                quantity=extracted_quantity,
                box=extracted_box,
                loose=extracted_loose,
                confidence=1,
                **extras,
            )
        )

    return ExtractedDocument(
        documentType="stock_list",
        supplierName=None,
        invoiceNumber=(
            meta.get("indentNo")
            or meta.get("invoiceNo")
            or meta.get("transportPassNo")
        ),
        invoiceDate=meta.get("indentDate") or meta.get("invoiceDate"),
        transportPassNo=meta.get("transportPassNo"),
        transportPassType=meta.get("transportPassType"),
        transportPassYear=meta.get("transportPassYear"),
        items=items,
    )


def _payload_from_html(html: str) -> dict[str, Any]:
    parser = _UpExciseHtmlParser()
    parser.feed(html[:1000000])
    return parser.payload()


async def _fetch_up_transport_page(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={"accept": "text/html,application/json,*/*"},
        )
        response.raise_for_status()
        return {
            "text": response.text,
            "contentType": response.headers.get("content-type", ""),
            "finalUrl": str(response.url),
            "statusCode": response.status_code,
        }


async def _render_up_transport_page(url: str) -> tuple[str, str]:
    """Render UP Excise patiently; its public page often fills rows late via JavaScript."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            # Production already runs under Xvfb. A headed browser is more reliable
            # with this legacy public portal, while tests/local shells can stay headless.
            headless=not bool(os.environ.get("DISPLAY")),
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
        )
        try:
            page = await context.new_page()
            last_html = ""
            last_url = url

            # UP Excise intermittently returns only the empty table shell. Give the
            # same public URL a few fresh browser loads before treating it as empty.
            for attempt in range(3):
                try:
                    if attempt == 0:
                        await page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=45000,
                        )
                    else:
                        await page.wait_for_timeout(1000 * attempt)
                        await page.reload(
                            wait_until="domcontentloaded",
                            timeout=45000,
                        )
                except Exception:
                    if attempt == 2:
                        raise
                    continue

                last_url = page.url
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    # The portal keeps background connections open on some loads.
                    pass

                # Do not rely on one brittle DOM selector. Parse successive DOM
                # snapshots with the same product parser used by the import flow.
                for _ in range(20):
                    last_html = await page.content()
                    if _product_rows(_payload_from_html(last_html)):
                        return last_html, page.url
                    await page.wait_for_timeout(1000)

            if not last_html:
                last_html = await page.content()
            return last_html, last_url
        finally:
            await context.close()
            await browser.close()

async def extract_up_transport_pass(
    service: Any,
    session: dict[str, Any],
    url: str,
) -> dict[str, Any]:
    clean_url = str(url or "").strip()
    transport_meta = parse_up_transport_url(clean_url)
    if not transport_meta:
        raise HTTPException(
            status_code=400,
            detail="QR is not a supported UP Excise transport-pass URL",
        )

    job_id = service._create_job(session, "QR_HTML", clean_url[:240])
    rendered_with_browser = False

    try:
        service._update_job(job_id, status="FETCHING_QR_LINK")
        fetched = await _fetch_up_transport_page(clean_url)
    except httpx.HTTPStatusError as exc:
        error = exc.response.text[:500] or "UP Excise QR link request failed"
        service._update_job(job_id, status="FAILED", error=error)
        raise HTTPException(status_code=exc.response.status_code, detail=error) from exc
    except httpx.HTTPError as exc:
        service._update_job(job_id, status="FAILED", error=str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"UP Excise QR link could not be opened: {exc}",
        ) from exc

    body = _payload_from_html(fetched["text"])
    extracted = _document_from_payload(body, transport_meta)

    # The public UP Excise page exposes the transport table shell in static HTML,
    # but may fill the actual pass rows with JavaScript. Only pay the browser cost
    # if the deterministic HTML parse finds no products.
    if not extracted.items:
        service._update_job(job_id, status="RENDERING_QR_LINK")
        try:
            rendered_html, rendered_url = await _render_up_transport_page(clean_url)
            rendered_body = _payload_from_html(rendered_html)
            rendered_document = _document_from_payload(rendered_body, transport_meta)
            if rendered_document.items:
                body = rendered_body
                extracted = rendered_document
                fetched["finalUrl"] = rendered_url
                rendered_with_browser = True
        except Exception as exc:
            fetched["renderError"] = str(exc)

    service._update_job(
        job_id,
        status="NORMALIZING",
        document_type=extracted.documentType,
        invoice_number=extracted.invoiceNumber,
        invoice_date=extracted.invoiceDate,
    )
    normalized = normalize_extracted_document(extracted, "QR_HTML")
    if not normalized:
        detail = "UP Excise did not populate product rows after automatic browser retries"
        service._update_job(job_id, status="FAILED", error=detail)
        raise HTTPException(status_code=422, detail=detail)

    service._persist_items(job_id, normalized)
    service._update_job(
        job_id,
        status="CHECKING_MAPPING",
        extracted_count=len(normalized),
    )

    try:
        await service.mapping_service.prepare_document_job(session, job_id)
        workspace = await service.mapping_service.workspace_for_session(
            session,
            job_id=job_id,
        )
    except MadhushalaApiError as exc:
        service._update_job(job_id, status="FAILED", error=str(exc))
        status_code = 401 if exc.status_code == 401 else 403 if exc.status_code == 403 else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    mapping_rows = workspace.get("unmappedItems", [])
    unmapped_count = sum(1 for row in mapping_rows if not row.get("selectedItemCode"))
    status = "MAPPING_REQUIRED" if unmapped_count else "READY"
    service._update_job(
        job_id,
        status=status,
        mapped_count=len(normalized) - unmapped_count,
    )

    return {
        "source": "QR_HTML",
        "provider": "UP_EXCISE_IESCMS",
        "url": clean_url,
        "finalUrl": fetched.get("finalUrl") or clean_url,
        "statusCode": fetched.get("statusCode") or 200,
        "contentType": fetched.get("contentType") or "text/html",
        "renderedWithBrowser": rendered_with_browser,
        "transportPass": transport_meta,
        "job": service.get_job(session, job_id),
        "summary": {
            "detected": len(normalized),
            "recognized": len(normalized) - unmapped_count,
            "needMapping": unmapped_count,
        },
        "extractedDocument": extracted.model_dump(),
        "normalizedItems": [item.model_dump() for item in normalized],
        "extracted": body,
    }
