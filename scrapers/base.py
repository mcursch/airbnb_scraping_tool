"""Base contract for all scraper providers.

Every concrete scraper (Airbnb, Booking.com direct, paid-API fallback, …)
must implement :class:`ScrapeProvider` so the pipeline can treat them
interchangeably.
"""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod

from pydantic import BaseModel

from schemas.search import SearchQuery


class RawPayload(BaseModel):
    """Raw response from a single scrape request, before any extraction."""

    source: str
    """Logical source identifier, e.g. ``'airbnb'``, ``'booking'``,
    ``'fallback_scraperapi'``, ``'fallback_apify'``."""

    url: str
    """The URL that was actually fetched."""

    payload: str
    """Raw response body (HTML or JSON string)."""

    fetched_at: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)
    """When the response was received (UTC)."""


class ScrapeProvider(ABC):
    """Abstract base class for all scraping back-ends."""

    @abstractmethod
    def search(self, query: SearchQuery) -> list[RawPayload]:
        """Execute a search and return one or more raw payloads.

        Parameters
        ----------
        query:
            Structured search parameters (area, dates, guests).

        Returns
        -------
        list[RawPayload]
            One entry per fetched page / response.  Never empty on success.

        Raises
        ------
        errors.ConfigurationError
            If required credentials are missing.
        errors.ScraperError
            For network or provider-level failures.
        """
