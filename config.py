"""Application configuration via pydantic-settings.

Settings are read from environment variables or a .env file.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    db_path: str = "scanner.db"

    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-4-8"

    # Scraping
    max_pages: int = 3
    request_delay_min: float = 1.0
    request_delay_max: float = 3.0
    # rate_limit_* are aliases kept in sync with request_delay_* for tests
    rate_limit_min_seconds: float = 1.0
    rate_limit_max_seconds: float = 3.0
    SCRAPER_API_KEY: str = ""
    FALLBACK_PROVIDER: str = "scraperapi"

    # Logging
    log_dir: str = "logs"

    # Extraction
    batch_threshold: int = 10  # use Batches API above this many scrapes

    # claude-opus-4-8 pricing in USD per million tokens (MTok)
    CLAUDE_OPUS_4_8_INPUT_PRICE_PER_MTOK: float = 15.0
    CLAUDE_OPUS_4_8_OUTPUT_PRICE_PER_MTOK: float = 75.0
    CLAUDE_OPUS_4_8_CACHE_READ_PRICE_PER_MTOK: float = 1.50

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
