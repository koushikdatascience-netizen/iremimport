from __future__ import annotations

from app.services.normalizer import normalize_brand, parse_decimal, parse_int, parse_ml
from app.modules.document_import.schemas import ExtractedDocument, NormalizedImportItem


def _float(value) -> float | None:
    parsed = parse_decimal(value)
    if parsed is None:
        return None
    return float(parsed)


def _confidence(value) -> float | None:
    parsed = parse_decimal(value)
    if parsed is None:
        return None
    number = float(parsed)
    if number > 1:
        number = number / 100
    return max(0.0, min(1.0, number))


def _clean_text(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def normalize_extracted_document(document: ExtractedDocument, source_type: str) -> list[NormalizedImportItem]:
    items: list[NormalizedImportItem] = []
    for index, item in enumerate(document.items):
        raw = item.model_dump()
        raw_name = (item.itemName or item.brand or "").strip()
        brand = (item.brand or raw_name).strip()
        ml = parse_ml(item.ml or raw_name) or None
        packing = parse_int(item.packing) or None
        if not raw_name or not ml:
            continue

        amount = _float(item.itemAmount if item.itemAmount is not None else item.amount)
        normalized_name = normalize_brand(raw_name)
        items.append(
            NormalizedImportItem(
                source=source_type,
                sourceItemId=f"{source_type.lower()}-{index + 1}",
                rawName=raw_name,
                normalizedName=normalized_name,
                brand=brand,
                ml=ml,
                packing=packing,
                quantity=_float(item.quantity),
                rate=_float(item.rate),
                mrp=_float(item.mrp),
                amount=amount,
                batchNo=_clean_text(item.batchNo),
                box=parse_int(item.box) or None,
                loose=parse_int(item.loose) or None,
                freeQnty=parse_int(item.freeQnty) or None,
                discount=_float(item.discount),
                cgst=_float(item.cgst),
                sgst=_float(item.sgst),
                cess=_float(item.cess),
                addCess=_float(item.addCess),
                igst=_float(item.igst),
                t1Amt=_float(item.t1Amt),
                t2Amt=_float(item.t2Amt),
                t3Amt=_float(item.t3Amt),
                t4Amt=_float(item.t4Amt),
                etd=_float(item.etd),
                barcode=(item.barcode or "").strip() or None,
                confidence=_confidence(item.confidence),
                rawData=raw,
            )
        )
    return items
