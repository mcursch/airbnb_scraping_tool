"""Abstract base for all scraping providers.

Every scraper (Airbnb, Booking.com, fallback API, …) implements this
interface so the pipeline can treat them uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class SearchQuery:
    """All parameters that define a single market search."""

    area: str
    checkin: str | None = None
    checkout: str | None = None
    guests: int = 1


@dataclass
class RawPayload:
    """A single intercepted API response from any scraper.

    This is the in-memory representation before it is persisted as a
    ``RawScrape`` row.
    """

    source: str
    url: str
    payload: str          # raw JSON string
    page_number: int = 1
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ScrapeProvider(ABC):
    """Abstract scraper interface.

    Concrete scrapers must implement :meth:`search`, which drives the
    acquisition loop and yields :class:`RawPayload` objects one at a time
    so callers can persist them incrementally (crash-safe).
    """

    source: str  # subclasses declare this as a class attribute

    @abstractmethod
    def search(
        self,
        query: SearchQuery,
        session: "Session",
        run_id: int | None = None,
    ) -> list[RawPayload]:
        """Run a full search for *query*, persist rows incrementally, and
        return all captured payloads."""
