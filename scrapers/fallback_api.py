"""Paid scraping-API fallback adapter.

When a primary :class:`~scrapers.base.ScrapeProvider` raises
:exc:`~scrapers.base.BlockedError`, the pipeline retries the same query
through this provider if ``SCRAPER_API_KEY`` is configured.

The adapter is intentionally provider-agnostic: it speaks to whichever
scraping API is configured (ScraperAPI, Apify, etc.) through a uniform
HTTP interface.  The full implementation is delivered in Stage 2; this
module already satisfies the contract so that pipeline wiring and tests
can be written first.
"""

from __future__ import annotations

from scrapers.base import RawPayload, ScrapeProvider


class FallbackApiProvider(ScrapeProvider):
    """Routes a search through a configured paid scraping-API endpoint.

    Args:
        api_key: Secret key for the scraping API service.
        source_name: Label written into :attr:`~scrapers.base.RawPayload.source`
            for every result returned by this provider (default ``"fallback"``).
    """

    def __init__(self, api_key: str, source_name: str = "fallback") -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._api_key = api_key
        self._source_name = source_name

    # ------------------------------------------------------------------
    # ScrapeProvider interface
    # ------------------------------------------------------------------

    def search(self, query: str) -> list[RawPayload]:
        """Proxy the search through the external scraping API.

        Full HTTP implementation is part of Stage 2.  The method signature is
        finalised here so that the pipeline and integration tests can be written
        against it without waiting for Stage 2.

        Raises:
            NotImplementedError: Until the Stage 2 HTTP implementation is
                merged.
        """
        raise NotImplementedError(  # pragma: no cover
            "FallbackApiProvider.search() will be implemented in Stage 2"
        )
