"""Explicit Andhra Pradesh ICDC purchase invoice upload. BAR INDENT is untouched.

Use the existing conservative 10-column ICDC OCR parser; accept only
fully-reconciled invoices. Printed money is evidence, not Calculate output.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from fastapi import HTTPException, UploadFile

from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
from app.modules.document_import.normalizer import normalize_extracted_document

def invoice_financials(summary: dict) -> dict:
    mapping = {
        "cess": "cess",
        "additional_previlage_fee": "additionalPrivilegeFee",
        "additional_retail_excise_tax": "additionalRetailExciseTax",
        "previlage_fee_clubs": "privilegeFeeForClubs",
        "tcs": "tcs",
    }
    taxes = {out: summary.get(source) for source, out in mapping.items()}
    # VAT has no independently verified label in this ICDC parser: leave null.
    taxes["vat"] = None
    return {
        "sourceDocumentType": "AP_ICDC_INVOICE",
        "totalAmount": summary.get("issue_price"),
        "totalNet": summary.get("less_this_invoice_value"),
        "icdcValue": summary.get("icdc_value"),
        "challanAmount": summary.get("challan_amount"),
        "previousCreditLeftover": summary.get("previous_credit_leftover"),
        "subTotal": summary.get("sub_total"),
        "amountCredited": summary.get("amount_credited"),
        "taxes": taxes,
    }




def validation_failure_detail(result: dict) -> dict:
    """Safe AP diagnostics: only counts and check names, never invoice contents."""
    validation = result.get("validation") or {}
    checks = validation.get("checks") or {}
    return {
        "message": "AP ICDC OCR reconciliation failed; import was blocked",
        "failedChecks": sorted(key for key, passed in checks.items() if not passed),
        "reason": validation.get("reason"),
        "ocrRowCount": len(result.get("items") or []),
        "gridColumnsDetected": (result.get("diagnostics") or {}).get("grid_columns_detected"),
        "pageCount": (result.get("diagnostics") or {}).get("page_count"),
        "ocrSeconds": (result.get("diagnostics") or {}).get("ocr_elapsed_seconds"),
    }


def verified_document_from_result(result: dict) -> ExtractedDocument:
    """Convert fully reconciled OCR evidence into source financials; never infer prices."""
    validation = result.get("validation") or {}
    checks = validation.get("checks") or {}
    if (validation.get("status") not in {"PASSED", "REVIEW_REQUIRED"}
            or not checks or not all(checks.values())):
        raise ValueError("AP ICDC invoice financial/row reconciliation failed")
    summary = result.get("summary") or {}
    financials = invoice_financials(summary)
    header = result.get("header") or {}
    products = []
    for row in result.get("items") or []:
        amount = row.get("total")
        if amount is None:
            raise ValueError("AP invoice row has no printed amount")
        products.append(ExtractedProduct(
            itemName=row["brand_name"], brand=row["brand_name"],
            ml=row.get("size_ml"), box=row.get("qty_cases"),
            loose=row.get("qty_bottles") or 0,
            amount=amount, invoiceAmount=amount,
            confidence=1, sourceState="ANDHRA_PRADESH",
            sourceDocumentType="AP_ICDC_INVOICE",
            sourceBrandCode=row.get("brand_number"),
            sourceSerial=row.get("sl_no"),
            sourcePages=row.get("pages"),
            quantitySemantics="explicit_cases_and_bottles",
        ))
    if not products:
        raise ValueError("AP invoice has no reconciled product rows")
    return ExtractedDocument(
        documentType="invoice", invoiceNumber=header.get("icdc_number"),
        transportPassNo=header.get("tp_number"),
        invoiceDate=header.get("invoice_date"),
        items=products, sourceState="ANDHRA_PRADESH",
        sourceDocumentType="AP_ICDC_INVOICE",
        invoiceFinancials=financials,
    )

async def process_icdc_invoice_upload(service, session, file: UploadFile):
    if not str(file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="AP ICDC invoice accepts PDF files only")
    data = await file.read()
    service._validate_file(file, data)
    job_id = service._create_job(session, "DOCUMENT_PDF", Path(file.filename).name)
    source = None
    try:
        service._update_job(job_id, status="EXTRACTING")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(data)
            source = Path(handle.name)
        from app.modules.document_import.ap_icdc.main import extract_pdf
        try:
            result = await asyncio.to_thread(extract_pdf, source, document_type="icdc")
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="ICDC invoice OCR runtime not installed") from exc
        validation = result.get("validation") or {}
        if (validation.get("status") not in {"PASSED", "REVIEW_REQUIRED"}
                or not (validation.get("checks") or {})
                or not all((validation.get("checks") or {}).values())):
            raise HTTPException(status_code=422, detail={
                "message": "AP ICDC invoice cannot be reconciled; purchase is blocked",
                "validation": validation, "rowCount": len(result.get("items") or []),
            })
        try:
            doc = verified_document_from_result(result)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        normalized = normalize_extracted_document(doc, "DOCUMENT_PDF")
        if len(normalized) != len(products):
            raise HTTPException(status_code=422, detail="AP invoice rows did not fully normalize")
        service._persist_items(job_id, normalized)
        service._update_job(job_id, status="REVIEW_REQUIRED",
                            document_type=doc.documentType,
                            invoice_number=doc.invoiceNumber,
                            invoice_date=doc.invoiceDate,
                            extracted_count=len(normalized), mapped_count=0, error=None)
        review = service.get_review_items(session, job_id)
        return {
            "job": service.get_job(session, job_id),
            "source": "DOCUMENT_PDF", "provider": "AP_ICDC_INVOICE",
            "summary": {
                "detected": len(review), "recognized": 0,
                "needMapping": len(review),
                "valid": sum(bool(x["valid"]) for x in review),
                "needsAttention": sum(not x["valid"] for x in review),
                "reviewRequired": True,
            },
            "extractedDocument": doc.model_dump(),
            "normalizedItems": [x.model_dump() for x in normalized],
            "reviewItems": review,
            "invoiceValidation": validation,
        }
    except HTTPException as exc:
        service._update_job(job_id, status="FAILED", error=str(exc.detail)[:400])
        raise
    finally:
        if source is not None:
            source.unlink(missing_ok=True)
