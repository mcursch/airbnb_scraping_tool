"""Base scraper interface for Short-Stay Market Scanner.

Defines the ``ScrapeProvider`` abstract base class that every scraper
implementation (Airbnb, Booking.com, paid-API fallback) must satisfy,
and the ``RawPayload`` type alias used throughout the pipeline.
"""

from __future__ import annotations

import abc
from typing import Any

# Type alias for a raw, unvalidated payload returned by a scraper.
# Each entry is a plain dict produced by JSON-deserialising the scraped
# response; downstream extraction (see extraction/) validates and normalises it.
RawPayload = dict[str, Any]


class ScrapeProvider(abc.ABC):
    """Abstract base class for all scraper backends.

    Subclasses implement :meth:`search` to fetch raw listing data for a given
    query and return it as a list of :data:`RawPayload` dicts.  The pipeline
    persists these immediately as ``RawScrape`` database rows before any
    extraction takes place, so a crash during extraction never loses fetched
    data.

    Subclasses may also override :meth:`close` to release browser instances,
    HTTP sessions, or other resources.
    """

    @abc.abstractmethod
    async def search(self, query: str) -> list[RawPayload]:
        """Perform a listing search and return raw payloads.

        Args:
            query: Human-readable area description, e.g. ``"Lisbon, Portugal"``.
                   Implementations are free to accept richer query objects via
                   keyword arguments, but this single-string signature is the
                   minimum that every provider must support.

        Returns:
            A list of raw payload dicts — one per scraped API response page or
            listing blob.  The structure is provider-specific and intentionally
            unvalidated; normalisation happens in the extraction layer.
        """

    async def close(self) -> None:
        """Release resources held by this provider.

        Default implementation is a no-op.  Subclasses with long-lived browser
        contexts or HTTP sessions should override this.
        """
