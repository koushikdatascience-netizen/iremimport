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
    assert rows[0].packing is None
    assert rows[0].rawData["packing"] == "48"
    assert rows[0].confidence == 0.91




def test_pdf_and_image_normalization_keep_only_identity_and_quantity_for_purchase():
    document = ExtractedDocument(
        documentType="invoice",
        supplierName="Supplier A",
        invoiceNumber="INV-42",
        invoiceDate="2026-09-18",
        items=[
            ExtractedProduct(
                itemName="100 PIPER 750 ML",
                brand="100 PIPER",
                ml=750,
                packing=12,
                quantity=18,
                box=2,
                loose=6,
                rate=211.8,
                mrp=1880,
                amount=3812.4,
                discount=99,
                cgst=5,
                sgst=5,
                t1Amt=100,
                etd=500,
                confidence=0.97,
            )
        ],
    )

    for source_type in ("DOCUMENT_PDF", "DOCUMENT_IMAGE"):
        rows = normalize_extracted_document(document, source_type)
        assert len(rows) == 1
        row = rows[0]

        assert row.rawName == "100 PIPER 750 ML"
        assert row.ml == 750
        assert row.quantity == 8.0
        assert row.box == 2
        assert row.loose == 6

        # PDF/image commercial values are audit-only. The purchase path must
        # source them later from Madhushala Item Master, exactly like QR.
        assert row.packing is None
        assert row.rate is None
        assert row.mrp is None
        assert row.amount is None
        assert row.discount is None
        assert row.cgst is None
        assert row.sgst is None
        assert row.t1Amt is None
        assert row.etd is None

        # Full extractor output is still retained for review/debugging.
        assert row.rawData["packing"] == 12
        assert row.rawData["box"] == 2
        assert row.rawData["loose"] == 6
        assert row.rawData["rate"] == 211.8
        assert row.rawData["mrp"] == 1880
        assert row.rawData["discount"] == 99


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

    def fake_local_extract(_path):
        return None, {"engine": "pymupdf", "usable": False, "reason": "insufficient_text_layer"}

    async def fake_scanned_pages(self, temp_path, filename):
        return ExtractedDocument(
            documentType="invoice",
            supplierName="Supplier",
            items=[ExtractedProduct(itemName="100 PIPER 180", brand="100 PIPER", ml=180, packing=48, quantity=48, confidence=0.95)],
        )

    async def fake_prepare(session, job_id):
        from app.db import conn
        with conn() as db:
            db.execute("UPDATE import_items SET excise_item_code='733', mapping_status='UNMAPPED' WHERE job_id=?", (job_id,))
        return {"preparedCount": 1}

    async def fake_workspace(session, capture=None, latest_only=True, job_id=None):
        return {"unmappedItems": [{"exciseItemCode": 733}]}

    monkeypatch.setattr(service_module, "extract_pdf_locally", fake_local_extract)
    monkeypatch.setattr(service_module.LlamaCloudClient, "extract_scanned_pdf_pages", fake_scanned_pages)
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
    assert payload["job"]["status"] == "REVIEW_REQUIRED"
    assert payload["summary"]["detected"] == 1
    assert payload["summary"]["reviewRequired"] is True
    items = client.get(f"/api/v1/document-import/jobs/{payload['job']['id']}/items", headers=auth(session)).json()["items"]
    assert items[0]["raw_name"] == "100 PIPER 180"




def test_native_pdf_uses_pymupdf_without_llama(client, monkeypatch):
    from app.db import conn
    from app.main import document_import_service
    from app.modules.document_import import service as service_module

    local_document = ExtractedDocument(
        documentType="invoice",
        supplierName="Native Supplier",
        invoiceNumber="INV-NATIVE-1",
        invoiceDate="2026-09-18",
        items=[
            ExtractedProduct(
                itemName="100 PIPER 750 ML",
                brand="100 PIPER",
                ml=750,
                quantity=18,
                loose=18,
                box=0,
            )
        ],
    )

    def fake_local_extract(_path):
        return local_document, {
            "engine": "pymupdf",
            "usable": True,
            "productCount": 1,
        }

    async def should_not_use_llama(self, temp_path, filename):
        raise AssertionError("LlamaParse must not run for a usable native PDF")

    async def should_not_use_scanned_pages(self, temp_path, filename):
        raise AssertionError("Scanned-page fallback must not run for a usable native PDF")

    async def fake_prepare(session, job_id):
        with conn() as db:
            db.execute(
                "UPDATE import_items SET excise_item_code='733', mapping_status='UNMAPPED' WHERE job_id=?",
                (job_id,),
            )
        return {"preparedCount": 1}

    async def fake_workspace(session, capture=None, latest_only=True, job_id=None):
        return {"unmappedItems": [{"exciseItemCode": 733}]}

    monkeypatch.setattr(service_module, "extract_pdf_locally", fake_local_extract)
    monkeypatch.setattr(service_module.LlamaCloudClient, "extract_products", should_not_use_llama)
    monkeypatch.setattr(service_module.LlamaCloudClient, "extract_scanned_pdf_pages", should_not_use_scanned_pages)
    monkeypatch.setattr(document_import_service.mapping_service, "prepare_document_job", fake_prepare)
    monkeypatch.setattr(document_import_service.mapping_service, "workspace_for_session", fake_workspace)

    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/upload/pdf",
        headers=auth(session),
        files={"file": ("native.pdf", b"%PDF-1.4 native-test", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["extraction"]["engine"] == "pymupdf"
    assert payload["extraction"]["usable"] is True
    assert payload["extractedDocument"]["supplierName"] == "Native Supplier"
    assert payload["normalizedItems"][0]["quantity"] == 18.0
    assert payload["normalizedItems"][0]["loose"] == 18


def test_scanned_pdf_falls_back_to_llamaparse(client, monkeypatch):
    from app.db import conn
    from app.main import document_import_service
    from app.modules.document_import import service as service_module

    def fake_local_extract(_path):
        return None, {
            "engine": "pymupdf",
            "usable": False,
            "reason": "insufficient_text_layer",
        }

    async def fake_scanned_pages(self, temp_path, filename):
        return ExtractedDocument(
            documentType="invoice",
            supplierName="Scanned Supplier",
            invoiceNumber="SCAN-1",
            invoiceDate="2026-09-18",
            items=[
                ExtractedProduct(
                    itemName="BACARDI RESERVA 750 ML",
                    brand="BACARDI",
                    ml=750,
                    quantity=2,
                )
            ],
        )

    async def fake_prepare(session, job_id):
        with conn() as db:
            db.execute(
                "UPDATE import_items SET excise_item_code='734', mapping_status='UNMAPPED' WHERE job_id=?",
                (job_id,),
            )
        return {"preparedCount": 1}

    async def fake_workspace(session, capture=None, latest_only=True, job_id=None):
        return {"unmappedItems": [{"exciseItemCode": 734}]}

    monkeypatch.setattr(service_module, "extract_pdf_locally", fake_local_extract)
    monkeypatch.setattr(service_module.LlamaCloudClient, "extract_scanned_pdf_pages", fake_scanned_pages)
    monkeypatch.setattr(document_import_service.mapping_service, "prepare_document_job", fake_prepare)
    monkeypatch.setattr(document_import_service.mapping_service, "workspace_for_session", fake_workspace)

    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/upload/pdf",
        headers=auth(session),
        files={"file": ("scan.pdf", b"%PDF-1.4 scan-test", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["extraction"]["engine"] == "llamaparse-page-by-page"
    assert payload["extraction"]["fallbackFrom"] == "pymupdf"
    assert payload["extraction"]["reason"] == "insufficient_text_layer"
    assert payload["normalizedItems"][0]["quantity"] == 2.0
    assert payload["normalizedItems"][0]["loose"] == 2


def test_purchase_image_uses_llamaparse_directly(client, monkeypatch):
    from app.db import conn
    from app.main import document_import_service
    from app.modules.document_import import service as service_module

    def should_not_use_pdf(_path):
        raise AssertionError("PyMuPDF must not run for image uploads")

    async def fake_llama(self, temp_path, filename):
        return ExtractedDocument(
            documentType="invoice",
            items=[
                ExtractedProduct(
                    itemName="100 PIPER 180 ML",
                    brand="100 PIPER",
                    ml=180,
                    quantity=6,
                )
            ],
        )

    async def fake_prepare(session, job_id):
        with conn() as db:
            db.execute(
                "UPDATE import_items SET excise_item_code='735', mapping_status='UNMAPPED' WHERE job_id=?",
                (job_id,),
            )
        return {"preparedCount": 1}

    async def fake_workspace(session, capture=None, latest_only=True, job_id=None):
        return {"unmappedItems": [{"exciseItemCode": 735}]}

    monkeypatch.setattr(service_module, "extract_pdf_locally", should_not_use_pdf)
    monkeypatch.setattr(service_module.LlamaCloudClient, "extract_products", fake_llama)
    monkeypatch.setattr(document_import_service.mapping_service, "prepare_document_job", fake_prepare)
    monkeypatch.setattr(document_import_service.mapping_service, "workspace_for_session", fake_workspace)

    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/upload/image",
        headers=auth(session),
        files={"file": ("invoice.png", b"\x89PNG\r\n\x1a\nimage-test", "image/png")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["extraction"]["engine"] == "llamaparse"
    assert payload["normalizedItems"][0]["quantity"] == 6.0
    assert payload["normalizedItems"][0]["loose"] == 6


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
    assert payload["job"]["status"] == "REVIEW_REQUIRED"
    assert payload["summary"]["detected"] == 2
    assert payload["summary"]["recognized"] == 0
    assert payload["summary"]["needMapping"] == 2
    assert payload["summary"]["reviewRequired"] is True
    assert payload["extractedDocument"]["invoiceNumber"] == "IND-LUCK-2026-00042"
    assert payload["extractedDocument"]["invoiceDate"] == "2026-06-16"
    assert payload["extractedDocument"]["transportPassNo"] == "WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674"

    normalized = payload["normalizedItems"]
    assert normalized[0]["rawName"] == "ROYAL STAG PREMIER WHISKY"
    assert normalized[0]["ml"] == 750
    assert normalized[0]["packing"] is None
    assert normalized[0]["quantity"] == 26.0
    assert normalized[0]["box"] == 2
    assert normalized[0]["loose"] == 24
    assert normalized[1]["rawName"] == "100 PIPERS DELUXE SCOTCH WHISKY"
    assert normalized[1]["ml"] == 180
    assert normalized[1]["packing"] is None
    assert normalized[1]["box"] == 1
    assert normalized[1]["loose"] == 48

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
    assert raw["box"] == 2
    assert raw["loose"] == 24
    assert raw["quantity"] == 26
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
    assert normalized[0].packing is None
    assert normalized[0].quantity == 13.0
    assert normalized[0].box == 1
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


def test_pdf_normalization_promotes_nested_physical_qty_to_loose_quantity():
    document = ExtractedDocument(
        documentType="invoice",
        items=[
            ExtractedProduct(
                itemName="ROYAL STAG 180ML",
                brand="ROYAL STAG 180ML",
                ml=180,
                quantity=None,
                **{
                    "rawPdfRow": {
                        "Brand Name": "ROYAL STAG 180ML",
                        "Physical Qty": "36",
                    }
                },
            )
        ],
    )

    rows = normalize_extracted_document(document, "DOCUMENT_PDF")
    assert len(rows) == 1
    assert rows[0].rawName == "ROYAL STAG 180ML"
    assert rows[0].quantity == 36.0
    assert rows[0].loose == 36
    assert rows[0].rawData["rawPdfRow"]["Physical Qty"] == "36"


def test_pdf_normalization_accepts_generic_physical_stock_bottle_field():
    document = ExtractedDocument(
        documentType="invoice",
        items=[
            ExtractedProduct(
                itemName="JOHNNIE WALKER RED LABEL BLENDED SCOTCH WHISKY",
                brand="JOHNNIE WALKER RED LABEL BLENDED SCOTCH WHISKY",
                ml=750,
                quantity=None,
                **{
                    "sourceRow": {
                        "Brand": "JOHNNIE WALKER RED LABEL BLENDED SCOTCH WHISKY",
                        "Physical Stock (Btls.)": "18 Bottles",
                    }
                },
            )
        ],
    )

    rows = normalize_extracted_document(document, "DOCUMENT_PDF")
    assert len(rows) == 1
    assert rows[0].quantity == 18.0
    assert rows[0].loose == 18
    assert rows[0].box is None


def test_pdf_continuation_page_repairs_llamaparse_packing_quantity_shift():
    document = ExtractedDocument(
        documentType="invoice",
        items=[
            ExtractedProduct(
                itemName="JOHNNIE WALKER RED LABEL BLENDED SCOTCH WHISKY",
                brand="JOHNNIE WALKER",
                ml="750 ML",
                packing=1,
                quantity=None,
                box=None,
                loose=None,
                rate=None,
                boxRate=None,
                looseRate=None,
                mrp=None,
                discount=None,
                amount=12584.22,
                **{"sourcePage": 2},
            ),
            ExtractedProduct(
                itemName="STERLING RESERVE B7 ORIGINAL BLENDED WHISKY",
                brand="STERLING RESERVE",
                ml="375 ML",
                packing=4,
                quantity=None,
                box=None,
                loose=None,
                rate=None,
                boxRate=None,
                looseRate=None,
                mrp=None,
                discount=None,
                amount=12697.11,
                **{"sourcePage": 2},
            ),
        ],
    )

    rows = normalize_extracted_document(document, "DOCUMENT_PDF")
    assert rows[0].quantity == 1.0
    assert rows[0].loose == 1
    assert rows[1].quantity == 4.0
    assert rows[1].loose == 4
    # Extracted packing remains audit-only; Madhushala Item Master supplies
    # real packing later.
    assert rows[0].packing is None
    assert rows[1].packing is None


def test_pdf_first_page_real_packing_is_not_mistaken_for_quantity():
    document = ExtractedDocument(
        documentType="invoice",
        items=[
            ExtractedProduct(
                itemName="SOME WHISKY 180 ML",
                brand="SOME WHISKY",
                ml=180,
                packing=48,
                quantity=None,
                rate=None,
                boxRate=None,
                looseRate=None,
                mrp=None,
                discount=None,
                **{"sourcePage": 1},
            )
        ],
    )

    rows = normalize_extracted_document(document, "DOCUMENT_PDF")
    assert rows[0].quantity is None
    assert rows[0].loose is None


def test_pdf_upload_surfaces_missing_quantity_in_review_before_mapping(client, monkeypatch):
    from app.main import document_import_service
    from app.modules.document_import import service as service_module

    def fake_local_extract(_path):
        return None, {
            "engine": "pymupdf",
            "usable": False,
            "reason": "insufficient_text_layer",
        }

    async def fake_scanned_pages(self, temp_path, filename):
        return ExtractedDocument(
            documentType="invoice",
            supplierName="Supplier",
            items=[
                ExtractedProduct(
                    itemName="UNRESOLVED WHISKY 750 ML",
                    brand="UNRESOLVED",
                    ml=750,
                    packing=12,
                    quantity=None,
                    **{"sourcePage": 1},
                )
            ],
        )

    called = {"prepare": False}

    async def should_not_prepare(*_args, **_kwargs):
        called["prepare"] = True
        raise AssertionError("Mapping must not run before user review confirmation")

    monkeypatch.setattr(service_module, "extract_pdf_locally", fake_local_extract)
    monkeypatch.setattr(service_module.LlamaCloudClient, "extract_scanned_pdf_pages", fake_scanned_pages)
    monkeypatch.setattr(document_import_service.mapping_service, "prepare_document_job", should_not_prepare)

    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/upload/pdf",
        headers=auth(session),
        files={"file": ("missing-qty.pdf", b"%PDF-1.4 missing-qty", "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["status"] == "REVIEW_REQUIRED"
    assert payload["summary"]["needsAttention"] == 1
    assert payload["reviewItems"][0]["box"] == 0
    assert payload["reviewItems"][0]["loose"] == 0
    assert payload["reviewItems"][0]["valid"] is False
    assert called["prepare"] is False



def test_review_confirm_persists_edits_then_starts_mapping(client, monkeypatch):
    from app.db import conn
    from app.main import document_import_service
    from app.modules.document_import import service as service_module

    document = ExtractedDocument(
        documentType="invoice",
        supplierName="Supplier",
        items=[
            ExtractedProduct(
                itemName="ROYAL STAG DELUXE WHISKY",
                brand="ROYAL STAG",
                ml=750,
                quantity=2,
            )
        ],
    )

    def fake_local_extract(_path):
        return document, {"engine": "pymupdf", "usable": True}

    called = {"prepare": 0}

    async def fake_prepare(session, job_id):
        called["prepare"] += 1
        with conn() as db:
            db.execute(
                "UPDATE import_items SET excise_item_code='991', mapping_status='UNMAPPED' WHERE job_id=?",
                (job_id,),
            )
        return {"preparedCount": 1}

    async def fake_workspace(session, capture=None, latest_only=True, job_id=None):
        return {
            "unmappedItems": [
                {
                    "jobItemId": "row",
                    "exciseItemCode": "991",
                    "selectedItemCode": None,
                }
            ]
        }

    monkeypatch.setattr(service_module, "extract_pdf_locally", fake_local_extract)
    monkeypatch.setattr(document_import_service.mapping_service, "prepare_document_job", fake_prepare)
    monkeypatch.setattr(document_import_service.mapping_service, "workspace_for_session", fake_workspace)

    session = create_session(client)
    upload = client.post(
        "/api/v1/document-import/upload/pdf",
        headers=auth(session),
        files={"file": ("review.pdf", b"%PDF-1.4 review", "application/pdf")},
    )
    assert upload.status_code == 200, upload.text
    payload = upload.json()
    assert payload["job"]["status"] == "REVIEW_REQUIRED"
    assert called["prepare"] == 0

    row = payload["reviewItems"][0]
    confirm = client.post(
        f"/api/v1/document-import/jobs/{payload['job']['id']}/review/confirm",
        headers=auth(session),
        json={
            "items": [
                {
                    "id": row["id"],
                    "name": "ROYAL STAG DELUXE WHISKY EDITED",
                    "brand": "ROYAL STAG",
                    "ml": 750,
                    "box": 1,
                    "loose": 5,
                }
            ]
        },
    )
    assert confirm.status_code == 200, confirm.text
    confirmed = confirm.json()
    assert called["prepare"] == 1
    assert confirmed["job"]["status"] == "MAPPING_REQUIRED"
    assert confirmed["reviewItems"][0]["name"] == "ROYAL STAG DELUXE WHISKY EDITED"
    assert confirmed["reviewItems"][0]["box"] == 1
    assert confirmed["reviewItems"][0]["loose"] == 5

    items = client.get(
        f"/api/v1/document-import/jobs/{payload['job']['id']}/items",
        headers=auth(session),
    ).json()["items"]
    assert items[0]["raw_name"] == "ROYAL STAG DELUXE WHISKY EDITED"
    assert items[0]["quantity"] == 6.0
    assert items[0]["box"] == 1
    assert items[0]["loose"] == 5
    raw = json.loads(items[0]["raw_data_json"])
    assert raw["quantity"] == 6
    assert raw["loose"] == 5
    assert raw["box"] == 1


def test_pymupdf_state_adapters_emit_canonical_box_loose():
    from app.modules.document_import.pdf_extractor import (
        _extract_jharkhand,
        _extract_madhya_pradesh,
        _extract_telangana,
        _extract_west_bengal,
    )

    telangana_rows = [[
        "1", "5016", "", "KING FISHER PREMIUM LAGER BEER", "", "Beer", "G",
        "12 / 650 ml", "7", "", "3", "1,501.00 / 125.08", "", "10,507.00",
    ]]
    products, _, _ = _extract_telangana(
        "GOVERNMENT OF TELANGANA\nICDC001160126020077\nInvoice Date: 16-Jan-2026",
        [(1, "GOVERNMENT OF TELANGANA ORIGINAL", [telangana_rows])],
    )
    assert len(products) == 1
    assert products[0].ml == 650
    assert products[0].box == 7
    assert products[0].loose == 3

    jharkhand_rows = [[
        "Comodity Group", "Kind Of Intoxicant", "Label Name", "Unit",
        "Physical Qty", "StockQty", "Qty", "Strength", "LPL", "BL",
    ], [
        "1", "beer", "KINGFISHER FINEST STRONG BEER", "650 ML",
        "0", "754", "17.20", "9.00-BL", "", "1498.24",
    ], [
        "2", "whisky", "ROYAL STAG DELUXE WHISKY", "750ML",
        "0", "754", "2.00", "9.00-BL", "", "1498.24",
    ]]
    products, _, _ = _extract_jharkhand(
        "Jharkhand State Beverages Corporation Limited\nExcise Permit No: DHA-2026-2027/6267\nIssued Date 01/09/2026",
        [(2, "INVOICE DETAIL", [jharkhand_rows])],
    )
    assert len(products) == 2
    assert products[0].ml == 650
    assert products[0].box == 17
    assert products[0].loose == 20
    assert products[0].model_dump()["quantitySemantics"] == "jharkhand_cases_dot_loose"
    assert products[0].model_dump()["rawPdfRow"]["Quantity (Cases)"] == "17.20"
    assert products[1].box == 2
    assert products[1].loose == 0



    mp_rows = [[
        "Sl", "Label Name", "Batch", "Capacity", "Quantity in Cases",
        "Duty", "Mfg Amount", "VAT",
    ], [
        "1", "Masala country spirit [CL/2023-2024/0012]", "",
        "180 (Pet Bottle)", "109", "283263.75", "79134.00", "51230.00",
    ]]
    products, invoice_number, invoice_date = _extract_madhya_pradesh(
        "Madhya Pradesh Excise Department\nDelivery Challan\nDemand Id- tCSDR/2026-2027/00288547\nDate- 17/08/2026",
        [(1, "Madhya Pradesh Excise Department Delivery Challan", [mp_rows])],
    )
    assert len(products) == 1
    assert products[0].itemName.startswith("Masala country spirit")
    assert products[0].ml == 180
    assert products[0].box == 109
    assert products[0].loose == 0
    assert products[0].model_dump()["packageType"] == "Pet Bottle"
    assert products[0].model_dump()["quantitySemantics"] == "capacity_is_ml_quantity_is_cases"
    assert products[0].model_dump()["rawPdfRow"]["Capacity"] == "180 (Pet Bottle)"
    assert invoice_number == "tCSDR/2026-2027/00288547"
    assert invoice_date == "17/08/2026"

    wb_rows = [
        [
            "Kind of Foreign Liquor(IMFL/OSBI/OS)", "Category", "Brand Name", "Measure",
            "Strength", "Batch No. & Date", "Quantity", "", "", "", "Amount",
        ],
        ["", "", "", "", "", "", "In Cases", "In Bottles", "In B.L", "In LPL", ""],
        [
            "IMFL", "Whisky", "MCDOWELLS NO 1 LUXURY WHISKY", "180 Ml.",
            "25 Under Proof", "265-1 & July,2026", "3 - 0", "144", "25.92", "19.44", "16848.00",
        ],
    ]
    products, _, _ = _extract_west_bengal(
        "ORIGINAL\nWest Bengal Excise Foreign Liquor Form No 3\nTransport Pass No. : tFLDR/2026-2027/07015578/P\nDate : 17/08/2026",
        [(1, "ORIGINAL", [wb_rows])],
    )
    assert len(products) == 1
    assert products[0].box == 3
    # West Bengal Form No. 3 uses the compound Case field as the purchase
    # quantity source. "3 - 0" means box=3, loose=0; the separate
    # "In Bottles" value is audit-only for this profile.
    assert products[0].loose == 0
    assert products[0].model_dump()["quantitySemantics"] == "west_bengal_case_field_only"
    assert products[0].model_dump()["rawPdfRow"]["In Bottles"] == "144"






def test_west_bengal_ignores_duplicate_triplicate_and_quadruplicate_copies():
    from app.modules.document_import.pdf_extractor import _extract_west_bengal

    rows = [
        [
            "Kind of Foreign Liquor(IMFL/OSBI/OS)", "Category", "Brand Name", "Measure",
            "Strength", "Batch No. & Date", "Quantity", "", "", "", "Amount",
        ],
        ["", "", "", "", "", "", "In Cases", "In Bottles", "In B.L", "In LPL", ""],
        ["IMFL", "Whisky", "TEST 750", "750 Ml.", "25 Under Proof", "B1", "3 - 0", "36", "27.00", "20.25", "1000.00"],
    ]
    pages = [
        (1, "ORIGINAL\nWest Bengal Excise Foreign Liquor Form No 3", [rows]),
        (3, "DUPLICATE\nWest Bengal Excise Foreign Liquor Form No 3", [rows]),
        (5, "TRIPLICATE\nWest Bengal Excise Foreign Liquor Form No 3", [rows]),
        (7, "QUADRUPLICATE\nWest Bengal Excise Foreign Liquor Form No 3", [rows]),
    ]

    products, _, _ = _extract_west_bengal(
        "Transport Pass No. : tFLDR/2026-2027/07015578/P\nDate : 17/08/2026",
        pages,
    )

    assert len(products) == 1
    assert products[0].box == 3
    assert products[0].loose == 0

def test_jharkhand_llama_quantity_decoder_preserves_two_digit_loose_suffix():
    from app.modules.document_import.llama_client import _decode_jharkhand_quantity_text

    assert _decode_jharkhand_quantity_text("17.20") == (17, 20)
    assert _decode_jharkhand_quantity_text("15.00") == (15, 0)
    assert _decode_jharkhand_quantity_text("2.04") == (2, 4)
    assert _decode_jharkhand_quantity_text("20") is None




@pytest.mark.asyncio
async def test_west_bengal_native_extraction_never_merges_llama_copy_rows(monkeypatch):
    from app.modules.document_import.service import DocumentImportService
    from app.modules.document_import import service as service_module

    primary = ExtractedDocument(
        documentType="invoice",
        invoiceNumber="tFLDR/2026-2027/07015578/P",
        invoiceDate="17/08/2026",
        items=[
            ExtractedProduct(itemName=f"ITEM {i} 750 ML", brand=f"ITEM {i}", ml=750, box=i, loose=0)
            for i in range(1, 7)
        ],
        extractionEngine="pymupdf-state-adapter",
        extractionProfile="WEST_BENGAL_FORM3",
        sourceState="WEST_BENGAL",
    )

    def fake_local(_path):
        return primary, {
            "engine": "pymupdf-state-adapter",
            "usable": True,
            "profile": "WEST_BENGAL_FORM3",
            "productCount": 6,
            "candidateRowCount": 22,
            "completeness": 6 / 22,
            "needsFallback": True,
        }

    async def should_not_run_llama(self, file_path, filename):
        raise AssertionError("Llama fallback must not run for a successful WB ORIGINAL table")

    monkeypatch.setattr(service_module, "extract_pdf_locally", fake_local)
    monkeypatch.setattr(
        service_module.LlamaCloudClient,
        "extract_scanned_pdf_pages",
        should_not_run_llama,
    )

    class FakeMappingService:
        pass

    service = DocumentImportService(FakeMappingService())
    extracted, meta = await service._extract_upload_part(
        b"%PDF-1.4 wb-test",
        "pdf",
        "wb-form3.pdf",
        1,
    )

    assert len(extracted.items) == 6
    assert meta["engine"] == "pymupdf-state-adapter"
    assert meta["needsFallback"] is False
    assert meta["fallbackSuppressed"] == "west_bengal_original_is_authoritative"

def test_multi_pdf_batch_merges_same_purchase_into_one_review(client, monkeypatch):
    from app.modules.document_import.service import DocumentImportService

    async def fake_extract_part(self, data, ext, filename, source_index):
        name = "ROYAL STAG 750 ML" if source_index == 1 else "SIGNATURE 375 ML"
        payload = ExtractedDocument(
            documentType="invoice",
            invoiceNumber="INV-100",
            invoiceDate="18/09/2026",
            items=[
                ExtractedProduct(
                    itemName=name,
                    brand=name,
                    ml=750 if source_index == 1 else 375,
                    box=2 if source_index == 1 else 1,
                    loose=3 if source_index == 1 else 4,
                    **{
                        "sourceFilename": filename,
                        "sourceFileIndex": source_index,
                    },
                )
            ],
        )
        return payload, {
            "engine": "fake",
            "filename": filename,
            "sourceFileIndex": source_index,
        }

    monkeypatch.setattr(DocumentImportService, "_extract_upload_part", fake_extract_part)

    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/upload/batch/pdf",
        headers=auth(session),
        files=[
            ("files", ("page-1.pdf", b"%PDF-1.4 page1", "application/pdf")),
            ("files", ("page-2.pdf", b"%PDF-1.4 page2", "application/pdf")),
        ],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["job"]["status"] == "REVIEW_REQUIRED"
    assert payload["summary"]["detected"] == 2
    assert payload["summary"]["sourceFiles"] == 2
    assert payload["extractedDocument"]["invoiceNumber"] == "INV-100"
    assert [row["sourceFile"] for row in payload["reviewItems"]] == ["page-1.pdf", "page-2.pdf"]
    assert payload["reviewItems"][0]["box"] == 2
    assert payload["reviewItems"][0]["loose"] == 3
    assert payload["reviewItems"][1]["box"] == 1
    assert payload["reviewItems"][1]["loose"] == 4


def test_multi_pdf_batch_rejects_different_document_numbers(client, monkeypatch):
    from app.modules.document_import.service import DocumentImportService

    async def fake_extract_part(self, data, ext, filename, source_index):
        return ExtractedDocument(
            documentType="invoice",
            invoiceNumber="INV-A" if source_index == 1 else "INV-B",
            invoiceDate="2026-09-18",
            items=[
                ExtractedProduct(
                    itemName=f"ITEM {source_index} 750 ML",
                    brand=f"ITEM {source_index}",
                    ml=750,
                    box=1,
                    loose=0,
                    **{
                        "sourceFilename": filename,
                        "sourceFileIndex": source_index,
                    },
                )
            ],
        ), {"engine": "fake"}

    monkeypatch.setattr(DocumentImportService, "_extract_upload_part", fake_extract_part)

    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/upload/batch/pdf",
        headers=auth(session),
        files=[
            ("files", ("a.pdf", b"%PDF-1.4 a", "application/pdf")),
            ("files", ("b.pdf", b"%PDF-1.4 b", "application/pdf")),
        ],
    )

    assert response.status_code == 422
    assert "different document numbers" in response.json()["detail"]


def test_company_scoped_product_mapping_does_not_leak_to_other_company(client):
    from app.db import conn
    from app.main import document_import_service
    from app.services.session_service import session_service

    session_two_response = create_session(client)
    session_two = session_service.by_token(session_two_response["sessionToken"])
    session_three_response = client.post(
        "/crm/session",
        json={
            "shopCode": "SHOP_A",
            "companyCode": "3",
            "billType": "AI",
            "accessToken": "token",
        },
    ).json()
    session_three = session_service.by_token(session_three_response["sessionToken"])

    normalized = normalize_extracted_document(
        ExtractedDocument(
            documentType="invoice",
            items=[
                ExtractedProduct(
                    itemName="SAME PRODUCT 750 ML",
                    brand="SAME PRODUCT",
                    ml=750,
                    box=1,
                    loose=0,
                )
            ],
        ),
        "DOCUMENT_PDF",
    )

    job_two = document_import_service._create_job(session_two, "DOCUMENT_PDF", "c2.pdf")
    document_import_service._persist_items(job_two, normalized)
    with conn() as db:
        row = db.execute("SELECT id FROM import_items WHERE job_id=?", (job_two,)).fetchone()
        db.execute(
            "UPDATE import_items SET mapped_item_code='C2ITEM', mapping_status='MAPPED', updated_at='2026-09-18T00:00:00Z' WHERE id=?",
            (row["id"],),
        )

    job_three = document_import_service._create_job(session_three, "DOCUMENT_PDF", "c3.pdf")
    document_import_service._persist_items(job_three, normalized)
    with conn() as db:
        row = db.execute(
            "SELECT mapped_item_code, mapping_status FROM import_items WHERE job_id=?",
            (job_three,),
        ).fetchone()

    assert not str(row["mapped_item_code"] or "").strip()
    assert row["mapping_status"] != "MAPPED"
