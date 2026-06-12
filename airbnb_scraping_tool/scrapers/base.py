"""Abstract base class for all scraping providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from airbnb_scraping_tool.schemas import RawPayload, SearchQuery


class ScrapeProvider(ABC):
    """Interface that every scraper must implement."""

    source: str  # class-level identifier, e.g. "airbnb" or "booking"

    @abstractmethod
    def search(self, query: SearchQuery) -> list[RawPayload]:
        """Run a search and return a list of raw payloads."""
        ...
