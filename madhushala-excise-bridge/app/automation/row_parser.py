"""Parse and normalize selected Warehouse Stock row snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.normalizer import normalize_brand, normalize_package_type, parse_int, parse_ml


def canonical_key(normalized_brand: str, measure_ml: int, normalized_package_type: str) -> str:
    return f"{normalized_brand}|{measure_ml}|{normalized_package_type}"


def normalize_raw_item(raw_item: dict[str, Any], captured_at: str | None = None) -> dict[str, Any] | None:
    brand = str(raw_item.get("brand") or "").strip()
    package_type = str(raw_item.get("packageType") or "").strip()
    measure_ml = parse_ml(raw_item.get("measureMl", "0"))

    if not brand or not package_type or measure_ml <= 0:
        return None

    normalized_brand = normalize_brand(brand)
    normalized_package_type = normalize_package_type(package_type)
    item_captured_at = captured_at or datetime.now(timezone.utc).isoformat()

    return {
        "brand": brand,
        "normalizedBrand": normalized_brand,
        "strengthRaw": str(raw_item.get("strengthRaw") or ""),
        "measureMl": measure_ml,
        "packageType": package_type,
        "retailerMargin": str(raw_item.get("retailerMargin") or "0"),
        "roundOffGovt": str(raw_item.get("roundOffGovt") or "0"),
        "specialPurposeFee": str(raw_item.get("specialPurposeFee") or "0"),
        "mrpPerUnit": str(raw_item.get("mrpPerUnit") or "0"),
        "bottlesPerCase": parse_int(raw_item.get("bottlesPerCase", "0")),
        "mrpPerCase": str(raw_item.get("mrpPerCase") or "0"),
        "supplier": str(raw_item.get("supplier") or ""),
        "warehouseCasesRaw": str(raw_item.get("warehouseCasesRaw") or "0"),
        "warehouseBottles": parse_int(raw_item.get("warehouseBottles", "0")),
        "requestedCases": parse_int(raw_item.get("requestedCases", "0")),
        "requestedBottles": parse_int(raw_item.get("requestedBottles", "0")),
        "canonicalKey": canonical_key(normalized_brand, measure_ml, normalized_package_type),
        "source": "WB_EXCISE_PREPARE_INDENT",
        "capturedAt": item_captured_at,
    }


def normalize_raw_items(raw_items: list[dict[str, Any]], captured_at: str | None = None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw_item in raw_items:
        item = normalize_raw_item(raw_item, captured_at=captured_at)
        if item:
            normalized.append(item)
    return normalized
