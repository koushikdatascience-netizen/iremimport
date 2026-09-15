from __future__ import annotations

import base64
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import init_db


UP_EXCISE_QR_URL = (
    "https://cms.upexciseonline.co/transport-pass-tracking/"
    "?tpnum=WHOLESALE1501-FL2-RETAIL995782-FL4C-LUCK-Jun26_00000674"
    "&tptype=FG&tpyear=2026"
)

# Small valid PNG. The decoder result is monkeypatched so this test exercises
# upload/session/image handling without depending on a generated QR fixture.
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII="
)


@pytest.fixture()
def client():
    db_path = os.path.join(tempfile.gettempdir(), "madhushala_qr_fallback_test.db")
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass
    object.__setattr__(settings, "DATABASE_PATH", db_path)
    object.__setattr__(settings, "CRM_INTEGRATION_KEY", "")
    init_db()
    from app.main import app

    return TestClient(app)


def create_session(client: TestClient) -> dict:
    response = client.post(
        "/crm/session",
        json={
            "shopCode": "SHOP_A",
            "companyCode": "2",
            "billType": "AI",
            "accessToken": "token",
        },
    )
    assert response.status_code == 200
    return response.json()


def auth(session: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['sessionToken']}"}


def test_document_import_loads_qr_compatibility_script(client: TestClient):
    response = client.get("/document-import")
    assert response.status_code == 200
    assert '<script src="./static/qr-browser-fallback.js"></script>' in response.text


def test_server_qr_decode_endpoint_returns_detected_url(client: TestClient, monkeypatch):
    from app.modules.document_import import qr_decoder

    class FakeBarcode:
        text = UP_EXCISE_QR_URL
        format = qr_decoder.zxingcpp.BarcodeFormat.QRCode

    monkeypatch.setattr(qr_decoder.zxingcpp, "read_barcodes", lambda _image: [FakeBarcode()])

    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/qr/decode",
        headers=auth(session),
        files={"file": ("transport-pass.png", ONE_PIXEL_PNG, "image/png")},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"value": UP_EXCISE_QR_URL}


def test_server_qr_decode_rejects_unreadable_image(client: TestClient):
    session = create_session(client)
    response = client.post(
        "/api/v1/document-import/qr/decode",
        headers=auth(session),
        files={"file": ("bad.png", b"not-an-image", "image/png")},
    )
    assert response.status_code == 400
    assert "readable image" in response.json()["detail"]
