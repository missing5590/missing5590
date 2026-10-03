"""Chekni qabul qilish va soliq saytidan ma'lumot olish."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from . import soliq
from .db import Database, Duplicate

log = logging.getLogger(__name__)


@dataclass
class SubmitResult:
    ok: bool
    message: str
    receipt_id: int | None = None


def fmt_money(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def summary(row: dict) -> str:
    items = json.loads(row["items_json"]) if row.get("items_json") else []
    return (
        f"Chek №{row['check_number']} ({row['check_date'].replace('T', ' ')})\n"
        f"{row.get('seller_name') or 'Sotuvchi ?'} (STIR {row.get('seller_inn') or '?'})\n"
        f"{len(items)} ta qator, jami {fmt_money(row.get('total'))} so'm"
    )


class ReceiptService:
    def __init__(self, db: Database):
        self.db = db

    async def submit(self, url: str, *, chat_id: int | None = None, message_id: int | None = None,
                     image_path: str | None = None) -> SubmitResult:
        try:
            ref = soliq.parse_check_url(url)
        except soliq.CheckUrlError as exc:
            return SubmitResult(False, f"❌ Havola noto'g'ri: {exc}")

        try:
            receipt_id = self.db.insert_receipt(
                url=ref.url, fm=ref.fm, check_number=ref.check_number, check_date=ref.check_date,
                fp=ref.fp, chat_id=chat_id, message_id=message_id, image_path=image_path,
            )
        except Duplicate as dup:
            existing = dup.existing
            extra = f", 1C: {existing['onec_document']}" if existing.get("onec_document") else ""
            return SubmitResult(
                False,
                f"⚠️ Bu chek allaqachon yuborilgan: №{ref.check_number} "
                f"(holati: {existing.get('status', '?')}{extra})",
                existing.get("id"),
            )

        row = await self.fetch_and_parse(receipt_id)
        return SubmitResult(row["status"] == "ready", self.status_message(row), receipt_id)

    async def fetch_and_parse(self, receipt_id: int) -> dict:
        row = self.db.get(receipt_id)
        attempts = row["fetch_attempts"] + 1
        try:
            html = await soliq.fetch_check_html(row["url"])
            if soliq.looks_not_found(html):
                raise soliq.FetchError("Chek soliq bazasida hali topilmadi")
        except soliq.FetchError as exc:
            self.db.update(receipt_id, status="fetch_error", last_error=str(exc),
                           fetch_attempts=attempts)
            log.warning("Chek %s: %s", receipt_id, exc)
            return self.db.get(receipt_id)

        self.db.update(receipt_id, raw_html=html, fetch_attempts=attempts)
        return self.apply_parse(receipt_id, html)

    def apply_parse(self, receipt_id: int, html: str) -> dict:
        try:
            parsed = soliq.parse_check_html(html)
        except Exception as exc:  # parser xatosi chekni yo'qotmasligi kerak
            log.exception("Chek %s tahlilida xato", receipt_id)
            self.db.update(receipt_id, status="parse_error", last_error=f"Parser xatosi: {exc}")
            return self.db.get(receipt_id)

        data = parsed.to_dict()
        fields = dict(
            seller_name=parsed.seller_name, seller_inn=parsed.seller_inn, address=parsed.address,
            total=parsed.total, vat_total=parsed.vat_total,
            items_json=json.dumps(data["items"], ensure_ascii=False),
        )
        if not parsed.items:
            self.db.update(receipt_id, status="parse_error",
                           last_error="Sahifadan tovarlar ajratilmadi", **fields)
        else:
            self.db.update(receipt_id, status="ready", last_error=None, **fields)
        return self.db.get(receipt_id)

    @staticmethod
    def status_message(row: dict) -> str:
        status = row["status"]
        if status == "ready":
            return "✅ Qabul qilindi, 1C ga yuklashga tayyor.\n" + summary(row)
        if status == "fetch_error":
            return (
                f"⏳ Chek №{row['check_number']} qabul qilindi, lekin soliq saytidan ma'lumot hali "
                f"olinmadi ({row['last_error']}). Avtomatik qayta urinib ko'raman."
            )
        if status == "parse_error":
            return (
                f"⚠️ Chek №{row['check_number']} qabul qilindi, lekin tovarlar o'qilmadi. "
                "Administrator tekshiradi."
            )
        return f"Chek №{row['check_number']}: {status}"
