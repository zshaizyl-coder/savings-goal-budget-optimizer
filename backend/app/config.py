from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    upload_max_bytes: int
    cors_origins: tuple[str, ...]


def get_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL", "sqlite:///data/private/app.sqlite3")
    secret_key = os.getenv("SECRET_KEY", "development-only-change-me")
    max_mib = int(os.getenv("UPLOAD_MAX_MIB", "12"))
    origins = tuple(value.strip() for value in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173"
    ).split(",") if value.strip())
    return Settings(database_url, secret_key, max_mib * 1024 * 1024, origins)


def sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("V1 currently supports sqlite DATABASE_URL only")
    return Path(database_url[len(prefix):])
