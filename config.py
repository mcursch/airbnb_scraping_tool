"""Central configuration via pydantic-settings.

All tunables are readable from environment variables or a .env file.
The names here are the canonical references used across the codebase.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite:///./scanner.db"

    # ── Scraper: pagination ───────────────────────────────────────────────────
    max_pages: int = 3
    """Maximum number of search-result pages to fetch per source per run."""

    # ── Scraper: polite rate limiting ─────────────────────────────────────────
    rate_limit_min_seconds: float = 2.0
    """Lower bound of the randomised inter-page sleep (seconds)."""

    rate_limit_max_seconds: float = 5.0
    """Upper bound of the randomised inter-page sleep (seconds)."""

    # ── Scraper: browser behaviour ────────────────────────────────────────────
    browser_headless: bool = True
    """Run Playwright browser in headless mode."""

    page_load_timeout_ms: int = 30_000
    """Playwright navigation timeout in milliseconds."""

    # ── LLM ───────────────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    """Anthropic API key (required for extraction; optional for acquire-only runs)."""


settings = Settings()
