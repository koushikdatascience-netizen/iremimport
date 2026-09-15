import os
import tempfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.db import init_db
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
from app.modules.document_import.normalizer import normalize_extracted_document


@pytest.fixture()
def client(monkeypatch):
    db_path = os.path.join(tempfile.gettempdir(), "madhushala_doc_import_test.db")
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass
    object.__setattr__(settings, "DATABASE_PATH", db_path)
    object.__setattr__(settings, "LLAMA_CLOUD_API_KEY", "test")
    object.__setattr__(settings, "CRM_INTEGRATION_KEY", "")
    init_db()
    from app.main import app

    return TestClient(app)


def create_session(client):
    response = client.post("/crm/session", json={"shopCode": "SHOP_A", "companyCode": "2", "billType": "AI", "accessToken": "token"})
    assert response.status_code == 200
    return response.json()


def auth(session):
    return {"Authorization": f"Bearer {session['sessionToken']}"}


def test_crm_session_includes_document_import_url(client):
    session = create_session(client)
    assert "/document-import" in session["documentImportUrl"]
    assert session["sessionToken"] in session["documentImportUrl"]


def test_document_normalization_ignores_bad_rows():
    document = ExtractedDocument(
        documentType="invoice",
        items=[
            ExtractedProduct(itemName="100 PIPER 180", brand="100 PIPER", ml="180", packing="48", confidence="91"),
            ExtractedProduct(itemName="TOTAL", brand="", ml=None),
        ],
    )
    rows = normalize_extracted_document(document, "DOCUMENT_PDF")
    assert len(rows) == 1
    assert rows[0].ml == 180
    assert rows[0].packing == 48
    assert rows[0].confidence == 0.91


def test_document_upload_rejects_invalid_extension(client):
    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/upload",
        headers=auth(session),
        files={"file": ("bad.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_document_upload_rejects_empty_file(client):
    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/upload",
        headers=auth(session),
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400


def test_document_upload_accepts_pdf_and_persists_job(client, monkeypatch):
    from app.main import document_import_service
    from app.modules.document_import import service as service_module

    async def fake_extract_products(self, temp_path, filename):
        return ExtractedDocument(
            documentType="invoice",
            supplierName="Supplier",
            items=[ExtractedProduct(itemName="100 PIPER 180", brand="100 PIPER", ml=180, packing=48, confidence=0.95)],
        )

    async def fake_prepare(session, job_id):
        from app.db import conn
        with conn() as db:
            db.execute("UPDATE import_items SET excise_item_code='733', mapping_status='UNMAPPED' WHERE job_id=?", (job_id,))
        return {"preparedCount": 1}

    async def fake_workspace(session, capture=None, latest_only=True, job_id=None):
        return {"unmappedItems": [{"exciseItemCode": 733}]}

    monkeypatch.setattr(service_module.LlamaCloudClient, "extract_products", fake_extract_products)
    monkeypatch.setattr(document_import_service.mapping_service, "prepare_document_job", fake_prepare)
    monkeypatch.setattr(document_import_service.mapping_service, "workspace_for_session", fake_workspace)

    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/upload",
        headers=auth(session),
        files={"file": ("invoice.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["status"] == "MAPPING_REQUIRED"
    assert payload["summary"]["detected"] == 1
    items = client.get(f"/api/v1/document-import/jobs/{payload['job']['id']}/items", headers=auth(session)).json()["items"]
    assert items[0]["raw_name"] == "100 PIPER 180"


def test_shop_cannot_read_other_shop_job(client, monkeypatch):
    from app.modules.document_import.service import DocumentImportService
    from app.main import mapping_service

    service = DocumentImportService(mapping_service)
    session_a = create_session(client)
    session_b = client.post("/crm/session", json={"shopCode": "SHOP_B", "companyCode": "2", "billType": "AI", "accessToken": "token"}).json()
    from app.services.session_service import session_service

    row_a = session_service.by_token(session_a["sessionToken"])
    row_b = session_service.by_token(session_b["sessionToken"])
    job_id = service._create_job(row_a, "DOCUMENT_PDF", "a.pdf")
    with pytest.raises(HTTPException):
        service.get_job(row_b, job_id)
