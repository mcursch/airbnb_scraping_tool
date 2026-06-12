"""Abstract base class that every scraper provider must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod

from schemas.models import RawPayload, SearchQuery


class ScrapeProvider(ABC):
    """Contract for all scraper backends (Airbnb, Booking.com, paid-API fallback, …).

    Each provider receives a :class:`~schemas.models.SearchQuery` and returns a list
    of :class:`~schemas.models.RawPayload` objects containing the unprocessed
    response content. The extraction stage later normalises these into structured
    ``Listing`` records.
    """

    @abstractmethod
    def search(self, query: SearchQuery) -> list[RawPayload]:
        """Execute a search and return raw scraped payloads.

        Args:
            query: The search parameters (area, dates, guests, sources).

        Returns:
            A list of :class:`~schemas.models.RawPayload` items, one per page /
            response captured during the search.  May be empty if no results
            were found or the site blocked the request.
        """
