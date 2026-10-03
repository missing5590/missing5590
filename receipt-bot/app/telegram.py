"""Telegram bot (long polling, kutubxonasiz - to'g'ridan-to'g'ri Bot API)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from .qr import decode_qr_urls
from .service import ReceiptService
from .soliq import find_check_urls

log = logging.getLogger(__name__)

HELP_TEXT = (
    "Chek rasmini yuboring (yaxshisi «fayl» sifatida - siqilmaydi) yoki chekdagi QR kod "
    "havolasini (https://ofd.soliq.uz/check?...) matn qilib yuboring."
)
QR_NOT_FOUND = (
    "❌ Rasmdan QR kod o'qilmadi. Chekni yaqinroqdan, tekis va yorug' joyda suratga oling "
    "va rasmni «fayl» sifatida yuboring. Yoki QR havolasini matn qilib yuboring."
)


class TelegramBot:
    def __init__(self, token: str, service: ReceiptService, images_dir: Path,
                 allowed_chat_ids: set[int] | None = None):
        self.api = f"https://api.telegram.org/bot{token}"
        self.file_api = f"https://api.telegram.org/file/bot{token}"
        self.service = service
        self.db = service.db
        self.images_dir = images_dir
        self.allowed = allowed_chat_ids or set()
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=15.0))

    async def close(self) -> None:
        await self.client.aclose()

    async def call(self, method: str, **params) -> dict:
        response = await self.client.post(f"{self.api}/{method}", json=params)
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data["result"]

    async def send(self, chat_id: int | None, text: str, reply_to: int | None = None) -> None:
        if not chat_id:
            return
        params = {"chat_id": chat_id, "text": text}
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        try:
            await self.call("sendMessage", **params)
        except Exception:
            log.exception("Xabar yuborilmadi (chat %s)", chat_id)

    async def run(self) -> None:
        # Webhook o'rnatilgan bo'lsa getUpdates ishlamaydi - o'chirib qo'yamiz.
        try:
            await self.call("deleteWebhook")
        except Exception:
            log.exception("deleteWebhook xatosi")
        while True:
            offset = int(self.db.get_kv("telegram_offset", "0"))
            try:
                updates = await self.call(
                    "getUpdates", offset=offset, timeout=50, allowed_updates=["message"]
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("getUpdates xatosi, 10 soniyadan keyin qayta urinish")
                await asyncio.sleep(10)
                continue
            for update in updates:
                try:
                    await self.handle_update(update)
                except Exception:
                    log.exception("Update %s ni qayta ishlashda xato", update.get("update_id"))
                    message = update.get("message") or {}
                    await self.send(
                        (message.get("chat") or {}).get("id"),
                        "❌ Ichki xato, chekni qayta yuboring.",
                        message.get("message_id"),
                    )
                # Xabar bazaga yozilgandan keyin tasdiqlanadi (xato bo'lsa ham - aks holda
                # bitta buzuq xabar navbatni to'xtatib qo'yadi).
                self.db.set_kv("telegram_offset", str(update["update_id"] + 1))

    async def handle_update(self, update: dict) -> None:
        message = update.get("message")
        if not message:
            return
        chat_id = message["chat"]["id"]
        message_id = message["message_id"]
        if self.allowed and chat_id not in self.allowed:
            await self.send(chat_id, f"Sizga ruxsat berilmagan. Chat ID: {chat_id}")
            return

        text = message.get("text") or message.get("caption") or ""
        if text.strip() in ("/start", "/help"):
            await self.send(chat_id, HELP_TEXT)
            return

        urls = find_check_urls(text)
        image_path = None
        file_id = self._image_file_id(message)
        if file_id:
            data = await self.download(file_id)
            image_path = self.images_dir / f"{chat_id}_{message_id}.jpg"
            image_path.write_bytes(data)
            if not urls:
                urls = await asyncio.to_thread(decode_qr_urls, data)
                if not urls:
                    await self.send(chat_id, QR_NOT_FOUND, message_id)
                    return
        elif not urls:
            await self.send(chat_id, HELP_TEXT, message_id)
            return

        for url in urls:
            result = await self.service.submit(
                url, chat_id=chat_id, message_id=message_id,
                image_path=str(image_path) if image_path else None,
            )
            await self.send(chat_id, result.message, message_id)

    @staticmethod
    def _image_file_id(message: dict) -> str | None:
        if message.get("photo"):
            return message["photo"][-1]["file_id"]  # eng katta o'lcham
        document = message.get("document")
        if document and (document.get("mime_type") or "").startswith("image/"):
            return document["file_id"]
        return None

    async def download(self, file_id: str) -> bytes:
        info = await self.call("getFile", file_id=file_id)
        response = await self.client.get(f"{self.file_api}/{info['file_path']}")
        response.raise_for_status()
        return response.content


async def retry_loop(service: ReceiptService, bot: TelegramBot | None, interval: int,
                     max_attempts: int) -> None:
    """Soliq saytidan olinmagan cheklarni vaqti-vaqti bilan qayta so'raydi."""
    while True:
        await asyncio.sleep(interval)
        for row in service.db.due_for_retry(max_attempts):
            try:
                updated = await service.fetch_and_parse(row["id"])
            except Exception:
                log.exception("Chek %s qayta urinishda xato", row["id"])
                continue
            if bot and updated["status"] in ("ready", "parse_error"):
                await bot.send(updated["chat_id"], service.status_message(updated),
                               updated["message_id"])
            elif bot and updated["fetch_attempts"] >= max_attempts:
                await bot.send(
                    updated["chat_id"],
                    f"❌ Chek №{updated['check_number']} bo'yicha soliq saytidan ma'lumot "
                    f"olinmadi: {updated['last_error']}",
                    updated["message_id"],
                )
