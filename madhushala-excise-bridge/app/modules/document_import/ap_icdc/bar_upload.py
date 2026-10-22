"""Dedicated Andhra Pradesh Bar Indent upload adapter.

No existing upload route is altered. Validated rows use the same review,
mapping and purchase-preview infrastructure as all document imports.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
from app.modules.document_import.normalizer import normalize_extracted_document


async def process_bar_indent_upload(service, session, file: UploadFile):
    if not str(file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="AP Bar Indent accepts PDF files only")
    data = await file.read()
    service._validate_file(file, data)
    job_id = service._create_job(session, "DOCUMENT_PDF", Path(file.filename).name)
    source = None
    try:
        service._update_job(job_id, status="EXTRACTING")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(data)
            source = Path(handle.name)
        try:
            from app.modules.document_import.ap_icdc.main import extract_pdf
            result = await asyncio.to_thread(extract_pdf, source, document_type="bar_indent")
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="Bar Indent OCR runtime not installed") from exc
        if result["validation"]["status"] != "PASSED":
            service._update_job(job_id, status="FAILED", error="Bar Indent row or total validation failed")
            raise HTTPException(status_code=422, detail={
                "message":"Bar Indent could not be reconciled; mapping and purchase are blocked.",
                "validation":result["validation"],
                "rows":len(result.get("items") or []),
            })
        header = result.get("header") or {}
        items = [
            ExtractedProduct(
                itemName=row["brand_name"], brand=row["brand_name"],
                ml=row["size_ml"], box=row["qty_cases"], loose=row["qty_bottles"],
                confidence=1, sourceState="ANDHRA_PRADESH",
                sourceDocumentType="AP_BAR_INDENT",
                sourceProductCode=row.get("product_code"),
                sourceBrandCode=row.get("brand_number"),
                sourceSerial=row.get("sl_no"),
                printedAmount=row.get("total"),
                sourcePages=row.get("pages"),
                quantitySemantics="explicit_cases_and_bottles",
            ) for row in result["items"]
        ]
        doc = ExtractedDocument(
            documentType="stock_list", invoiceNumber=header.get("indent_number"),
            invoiceDate=header.get("indent_date"), items=items,
            sourceState="ANDHRA_PRADESH", sourceDocumentType="AP_BAR_INDENT",
            extractionDiagnostics=result.get("diagnostics"),
            barIndentTotals=result.get("summary"),
        )
        normalized = normalize_extracted_document(doc, "DOCUMENT_PDF")
        if len(normalized) != len(items):
            raise HTTPException(status_code=422, detail="Some Bar Indent rows did not normalize; import blocked")
        service._persist_items(job_id, normalized)
        service._update_job(job_id, status="REVIEW_REQUIRED",
            document_type=doc.documentType, invoice_number=doc.invoiceNumber,
            invoice_date=doc.invoiceDate,
            extracted_count=len(normalized), mapped_count=0, error=None)
        review = service.get_review_items(session, job_id)
        return {
            "job":service.get_job(session,job_id),
            "source":"DOCUMENT_PDF", "provider":"AP_BAR_INDENT",
            "summary":{"detected":len(review),"recognized":0,"needMapping":len(review),
                "valid":sum(bool(x["valid"]) for x in review),
                "needsAttention":sum(not x["valid"] for x in review),
                "reviewRequired":True},
            "extractedDocument":doc.model_dump(),
            "normalizedItems":[x.model_dump() for x in normalized],
            "reviewItems":review,
            "extraction":result.get("diagnostics"),
            "barIndentValidation":result.get("validation"),
        }
    except HTTPException as exc:
        service._update_job(job_id,status="FAILED",error=str(exc.detail)[:400])
        raise
    finally:
        if source is not None:
            source.unlink(missing_ok=True)
