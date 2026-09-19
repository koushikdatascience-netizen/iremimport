from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz

from app.modules.document_import.quantity import resolve_case_loose
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


def _int(value: Any) -> int:
    number = _number(value)
    try:
        return max(0, int(float(number or 0)))
    except (TypeError, ValueError):
        return 0


def _jharkhand_case_loose(value: Any) -> tuple[int, int]:
    """Decode Jharkhand Quantity (Cases) values like 17.20 as 17 cases + 20 loose.

    The decimal point is a separator in this document format, not a fractional
    case. Preserve the printed two-digit suffix as loose bottles.
    """
    text = _clean(value).replace(",", "")
    match = re.search(r"(\d+)\.(\d{1,2})", text)
    if match:
        return max(0, int(match.group(1))), max(0, int(match.group(2)))
    return _int(text), 0


def _ml(*values: Any) -> int | None:
    for value in values:
        text = _clean(value)
        match = re.search(r"(\d{2,5})\s*m\.?l\.?", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _capacity_ml(value: Any) -> int | None:
    """Extract bottle capacity when the column itself defines the unit.

    MP Delivery Challans commonly print values like "180 (Pet Bottle)" without
    the literal letters "ML". In a Capacity column, the leading numeric value
    is the container capacity in millilitres.
    """
    text = _clean(value).replace(",", "")
    match = re.search(r"(?<!\d)(\d{2,5})(?!\d)", text)
    if not match:
        return None
    number = int(match.group(1))
    return number if 30 <= number <= 5000 else None


def _normalize_date(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    match = re.search(
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[-/ ][A-Za-z]{3,}[-/ ]\d{4})",
        text,
        re.IGNORECASE,
    )
    return match.group(1) if match else text[:40]


def _header_value(text: str, *labels: str) -> str | None:
    for label in labels:
        pattern = rf"{label}\s*[:\-]?\s*([^\n\r]+)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _clean(match.group(1))
    return None


def _dedupe(products: list[ExtractedProduct]) -> list[ExtractedProduct]:
    deduped: list[ExtractedProduct] = []
    seen: set[tuple[Any, ...]] = set()
    for product in products:
        payload = product.model_dump()
        raw = payload.get("rawPdfRow") if isinstance(payload.get("rawPdfRow"), dict) else {}
        source_page = payload.get("sourcePage")
        source_row = raw.get("column1") or raw.get("S.No") or raw.get("Sr No") or raw.get("Sl No")
        # Include physical row identity so two legitimate repeated invoice
        # lines are never collapsed merely because product/ML/quantity match.
        # product.ml has already been normalized by _product(). Do not pass
        # the numeric value back through _ml(), because _ml() expects text with
        # an explicit "ML" suffix. Doing so turned 180/375/750 into None and
        # collapsed same-name, same-quantity size variants as duplicates.
        product_ml = _int(product.ml)
        if product_ml <= 0:
            product_ml = _ml(product.itemName) or 0

        signature = (
            source_page,
            str(source_row or "").strip(),
            _key(product.itemName),
            product_ml,
            _int(product.box),
            _int(product.loose),
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(product)
    return deduped


def _product(
    *,
    name: Any,
    ml: Any,
    box: Any = 0,
    loose: Any = 0,
    raw: dict[str, Any],
    state: str,
    page: int,
    semantics: str,
) -> ExtractedProduct | None:
    clean_name = _clean(name)
    measure = _ml(ml, clean_name)
    cases = _int(box)
    bottles = _int(loose)
    if not clean_name or not measure or (cases <= 0 and bottles <= 0):
        return None
    return ExtractedProduct(
        itemName=clean_name,
        brand=clean_name,
        ml=measure,
        box=cases,
        loose=bottles,
        quantity=None,
        packing=None,
        confidence=1,
        **{
            "sourcePage": page,
            "sourceState": state,
            "quantitySemantics": semantics,
            "rawPdfRow": raw,
        },
    )


def _page_tables(page: fitz.Page) -> list[list[list[str]]]:
    finder = getattr(page, "find_tables", None)
    if not callable(finder):
        return []
    try:
        found = finder()
    except Exception:
        return []
    output: list[list[list[str]]] = []
    for table in getattr(found, "tables", []) or []:
        try:
            rows = table.extract()
        except Exception:
            continue
        clean_rows = [
            [_clean(cell) if cell is not None else "" for cell in row]
            for row in (rows or [])
        ]
        if clean_rows:
            output.append(clean_rows)
    return output


def _detect_profile(text: str) -> str:
    lower = text.casefold()
    if "government of telangana" in lower and ("invoice cum delivery challan" in lower or "icdc" in lower):
        return "TELANGANA_ICDC"
    if "west bengal excise foreign" in lower or "wbexcise.gov.in" in lower:
        return "WEST_BENGAL_FORM3"
    if "government of jharkhand" in lower or "jharkhand state beverages corporation" in lower:
        return "JHARKHAND_EXCISE"
    if "madhya pradesh excise department" in lower and "delivery challan" in lower:
        return "MADHYA_PRADESH_DELIVERY_CHALLAN"
    return "GENERIC"


def _extract_telangana(
    full_text: str,
    pages: list[tuple[int, str, list[list[list[str]]]]],
) -> tuple[list[ExtractedProduct], str | None, str | None]:
    products: list[ExtractedProduct] = []

    for page_no, _page_text, page_tables in pages:
        for rows in page_tables:
            for cells in rows:
                if not cells or not re.fullmatch(r"\d+", _clean(cells[0])):
                    continue

                pack_index = next(
                    (
                        index
                        for index, cell in enumerate(cells)
                        if re.search(r"\d+\s*/\s*\d+\s*m\.?l\.?", _clean(cell), re.IGNORECASE)
                    ),
                    None,
                )
                if pack_index is None:
                    continue

                integer_cells: list[int] = []
                for cell in cells[pack_index + 1 :]:
                    text = _clean(cell).replace(",", "")
                    if re.fullmatch(r"\d+(?:\.0+)?", text):
                        integer_cells.append(_int(text))
                    if len(integer_cells) >= 2:
                        break
                if len(integer_cells) < 2:
                    continue

                name_candidates = [
                    _clean(cell)
                    for cell in cells[1:pack_index]
                    if re.search(r"[A-Za-z]{3}", _clean(cell))
                    and _key(cell) not in {"iml", "g", "p", "c", "beer", "dutypaid"}
                ]
                if not name_candidates:
                    continue
                name = max(name_candidates, key=len)
                cases, loose = resolve_case_loose(integer_cells[0], integer_cells[1])
                raw = {f"column{index + 1}": value for index, value in enumerate(cells)}
                raw.update(
                    {
                        "Pack Qty / Size (ml)": cells[pack_index],
                        "Qty(Cases Delivered)": cases,
                        "Qty(Bottles Delivered)": loose,
                    }
                )
                item = _product(
                    name=name,
                    ml=cells[pack_index],
                    box=cases,
                    loose=loose,
                    raw=raw,
                    state="TELANGANA",
                    page=page_no,
                    semantics="cases_plus_loose_bottles",
                )
                if item:
                    products.append(item)

    invoice_number = None
    number_match = re.search(r"\bICDC\d{8,}\b", full_text, re.IGNORECASE)
    if number_match:
        invoice_number = number_match.group(0)

    date_match = re.search(r"Invoice\s*Date\s*:\s*([^\n\r]+)", full_text, re.IGNORECASE)
    invoice_date = _normalize_date(date_match.group(1)) if date_match else None
    return _dedupe(products), invoice_number, invoice_date


def _extract_jharkhand(
    full_text: str,
    pages: list[tuple[int, str, list[list[list[str]]]]],
) -> tuple[list[ExtractedProduct], str | None, str | None]:
    products: list[ExtractedProduct] = []
    column_map: tuple[int, int, int] | None = None

    for page_no, _page_text, page_tables in pages:
        for rows in page_tables:
            for cells in rows:
                keys = [_key(cell) for cell in cells]
                if "labelname" in keys and "qty" in keys:
                    name_idx = keys.index("labelname")
                    qty_idx = keys.index("qty")
                    unit_idx = keys.index("unit") if "unit" in keys else -1
                    column_map = (name_idx, qty_idx, unit_idx)
                    continue

                if (
                    column_map
                    and cells
                    and re.fullmatch(r"\d+", _clean(cells[0]))
                ):
                    name_idx, qty_idx, unit_idx = column_map
                    if len(cells) <= max(name_idx, qty_idx):
                        continue
                    name = cells[name_idx]
                    raw_qty = cells[qty_idx]
                    cases, loose = _jharkhand_case_loose(raw_qty)
                    unit = cells[unit_idx] if unit_idx >= 0 and len(cells) > unit_idx else ""
                    if not name or (cases <= 0 and loose <= 0):
                        continue
                    raw = {f"column{index + 1}": value for index, value in enumerate(cells)}
                    raw.update(
                        {
                            "Label Name": name,
                            "Unit": unit,
                            "Qty": raw_qty,
                            "Quantity (Cases)": raw_qty,
                            "decodedCases": cases,
                            "decodedLoose": loose,
                        }
                    )
                    item = _product(
                        name=name,
                        ml=unit,
                        box=cases,
                        loose=loose,
                        raw=raw,
                        state="JHARKHAND",
                        page=page_no,
                        semantics="jharkhand_cases_dot_loose",
                    )
                    if item:
                        products.append(item)

    invoice_number = (
        _header_value(full_text, r"Excise\s*Permit\s*No\.?", r"Registered\s*Permit\s*No\.?", r"Invoice\s*No\.?")
        or None
    )
    date_match = re.search(
        r"(?:Issued\s*Date|Date\s*of\s*Issue|Invoice\s*Date)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}-\d{2}-\d{2})",
        full_text,
        re.IGNORECASE,
    )
    invoice_date = _normalize_date(date_match.group(1)) if date_match else None
    return _dedupe(products), invoice_number, invoice_date


def _extract_west_bengal(
    full_text: str,
    pages: list[tuple[int, str, list[list[list[str]]]]],
) -> tuple[list[ExtractedProduct], str | None, str | None]:
    products: list[ExtractedProduct] = []

    original_pages = [
        entry
        for entry in pages
        if re.search(r"\bORIGINAL\b", entry[1], re.IGNORECASE)
        and not re.search(r"\b(?:DUPLICATE|TRIPLICATE|QUADRUPLICATE)\b", entry[1], re.IGNORECASE)
    ]
    scan_pages = original_pages[:1] if original_pages else pages[:1]

    for page_no, _page_text, page_tables in scan_pages:
        for rows in page_tables:
            column_map: tuple[int, int, int, int] | None = None
            skip_next_header = False
            for row_index, cells in enumerate(rows):
                keys = [_key(cell) for cell in cells]
                has_brand_header = any("brandname" in value for value in keys)
                if has_brand_header:
                    next_row = rows[row_index + 1] if row_index + 1 < len(rows) else []
                    combined = [
                        f"{_key(cell)} {_key(next_row[index]) if index < len(next_row) else ''}".strip()
                        for index, cell in enumerate(cells)
                    ]
                    name_idx = next((i for i, value in enumerate(combined) if "brandname" in value), None)
                    unit_idx = next((i for i, value in enumerate(combined) if "measure" in value), None)
                    case_idx = next((i for i, value in enumerate(combined) if "incases" in value), None)
                    bottle_idx = next((i for i, value in enumerate(combined) if "inbottles" in value), None)
                    if None not in (name_idx, unit_idx, case_idx, bottle_idx):
                        column_map = (int(name_idx), int(unit_idx), int(case_idx), int(bottle_idx))
                        skip_next_header = True
                    continue

                if skip_next_header:
                    skip_next_header = False
                    continue
                if not column_map or not cells:
                    continue

                name_idx, unit_idx, case_idx, bottle_idx = column_map
                if len(cells) <= max(column_map):
                    continue
                category = _clean(cells[0]).upper()
                if category not in {"IMFL", "OSBI", "OS"}:
                    continue

                name = cells[name_idx]
                cases, bottles = resolve_case_loose(cells[case_idx], None)
                if not name or (cases <= 0 and bottles <= 0):
                    continue

                raw = {f"column{index + 1}": value for index, value in enumerate(cells)}
                raw.update(
                    {
                        "Brand Name": name,
                        "Measure": cells[unit_idx],
                        "In Cases": cells[case_idx],
                        "In Bottles": cells[bottle_idx],
                    }
                )
                item = _product(
                    name=name,
                    ml=cells[unit_idx],
                    box=cases,
                    loose=bottles,
                    raw=raw,
                    state="WEST_BENGAL",
                    page=page_no,
                    semantics="west_bengal_case_field_only",
                )
                if item:
                    products.append(item)

    number_match = re.search(r"Transport\s*Pass\s*No\.?\s*:\s*([^\n\r]+)", full_text, re.IGNORECASE)
    invoice_number = _clean(number_match.group(1)) if number_match else None
    date_match = re.search(r"\bDate\s*:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})", full_text, re.IGNORECASE)
    invoice_date = _normalize_date(date_match.group(1)) if date_match else None
    return _dedupe(products), invoice_number, invoice_date


def _extract_madhya_pradesh(
    full_text: str,
    pages: list[tuple[int, str, list[list[list[str]]]]],
) -> tuple[list[ExtractedProduct], str | None, str | None]:
    """Madhya Pradesh Excise Department Delivery Challan.

    Capacity is the bottle/container capacity (for example
    '180 (Pet Bottle)') and must become ML, never quantity.
    'Quantity in Cases' is the case count.
    """
    products: list[ExtractedProduct] = []

    for page_no, _page_text, page_tables in pages:
        for rows in page_tables:
            mapping: tuple[int, int, int] | None = None
            for cells in rows:
                keys = [_key(cell) for cell in cells]

                label_idx = next((i for i, key in enumerate(keys) if key == "labelname"), None)
                capacity_idx = next((i for i, key in enumerate(keys) if key == "capacity"), None)
                cases_idx = next(
                    (
                        i for i, key in enumerate(keys)
                        if key in {"quantityincases", "qtyincases", "quantitycases", "cases"}
                    ),
                    None,
                )
                if label_idx is not None and capacity_idx is not None and cases_idx is not None:
                    mapping = (label_idx, capacity_idx, cases_idx)
                    continue

                if mapping is None or not cells:
                    continue

                label_idx, capacity_idx, cases_idx = mapping
                if len(cells) <= max(mapping):
                    continue

                serial = _clean(cells[0])
                if not re.fullmatch(r"\d+", serial):
                    continue

                name = _clean(cells[label_idx])
                capacity = _clean(cells[capacity_idx])
                cases, loose = resolve_case_loose(cells[cases_idx], None)
                measure = _capacity_ml(capacity) or _ml(capacity, name)
                if not name or not measure or (cases <= 0 and loose <= 0):
                    continue

                package_match = re.search(r"\(([^)]+)\)", capacity)
                package_type = _clean(package_match.group(1)) if package_match else None

                raw = {f"column{index + 1}": value for index, value in enumerate(cells)}
                raw.update(
                    {
                        "Label Name": name,
                        "Capacity": capacity,
                        "Quantity in Cases": cells[cases_idx],
                        "packageType": package_type,
                    }
                )

                item = _product(
                    name=name,
                    ml=f"{measure} ML",
                    box=cases,
                    loose=loose,
                    raw=raw,
                    state="MADHYA_PRADESH",
                    page=page_no,
                    semantics="capacity_is_ml_quantity_is_cases",
                )
                if item:
                    payload = item.model_dump()
                    payload["packageType"] = package_type
                    products.append(ExtractedProduct.model_validate(payload))

    demand_match = re.search(r"Demand\s*Id\s*[-:]?\s*([^\n\r]+)", full_text, re.IGNORECASE)
    invoice_number = _clean(demand_match.group(1)) if demand_match else None
    date_match = re.search(r"\bDate\s*[-:]?\s*(\d{1,2}/\d{1,2}/\d{4})", full_text, re.IGNORECASE)
    invoice_date = _normalize_date(date_match.group(1)) if date_match else None
    return _dedupe(products), invoice_number, invoice_date


def _extract_generic(
    full_text: str,
    pages: list[tuple[int, str, list[list[list[str]]]]],
) -> tuple[list[ExtractedProduct], str | None, str | None]:
    """Conservative generic adapter.

    It only accepts explicit Name/Brand + ML + Cases and/or Bottles headers.
    Ambiguous generic "Qty" columns are intentionally rejected so the caller
    falls back to LlamaParse rather than guessing quantity semantics.
    """
    products: list[ExtractedProduct] = []

    for page_no, _page_text, page_tables in pages:
        for rows in page_tables:
            mapping: tuple[int, int, int | None, int | None] | None = None
            for cells in rows:
                keys = [_key(cell) for cell in cells]
                name_idx = next(
                    (
                        i
                        for i, value in enumerate(keys)
                        if value in {"brandname", "labelname", "itemname", "productname", "description", "descriptionofgoods"}
                    ),
                    None,
                )
                if name_idx is not None:
                    unit_idx = next(
                        (i for i, value in enumerate(keys) if value in {"measure", "unit", "unitname", "size", "ml", "packagingsize"}),
                        None,
                    )
                    case_idx = next(
                        (
                            i
                            for i, value in enumerate(keys)
                            if ("case" in value or "carton" in value) and "rate" not in value and "pack" not in value
                        ),
                        None,
                    )
                    bottle_idx = next(
                        (
                            i
                            for i, value in enumerate(keys)
                            if ("bottle" in value or "loose" in value) and "rate" not in value and "pack" not in value
                        ),
                        None,
                    )
                    if unit_idx is not None and (case_idx is not None or bottle_idx is not None):
                        mapping = (name_idx, unit_idx, case_idx, bottle_idx)
                    continue

                if not mapping:
                    continue
                name_idx, unit_idx, case_idx, bottle_idx = mapping
                required = [name_idx, unit_idx] + [i for i in (case_idx, bottle_idx) if i is not None]
                if not required or len(cells) <= max(required):
                    continue
                name = cells[name_idx]
                case_value = cells[case_idx] if case_idx is not None else None
                bottle_value = cells[bottle_idx] if bottle_idx is not None else None
                cases, bottles = resolve_case_loose(case_value, bottle_value)
                raw = {f"column{index + 1}": value for index, value in enumerate(cells)}
                item = _product(
                    name=name,
                    ml=cells[unit_idx],
                    box=cases,
                    loose=bottles,
                    raw=raw,
                    state="GENERIC",
                    page=page_no,
                    semantics="explicit_cases_and_or_bottles",
                )
                if item:
                    products.append(item)

    invoice_number = _header_value(
        full_text,
        r"Invoice\s*No\.?",
        r"Invoice\s*Number",
        r"Document\s*No\.?",
        r"Permit\s*No\.?",
        r"Transport\s*Pass\s*No\.?",
    )
    invoice_date = _normalize_date(
        _header_value(full_text, r"Invoice\s*Date", r"Document\s*Date", r"Date\s*of\s*Issue", r"Date")
    )
    return _dedupe(products), invoice_number, invoice_date


def _candidate_row_count(
    profile: str,
    pages: list[tuple[int, str, list[list[list[str]]]]],
) -> int:
    """Estimate unique product rows without assigning business semantics.

    This is deliberately broader than the deterministic adapters. It is used
    only as a completeness signal: under-extraction triggers LlamaParse rather
    than inventing missing products.
    """
    selected_pages = pages
    if profile == "WEST_BENGAL_FORM3":
        originals = [entry for entry in pages if re.search(r"\bORIGINAL\b", entry[1], re.IGNORECASE)]
        selected_pages = originals or pages[:2]

    signatures: set[str] = set()
    for _page_no, _text, page_tables in selected_pages:
        for rows in page_tables:
            for cells in rows:
                cleaned = [_clean(cell) for cell in cells]
                joined = " | ".join(cleaned)
                if not joined.strip():
                    continue

                looks_like_product = False
                if profile == "WEST_BENGAL_FORM3":
                    category = _clean(cleaned[0]).upper() if cleaned else ""
                    looks_like_product = (
                        category in {"IMFL", "OSBI", "OS"}
                        and any(re.search(r"\d{2,5}\s*m\.?l\.?", value, re.IGNORECASE) for value in cleaned)
                    )
                elif profile == "TELANGANA_ICDC":
                    looks_like_product = bool(
                        cleaned
                        and re.fullmatch(r"\d+", cleaned[0])
                        and any(re.search(r"\d+\s*/\s*\d+\s*m\.?l\.?", value, re.IGNORECASE) for value in cleaned)
                    )
                elif profile == "JHARKHAND_EXCISE":
                    looks_like_product = bool(
                        cleaned
                        and re.fullmatch(r"\d+", cleaned[0])
                        and any(re.search(r"\d{2,5}\s*m\.?l\.?", value, re.IGNORECASE) for value in cleaned)
                        and any(re.search(r"[A-Za-z]{4}", value) for value in cleaned)
                    )
                else:
                    looks_like_product = bool(
                        any(re.search(r"\d{2,5}\s*m\.?l\.?", value, re.IGNORECASE) for value in cleaned)
                        and any(re.search(r"[A-Za-z]{4}", value) for value in cleaned)
                        and any(re.search(r"\b\d+(?:\.\d+)?\b", value) for value in cleaned)
                    )

                if looks_like_product:
                    signatures.add(_key(joined))
    return len(signatures)


def extract_pdf_locally(file_path: Path) -> tuple[ExtractedDocument | None, dict[str, Any]]:
    """PyMuPDF-first deterministic PDF extraction.

    Recognized state adapters are deterministic. Unknown/ambiguous layouts only
    pass when explicit case/bottle headers are present; otherwise the caller
    falls back to LlamaParse.
    """
    try:
        document = fitz.open(file_path)
    except Exception as exc:
        return None, {"engine": "pymupdf", "usable": False, "reason": f"open_failed:{exc}"}

    try:
        pages: list[tuple[int, str, list[list[list[str]]]]] = []
        page_texts: list[str] = []
        word_count = 0
        table_count = 0

        for page_index, page in enumerate(document, start=1):
            text = page.get_text("text") or ""
            page_texts.append(text)
            try:
                word_count += len(page.get_text("words") or [])
            except Exception:
                pass
            page_tables = _page_tables(page)
            table_count += len(page_tables)
            pages.append((page_index, text, page_tables))

        full_text = "\n".join(page_texts)
        text_chars = len(re.sub(r"\s+", "", full_text))
        if text_chars < 150 or word_count < 20:
            return None, {
                "engine": "pymupdf",
                "usable": False,
                "reason": "insufficient_text_layer",
                "textChars": text_chars,
                "wordCount": word_count,
                "pageCount": len(document),
            }

        profile = _detect_profile(full_text)
        extractor = {
            "TELANGANA_ICDC": _extract_telangana,
            "WEST_BENGAL_FORM3": _extract_west_bengal,
            "JHARKHAND_EXCISE": _extract_jharkhand,
            "MADHYA_PRADESH_DELIVERY_CHALLAN": _extract_madhya_pradesh,
            "GENERIC": _extract_generic,
        }[profile]
        products, invoice_number, invoice_date = extractor(full_text, pages)
        candidate_rows = _candidate_row_count(profile, pages)
        product_count = len(products)
        completeness = (
            min(1.0, product_count / candidate_rows)
            if candidate_rows > 0
            else (1.0 if product_count else 0.0)
        )
        needs_fallback = bool(candidate_rows > product_count)

        if not products:
            return None, {
                "engine": "pymupdf",
                "usable": False,
                "reason": "no_confident_canonical_product_table",
                "profile": profile,
                "textChars": text_chars,
                "wordCount": word_count,
                "pageCount": len(document),
                "tableCount": table_count,
            }

        state = {
            "TELANGANA_ICDC": "TELANGANA",
            "WEST_BENGAL_FORM3": "WEST_BENGAL",
            "JHARKHAND_EXCISE": "JHARKHAND",
        }.get(profile, "UNKNOWN")

        extracted = ExtractedDocument(
            documentType="invoice",
            supplierName=None,
            invoiceNumber=invoice_number,
            invoiceDate=invoice_date,
            items=products,
            extractionEngine="pymupdf-state-adapter",
            extractionProfile=profile,
            sourceState=state,
            extractionDiagnostics={
                "pageCount": len(document),
                "textChars": text_chars,
                "wordCount": word_count,
                "tableCount": table_count,
                "productCount": product_count,
                "candidateRowCount": candidate_rows,
                "completeness": completeness,
                "needsFallback": needs_fallback,
            },
        )
        return extracted, {
            "engine": "pymupdf-state-adapter",
            "usable": True,
            "profile": profile,
            "state": state,
            "pageCount": len(document),
            "textChars": text_chars,
            "wordCount": word_count,
            "tableCount": table_count,
            "productCount": product_count,
            "candidateRowCount": candidate_rows,
            "completeness": completeness,
            "needsFallback": needs_fallback,
        }
    finally:
        document.close()
