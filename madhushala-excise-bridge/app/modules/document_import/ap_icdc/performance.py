"""Opt-in ROI OCR. Full-page mode remains the accuracy baseline.

Only crop the uninterrupted item-table region on middle pages, and translate
bounding boxes back into page coordinates. Last page stays full to preserve fees.
"""
from __future__ import annotations
import cv2
from .extract import grid, ocr_boxes

def ocr_page(engine, image, page_number, page_count, *, mode="full"):
    height, width = image.shape[:2]
    if mode != "table" or page_number == 1 or page_number == page_count:
        return ocr_boxes(engine, image), {"mode": "full", "saved_pixels": 0}
    xs, ys = grid(image)
    # Only crop when an unambiguous 10-column item table exists.
    if len(xs) != 11 or len(ys) < 3:
        return ocr_boxes(engine, image), {"mode": "full_grid_fallback", "saved_pixels": 0}
    # Include full horizontal width to avoid cutting merged OCR boxes.
    top = max(0, ys[0] - 45)
    bottom = min(height, ys[-1] + 90)
    if top > height * .20 or bottom < height * .70 or bottom - top > height * .96:
        return ocr_boxes(engine, image), {"mode": "full_unsafe_crop", "saved_pixels": 0}
    cropped = image[top:bottom, :].copy()
    boxes = ocr_boxes(engine, cropped)
    for box in boxes:
        box["y0"] += top
        box["y1"] += top
    return boxes, {"mode": "table", "saved_pixels": width * (height - (bottom-top)), "crop": [0, top, width, bottom]}
