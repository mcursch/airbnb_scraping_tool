"""Paid scraping-API fallback adapter.

When a primary :class:`~scrapers.base.ScrapeProvider` raises
:exc:`~scrapers.base.BlockedError`, the pipeline retries the same query
through this provider if ``SCRAPER_API_KEY`` is configured.

The adapter supports three backends selected by ``settings.FALLBACK_PROVIDER``:

* ``"scraperapi"`` — proxies the Airbnb search page through ScraperAPI.
* ``"apify"`` — triggers an Airbnb-scraper actor on Apify and collects the
  dataset items.
* ``"brightdata"`` — fetches the page through Bright Data's Web Unlocker
  (pay-per-successful-request), returning the raw page for the LLM extractor.
  Requires ``settings.BRIGHTDATA_ZONE`` (the Web Unlocker zone name) in
  addition to ``SCRAPER_API_KEY`` (the Bright Data API token).
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
_BRIGHTDATA_URL: str = "https://api.brightdata.com/request"
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
        # Web Unlocker / paid solvers can take tens of seconds on hard targets
        # (e.g. DataDome-protected Vrbo/Expedia), so use a generous timeout —
        # httpx's 5s default would abort mid-solve.
        self._http_client = (
            http_client if http_client is not None else httpx.Client(timeout=120.0)
        )

    # ------------------------------------------------------------------
    # ScrapeProvider interface
    # ------------------------------------------------------------------

    def search(
        self,
        query: SearchQuery,
        *,
        target_url: str | None = None,
        source_label: str | None = None,
    ) -> list[RawScrape]:  # type: ignore[override]
        """Proxy the search through the external scraping API.

        Parameters
        ----------
        target_url:
            Exact URL to fetch through the paid provider. When omitted, the
            Airbnb search URL for *query* is used (backward-compatible default).
            Pass the blocked source's own search URL to make the fallback
            source-aware (e.g. fetch the Vrbo/Expedia/Booking page, not Airbnb).
        source_label:
            Source identifier to stamp on the returned :class:`RawScrape` (e.g.
            ``"vrbo"``), so the fetched data is attributed to the real source.
            Defaults to a ``"fallback_<provider>"`` marker.

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
        url = target_url or self._build_airbnb_url(query)

        if provider == "scraperapi":
            return self._search_scraperapi(url, str(api_key), source_label)
        if provider == "apify":
            return self._search_apify(url, str(api_key), source_label)
        if provider == "brightdata":
            return self._search_brightdata(url, str(api_key), source_label)

        raise ConfigurationError(
            f"Unknown FALLBACK_PROVIDER {provider!r}. "
            "Supported values: 'scraperapi', 'apify', 'brightdata'."
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_airbnb_url(self, query: SearchQuery) -> str:  # type: ignore[override]
        """Return the Airbnb search URL for *query*."""
        area_encoded = urllib.parse.quote(query.area, safe="")
        return _AIRBNB_SEARCH_PATH.format(area=area_encoded)

    def _search_scraperapi(
        self, target_url: str, api_key: str, source_label: str | None = None
    ) -> list[RawScrape]:  # type: ignore[override]
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
                source=source_label or "fallback_scraperapi",
                url=target_url,
                payload=response.text,
            )
        ]

    def _search_apify(
        self, target_url: str, api_key: str, source_label: str | None = None
    ) -> list[RawScrape]:  # type: ignore[override]
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
                source=source_label or "fallback_apify",
                url=target_url,
                payload=response.text,
            )
        ]

    def _search_brightdata(
        self, target_url: str, api_key: str, source_label: str | None = None
    ) -> list[RawScrape]:  # type: ignore[override]
        """Fetch *target_url* through Bright Data's Web Unlocker.

        Pay-per-successful-request; returns the raw page body (``format: raw``)
        which the LLM extractor then parses, just like the ScraperAPI path.
        Works for any source's URL (Airbnb, Vrbo, Expedia, …), not just Airbnb.
        """
        from config import settings

        zone = getattr(settings, "BRIGHTDATA_ZONE", "") or "web_unlocker"
        try:
            response = self._http_client.post(
                _BRIGHTDATA_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"zone": zone, "url": target_url, "format": "raw"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ScraperError(
                f"Bright Data returned HTTP {exc.response.status_code}"
            ) from exc

        return [
            RawScrape(
                source=source_label or "fallback_brightdata",
                url=target_url,
                payload=response.text,
            )
        ]
