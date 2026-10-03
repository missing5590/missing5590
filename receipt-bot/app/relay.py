"""Xosting (relay) bilan sinxronizatsiya.

Ichki serverga tashqaridan kirib bo'lmaydi, shuning uchun:
  * tayyor cheklar xostingga yuboriladi (POST /api/relay/push);
  * 1C natijalari (kiritildi / rad etildi / xato) xostingdan o'qiladi (GET /api/relay/events)
    va xodimga Telegram orqali bildiriladi.
Ikkala yo'nalishda ham ulanishni ichki server o'zi ochadi.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .db import Database, to_public

log = logging.getLogger(__name__)


class RelaySync:
    def __init__(self, db: Database, url: str, key: str, notify=None,
                 client: httpx.AsyncClient | None = None):
        self.db = db
        self.url = url.rstrip("/")
        self.notify = notify  # async (row, text) -> None
        self.client = client or httpx.AsyncClient(timeout=30.0)
        self.client.headers["X-API-Key"] = key

    async def close(self) -> None:
        await self.client.aclose()

    async def run(self, interval: int) -> None:
        while True:
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Xosting bilan sinxronizatsiya xatosi")
            await asyncio.sleep(interval)

    async def sync_once(self) -> None:
        await self.push_ready()
        await self.pull_events()

    async def push_ready(self) -> None:
        for row in self.db.list("ready", limit=100):
            payload = to_public(row)
            payload["source_id"] = row["id"]
            payload["dedup_key"] = row["dedup_key"]
            response = await self.client.post(f"{self.url}/api/relay/push", json=payload)
            if response.status_code == 409:
                self.db.update(row["id"], status="duplicate",
                               last_error="Xostingda bu chek allaqachon bor")
                await self._notify(row, f"⚠️ Chek №{row['check_number']} allaqachon 1C navbatida bor.")
                continue
            response.raise_for_status()
            self.db.update(row["id"], status="relayed", relay_id=response.json()["id"])
            log.info("Chek %s xostingga yuborildi", row["id"])

    async def pull_events(self) -> None:
        after = int(self.db.get_kv("relay_event_id", "0"))
        response = await self.client.get(
            f"{self.url}/api/relay/events", params={"after": after, "limit": 200}
        )
        response.raise_for_status()
        for event in response.json()["events"]:
            row = self.db.get(event["source_id"])
            # Ichki baza qayta yaratilgan bo'lsa id lar mos kelmasligi mumkin - chek raqami bilan tekshiramiz.
            if row and row["check_number"] == event.get("check_number"):
                await self._apply(row, event)
            self.db.set_kv("relay_event_id", str(event["id"]))

    async def _apply(self, row: dict, event: dict) -> None:
        kind = event["type"]
        if kind == "imported":
            self.db.update(row["id"], status="imported", onec_document=event["document"],
                           last_error=None)
            await self._notify(row, f"📥 Chek №{row['check_number']} 1C ga kiritildi: {event['document']}")
        elif kind == "rejected":
            self.db.update(row["id"], status="rejected", dedup_key=None, last_error=event["error"])
            await self._notify(row, f"🚫 Chek №{row['check_number']} rad etildi: {event['error']}")
        elif kind == "failed":
            # Chek xostingda navbatda qoladi; xatoni faqat qayd etamiz.
            self.db.update(row["id"], last_error=f"1C: {event['error']}")

    async def _notify(self, row: dict, text: str) -> None:
        if self.notify:
            await self.notify(row, text)

