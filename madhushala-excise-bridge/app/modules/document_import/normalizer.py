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
    document_source = str(source_type or "").upper() in {"DOCUMENT_PDF", "DOCUMENT_IMAGE"}

    for index, item in enumerate(document.items):
        # Keep the full extractor output for review/debugging, but do not let
        # PDF/image commercial fields become purchase inputs. Those documents
        # own only item identity + physical quantity; Item Master owns the
        # commercial/tax values used by Calculate.
        raw = item.model_dump()
        raw_name = (item.itemName or item.brand or "").strip()
        brand = (item.brand or raw_name).strip()
        ml = parse_ml(item.ml or raw_name) or None
        if not raw_name or not ml:
            continue

        normalized_name = normalize_brand(raw_name)

        if document_source:
            physical_quantity = (
                parse_int(item.quantity)
                or parse_int(item.loose)
                or parse_int(item.box)
                or None
            )
            items.append(
                NormalizedImportItem(
                    source=source_type,
                    sourceItemId=f"{source_type.lower()}-{index + 1}",
                    rawName=raw_name,
                    normalizedName=normalized_name,
                    brand=brand,
                    ml=ml,
                    packing=None,
                    quantity=float(physical_quantity) if physical_quantity is not None else None,
                    rate=None,
                    mrp=None,
                    amount=None,
                    batchNo=None,
                    box=None,
                    loose=physical_quantity,
                    freeQnty=None,
                    discount=None,
                    cgst=None,
                    sgst=None,
                    cess=None,
                    addCess=None,
                    igst=None,
                    t1Amt=None,
                    t2Amt=None,
                    t3Amt=None,
                    t4Amt=None,
                    etd=None,
                    barcode=None,
                    confidence=_confidence(item.confidence),
                    rawData=raw,
                )
            )
            continue

        packing = parse_int(item.packing) or None
        amount = _float(item.itemAmount if item.itemAmount is not None else item.amount)
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
