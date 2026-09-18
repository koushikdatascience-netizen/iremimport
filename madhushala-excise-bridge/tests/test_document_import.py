import json
import os
import tempfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.db import init_db
from app.modules.document_import.schemas import ExtractedDocument, ExtractedProduct
from app.modules.document_import.normalizer import normalize_extracted_document


UP_EXCISE_QR_URL = (
    "https://cms.upexciseonline.co/transport-pass-tracking/"
    "?tpnum=WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674"
    "&tptype=FG&tpyear=2026"
)

UP_EXCISE_TRANSPORT_HTML = """
<!doctype html>
<html>
<head><title>Transport Pass Tracking – UP Excise</title></head>
<body>
  <h4>TRANSPORT PASS DETAILS</h4>
  <div>Indent &amp; Dispatch Details</div>
  <table id="indent-details">
    <tr>
      <th>S.No</th>
      <th>Indent No.</th>
      <th>Indent Date</th>
      <th>No. of Cases / Monocartons Requested</th>
      <th>No. of Bottles Requested</th>
      <th>Total Bulk Litres</th>
    </tr>
    <tr>
      <td>1</td>
      <td>IND-LUCK-2026-00042</td>
      <td>16-Jun-2026</td>
      <td>3</td>
      <td>72</td>
      <td>45.72</td>
    </tr>
  </table>

  <table id="finished-goods">
    <tr>
      <th>S.No</th>
      <th>Brand</th>
      <th>Liquor Type</th>
      <th>Liquor Sub Type</th>
      <th>Packaging Size</th>
      <th>Packaging Type</th>
      <th>No of Cases/ Monocartons Requested</th>
      <th>No of Bottles Requested</th>
      <th>No of Cases/ Monocartons Dispatched</th>
      <th>No of Bottles Dispatched</th>
      <th>BULK LITRES</th>
    </tr>
    <tr>
      <td>1</td>
      <td>ROYAL STAG PREMIER WHISKY</td>
      <td>FL</td>
      <td>WHISKY</td>
      <td>750 ML</td>
      <td>Glass Bottle</td>
      <td>2</td>
      <td>24</td>
      <td>2</td>
      <td>24</td>
      <td>18.00</td>
    </tr>
    <tr>
      <td>2</td>
      <td>100 PIPERS DELUXE SCOTCH WHISKY</td>
      <td>FL</td>
      <td>WHISKY</td>
      <td>180 ML</td>
      <td>PET Bottle</td>
      <td>1</td>
      <td>48</td>
      <td>1</td>
      <td>48</td>
      <td>8.64</td>
    </tr>
  </table>
</body>
</html>
"""


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


def test_up_excise_transport_pass_qr_exact_url_and_table_shape(client, monkeypatch):
    from app.db import conn
    from app.main import document_import_service
    from app.modules.document_import import up_excise_qr

    assert up_excise_qr.is_up_transport_pass_url(UP_EXCISE_QR_URL)
    assert up_excise_qr.parse_up_transport_url(UP_EXCISE_QR_URL) == {
        "transportPassNo": "WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674",
        "transportPassType": "FG",
        "transportPassYear": "2026",
    }

    async def fake_fetch(url):
        assert url == UP_EXCISE_QR_URL
        return {
            "text": UP_EXCISE_TRANSPORT_HTML,
            "contentType": "text/html; charset=UTF-8",
            "finalUrl": url,
            "statusCode": 200,
        }

    async def should_not_render(url):
        raise AssertionError("Browser fallback should not run when populated HTML tables are already present")

    async def fake_prepare(session, job_id):
        with conn() as db:
            rows = db.execute("SELECT id FROM import_items WHERE job_id=? ORDER BY created_at, id", (job_id,)).fetchall()
            for index, row in enumerate(rows, start=733):
                db.execute(
                    "UPDATE import_items SET excise_item_code=?, mapping_status='UNMAPPED' WHERE id=?",
                    (str(index), row["id"]),
                )
        return {"preparedCount": len(rows)}

    async def fake_workspace(session, capture=None, latest_only=True, job_id=None):
        return {
            "unmappedItems": [
                {"exciseItemCode": 733},
                {"exciseItemCode": 734},
            ]
        }

    monkeypatch.setattr(up_excise_qr, "_fetch_up_transport_page", fake_fetch)
    monkeypatch.setattr(up_excise_qr, "_render_up_transport_page", should_not_render)
    monkeypatch.setattr(document_import_service.mapping_service, "prepare_document_job", fake_prepare)
    monkeypatch.setattr(document_import_service.mapping_service, "workspace_for_session", fake_workspace)

    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/qr/extract",
        headers=auth(session),
        json={"url": UP_EXCISE_QR_URL},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["provider"] == "UP_EXCISE_IESCMS"
    assert payload["renderedWithBrowser"] is False
    assert payload["transportPass"] == {
        "transportPassNo": "WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674",
        "transportPassType": "FG",
        "transportPassYear": "2026",
    }
    assert payload["job"]["status"] == "MAPPING_REQUIRED"
    assert payload["summary"] == {"detected": 2, "recognized": 0, "needMapping": 2}
    assert payload["extractedDocument"]["invoiceNumber"] == "IND-LUCK-2026-00042"
    assert payload["extractedDocument"]["invoiceDate"] == "2026-06-16"
    assert payload["extractedDocument"]["transportPassNo"] == "WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674"

    normalized = payload["normalizedItems"]
    assert normalized[0]["rawName"] == "ROYAL STAG PREMIER WHISKY"
    assert normalized[0]["ml"] == 750
    assert normalized[0]["packing"] == 12
    assert normalized[0]["quantity"] == 24.0
    assert normalized[0]["box"] is None
    assert normalized[0]["loose"] == 24
    assert normalized[1]["rawName"] == "100 PIPERS DELUXE SCOTCH WHISKY"
    assert normalized[1]["ml"] == 180
    assert normalized[1]["packing"] == 48

    items = client.get(
        f"/api/v1/document-import/jobs/{payload['job']['id']}/items",
        headers=auth(session),
    ).json()["items"]
    assert len(items) == 2
    royal_stag = next(item for item in items if item["raw_name"] == "ROYAL STAG PREMIER WHISKY")
    raw = json.loads(royal_stag["raw_data_json"])
    assert raw["transportPassNo"] == "WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674"
    assert raw["transportPassType"] == "FG"
    assert raw["transportPassYear"] == "2026"
    assert raw["box"] == 0
    assert raw["loose"] == 24
    assert raw["quantity"] == 24
    assert raw["qnty"] == 24
    assert raw["bulkLitres"] == "18.00"


def test_up_excise_transport_pass_qr_reads_script_embedded_rows():
    from app.modules.document_import import up_excise_qr

    html = """
    <html><body>
      <div>Indent No.: RETAIL995782-20260615110345746</div>
      <div>Indent Date: 15-Jun-2026</div>
      <script>
        window.transportPass = {
          "items": [
            {
              "brand": "Tenjaku Blended Whisky",
              "packagingSize": "700 ML",
              "packagingType": "Glass Bottle",
              "noOfCasesDispatched": "1",
              "noOfBottlesDispatched": "12",
              "bulkLitres": "8.40"
            }
          ]
        };
      </script>
    </body></html>
    """

    payload = up_excise_qr._payload_from_html(html)
    document = up_excise_qr._document_from_payload(
        payload,
        up_excise_qr.parse_up_transport_url(UP_EXCISE_QR_URL),
    )
    normalized = normalize_extracted_document(document, "QR_HTML")

    assert len(normalized) == 1
    assert normalized[0].rawName == "Tenjaku Blended Whisky"
    assert normalized[0].ml == 700
    assert normalized[0].packing == 12
    assert normalized[0].quantity == 12.0
    assert normalized[0].box is None
    assert normalized[0].loose == 12

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
