"""Ichki server ↔ xosting (relay-php) integratsiya testi. PHP o'rnatilmagan bo'lsa o'tkazib yuboriladi."""
import asyncio
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.db import Database
from app.relay import RelaySync
from app.service import ReceiptService
from tests.test_soliq import HTML, URL

RELAY_DIR = Path(__file__).resolve().parents[2] / "relay-php"
SERVER_KEY, ONEC_KEY = "server-key", "onec-key"

pytestmark = pytest.mark.skipif(shutil.which("php") is None, reason="php o'rnatilmagan")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def relay_url(tmp_path):
    # Standart - SQLite. MySQL/MariaDB bilan tekshirish uchun:
    #   RELAY_TEST_DSN='mysql:host=localhost;dbname=cheklar' RELAY_TEST_USER=.. RELAY_TEST_PASS=..
    dsn = os.environ.get("RELAY_TEST_DSN") or f"sqlite:{tmp_path / 'relay.db'}"
    user = os.environ.get("RELAY_TEST_USER", "")
    password = os.environ.get("RELAY_TEST_PASS", "")
    config = tmp_path / "config.php"
    config.write_text(
        f"<?php return ['dsn' => '{dsn}', 'db_user' => '{user}', 'db_pass' => '{password}', "
        f"'server_key' => '{SERVER_KEY}', 'onec_key' => '{ONEC_KEY}', 'lease_minutes' => 15];"
    )
    if dsn.startswith("mysql:"):
        subprocess.run(["php", "-r", f"$db = new PDO('{dsn}', '{user}', '{password}');"
                        "$db->exec('DROP TABLE IF EXISTS receipts, events');"], check=True)
    port = _free_port()
    proc = subprocess.Popen(
        ["php", "-S", f"127.0.0.1:{port}", "-t", str(RELAY_DIR / "public_html"),
         str(RELAY_DIR / "public_html" / "index.php")],
        env={"RELAY_CONFIG": str(config), "PATH": "/usr/bin:/bin"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if httpx.get(f"{url}/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait()


async def fake_fetch(url, timeout=30.0):
    return HTML


def test_bot_to_relay_to_onec(tmp_path, relay_url):
    async def scenario():
        db = Database(tmp_path / "local.db")
        service = ReceiptService(db)
        notified = []

        async def notify(row, text):
            notified.append(text)

        relay = RelaySync(db, relay_url, SERVER_KEY, notify)
        onec = httpx.AsyncClient(base_url=relay_url, headers={"X-API-Key": ONEC_KEY})

        with patch("app.soliq.fetch_check_html", fake_fetch):
            result = await service.submit(URL)
        assert result.ok

        await relay.sync_once()
        local = db.get(result.receipt_id)
        assert local["status"] == "relayed" and local["relay_id"]

        # Takroriy sinxronizatsiya dublikat yaratmaydi
        await relay.sync_once()

        # Kalitlar almashtirilmaydi: 1C kaliti server yo'llariga, server kaliti 1C yo'llariga kira olmaydi
        assert (await onec.get("/api/relay/events")).status_code == 401
        assert (await relay.client.post(f"{relay_url}/api/receipts/claim")).status_code == 401

        claimed = (await onec.post("/api/receipts/claim")).json()["receipts"]
        assert len(claimed) == 1
        receipt = claimed[0]
        assert receipt["check_number"] == "415009"
        assert receipt["seller_inn"] == "202099756"
        assert len(receipt["items"]) == 2
        assert (await onec.post("/api/receipts/claim")).json()["receipts"] == []

        r = await onec.post(f"/api/receipts/{receipt['id']}/fail", json={"error": "Номенклатура"})
        assert r.status_code == 200
        again = (await onec.post("/api/receipts/claim")).json()["receipts"]
        assert again[0]["last_error"] == "Номенклатура"

        r = await onec.post(f"/api/receipts/{receipt['id']}/ack", json={"document": "Поступление №7"})
        assert r.json() == {"ok": True}

        await relay.sync_once()
        local = db.get(result.receipt_id)
        assert local["status"] == "imported"
        assert local["onec_document"] == "Поступление №7"
        assert any("1C ga kiritildi" in t for t in notified)

        await onec.aclose()
        await relay.close()

    asyncio.run(scenario())


def test_relay_rejects_duplicate_check_number(tmp_path, relay_url):
    async def scenario():
        # Ikki xil mahalliy baza (masalan, server qayta o'rnatilgan): o'sha chek raqami boshqa id bilan
        # kelsa, xosting uni chek raqami bo'yicha dublikat deb ushlaydi.
        for name in ("a.db", "b.db"):
            db = Database(tmp_path / name)
            service = ReceiptService(db)
            with patch("app.soliq.fetch_check_html", fake_fetch):
                if name == "b.db":
                    await service.submit(URL.replace("r=415009", "r=1"))
                    db.update(1, status="imported")
                result = await service.submit(URL)
            relay = RelaySync(db, relay_url, SERVER_KEY)
            await relay.sync_once()
            await relay.close()
        assert db.get(result.receipt_id)["status"] == "duplicate"

    asyncio.run(scenario())
