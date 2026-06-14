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

    # Enrichment (reason-and-act web research to fill missing fields)
    enrich_model: str = "claude-opus-4-8"
    enrich_max_listings: int = 5   # cap enriched listings per run (cost control)
    enrich_min_gaps: int = 3       # only enrich listings missing ≥ this many fields
    enrich_max_fields: int = 6     # research at most this many gaps per listing
    # Approx. Anthropic web-search billing: ~$10 per 1,000 search requests.
    WEB_SEARCH_PRICE_PER_REQUEST: float = 0.01

    # Scraping
    max_pages: int = 3
    request_delay_min: float = 1.0
    request_delay_max: float = 3.0
    # rate_limit_* are aliases kept in sync with request_delay_* for tests
    rate_limit_min_seconds: float = 1.0
    rate_limit_max_seconds: float = 3.0
    SCRAPER_API_KEY: str = ""
    FALLBACK_PROVIDER: str = "scraperapi"  # scraperapi | apify | brightdata
    # Bright Data Web Unlocker zone name (used when FALLBACK_PROVIDER=brightdata)
    BRIGHTDATA_ZONE: str = "web_unlocker"

    # Logging
    log_dir: str = "logs"

    # Extraction
    batch_threshold: int = 10  # use Batches API above this many scrapes

    # claude-opus-4-8 pricing in USD per million tokens (MTok).
    # Source: Anthropic pricing — $5 input / $25 output / $0.50 cache-read per MTok.
    CLAUDE_OPUS_4_8_INPUT_PRICE_PER_MTOK: float = 5.0
    CLAUDE_OPUS_4_8_OUTPUT_PRICE_PER_MTOK: float = 25.0
    CLAUDE_OPUS_4_8_CACHE_READ_PRICE_PER_MTOK: float = 0.50

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
