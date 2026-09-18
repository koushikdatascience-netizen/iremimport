from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from pydantic import BaseModel

from app.services.session_service import session_service
from app.integrations.madhushala.client import MadhushalaApiError
from app.modules.document_import.service import DocumentImportService
from app.modules.document_import.purchase_adapter import DocumentPurchaseAdapter
from app.modules.document_import.purchase_context import build_purchase_context, up_supplier_hint
from app.modules.document_import.purchase_preview import calculate_purchase_preview
from app.modules.document_import.purchase_required import resolve_required_purchase_header
from app.modules.document_import.qr_decoder import decode_qr_upload
from app.modules.document_import.document_mapping import save_document_row_mappings
from app.modules.document_import.up_excise_qr import (
    extract_up_transport_pass,
    is_up_transport_pass_url,
)
from app.observability import reset_correlation_id, set_correlation_id
from app.services.purchase_transaction_service import purchase_transaction_service


class QrExtractRequest(BaseModel):
    url: str


class PurchaseSaveRequest(BaseModel):
    header: dict[str, Any]


class DocumentMappingSaveRequest(BaseModel):
    mappings: list[dict[str, Any]]


class DocumentReviewItem(BaseModel):
    id: str
    name: str
    brand: str
    ml: int
    box: int = 0
    loose: int = 0


class DocumentReviewConfirmRequest(BaseModel):
    items: list[DocumentReviewItem]


def create_router(service: DocumentImportService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/document-import", tags=["document-import"])
    purchase_adapter = DocumentPurchaseAdapter(service)

    @router.post("/upload")
    async def upload_document(request: Request, file: UploadFile = File(...)):
        # Backward-compatible mixed upload endpoint.
        session = session_service.from_request(request)
        return await service.process_upload(session, file)

    @router.post("/upload/pdf")
    async def upload_pdf(request: Request, file: UploadFile = File(...)):
        filename = str(file.filename or "").casefold()
        if not filename.endswith(".pdf"):
            raise HTTPException(status_code=400, detail="PDF upload accepts .pdf files only")
        session = session_service.from_request(request)
        return await service.process_upload(session, file)

    @router.post("/upload/image")
    async def upload_image(request: Request, file: UploadFile = File(...)):
        filename = str(file.filename or "").casefold()
        if not filename.endswith((".jpg", ".jpeg", ".png")):
            raise HTTPException(status_code=400, detail="Image upload accepts JPG, JPEG or PNG files only")
        session = session_service.from_request(request)
        return await service.process_upload(session, file)

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str, request: Request):
        session = session_service.from_request(request)
        return service.get_job(session, job_id)

    @router.get("/jobs/{job_id}/items")
    async def get_job_items(job_id: str, request: Request):
        session = session_service.from_request(request)
        return {
            "items": service.get_items(session, job_id),
            "reviewItems": service.get_review_items(session, job_id),
        }

    @router.post("/jobs/{job_id}/review/confirm")
    async def confirm_document_review(
        job_id: str,
        payload: DocumentReviewConfirmRequest,
        request: Request,
    ):
        session = session_service.from_request(request)
        try:
            return await service.confirm_review(
                session,
                job_id,
                [item.model_dump() for item in payload.items],
            )
        except MadhushalaApiError as exc:
            status_code = exc.status_code if exc.status_code and exc.status_code >= 400 else 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @router.post("/jobs/{job_id}/mapping/save")
    async def save_document_mapping(job_id: str, payload: DocumentMappingSaveRequest, request: Request):
        session = session_service.from_request(request)
        try:
            return await save_document_row_mappings(service, session, job_id, payload.mappings)
        except MadhushalaApiError as exc:
            status_code = exc.status_code if exc.status_code and exc.status_code >= 400 else 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @router.post("/qr/decode")
    async def decode_qr_image(request: Request, file: UploadFile = File(...)):
        session_service.from_request(request)
        return {"value": await decode_qr_upload(file)}

    @router.post("/qr/extract")
    async def extract_qr_link(payload: QrExtractRequest, request: Request):
        session = session_service.from_request(request)
        if is_up_transport_pass_url(payload.url):
            result = await extract_up_transport_pass(service, session, payload.url)
            supplier_name = up_supplier_hint(result.get("extracted"))
            if supplier_name:
                job_id = str((result.get("job") or {}).get("id") or "").strip()
                if job_id:
                    service._update_job(job_id, supplier_name=supplier_name)
                    result["job"] = service.get_job(session, job_id)
                if isinstance(result.get("extractedDocument"), dict):
                    result["extractedDocument"]["supplierName"] = supplier_name
            return result
        return await service.extract_qr_link(session, payload.url)

    @router.get("/purchase/context")
    async def get_purchase_context(request: Request, supplierName: str = ""):
        session = session_service.from_request(request)
        return await build_purchase_context(session, supplier_name=supplierName)

    @router.get("/jobs/{job_id}/purchase/transaction")
    async def get_purchase_transaction(job_id: str, request: Request):
        session = session_service.from_request(request)
        service.get_job(session, job_id)
        return {
            "transaction": purchase_transaction_service.get(job_id),
            "events": purchase_transaction_service.events(job_id),
        }

    @router.post("/jobs/{job_id}/purchase/calculate-preview")
    async def preview_purchase(job_id: str, payload: PurchaseSaveRequest, request: Request):
        """Validate the current mapped document against live Madhushala Calculate.

        This endpoint intentionally never invokes /api/purchase/save. It exists so
        the browser can verify the exact financial calculation and surface the
        redacted request/response before the user is allowed to submit Purchase.
        """
        session = session_service.from_request(request)
        correlation = request.headers.get("X-Correlation-ID") or f"purchase-preview-{job_id}"
        token = set_correlation_id(correlation)
        try:
            header = await resolve_required_purchase_header(
                service,
                session,
                job_id,
                payload.header,
            )
            return await calculate_purchase_preview(service, session, job_id, header)
        finally:
            reset_correlation_id(token)

    @router.post("/jobs/{job_id}/purchase/save")
    async def save_purchase(job_id: str, payload: PurchaseSaveRequest, request: Request):
        session = session_service.from_request(request)
        correlation = request.headers.get("X-Correlation-ID") or f"purchase-{job_id}"
        token = set_correlation_id(correlation)
        try:
            header = await resolve_required_purchase_header(
                service,
                session,
                job_id,
                payload.header,
            )
            return await purchase_adapter.save_purchase(session, job_id, header)
        except MadhushalaApiError as exc:
            status_code = exc.status_code if exc.status_code and exc.status_code >= 400 else 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        finally:
            reset_correlation_id(token)

    return router
