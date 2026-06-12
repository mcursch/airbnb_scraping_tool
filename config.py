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
    scraper_api_key: str = ""

    # Extraction
    batch_threshold: int = 10  # use Batches API above this many scrapes

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
