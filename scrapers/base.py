"""Base contracts for all scrape providers.

Every scraper in this project must implement ``ScrapeProvider`` and raise
``BlockedError`` when it detects it has been blocked or challenged (CAPTCHA,
rate-limit, HTTP 403, etc.).  The pipeline uses these two symbols to decide
whether to engage the fallback provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


class BlockedError(Exception):
    """Raised by a :class:`ScrapeProvider` when bot-detection or rate-limiting
    prevents the search from completing.

    The pipeline catches this at the provider boundary and either retries via
    the configured :class:`~scrapers.fallback_api.FallbackApiProvider` or logs
    a structured warning and skips the source.
    """


@dataclass
class RawPayload:
    """One unit of raw scraped data, before normalisation.

    This is the return type of :meth:`ScrapeProvider.search` and maps 1-to-1
    onto a ``RawScrape`` database row once the pipeline persists it.
    """

    source: str
    """Logical name for the origin site/provider (e.g. ``"booking"`` or ``"fallback"``)."""

    url: str
    """Canonical URL of the scraped resource."""

    payload: str
    """Raw response body (HTML or JSON text)."""

    fetched_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    """UTC timestamp recorded at fetch time."""


class ScrapeProvider(ABC):
    """Abstract base class for every acquire-stage provider.

    Subclasses must override :meth:`search` and may raise :exc:`BlockedError`
    to signal that the request was blocked.  Any other exception is treated as
    an unexpected error and propagates to the caller.
    """

    @abstractmethod
    def search(self, query: str) -> list[RawPayload]:
        """Execute a search and return raw payloads.

        Args:
            query: Human-readable location / keyword string as entered by the
                user (e.g. ``"Lisbon, Portugal"``).

        Returns:
            A (possibly empty) list of :class:`RawPayload` objects.

        Raises:
            BlockedError: When the provider detects it is being blocked and
                cannot complete the request.
        """
