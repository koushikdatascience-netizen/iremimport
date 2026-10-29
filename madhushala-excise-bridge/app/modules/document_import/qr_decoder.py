from __future__ import annotations

from io import BytesIO

import zxingcpp
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, ImageEnhance, ImageFilter, UnidentifiedImageError


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

    # Start with the original pixels. Only run bounded fallback passes if
    # necessary; this avoids slowing down QR codes that already decode.
    variants = [image]
    grayscale = ImageOps.autocontrast(ImageOps.grayscale(image))
    variants.append(grayscale)
    variants.append(grayscale.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=2)))
    variants.append(ImageEnhance.Contrast(grayscale).enhance(1.7))
    width, height = image.size
    # A QR often takes a small fraction of a photograph. Crop overlapping
    # regions so barcode detection sees it at a more useful relative scale.
    for left, top, right, bottom in (
        (0, 0, width // 2, height // 2),
        (width // 2, 0, width, height // 2),
        (0, height // 2, width // 2, height),
        (width // 2, height // 2, width, height),
        (width // 4, height // 4, 3 * width // 4, 3 * height // 4),
    ):
        crop = grayscale.crop((left, top, right, bottom))
        if crop.width >= 100 and crop.height >= 100:
            variants.append(crop)

    # WhatsApp photographs often contain a tiny, compressed QR within a full
    # page. Re-sample focused windows before giving up. Keep a strict bound
    # on candidate size and number of reads to avoid decode DoS.
    def _decode(candidate):
        try:
            barcodes = zxingcpp.read_barcodes(candidate)
        except Exception:
            return None
        for barcode in barcodes:
            value = str(getattr(barcode, "text", "") or "").strip()
            if value and getattr(barcode, "format", None) == zxingcpp.BarcodeFormat.QRCode:
                return value
        for barcode in barcodes:
            value = str(getattr(barcode, "text", "") or "").strip()
            if value:
                return value
        return None

    for candidate in variants:
        value = _decode(candidate)
        if value:
            return value

    # The QR can occupy less than 200 source pixels. Bicubic/Lanczos resizing
    # and thresholding often rescue photographed codes; do not enlarge an
    # already large image or turn the entire original into many huge variants.
    for candidate in variants[1:]:
        if candidate.width < 70 or candidate.height < 70:
            continue
        if max(candidate.size) > 2200:
            continue
        scale = min(3, max(1, 800 // max(candidate.size)))
        if scale > 1:
            enlarged = candidate.resize((candidate.width * scale, candidate.height * scale),
                                         Image.Resampling.LANCZOS)
            value = _decode(enlarged)
            if value:
                return value
        else:
            enlarged = candidate
        for threshold in (110, 150, 190):
            binary = enlarged.point(lambda p, t=threshold: 255 if p > t else 0)
            value = _decode(binary)
            if value:
                return value

    raise HTTPException(status_code=422, detail="No QR code detected after enhanced photo decoding")


async def decode_qr_upload(file: UploadFile) -> str:
    content_type = str(file.content_type or "").casefold().strip()
    if content_type and content_type not in ALLOWED_IMAGE_TYPES and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Upload a QR image file")

    data = await file.read(MAX_QR_IMAGE_BYTES + 1)
    if len(data) > MAX_QR_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="QR image is too large")
    return decode_qr_image_bytes(data)
