from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import fitz
import httpx

from app.config import settings
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct


PRODUCT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "documentType": {"type": "string", "enum": ["invoice", "price_list", "stock_list", "unknown"]},
        "supplierName": {"type": ["string", "null"]},
        "invoiceNumber": {"type": ["string", "null"]},
        "invoiceDate": {"type": ["string", "null"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "itemName": {"type": "string"},
                    "brand": {"type": "string"},
                    "ml": {"type": ["integer", "string", "null"]},
                    "packing": {"type": ["integer", "string", "null"]},
                    "quantity": {"type": ["number", "string", "null"]},
                    "box": {"type": ["number", "string", "null"]},
                    "loose": {"type": ["number", "string", "null"]},
                    "freeQnty": {"type": ["number", "string", "null"]},
                    "batchNo": {"type": ["string", "null"]},
                    "rate": {"type": ["number", "string", "null"]},
                    "boxRate": {"type": ["number", "string", "null"]},
                    "looseRate": {"type": ["number", "string", "null"]},
                    "mrp": {"type": ["number", "string", "null"]},
                    "amount": {"type": ["number", "string", "null"]},
                    "itemAmount": {"type": ["number", "string", "null"]},
                    "discount": {"type": ["number", "string", "null"]},
                    "cgst": {"type": ["number", "string", "null"]},
                    "sgst": {"type": ["number", "string", "null"]},
                    "cess": {"type": ["number", "string", "null"]},
                    "addCess": {"type": ["number", "string", "null"]},
                    "igst": {"type": ["number", "string", "null"]},
                    "t1Amt": {"type": ["number", "string", "null"]},
                    "t2Amt": {"type": ["number", "string", "null"]},
                    "t3Amt": {"type": ["number", "string", "null"]},
                    "t4Amt": {"type": ["number", "string", "null"]},
                    "etd": {"type": ["number", "string", "null"]},
                    "t1Rate": {"type": ["number", "string", "null"]},
                    "t2Rate": {"type": ["number", "string", "null"]},
                    "t3Rate": {"type": ["number", "string", "null"]},
                    "t4Rate": {"type": ["number", "string", "null"]},
                    "cgstInptLdgr": {"type": ["string", "null"]},
                    "sgstInptLdgr": {"type": ["string", "null"]},
                    "cessInptLdgr": {"type": ["string", "null"]},
                    "adCessInptLdgr": {"type": ["string", "null"]},
                    "igstInptLdgr": {"type": ["string", "null"]},
                    "barcode": {"type": ["string", "null"]},
                    "confidence": {"type": ["number", "string", "null"]},
                },
            },
        },
    },
    "required": ["documentType", "items"],
}

EXTRACTION_PROMPT = (
    "Extract only the source-document facts needed for liquor purchase review. At document level extract "
    "supplierName when visible, invoiceNumber as the invoice/document/permit/transport-pass identifier, "
    "and invoiceDate as the source document date. For every product preserve the complete printed liquor "
    "name in itemName and brand, and extract the ML/measure. Quantity semantics are strict: box means the "
    "number of CASES/CARTONS explicitly shown for that product; loose means the number of individual "
    "BOTTLES/LOOSE UNITS explicitly shown for that product. If both case and bottle columns are present, "
    "extract both exactly as printed. If only cases are present, set box and leave loose zero/null. If only "
    "bottles/loose units are present, set loose and leave box zero/null. Never multiply cases by packing, "
    "never divide bottles by packing, never infer bottles-per-case, and never convert one quantity type "
    "into the other. Do not treat stock balance, Physical Qty inventory, BL/LPL, strength, packing size, "
    "rate, MRP, amount, or totals as purchase box/loose quantities unless the document explicitly labels "
    "that field as the delivered/purchased cases or bottles for the row. Keep packing and commercial/tax "
    "fields null unless needed only as raw audit context. Use quantity only as a legacy audit field; the "
    "canonical purchase quantities are box and loose. Use null when uncertain. Do not hallucinate. Ignore "
    "totals, summaries, signatures and repeated page headers as products."
)


class LlamaCloudError(RuntimeError):
    pass


class LlamaCloudClient:
    def __init__(self) -> None:
        if not settings.LLAMA_CLOUD_API_KEY:
            raise LlamaCloudError("LLAMA_CLOUD_API_KEY is not configured")
        self.base_url = settings.LLAMA_CLOUD_BASE_URL
        self.headers = {"Authorization": f"Bearer {settings.LLAMA_CLOUD_API_KEY}", "accept": "application/json"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.request(method, f"{self.base_url}{path}", headers={**self.headers, **kwargs.pop("headers", {})}, **kwargs)
        text = response.text
        try:
            payload = response.json() if text else None
        except ValueError:
            payload = {"raw": text}
        if not response.is_success:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise LlamaCloudError(str(detail or payload or response.reason_phrase))
        return payload

    async def upload_file(self, file_path: Path, filename: str) -> str:
        data = file_path.read_bytes()
        files = {"file": (filename, data, "application/octet-stream")}
        response = await self._request("POST", "/api/v1/beta/files", data={"purpose": "extract"}, files=files)
        file_id = response.get("id") or response.get("file_id") if isinstance(response, dict) else None
        if not file_id:
            raise LlamaCloudError("LlamaCloud did not return a file id")
        return str(file_id)

    async def start_extraction(self, file_id: str) -> str | dict[str, Any]:
        response = await self._request(
            "POST",
            "/api/v1/extraction/run",
            headers={"Content-Type": "application/json"},
            json={
                "file_id": file_id,
                "data_schema": PRODUCT_SCHEMA,
                "config": {"extraction_target": "PER_DOC", "extraction_mode": settings.DOCUMENT_IMPORT_EXTRACTION_MODE},
                "prompt": EXTRACTION_PROMPT,
            },
        )
        job_id = response.get("id") or response.get("job_id") if isinstance(response, dict) else None
        if job_id and response.get("status") in {"PENDING", "RUNNING", "PROCESSING"}:
            return str(job_id)
        return response

    async def poll_extraction(self, job_id: str) -> dict[str, Any]:
        for _ in range(90):
            state = await self._request("GET", f"/api/v1/extraction/jobs/{job_id}")
            status = state.get("status") if isinstance(state, dict) else None
            if status == "SUCCESS":
                return await self._request("GET", f"/api/v1/extraction/jobs/{job_id}/result")
            if status in {"ERROR", "FAILED", "CANCELLED"}:
                raise LlamaCloudError(f"Extraction job failed: {status}")
            await asyncio.sleep(max(0.5, settings.DOCUMENT_IMPORT_POLL_SECONDS))
        raise LlamaCloudError("Timed out waiting for extraction")

    async def extract_products(self, file_path: Path, filename: str) -> ExtractedDocument:
        file_id = await self.upload_file(file_path, filename)
        result_or_job = await self.start_extraction(file_id)
        result = await self.poll_extraction(result_or_job) if isinstance(result_or_job, str) else result_or_job
        payload = result.get("data") or result.get("result") or result if isinstance(result, dict) else {}
        return ExtractedDocument.model_validate(payload)


    async def extract_scanned_pdf_pages(self, file_path: Path, filename: str) -> ExtractedDocument:
        """Extract every scanned PDF page independently, then merge results.

        Some multi-page scanned invoices are visually clear but a PER_DOC
        extraction can stop after the first table/page. Splitting the PDF into
        one-page PDFs guarantees continuation pages are independently parsed.
        """
        source = fitz.open(file_path)
        page_paths: list[Path] = []
        try:
            for page_index in range(len(source)):
                one_page = fitz.open()
                try:
                    one_page.insert_pdf(source, from_page=page_index, to_page=page_index)
                    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                    handle.close()
                    page_path = Path(handle.name)
                    one_page.save(page_path)
                    page_paths.append(page_path)
                finally:
                    one_page.close()
        finally:
            source.close()

        semaphore = asyncio.Semaphore(3)

        async def extract_page(page_number: int, page_path: Path) -> tuple[int, ExtractedDocument]:
            async with semaphore:
                page_name = f"{Path(filename).stem}-page-{page_number}.pdf"
                result = await self.extract_products(page_path, page_name)
                return page_number, result

        try:
            page_results = await asyncio.gather(
                *(extract_page(i + 1, path) for i, path in enumerate(page_paths))
            )
        finally:
            for path in page_paths:
                path.unlink(missing_ok=True)

        page_results.sort(key=lambda pair: pair[0])
        merged_items: list[ExtractedProduct] = []
        document_type = "unknown"
        supplier_name = None
        invoice_number = None
        invoice_date = None

        for page_number, page_document in page_results:
            if document_type == "unknown" and page_document.documentType != "unknown":
                document_type = page_document.documentType
            supplier_name = supplier_name or page_document.supplierName
            invoice_number = invoice_number or page_document.invoiceNumber
            invoice_date = invoice_date or page_document.invoiceDate

            for item in page_document.items:
                payload = item.model_dump()
                payload["sourcePage"] = page_number
                merged_items.append(ExtractedProduct.model_validate(payload))

        deduped_items: list[ExtractedProduct] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in merged_items:
            signature = (
                str(item.itemName or "").strip().casefold(),
                str(item.ml or "").strip().casefold(),
                str(item.box or 0),
                str(item.loose or 0),
            )
            if signature in seen:
                continue
            seen.add(signature)
            deduped_items.append(item)

        return ExtractedDocument(
            documentType=document_type,
            supplierName=supplier_name,
            invoiceNumber=invoice_number,
            invoiceDate=invoice_date,
            items=deduped_items,
            extractionEngine="llamaparse-page-by-page",
            extractedPageCount=len(page_results),
            pageItemCounts={
                str(page_number): len(page_document.items)
                for page_number, page_document in page_results
            },
        )


