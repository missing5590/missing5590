from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_soliq import HTML, URL

H = {"X-API-Key": "test-key"}


async def fake_fetch(url, timeout=30.0):
    return HTML


@pytest.fixture
def client(settings):
    app = create_app(settings, start_bot=False)
    with TestClient(app) as c:
        yield c


def submit(client, url=URL):
    import asyncio
    with patch("app.soliq.fetch_check_html", fake_fetch):
        return asyncio.run(client.app.state.service.submit(url, chat_id=None))


def test_auth_required(client):
    assert client.post("/api/receipts/claim").status_code == 401
    assert client.post("/api/receipts/claim", headers={"X-API-Key": "x"}).status_code == 401


def test_full_flow(client):
    result = submit(client)
    assert result.ok

    dup = submit(client)
    assert not dup.ok and "allaqachon" in dup.message

    data = client.post("/api/receipts/claim", headers=H).json()["receipts"]
    assert len(data) == 1
    receipt = data[0]
    assert receipt["check_number"] == "415009"
    assert receipt["seller_inn"] == "202099756"
    assert len(receipt["items"]) == 2

    # Ikkinchi «Yuklash» shu chekni qayta olmaydi
    assert client.post("/api/receipts/claim", headers=H).json()["receipts"] == []

    # 1C xato qaytarsa - chek yana tayyor
    client.post(f"/api/receipts/{receipt['id']}/fail", headers=H, json={"error": "Nomenklatura yo'q"})
    again = client.post("/api/receipts/claim", headers=H).json()["receipts"]
    assert again[0]["last_error"] == "Nomenklatura yo'q"

    r = client.post(f"/api/receipts/{receipt['id']}/ack", headers=H, json={"document": "Поступление №1"})
    assert r.json() == {"ok": True}
    row = client.get(f"/api/receipts/{receipt['id']}", headers=H).json()
    assert row["status"] == "imported" and row["onec_document"] == "Поступление №1"


def test_reject_frees_duplicate(client):
    first = submit(client)
    client.post(f"/api/receipts/{first.receipt_id}/reject", headers=H, json={"error": "xato"})
    assert submit(client).ok


def test_dedup_by_number_and_fm(tmp_path):
    from app.config import Settings
    settings = Settings(bot_token="", api_key="test-key", data_dir=tmp_path,
                        dedup_by="check_number_fm")
    with TestClient(create_app(settings, start_bot=False)) as c:
        assert submit(c).ok
        other_kassa = URL.replace("NA000000053505", "NA000000099999")
        assert submit(c, other_kassa).ok
