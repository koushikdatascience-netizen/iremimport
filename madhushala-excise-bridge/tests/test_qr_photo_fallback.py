"""QR photographs must retry ZXing after ordinary detection fails."""
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from app.modules.document_import import qr_decoder


def _photo():
    out = BytesIO()
    Image.new("RGB", (720, 1280), "white").save(out, format="JPEG")
    return out.getvalue()


def test_enhanced_backend_retry(monkeypatch):
    attempts = []
    def fake_decoder(image):
        attempts.append(image.size)
        if len(attempts) == 2:
            return [SimpleNamespace(
                text="https://portal.haryanatax.gov.in/Department/QRGeneration/PassDetails?passheaderid=1010712",
                format=qr_decoder.zxingcpp.BarcodeFormat.QRCode,
            )]
        return []
    monkeypatch.setattr(qr_decoder.zxingcpp, "read_barcodes", fake_decoder)
    result = qr_decoder.decode_qr_image_bytes(_photo())
    assert "passheaderid=1010712" in result
    assert len(attempts) == 2


def test_exhausted_tries_still_return_422(monkeypatch):
    attempts = []
    monkeypatch.setattr(qr_decoder.zxingcpp,"read_barcodes",
                        lambda img: attempts.append(img.size) or [])
    with pytest.raises(HTTPException) as failure:
        qr_decoder.decode_qr_image_bytes(_photo())
    assert failure.value.status_code == 422
    assert len(attempts) > 2


def test_original_image_fast_path(monkeypatch):
    tries = []
    def decode(image):
        tries.append(image.size)
        return [SimpleNamespace(text="https://example.org",format=qr_decoder.zxingcpp.BarcodeFormat.QRCode)]
    monkeypatch.setattr(qr_decoder.zxingcpp,"read_barcodes",decode)
    assert qr_decoder.decode_qr_image_bytes(_photo()) == "https://example.org"
    assert len(tries) == 1
