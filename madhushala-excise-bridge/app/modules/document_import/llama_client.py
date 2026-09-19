from __future__ import annotations

import asyncio
import re
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
        "transportPassNo": {"type": ["string", "null"]},
        "invoiceDate": {"type": ["string", "null"]},
        "documentProfile": {"type": ["string", "null"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "itemName": {"type": "string"},
                    "brand": {"type": "string"},
                    "ml": {"type": ["integer", "string", "null"]},
                    "sourceRowNumber": {"type": ["integer", "string", "null"]},
                    "sourceUnitText": {"type": ["string", "null"]},
                    "packing": {"type": ["integer", "string", "null"]},
                    "quantity": {"type": ["number", "string", "null"]},
                    "sourceQuantityText": {"type": ["string", "null"]},
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
    "supplierName when visible. Extract invoiceNumber as the business document number (invoice number, delivery "
    "challan/DC number, demand/document number). Extract transportPassNo separately when the document also shows a "
    "transport pass, excise permit, registered permit, TP pass, or permit number. If the document has only one such "
    "identifier, invoiceNumber may use that identifier and transportPassNo may be null; the application will apply "
    "the fallback. Extract invoiceDate as the source document date, and documentProfile. Set documentProfile to "
    "'JHARKHAND_STATE_BEVERAGES' when the page/document is issued by JHARKHAND STATE BEVERAGES CORPORATION "
    "LIMITED or is a continuation of that invoice format; otherwise use null unless another profile is known. "
    "For every product preserve the complete printed liquor name in itemName and brand. Always copy the exact "
    "printed Unit Name/Measure cell into sourceUnitText, and extract its ML value into ml. For example, Unit Name "
    "'180 ML' means sourceUnitText='180 ML' and ml=180. Always copy the exact printed quantity cell text into "
    "sourceQuantityText before interpreting it. If a product table contains a Batch, Batch No., Batch No. & Date, "
    "Lot/Batch or equivalent per-item column, copy that exact populated cell into batchNo for that product. If the "
    "batch cell is blank or the document has no batch column, use null. Never derive batchNo from invoice numbers, "
    "permit numbers, dates or other headers. Quantity semantics are strict: box means CASES/CARTONS and loose means individual BOTTLES/LOOSE UNITS. "
    "Special Jharkhand rule: for JHARKHAND STATE BEVERAGES CORPORATION LIMITED invoices, preserve EVERY visible "
    "product row, including continuation pages that may start directly with row 21, 22, 23, etc. The table columns are "
    "Sr No, Brand Name, Label Name, Unit Name, Quantity (Cases), followed by financial columns. Copy the visible Sr No "
    "to sourceRowNumber, copy Unit Name exactly to sourceUnitText, and copy Quantity (Cases) exactly to "
    "sourceQuantityText before interpretation. Unit Name examples such as 180 ML, 375 ML, 500 ML, 600 ML (CL), "
    "200 ML (CL), and 750 ML (FML) must populate ml with the numeric ML value. Quantity (Cases) uses CASES.LOOSE "
    "notation, not a decimal fraction: 17.20 means box=17 and loose=20, 15.00 means box=15 and loose=0, "
    "4.00 means box=4 and loose=0, and 2.04 means box=2 and loose=4. Never omit a visible product row because "
    "its name repeats another row at a different ML. Keep sourceQuantityText exactly as printed, including trailing "
    "zeroes. Special West Bengal rule: "
    "for West Bengal Excise Foreign Liquor Form No. 3 transport passes, extract product rows only from the "
    "top-level copy marked ORIGINAL. Ignore DUPLICATE, TRIPLICATE and QUADRUPLICATE copies of the same pass. "
    "For that WB form, use the compound 'In Cases' value as the purchase quantity: for example '3 - 0' means "
    "box=3 and loose=0. The separate 'In Bottles' column is audit-only for this purchase flow and must not "
    "populate loose. Special Madhya Pradesh rule: "
    "for 'Madhya Pradesh Excise Department' 'Delivery Challan' documents, the 'Capacity' column is the "
    "bottle/container capacity and must populate ml (for example '180 (Pet Bottle)' means ml=180 and "
    "packageType='Pet Bottle'); it is never a quantity. 'Quantity in Cases' is the case count, so set box "
    "to that value and loose to 0 unless another explicit loose/bottle column exists. For ordinary documents with both "
    "case and bottle columns, extract both exactly as printed. If only ordinary cases are present, set box and "
    "leave loose zero/null; if only bottles are present, set loose and leave box zero/null. Never multiply "
    "cases by packing, never divide bottles by packing, and never infer bottles-per-case. Do not treat stock "
    "balance, BL/LPL, strength, packing size, rate, MRP, amount or totals as purchase quantities. Keep packing "
    "and commercial/tax fields null except raw audit context. Use null when uncertain. Do not hallucinate. "
    "Ignore totals, summaries, signatures and repeated page headers as products."
)


def _decode_jharkhand_quantity_text(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip().replace(",", "")
    match = __import__("re").search(r"(\d+)\.(\d{1,2})", text)
    if not match:
        return None
    return max(0, int(match.group(1))), max(0, int(match.group(2)))


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

    async def start_extraction(
        self,
        file_id: str,
        *,
        extraction_mode: str | None = None,
        prompt: str | None = None,
    ) -> str | dict[str, Any]:
        response = await self._request(
            "POST",
            "/api/v1/extraction/run",
            headers={"Content-Type": "application/json"},
            json={
                "file_id": file_id,
                "data_schema": PRODUCT_SCHEMA,
                "config": {
                    "extraction_target": "PER_DOC",
                    "extraction_mode": extraction_mode or settings.DOCUMENT_IMPORT_EXTRACTION_MODE,
                },
                "prompt": prompt or EXTRACTION_PROMPT,
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

    async def extract_products(
        self,
        file_path: Path,
        filename: str,
        *,
        extraction_mode: str | None = None,
        prompt: str | None = None,
    ) -> ExtractedDocument:
        file_id = await self.upload_file(file_path, filename)
        result_or_job = await self.start_extraction(
            file_id,
            extraction_mode=extraction_mode,
            prompt=prompt,
        )
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
                result = await self.extract_products(
                    page_path,
                    page_name,
                    extraction_mode=settings.DOCUMENT_IMPORT_SCANNED_EXTRACTION_MODE,
                )
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
        transport_pass_no = None
        invoice_date = None
        document_profile = None

        wb_original_page: int | None = None
        source_doc = fitz.open(file_path)
        try:
            page_labels: dict[int, str] = {}
            for index, page in enumerate(source_doc, start=1):
                text = page.get_text("text") or ""
                top = "\n".join(text.splitlines()[:25]).upper()
                label = ""
                if "QUADRUPLICATE" in top:
                    label = "QUADRUPLICATE"
                elif "TRIPLICATE" in top:
                    label = "TRIPLICATE"
                elif "DUPLICATE" in top:
                    label = "DUPLICATE"
                elif re.search(r"\bORIGINAL\b", top):
                    label = "ORIGINAL"
                page_labels[index] = label
                if label == "ORIGINAL" and wb_original_page is None:
                    wb_original_page = index
        finally:
            source_doc.close()

        for page_number, page_document in page_results:
            if document_type == "unknown" and page_document.documentType != "unknown":
                document_type = page_document.documentType
            supplier_name = supplier_name or page_document.supplierName
            invoice_number = invoice_number or page_document.invoiceNumber
            transport_pass_no = transport_pass_no or getattr(page_document, "transportPassNo", None)
            invoice_date = invoice_date or page_document.invoiceDate
            page_profile = str(getattr(page_document, "documentProfile", "") or "").strip()
            if page_profile:
                document_profile = document_profile or page_profile
            if not document_profile and "jharkhand" in str(page_document.supplierName or "").casefold():
                document_profile = "JHARKHAND_STATE_BEVERAGES"

            page_label = page_labels.get(page_number, "")
            is_wb_page = "west bengal" in str(page_document.supplierName or "").casefold() or (
                "WEST_BENGAL" in str(page_profile or "").upper()
            )
            if wb_original_page is not None and page_label in {"DUPLICATE", "TRIPLICATE", "QUADRUPLICATE"}:
                continue

            for item in page_document.items:
                payload = item.model_dump()
                payload["sourcePage"] = page_number
                if wb_original_page is not None and page_number == wb_original_page:
                    case_text = str(payload.get("box") or payload.get("sourceQuantityText") or "").strip()
                    match = re.fullmatch(r"(\d+)\s*[-.]\s*(\d+)", case_text)
                    if match:
                        payload["box"] = int(match.group(1))
                        payload["loose"] = int(match.group(2))
                        payload["quantitySemantics"] = "west_bengal_case_field_only"
                        payload["sourceState"] = "WEST_BENGAL"
                merged_items.append(ExtractedProduct.model_validate(payload))

        if document_profile == "JHARKHAND_STATE_BEVERAGES":
            corrected_items: list[ExtractedProduct] = []
            for item in merged_items:
                payload = item.model_dump()
                quantity_candidates = (
                    payload.get("sourceQuantityText"),
                    payload.get("box"),
                    payload.get("quantity"),
                )
                decoded = next(
                    (
                        value
                        for candidate in quantity_candidates
                        if (value := _decode_jharkhand_quantity_text(candidate)) is not None
                    ),
                    None,
                )
                if decoded is not None:
                    box, loose = decoded
                    payload["box"] = box
                    payload["loose"] = loose
                    payload["quantity"] = payload.get("sourceQuantityText") or payload.get("quantity")
                    payload["quantitySemantics"] = "jharkhand_cases_dot_loose"
                    payload["sourceState"] = "JHARKHAND"

                if not payload.get("ml"):
                    unit_text = str(payload.get("sourceUnitText") or "").strip()
                    unit_match = re.search(r"(\d{2,5})\s*m\.?l\.?", unit_text, re.IGNORECASE)
                    if unit_match:
                        payload["ml"] = int(unit_match.group(1))

                corrected_items.append(ExtractedProduct.model_validate(payload))
            merged_items = corrected_items

        # Page-by-page extraction has no overlapping document window.
        # Preserve every extracted source row; identical products can be valid
        # separate invoice lines and must not be silently dropped.
        deduped_items = merged_items

        return ExtractedDocument(
            documentType=document_type,
            supplierName=supplier_name,
            invoiceNumber=invoice_number,
            transportPassNo=transport_pass_no,
            invoiceDate=invoice_date,
            items=deduped_items,
            documentProfile=document_profile,
            extractionEngine="llamaparse-page-by-page",
            extractedPageCount=len(page_results),
            pageItemCounts={
                str(page_number): len(page_document.items)
                for page_number, page_document in page_results
            },
        )


