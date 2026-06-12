"""
scrapers/base.py
================
Shared contracts for all scraping providers.

Exports
-------
BlockedError      -- raised when a provider detects a CAPTCHA / IP block.
SearchQuery       -- validated input for a scraper search call.
RawScrape         -- record of one fetched page, persisted immediately.
RawPayload        -- alias for RawScrape (backward-compatible convenience name).
ScrapeProvider    -- abstract base class every scraper must implement.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BlockedError(Exception):
    """
    Raised when the scraper detects that the target site is blocking access
    (CAPTCHA page, 403/429/503 response, bot-detection challenge, etc.).

    Callers should surface this clearly in run statistics rather than silently
    returning empty results.
    """

    def __init__(self, *, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"Blocked at {url!r}: {reason}")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class SearchQuery(BaseModel):
    """Parameters for a single scraper search call."""

    area: str
    checkin: Optional[str] = None   # ISO date string, e.g. "2025-08-01"
    checkout: Optional[str] = None  # ISO date string, e.g. "2025-08-07"
    guests: int = 2
    page_limit: int = 5


class RawScrape(BaseModel):
    """
    One raw HTTP response captured from a scraping provider.

    Persisted immediately after each page fetch so that a crash during
    extraction never loses already-fetched data.
    """

    id: Optional[int] = None
    run_id: Optional[int] = None
    source: str                          # e.g. "booking", "airbnb"
    url: str
    payload: str                         # raw HTML (or JSON) body
    content_hash: str = ""               # SHA-256 of payload; computed automatically
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "pending"              # "pending" | "extracted" | "failed"
    error: Optional[str] = None

    @model_validator(mode="after")
    def _compute_hash(self) -> "RawScrape":
        if not self.content_hash:
            self.content_hash = hashlib.sha256(self.payload.encode()).hexdigest()
        return self


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


# Alias so that callers can import ``RawPayload`` from this module.
# ``RawScrape`` is the canonical class; ``RawPayload`` is a convenience alias.
RawPayload = RawScrape


class ScrapeProvider(ABC):
    """
    Common interface for all scraping back-ends.

    Implementations must return a list of :class:`RawScrape` records (one per
    page fetched) and persist them immediately after each fetch.  The
    extraction pipeline consumes these records asynchronously in a later stage.

    Raises
    ------
    BlockedError
        When the provider detects a CAPTCHA or block response that cannot be
        worked around.  Callers record the failure in run statistics and surface
        it to the user; they do *not* silently return empty results.
    """

    @abstractmethod
    def search(self, query: SearchQuery) -> list[RawScrape]:
        """
        Execute a search for *query* and return raw page payloads.

        Parameters
        ----------
        query:
            Describes the area, dates, and guest count to search for.

        Returns
        -------
        list[RawScrape]
            One record per fetched page, already persisted if a DB session
            was injected at construction time.
        """
        ...
