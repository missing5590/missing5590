"""SQLite ombori: cheklar navbati va bot holati."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

# Holatlar:
#   new          - qabul qilindi, soliq saytidan ma'lumot hali olinmagan
#   fetch_error  - sayt javob bermadi / chek hali topilmadi (avtomatik qayta urinadi)
#   parse_error  - sahifa olindi, lekin tovarlar ajratilmadi (parserni tekshirish kerak)
#   ready        - 1C ga yuklashga tayyor
#   processing   - 1C oldi, natijasini (ack/fail) kutyapmiz
#   imported     - 1C da hujjat yaratildi
#   rejected     - buxgalter rad etdi (dublikat kaliti bo'shatiladi)
#   relayed      - xostingga yuborildi, 1C o'sha yerdan oladi (relay rejimi)
#   duplicate    - xosting bu chek raqamini allaqachon qabul qilgan
SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key       TEXT UNIQUE,
    url             TEXT NOT NULL,
    fm              TEXT NOT NULL,
    check_number    TEXT NOT NULL,
    check_date      TEXT NOT NULL,
    fp              TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'new',
    seller_name     TEXT,
    seller_inn      TEXT,
    address         TEXT,
    total           REAL,
    vat_total       REAL,
    items_json      TEXT,
    raw_html        TEXT,
    last_error      TEXT,
    fetch_attempts  INTEGER NOT NULL DEFAULT 0,
    chat_id         INTEGER,
    message_id      INTEGER,
    image_path      TEXT,
    claimed_at      TEXT,
    onec_document   TEXT,
    relay_id        INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_receipts_status ON receipts(status);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

PUBLIC_FIELDS = (
    "id", "status", "url", "fm", "check_number", "check_date", "fp", "seller_name",
    "seller_inn", "address", "total", "vat_total", "last_error", "onec_document",
    "created_at", "updated_at",
)


class Duplicate(Exception):
    def __init__(self, existing: dict):
        super().__init__("duplicate")
        self.existing = existing


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path, dedup_by: str = "check_number"):
        self.dedup_by = dedup_by
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        columns = {r[1] for r in self._conn.execute("PRAGMA table_info(receipts)")}
        if "relay_id" not in columns:  # eski bazani yangilash
            self._conn.execute("ALTER TABLE receipts ADD COLUMN relay_id INTEGER")

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------ yordamchi

    def dedup_key(self, check_number: str, fm: str) -> str:
        if self.dedup_by == "check_number_fm":
            return f"{fm}|{check_number}"
        return check_number

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------ kv

    def get_kv(self, key: str, default: str | None = None) -> str | None:
        rows = self._query("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_kv(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------ cheklar

    def find_by_dedup(self, check_number: str, fm: str) -> dict | None:
        rows = self._query(
            "SELECT * FROM receipts WHERE dedup_key = ?", (self.dedup_key(check_number, fm),)
        )
        return rows[0] if rows else None

    def insert_receipt(self, *, url: str, fm: str, check_number: str, check_date: datetime,
                       fp: str, chat_id: int | None, message_id: int | None,
                       image_path: str | None) -> int:
        now = _now()
        try:
            cur = self._execute(
                "INSERT INTO receipts(dedup_key, url, fm, check_number, check_date, fp, chat_id,"
                " message_id, image_path, created_at, updated_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.dedup_key(check_number, fm), url, fm, check_number,
                 check_date.isoformat(timespec="seconds"), fp, chat_id, message_id,
                 image_path, now, now),
            )
        except sqlite3.IntegrityError:
            raise Duplicate(self.find_by_dedup(check_number, fm) or {})
        return cur.lastrowid

    def get(self, receipt_id: int) -> dict | None:
        rows = self._query("SELECT * FROM receipts WHERE id = ?", (receipt_id,))
        return rows[0] if rows else None

    def update(self, receipt_id: int, **fields) -> None:
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._execute(f"UPDATE receipts SET {cols} WHERE id = ?", (*fields.values(), receipt_id))

    def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        if status:
            return self._query(
                "SELECT * FROM receipts WHERE status = ? ORDER BY id LIMIT ?", (status, limit)
            )
        return self._query("SELECT * FROM receipts ORDER BY id DESC LIMIT ?", (limit,))

    def due_for_retry(self, max_attempts: int) -> list[dict]:
        return self._query(
            "SELECT * FROM receipts WHERE status IN ('new', 'fetch_error') AND fetch_attempts < ?"
            " ORDER BY id",
            (max_attempts,),
        )

    def claim_ready(self, limit: int, lease_minutes: int) -> list[dict]:
        """Tayyor cheklarni 1C ga beradi va ularni 'processing' holatiga o'tkazadi.

        Bir vaqtda ikki kompyuterdan «Yuklash» bosilsa ham bitta chek faqat bittasiga beriladi.
        Tasdiqlanmagan (ack/fail yuborilmagan) cheklar lease_minutes dan keyin qayta beriladi.
        """
        expired = (datetime.now() - timedelta(minutes=lease_minutes)).isoformat(timespec="seconds")
        now = _now()
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE receipts SET status = 'ready', updated_at = ?"
                    " WHERE status = 'processing' AND claimed_at < ?",
                    (now, expired),
                )
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM receipts WHERE status = 'ready' ORDER BY id LIMIT ?", (limit,)
                ).fetchall()]
                for row in rows:
                    conn.execute(
                        "UPDATE receipts SET status = 'processing', claimed_at = ?, updated_at = ?"
                        " WHERE id = ?",
                        (now, now, row["id"]),
                    )
                    row["status"] = "processing"
                    row["claimed_at"] = now
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return rows


def to_public(row: dict, with_items: bool = True) -> dict:
    data = {k: row.get(k) for k in PUBLIC_FIELDS}
    if with_items:
        data["items"] = json.loads(row["items_json"]) if row.get("items_json") else []
    return data
