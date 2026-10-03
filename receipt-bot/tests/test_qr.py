import io

import numpy as np
import zxingcpp
from PIL import Image

from app.qr import decode_qr_urls

URL = "https://ofd.soliq.uz/check?t=NA000000053505&r=415009&c=20260930102457&s=592431510681"


def _png(text: str, scale: int) -> bytes:
    barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    qr = Image.fromarray(np.array(barcode.to_image(scale=scale)))
    canvas = Image.new("L", (qr.width * 4, qr.height * 4), 255)  # QR rasmning kichik qismi
    canvas.paste(qr, (qr.width, qr.height))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def test_decode_qr():
    assert decode_qr_urls(_png(URL, 4)) == [URL]


def test_decode_ignores_other_qr():
    assert decode_qr_urls(_png("https://example.com", 4)) == []
