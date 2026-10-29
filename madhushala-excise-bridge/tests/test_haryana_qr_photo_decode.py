"""Regression for small WhatsApp QR photographs needing an upscale."""
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
from app.modules.document_import import qr_decoder


def test_qr_photo_uses_bounded_upscaled_candidate(monkeypatch):
    image = Image.new("RGB", (600, 900), "white")
    stream = BytesIO()
    image.save(stream, format="JPEG")
    seen = []
    def read_barcodes(candidate):
        seen.append(candidate.size)
        # Mock source that needs a resized cropped image.
        if candidate.width >= 700 and candidate.height >= 700:
            return [SimpleNamespace(text="https://example.org/pass/123",
                                    format=qr_decoder.zxingcpp.BarcodeFormat.QRCode)]
        return []
    monkeypatch.setattr(qr_decoder.zxingcpp, "read_barcodes", read_barcodes)
    assert qr_decoder.decode_qr_image_bytes(stream.getvalue()) == "https://example.org/pass/123"
    assert len(seen) <= 55


def test_empty_photo_fails_with_clear_decode_error():
    from fastapi import HTTPException
    try:
        qr_decoder.decode_qr_image_bytes(b"")
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("Empty QR image should fail")


def test_opencv_fallback_when_zxing_cannot_decode(monkeypatch):
    from unittest.mock import Mock
    image = Image.new("RGB", (720, 1280), "white")
    stream = BytesIO()
    image.save(stream, "JPEG")
    monkeypatch.setattr(qr_decoder.zxingcpp, "read_barcodes", lambda _: [])
    detector = Mock()
    detector.detectAndDecode.return_value = (
        "https://portal.haryanatax.gov.in/Department/QRGeneration/PassDetails?passheaderid=1010712",
        None, None,
    )
    monkeypatch.setattr(qr_decoder.cv2, "QRCodeDetector", lambda: detector)
    result = qr_decoder.decode_qr_image_bytes(stream.getvalue())
    assert "passheaderid=1010712" in result
    assert detector.detectAndDecode.called
