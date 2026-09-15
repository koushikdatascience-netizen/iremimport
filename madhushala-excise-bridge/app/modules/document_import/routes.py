from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, UploadFile, File
from pydantic import BaseModel

from app.services.session_service import session_service
from app.modules.document_import.service import DocumentImportService
from app.modules.document_import.up_excise_qr import (
    extract_up_transport_pass,
    is_up_transport_pass_url,
)


class QrExtractRequest(BaseModel):
    url: str


class PurchaseSaveRequest(BaseModel):
    header: dict[str, Any]



def create_router(service: DocumentImportService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/document-import", tags=["document-import"])

    @router.post("/upload")
    async def upload_document(request: Request, file: UploadFile = File(...)):
        session = session_service.from_request(request)
        return await service.process_upload(session, file)

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str, request: Request):
        session = session_service.from_request(request)
        return service.get_job(session, job_id)

    @router.get("/jobs/{job_id}/items")
    async def get_job_items(job_id: str, request: Request):
        session = session_service.from_request(request)
        return {"items": service.get_items(session, job_id)}

    @router.post("/qr/extract")
    async def extract_qr_link(payload: QrExtractRequest, request: Request):
        session = session_service.from_request(request)
        if is_up_transport_pass_url(payload.url):
            return await extract_up_transport_pass(service, session, payload.url)
        return await service.extract_qr_link(session, payload.url)

    @router.post("/jobs/{job_id}/purchase/save")
    async def save_purchase(job_id: str, payload: PurchaseSaveRequest, request: Request):
        session = session_service.from_request(request)
        return await service.save_purchase(session, job_id, payload.header)

    return router