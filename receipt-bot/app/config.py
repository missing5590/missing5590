import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _chat_ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


@dataclass
class Settings:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", "").strip())
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY", "").strip())
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("PORT", 8090))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")))
    dedup_by: str = field(default_factory=lambda: os.getenv("DEDUP_BY", "check_number").strip())
    allowed_chat_ids: set[int] = field(
        default_factory=lambda: _chat_ids(os.getenv("ALLOWED_CHAT_IDS", ""))
    )
    retry_interval: int = field(default_factory=lambda: _int("RETRY_INTERVAL", 600))
    max_fetch_attempts: int = field(default_factory=lambda: _int("MAX_FETCH_ATTEMPTS", 36))
    lease_minutes: int = field(default_factory=lambda: _int("LEASE_MINUTES", 15))
    # Xosting (relay) rejimi: tayyor cheklar shu manzilga yuboriladi, 1C ularni o'sha yerdan oladi.
    relay_url: str = field(default_factory=lambda: os.getenv("RELAY_URL", "").strip().rstrip("/"))
    relay_key: str = field(default_factory=lambda: os.getenv("RELAY_KEY", "").strip())
    relay_interval: int = field(default_factory=lambda: _int("RELAY_INTERVAL", 30))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "receipts.db"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    def validate(self) -> None:
        if not self.api_key and not self.relay_url:
            raise RuntimeError("API_KEY yoki RELAY_URL/RELAY_KEY .env faylda ko'rsatilmagan")
        if self.dedup_by not in ("check_number", "check_number_fm"):
            raise RuntimeError("DEDUP_BY faqat check_number yoki check_number_fm bo'lishi mumkin")
        if self.relay_url and not self.relay_key:
            raise RuntimeError("RELAY_URL ko'rsatilgan, lekin RELAY_KEY yo'q")
        self.images_dir.mkdir(parents=True, exist_ok=True)
