"""Paid scraping-API fallback adapter.

When direct scraping is blocked this provider re-issues the same search
query through a third-party paid scraping service.  Two providers are
supported, selected by the ``FALLBACK_PROVIDER`` config key:

* ``"scraperapi"`` (default) — routes each URL through
  ``https://api.scraperapi.com/``.
* ``"apify"`` — submits a run to an Apify actor and returns the resulting
  dataset items as a single JSON payload.

The key ``SCRAPER_API_KEY`` must be set in the environment (or ``.env``)
before calling :meth:`FallbackApiProvider.search`; otherwise a
:class:`errors.ConfigurationError` is raised immediately with a human-
readable message that names the missing key.
"""

from __future__ import annotations

import datetime
import json
import urllib.parse

import httpx

from config import settings
from errors import ConfigurationError, ScraperError
from schemas.search import SearchQuery
from scrapers.base import RawPayload, ScrapeProvider

# ── Provider constants ────────────────────────────────────────────────────────

_SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"

# Apify: generic "web scraper" actor that accepts a startUrls payload.
# Users may override this via APIFY_ACTOR_ID if they prefer a specialised actor.
_APIFY_ACTOR_DEFAULT = "apify~web-scraper"
_APIFY_RUN_ENDPOINT = (
    "https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items"
)

# Booking.com search URL template used to build the target URL from a query.
_BOOKING_SEARCH_URL = (
    "https://www.booking.com/searchresults.html"
    "?ss={area}"
    "&checkin_year={checkin_year}&checkin_month={checkin_month}&checkin_monthday={checkin_day}"
    "&checkout_year={checkout_year}&checkout_month={checkout_month}&checkout_monthday={checkout_day}"
    "&group_adults={guests}"
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_booking_url(query: SearchQuery) -> str:
    """Return a Booking.com search URL for *query*."""
    today = datetime.date.today()
    checkin = query.checkin or today
    checkout = query.checkout or (today + datetime.timedelta(days=1))
    return _BOOKING_SEARCH_URL.format(
        area=urllib.parse.quote_plus(query.area),
        checkin_year=checkin.year,
        checkin_month=checkin.month,
        checkin_day=checkin.day,
        checkout_year=checkout.year,
        checkout_month=checkout.month,
        checkout_day=checkout.day,
        guests=query.guests,
    )


def _require_api_key() -> str:
    """Return the API key or raise a descriptive :class:`ConfigurationError`."""
    key = settings.SCRAPER_API_KEY
    if not key:
        raise ConfigurationError(
            "SCRAPER_API_KEY is not configured. "
            "Set the SCRAPER_API_KEY environment variable (or add it to your .env file) "
            "to enable the paid-API fallback provider."
        )
    return key


# ── Provider ──────────────────────────────────────────────────────────────────


class FallbackApiProvider(ScrapeProvider):
    """Routes a :class:`SearchQuery` through a paid scraping API.

    Parameters
    ----------
    http_client:
        Optional pre-configured :class:`httpx.Client`.  Injected in tests to
        avoid real network calls; defaults to a fresh client with a 60-second
        timeout.
    """

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(timeout=60.0)

    # ── Public interface ──────────────────────────────────────────────────────

    def search(self, query: SearchQuery) -> list[RawPayload]:
        """Execute *query* via the configured paid scraping provider.

        Raises
        ------
        ConfigurationError
            If ``SCRAPER_API_KEY`` is not set.
        ScraperError
            If the provider returns an HTTP error.
        """
        api_key = _require_api_key()
        provider = settings.FALLBACK_PROVIDER or "scraperapi"

        if provider == "scraperapi":
            return self._search_scraperapi(query, api_key)
        elif provider == "apify":
            return self._search_apify(query, api_key)
        else:
            raise ConfigurationError(
                f"Unknown FALLBACK_PROVIDER '{provider}'. "
                "Supported values are 'scraperapi' and 'apify'."
            )

    # ── ScraperAPI ────────────────────────────────────────────────────────────

    def _search_scraperapi(
        self, query: SearchQuery, api_key: str
    ) -> list[RawPayload]:
        target_url = _build_booking_url(query)
        try:
            response = self._client.get(
                _SCRAPERAPI_ENDPOINT,
                params={"api_key": api_key, "url": target_url},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ScraperError(
                f"ScraperAPI returned HTTP {exc.response.status_code} "
                f"for target URL {target_url!r}"
            ) from exc
        except httpx.RequestError as exc:
            raise ScraperError(
                f"Network error contacting ScraperAPI: {exc}"
            ) from exc

        return [
            RawPayload(
                source="fallback_scraperapi",
                url=target_url,
                payload=response.text,
                fetched_at=datetime.datetime.now(datetime.timezone.utc),
            )
        ]

    # ── Apify ─────────────────────────────────────────────────────────────────

    def _search_apify(self, query: SearchQuery, api_key: str) -> list[RawPayload]:
        actor_id = _APIFY_ACTOR_DEFAULT
        target_url = _build_booking_url(query)
        endpoint = _APIFY_RUN_ENDPOINT.format(actor_id=actor_id)

        body = {
            "startUrls": [{"url": target_url}],
            "maxPagesPerCrawl": 1,
        }
        try:
            response = self._client.post(
                endpoint,
                params={"token": api_key},
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ScraperError(
                f"Apify returned HTTP {exc.response.status_code} "
                f"for actor {actor_id!r}"
            ) from exc
        except httpx.RequestError as exc:
            raise ScraperError(
                f"Network error contacting Apify: {exc}"
            ) from exc

        # Apify returns a JSON array of crawled items; serialise back to a
        # string so the payload is uniform with other providers.
        items: list[object] = response.json()
        payload_text = json.dumps(items)

        return [
            RawPayload(
                source="fallback_apify",
                url=target_url,
                payload=payload_text,
                fetched_at=datetime.datetime.now(datetime.timezone.utc),
            )
        ]
