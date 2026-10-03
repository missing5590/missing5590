"""HTTP API (1C uchun) va botni ishga tushirish."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import Settings
from .db import Database, to_public
from .relay import RelaySync
from .service import ReceiptService
from .telegram import TelegramBot, retry_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("receipt-bot")


class AckBody(BaseModel):
    document: str  # 1C hujjat taqdimoti, masalan "Поступление №123 от 30.09.2026"


class FailBody(BaseModel):
    error: str


def create_app(settings: Settings | None = None, start_bot: bool = True) -> FastAPI:
    settings = settings or Settings()
    settings.validate()
    db = Database(settings.db_path, settings.dedup_by)
    service = ReceiptService(db)
    bot = (
        TelegramBot(settings.bot_token, service, settings.images_dir, settings.allowed_chat_ids)
        if settings.bot_token and start_bot else None
    )

    async def notify(row: dict, text: str) -> None:
        if bot:
            await bot.send(row.get("chat_id"), text, row.get("message_id"))

    relay = (
        RelaySync(db, settings.relay_url, settings.relay_key, notify)
        if settings.relay_url and start_bot else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = []
        if relay:
            tasks.append(asyncio.create_task(relay.run(settings.relay_interval)))
            log.info("Xosting rejimi: cheklar %s ga yuboriladi", settings.relay_url)
        if bot:
            tasks.append(asyncio.create_task(bot.run()))
            log.info("Telegram bot ishga tushdi")
        elif start_bot:
            log.warning("BOT_TOKEN ko'rsatilmagan - bot ishlamaydi, faqat API")
        if start_bot:
            tasks.append(asyncio.create_task(
                retry_loop(service, bot, settings.retry_interval, settings.max_fetch_attempts)
            ))
        yield
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if bot:
            await bot.close()
        if relay:
            await relay.close()
        db.close()

    app = FastAPI(title="Receipt bot API", lifespan=lifespan)
    app.state.db = db
    app.state.service = service

    def auth(x_api_key: str = Header(default="")) -> None:
        # API_KEY bo'sh bo'lsa (faqat xosting rejimi) - mahalliy API yopiq.
        if not settings.api_key or not secrets.compare_digest(x_api_key, settings.api_key):
            raise HTTPException(status_code=401, detail="API kalit noto'g'ri")

    def get_row(receipt_id: int) -> dict:
        row = db.get(receipt_id)
        if not row:
            raise HTTPException(status_code=404, detail="Chek topilmadi")
        return row

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/api/receipts/claim", dependencies=[Depends(auth)])
    def claim(limit: int = Query(50, ge=1, le=500)):
        """1C «Yuklash»: tayyor cheklarni oladi. Har biri uchun keyin ack yoki fail yuboriladi."""
        rows = db.claim_ready(limit, settings.lease_minutes)
        return {"receipts": [to_public(r) for r in rows]}

    @app.get("/api/receipts", dependencies=[Depends(auth)])
    def list_receipts(status: str | None = None, limit: int = Query(100, ge=1, le=1000)):
        return {"receipts": [to_public(r, with_items=False) for r in db.list(status, limit)]}

    @app.get("/api/receipts/{receipt_id}", dependencies=[Depends(auth)])
    def get_receipt(receipt_id: int):
        return to_public(get_row(receipt_id))

    @app.get("/api/receipts/{receipt_id}/image", dependencies=[Depends(auth)])
    def get_image(receipt_id: int):
        row = get_row(receipt_id)
        if not row.get("image_path") or not Path(row["image_path"]).exists():
            raise HTTPException(status_code=404, detail="Rasm yo'q")
        return FileResponse(row["image_path"], media_type="image/jpeg")

    @app.post("/api/receipts/{receipt_id}/ack", dependencies=[Depends(auth)])
    async def ack(receipt_id: int, body: AckBody):
        row = get_row(receipt_id)
        if row["status"] == "imported":
            return {"ok": True, "already": True}
        db.update(receipt_id, status="imported", onec_document=body.document, last_error=None)
        await notify(row, f"📥 Chek №{row['check_number']} 1C ga kiritildi: {body.document}")
        return {"ok": True}

    @app.post("/api/receipts/{receipt_id}/fail", dependencies=[Depends(auth)])
    def fail(receipt_id: int, body: FailBody):
        """1C hujjat yarata olmadi - chek 'ready' ga qaytadi, keyingi yuklashda yana beriladi."""
        get_row(receipt_id)
        db.update(receipt_id, status="ready", last_error=body.error, claimed_at=None)
        return {"ok": True}

    @app.post("/api/receipts/{receipt_id}/reject", dependencies=[Depends(auth)])
    async def reject(receipt_id: int, body: FailBody):
        """Buxgalter rad etdi. Dublikat kaliti bo'shatiladi - chekni qayta yuborish mumkin."""
        row = get_row(receipt_id)
        db.update(receipt_id, status="rejected", dedup_key=None, last_error=body.error)
        await notify(row, f"🚫 Chek №{row['check_number']} rad etildi: {body.error}")
        return {"ok": True}

    @app.post("/api/receipts/{receipt_id}/reparse", dependencies=[Depends(auth)])
    async def reparse(receipt_id: int, refetch: bool = False):
        """Parser tuzatilgandan keyin saqlangan HTML'ni qayta tahlil qilish (yoki qayta yuklash)."""
        row = get_row(receipt_id)
        if row["status"] in ("imported", "processing", "relayed"):
            raise HTTPException(status_code=409, detail=f"Holati {row['status']} - o'zgartirilmaydi")
        if refetch or not row.get("raw_html"):
            row = await service.fetch_and_parse(receipt_id)
        else:
            row = service.apply_parse(receipt_id, row["raw_html"])
        return to_public(row)

    return app


def run() -> None:
    import uvicorn

    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
