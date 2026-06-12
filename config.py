"""Application configuration via pydantic-settings.

Settings are read from environment variables or a .env file.
"""

from __future__ import annotations

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

    # Per-call timeouts in seconds
    scraper_timeout: float = 30.0
    extractor_timeout: float = 60.0

    # Max retries on timeout or transient error (0 = no retries)
    scraper_max_retries: int = 2
    extractor_max_retries: int = 2

    # Per-run JSON-lines log directory
    log_dir: str = "logs"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
