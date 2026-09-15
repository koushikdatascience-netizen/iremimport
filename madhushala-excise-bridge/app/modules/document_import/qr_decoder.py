from __future__ import annotations

from io import BytesIO

import zxingcpp
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_QR_IMAGE_BYTES = 12 * 1024 * 1024
MAX_QR_IMAGE_PIXELS = 30_000_000
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/bmp",
    "image/gif",
    "application/octet-stream",
}


def decode_qr_image_bytes(data: bytes) -> str:
    if not data:
        raise HTTPException(status_code=400, detail="QR image is empty")
    if len(data) > MAX_QR_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="QR image is too large")

    try:
        with Image.open(BytesIO(data)) as source:
            source = ImageOps.exif_transpose(source)
            width, height = source.size
            if width <= 0 or height <= 0:
                raise HTTPException(status_code=400, detail="QR image has invalid dimensions")
            if width * height > MAX_QR_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="QR image dimensions are too large")
            image = source.convert("RGB")
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Uploaded file is not a readable image") from exc

    try:
        barcodes = zxingcpp.read_barcodes(image)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="QR image could not be decoded") from exc

    # Prefer an actual QR result when an image contains multiple barcode types.
    for barcode in barcodes:
        value = str(getattr(barcode, "text", "") or "").strip()
        if value and getattr(barcode, "format", None) == zxingcpp.BarcodeFormat.QRCode:
            return value

    # Some zxing-cpp builds expose equivalent format values differently. If a
    # readable barcode was returned, let the existing QR-link validator decide
    # whether its text is a supported HTTP(S) URL.
    for barcode in barcodes:
        value = str(getattr(barcode, "text", "") or "").strip()
        if value:
            return value

    raise HTTPException(status_code=422, detail="No QR code detected in this image")


async def decode_qr_upload(file: UploadFile) -> str:
    content_type = str(file.content_type or "").casefold().strip()
    if content_type and content_type not in ALLOWED_IMAGE_TYPES and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Upload a QR image file")

    data = await file.read(MAX_QR_IMAGE_BYTES + 1)
    if len(data) > MAX_QR_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="QR image is too large")
    return decode_qr_image_bytes(data)
