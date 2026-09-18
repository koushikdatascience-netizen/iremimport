from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz

from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _clean(value).casefold())


def _number(value: Any) -> int | float | None:
    text = _clean(value).replace(",", "")
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group(0))
    return int(number) if number.is_integer() else number


def _ml(*values: Any) -> int | None:
    for value in values:
        text = _clean(value)
        match = re.search(r"(\d{2,5})\s*m\.?l\.?", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        number = _number(text)
        if isinstance(number, (int, float)) and 30 <= number <= 5000:
            return int(number)
    return None


def _header_value(text: str, *labels: str) -> str | None:
    for label in labels:
        pattern = rf"{label}\s*[:\-]?\s*([^\n\r]+)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _clean(match.group(1))
    return None


def _date_value(text: str) -> str | None:
    raw = _header_value(
        text,
        r"Invoice\s*Date",
        r"Date\s*of\s*Issue",
        r"Permit\s*Date",
        r"Document\s*Date",
        r"Date",
    )
    if not raw:
        return None
    match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})", raw)
    return match.group(1) if match else raw[:40]


def _table_rows(page: fitz.Page) -> list[list[str]]:
    finder = getattr(page, "find_tables", None)
    if not callable(finder):
        return []
    try:
        result = finder()
    except Exception:
        return []

    rows: list[list[str]] = []
    for table in getattr(result, "tables", []) or []:
        try:
            extracted = table.extract()
        except Exception:
            continue
        for row in extracted or []:
            rows.append([_clean(cell) for cell in row])
    return rows


NAME_HEADERS = {
    "labelname",
    "brandname",
    "itemname",
    "productname",
    "description",
    "descriptionofgoods",
    "commodity",
}
QTY_HEADERS = {
    "physicalqty",
    "physicalquantity",
    "quantity",
    "qty",
    "bottles",
    "noofbottles",
    "noofbottlesdispatched",
}
UNIT_HEADERS = {
    "unit",
    "unitname",
    "size",
    "packagingsize",
    "packing",
    "ml",
}


def _products_from_rows(rows: list[list[str]]) -> list[ExtractedProduct]:
    products: list[ExtractedProduct] = []
    header: list[str] | None = None
    name_idx = qty_idx = unit_idx = None

    for cells in rows:
        keys = [_key(cell) for cell in cells]

        detected_name = next((i for i, key in enumerate(keys) if key in NAME_HEADERS), None)
        detected_qty = next((i for i, key in enumerate(keys) if key in QTY_HEADERS), None)
        if detected_name is not None and detected_qty is not None:
            header = cells
            name_idx = detected_name
            qty_idx = detected_qty
            unit_idx = next((i for i, key in enumerate(keys) if key in UNIT_HEADERS), None)
            continue

        if header is None or name_idx is None or qty_idx is None:
            continue
        if len(cells) <= max(name_idx, qty_idx):
            continue

        name = _clean(cells[name_idx])
        quantity = _number(cells[qty_idx])
        if not name or quantity in (None, 0):
            continue
        if _key(name).startswith(("total", "grandtotal", "subtotal")):
            continue

        unit = cells[unit_idx] if unit_idx is not None and len(cells) > unit_idx else ""
        ml = _ml(unit, name)
        raw = {
            (header[i] if i < len(header) and header[i] else f"column{i + 1}"): value
            for i, value in enumerate(cells)
        }
        products.append(
            ExtractedProduct(
                itemName=name,
                brand=name,
                ml=ml,
                quantity=quantity,
                loose=quantity,
                box=0,
                confidence=1,
                **{"rawPdfRow": raw},
            )
        )

    # Some permits repeat the item table on later pages. Keep the first
    # occurrence of each physical line so repeated dispatch tables do not
    # duplicate purchase quantities.
    deduped: list[ExtractedProduct] = []
    seen: set[tuple[str, int | None, str]] = set()
    for product in products:
        key = (
            _key(product.itemName),
            _ml(product.ml, product.itemName),
            str(product.quantity),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(product)
    return deduped


def extract_pdf_locally(file_path: Path) -> tuple[ExtractedDocument | None, dict[str, Any]]:
    """Extract a native text PDF with PyMuPDF.

    Returns (None, diagnostics) when the PDF is scanned or local structured
    extraction is not trustworthy enough, allowing the caller to fall back to
    LlamaParse.
    """
    try:
        document = fitz.open(file_path)
    except Exception as exc:
        return None, {"engine": "pymupdf", "usable": False, "reason": f"open_failed:{exc}"}

    try:
        page_texts: list[str] = []
        all_rows: list[list[str]] = []
        word_count = 0

        for page in document:
            text = page.get_text("text") or ""
            page_texts.append(text)
            try:
                word_count += len(page.get_text("words") or [])
            except Exception:
                pass
            all_rows.extend(_table_rows(page))

        full_text = "\n".join(page_texts)
        text_chars = len(re.sub(r"\s+", "", full_text))
        native_text = text_chars >= 150 and word_count >= 20
        if not native_text:
            return None, {
                "engine": "pymupdf",
                "usable": False,
                "reason": "insufficient_text_layer",
                "textChars": text_chars,
                "wordCount": word_count,
                "pageCount": len(document),
            }

        products = _products_from_rows(all_rows)
        if not products:
            return None, {
                "engine": "pymupdf",
                "usable": False,
                "reason": "no_confident_product_table",
                "textChars": text_chars,
                "wordCount": word_count,
                "pageCount": len(document),
            }

        invoice_number = _header_value(
            full_text,
            r"Invoice\s*No\.?",
            r"Invoice\s*Number",
            r"Permit\s*No\.?",
            r"Excise\s*Permit\s*No\.?",
            r"Document\s*No\.?",
        )
        supplier = _header_value(
            full_text,
            r"Supplier\s*Name",
            r"Consignor\s*Name",
            r"Dealer\s*Name",
            r"Retailer\s*Name",
        )

        extracted = ExtractedDocument(
            documentType="invoice",
            supplierName=supplier,
            invoiceNumber=invoice_number,
            invoiceDate=_date_value(full_text),
            items=products,
            extractionEngine="pymupdf",
            extractionDiagnostics={
                "pageCount": len(document),
                "textChars": text_chars,
                "wordCount": word_count,
                "tableRowCount": len(all_rows),
                "productCount": len(products),
            },
        )
        return extracted, {
            "engine": "pymupdf",
            "usable": True,
            "pageCount": len(document),
            "textChars": text_chars,
            "wordCount": word_count,
            "tableRowCount": len(all_rows),
            "productCount": len(products),
        }
    finally:
        document.close()
