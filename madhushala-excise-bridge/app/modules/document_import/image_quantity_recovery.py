"""Merge separately read quantity cells only for uniquely identified image rows."""
from collections import Counter
import re

from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
from app.services.normalizer import normalize_brand, parse_ml


def _key(row):
    name = normalize_brand(row.get("itemName") or row.get("brand") or "")
    size = parse_ml(row.get("ml"))
    return (name, size) if name and size else None


def _cell(header, cell, allowed):
    label = re.sub(r"[^A-Z]", "", str(header or "").upper())
    text = str(cell if cell is not None else "").strip()
    if label not in allowed or not re.fullmatch(r"\d{1,5}", text):
        return None
    return int(text)


def recover_image_quantities(document: ExtractedDocument, evidence: dict) -> tuple[ExtractedDocument, int]:
    rows = evidence.get("items") if isinstance(evidence, dict) else None
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    originals = [item.model_dump() for item in document.items]
    original_counts = Counter(_key(row) for row in originals)
    evidence_counts = Counter(_key(row) for row in rows)
    by_key = {_key(row): row for row in rows if _key(row) is not None}
    recovered = 0
    for row in originals:
        key = _key(row)
        if not key or original_counts[key] != 1 or evidence_counts[key] != 1:
            continue
        # Do not overwrite any quantity that the first pass already supplied.
        if any(row.get(field) not in (None, "", 0, "0") for field in ("box", "loose", "quantity")):
            continue
        source = by_key[key]
        box = _cell(source.get("caseHeader"), source.get("caseCell"), {"CS", "CASE", "CASES", "BOX", "BOXES", "CARTON", "CARTONS"})
        loose = _cell(source.get("bottleHeader"), source.get("bottleCell"), {"BT", "BTL", "BOTTLE", "BOTTLES", "LOOSE"})
        if not ((box or 0) > 0 or (loose or 0) > 0):
            continue
        row.update(box=box, loose=loose, imageQuantityEvidence=source)
        recovered += 1
    return document.model_copy(update={"items": [ExtractedProduct.model_validate(row) for row in originals]}), recovered
