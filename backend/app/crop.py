"""Crop a detected item's box out of a Trending inspiration image. Boxes are
padded ~8% by default: tight detector boxes crop out context that helps the
Sources' own "search by image" match better.
"""
import io
import math

from PIL import Image

MAX_DIMENSION = 500
JPEG_QUALITY = 82
DEFAULT_PAD_FRACTION = 0.08


def crop_and_encode(
    image_bytes: bytes,
    box: tuple[float, float, float, float],
    pad: bool = True,
) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img_w, img_h = img.size

    x1, y1, x2, y2 = box
    pad_x = (x2 - x1) * DEFAULT_PAD_FRACTION if pad else 0
    pad_y = (y2 - y1) * DEFAULT_PAD_FRACTION if pad else 0

    left = max(0, math.floor(x1 - pad_x))
    top = max(0, math.floor(y1 - pad_y))
    right = min(img_w, math.ceil(x2 + pad_x))
    bottom = min(img_h, math.ceil(y2 + pad_y))
    right = max(right, left + 1)
    bottom = max(bottom, top + 1)

    cropped = img.crop((left, top, right, bottom))
    cropped.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()
