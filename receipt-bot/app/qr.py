"""Chek rasmidan QR kodni o'qish (zxing-cpp)."""
import io

import zxingcpp
from PIL import Image, ImageOps

from .soliq import find_check_urls


def _variants(image: Image.Image):
    gray = ImageOps.grayscale(image)
    yield image
    yield gray
    # Telefon rasmlarida QR kichik bo'ladi - kattalashtirish ko'p hollarda yordam beradi.
    for scale in (2, 3):
        yield gray.resize((gray.width * scale, gray.height * scale), Image.LANCZOS)
    contrast = ImageOps.autocontrast(gray)
    yield contrast.resize((contrast.width * 2, contrast.height * 2), Image.LANCZOS)
    yield contrast.point(lambda p: 255 if p > 128 else 0).resize(
        (contrast.width * 2, contrast.height * 2), Image.NEAREST
    )


def decode_qr_urls(data: bytes) -> list[str]:
    """Rasmdagi ofd.soliq.uz havolalarini qaytaradi (topilmasa bo'sh ro'yxat)."""
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image).convert("RGB")
    # Juda katta rasmlarni kichraytiramiz, aks holda kattalashtirish xotirani ko'p oladi.
    if max(image.size) > 2500:
        image.thumbnail((2500, 2500))
    for variant in _variants(image):
        texts = [r.text for r in zxingcpp.read_barcodes(variant)]
        urls = find_check_urls("\n".join(texts))
        if urls:
            return urls
    return []
