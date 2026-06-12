"""Abstract base class for all scraping providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from airbnb_scraping_tool.schemas import RawPayload, SearchQuery


class ScrapeProvider(ABC):
    """Interface that every scraper must implement.

    ``search`` is the only required method; it should persist no state itself
    — raw payloads are persisted by the pipeline.
    """

    source: str  # class-level identifier, e.g. "airbnb" or "booking"

    @abstractmethod
    def search(self, query: SearchQuery) -> list[RawPayload]:
        """Run a search and return a list of raw payloads.

        Each payload represents one scraped page / API response.
        The pipeline is responsible for dedup-checking and persisting them.
        """
        ...
