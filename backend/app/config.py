from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    upload_max_bytes: int
    cors_origins: tuple[str, ...]
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_enabled: bool = False
    merchant_search_api_key: str = ""
    merchant_search_enabled: bool = False
    merchant_search_base_url: str = "https://api.search.brave.com/res/v1/web/search"


def get_settings() -> Settings:
    # Load local secrets lazily so importing Settings in tests does not mutate
    # the process environment used by independent Alembic test databases.
    load_dotenv(override=False)
    database_url = os.getenv("DATABASE_URL", "sqlite:///data/private/app.sqlite3")
    secret_key = os.getenv("SECRET_KEY", "development-only-change-me")
    max_mib = int(os.getenv("UPLOAD_MAX_MIB", "12"))
    origins = tuple(value.strip() for value in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173"
    ).split(",") if value.strip())
    deepseek_enabled = os.getenv("DEEPSEEK_CLASSIFICATION_ENABLED", "false").lower() in {
        "1", "true", "yes", "on",
    }
    merchant_search_enabled = os.getenv("MERCHANT_SEARCH_ENABLED", "false").lower() in {
        "1", "true", "yes", "on",
    }
    return Settings(
        database_url, secret_key, max_mib * 1024 * 1024, origins,
        os.getenv("DEEPSEEK_API_KEY", ""),
        os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_enabled,
        os.getenv("BRAVE_SEARCH_API_KEY", ""),
        merchant_search_enabled,
        os.getenv(
            "MERCHANT_SEARCH_BASE_URL",
            "https://api.search.brave.com/res/v1/web/search",
        ),
    )


def sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("V1 currently supports sqlite DATABASE_URL only")
    return Path(database_url[len(prefix):])
