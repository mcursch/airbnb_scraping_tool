"""Application configuration via pydantic-settings.

All fields can be overridden by environment variables or a .env file.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── LLM ───────────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str | None = None

    # ── Paid scraping-API fallback (both optional; fallback is disabled when
    #    SCRAPER_API_KEY is unset) ────────────────────────────────────────────
    SCRAPER_API_KEY: str | None = None
    FALLBACK_PROVIDER: Literal["scraperapi", "apify"] | None = None

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./scanner.db"


settings = Settings()
