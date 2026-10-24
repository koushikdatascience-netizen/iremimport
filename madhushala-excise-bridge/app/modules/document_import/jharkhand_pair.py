"""Fail-closed pairing of scanned Jharkhand invoice with Form-T permit.

The invoice is the sole source of purchase items. Permit rows are evidence,
not a second purchase. Exact row quantity/ML/name matching is required.
"""
from __future__ import annotations

import re
from collections import Counter

from app.modules.document_import.quantity import resolve_case_loose
from app.modules.document_import.schemas import ExtractedDocument


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _signature(item) -> tuple[str, int, int, int]:
    name = _key(item.itemName or item.brand)
    raw_ml = str(item.ml or "").strip()
    match = re.fullmatch(r"(\d{2,5})(?:\s*ML)?", raw_ml, re.I)
    if not name or match is None:
        raise ValueError("Jharkhand row is missing a verified product name or ML")
    box, loose = resolve_case_loose(item.box, item.loose)
    if box + loose <= 0:
        raise ValueError("Jharkhand row has missing case/loose quantity")
    return name, int(match.group(1)), box, loose


def join_verified_pair(invoice: ExtractedDocument, permit: ExtractedDocument) -> ExtractedDocument:
    if (str(getattr(invoice, "sourceState", "")).upper() != "JHARKHAND"
            or str(getattr(permit, "sourceState", "")).upper() != "JHARKHAND"):
        raise ValueError("Jharkhand pair requires two Jharkhand documents")
    bill = str(invoice.invoiceNumber or "").strip()
    pass_no = str(permit.transportPassNo or "").strip()
    if not bill or not pass_no or permit.invoiceNumber:
        raise ValueError("Expected scanned Jharkhand invoice and separate Form-T permit")
    if not re.fullmatch(r"DHA-\d{4}-\d{4}/\d+", pass_no, re.I):
        raise ValueError("Invalid Jharkhand Form-T permit number")
    if invoice.transportPassNo and str(invoice.transportPassNo).strip() != pass_no:
        raise ValueError("Jharkhand invoice and Form-T permit numbers disagree")
    if not invoice.items or len(invoice.items) != len(permit.items):
        raise ValueError("Jharkhand invoice and Form-T product counts differ")
    invoice_rows = Counter(_signature(item) for item in invoice.items)
    permit_rows = Counter(_signature(item) for item in permit.items)
    if invoice_rows != permit_rows:
        raise ValueError("Jharkhand invoice and Form-T products, ML, or quantities disagree")
    result = invoice.model_dump()
    result["transportPassNo"] = pass_no
    result["sourceState"] = "JHARKHAND"
    result["jharkhandPairValidated"] = True
    result["jharkhandPermitProductCount"] = len(permit.items)
    # Preserve invoice rows unchanged; never append permit rows.
    return ExtractedDocument.model_validate(result)
