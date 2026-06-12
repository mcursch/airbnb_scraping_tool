"""Application settings loaded from environment variables or a .env file."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API keys ---
    anthropic_api_key: str | None = Field(
        default=None,
        description="Anthropic API key (required for the extraction stage).",
    )
    scraper_api_key: str | None = Field(
        default=None,
        description="Paid scraping-API key (e.g. ScraperAPI/Apify); optional fallback.",
    )

    # --- Database ---
    database_url: str = Field(
        default="sqlite:///./scanner.db",
        description="SQLAlchemy database URL.  Defaults to a local SQLite file.",
    )

    # --- Scraper tunables ---
    scraper_page_limit: int = Field(
        default=5,
        ge=1,
        description="Maximum number of pages to fetch per search source.",
    )
    scraper_request_delay_min: float = Field(
        default=1.5,
        ge=0.0,
        description="Minimum seconds to wait between requests (randomised lower bound).",
    )
    scraper_request_delay_max: float = Field(
        default=4.0,
        ge=0.0,
        description="Maximum seconds to wait between requests (randomised upper bound).",
    )
    scraper_headless: bool = Field(
        default=True,
        description="Run Playwright in headless mode.",
    )

    # --- Extraction tunables ---
    extraction_batch_threshold: int = Field(
        default=20,
        ge=1,
        description=(
            "Number of pending RawScrape records above which the Batches API "
            "path is used instead of real-time extraction."
        ),
    )
    extraction_model: str = Field(
        default="claude-opus-4-8",
        description="Anthropic model used for structured extraction.",
    )
