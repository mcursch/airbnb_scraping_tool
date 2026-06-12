"""Paid scraping-API fallback adapter.

When a primary :class:`~scrapers.base.ScrapeProvider` raises
:exc:`~scrapers.base.BlockedError`, the pipeline retries the same query
through this provider if ``SCRAPER_API_KEY`` is configured.

The adapter supports two backends selected by ``settings.FALLBACK_PROVIDER``:

* ``"scraperapi"`` — proxies the Airbnb search page through ScraperAPI.
* ``"apify"`` — triggers an Airbnb-scraper actor on Apify and collects the
  dataset items.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import httpx

from errors import ConfigurationError, ScraperError
from scrapers.base import RawScrape, ScrapeProvider, SearchQuery


# ---------------------------------------------------------------------------
# Endpoint URLs
# ---------------------------------------------------------------------------

_SCRAPERAPI_URL: str = "https://api.scraperapi.com/"
_APIFY_RUN_URL: str = (
    "https://api.apify.com/v2/acts/dtrungtin~airbnb-scraper"
    "/run-sync-get-dataset-items"
)
_AIRBNB_SEARCH_PATH: str = "https://www.airbnb.com/s/{area}/homes"


class FallbackApiProvider(ScrapeProvider):
    """Routes a search through a configured paid scraping-API endpoint.

    Parameters
    ----------
    http_client:
        Optional pre-built :class:`httpx.Client`.  A new client is created
        when omitted (useful in production; override in tests).
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._http_client = http_client if http_client is not None else httpx.Client()

    # ------------------------------------------------------------------
    # ScrapeProvider interface
    # ------------------------------------------------------------------

    def search(self, query: SearchQuery) -> list[RawScrape]:  # type: ignore[override]
        """Proxy the search through the external scraping API.

        Raises
        ------
        ConfigurationError
            When ``SCRAPER_API_KEY`` is missing or the configured
            ``FALLBACK_PROVIDER`` is not recognised.
        ScraperError
            When the upstream API returns an HTTP error response.
        """
        from config import settings  # local import avoids circular deps

        api_key: Any = getattr(settings, "SCRAPER_API_KEY", None)
        if not api_key:
            raise ConfigurationError(
                "SCRAPER_API_KEY is not configured. "
                "Set the SCRAPER_API_KEY environment variable to enable the "
                "fallback scraping provider."
            )

        provider: str = getattr(settings, "FALLBACK_PROVIDER", "scraperapi")

        if provider == "scraperapi":
            return self._search_scraperapi(query, str(api_key))
        if provider == "apify":
            return self._search_apify(query, str(api_key))

        raise ConfigurationError(
            f"Unknown FALLBACK_PROVIDER {provider!r}. "
            "Supported values: 'scraperapi', 'apify'."
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_airbnb_url(self, query: SearchQuery) -> str:  # type: ignore[override]
        """Return the Airbnb search URL for *query*."""
        area_encoded = urllib.parse.quote(query.area, safe="")
        return _AIRBNB_SEARCH_PATH.format(area=area_encoded)

    def _search_scraperapi(self, query: SearchQuery, api_key: str) -> list[RawScrape]:  # type: ignore[override]
        target_url = self._build_airbnb_url(query)
        try:
            response = self._http_client.get(
                _SCRAPERAPI_URL,
                params={"api_key": api_key, "url": target_url},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ScraperError(
                f"ScraperAPI returned HTTP {exc.response.status_code}"
            ) from exc

        return [
            RawScrape(
                source="fallback_scraperapi",
                url=target_url,
                payload=response.text,
            )
        ]

    def _search_apify(self, query: SearchQuery, api_key: str) -> list[RawScrape]:  # type: ignore[override]
        target_url = self._build_airbnb_url(query)
        try:
            response = self._http_client.post(
                _APIFY_RUN_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"startUrls": [{"url": target_url}]},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ScraperError(
                f"Apify returned HTTP {exc.response.status_code}"
            ) from exc

        return [
            RawScrape(
                source="fallback_apify",
                url=target_url,
                payload=response.text,
            )
        ]
