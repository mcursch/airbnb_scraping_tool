"""Airbnb scraper — Playwright + playwright-stealth implementation.

Strategy
--------
1. Launch a headless Chromium browser with stealth patches applied so that
   Airbnb's bot-detection is less likely to fire.
2. Navigate to the Airbnb homes search URL built from the ``SearchQuery``.
3. Collect every network *response* whose URL contains one of the endpoint
   patterns from ``scrapers.constants``.
4. After the page reaches network-idle, parse each collected response body as
   JSON and yield ``(url, payload)`` pairs to the caller.

All selectors/endpoint patterns are imported exclusively from
``scrapers.constants`` — no literal strings related to Airbnb's API appear
in this file.
"""

from __future__ import annotations

import asyncio
import random
import urllib.parse
from collections.abc import AsyncGenerator

from playwright.async_api import Response, async_playwright
from playwright_stealth import Stealth

from scrapers.base import RawPayload, ScrapeProvider
from scrapers.constants import (
    AIRBNB_BASE_URL,
    AIRBNB_ENDPOINT_PATTERNS,
    AIRBNB_SEARCH_PATH,
    DEFAULT_EXTRA_WAIT_MAX,
    DEFAULT_EXTRA_WAIT_MIN,
    DEFAULT_HEADLESS,
    DEFAULT_PAGE_TIMEOUT_MS,
    DEFAULT_USER_AGENT,
)
from schemas.search_query import SearchQuery


class AirbnbScraper(ScrapeProvider):
    """Playwright-based scraper for Airbnb search results.

    Parameters
    ----------
    headless:
        Pass ``False`` to open a visible browser window (useful for debugging).
    page_timeout_ms:
        Maximum milliseconds to wait for ``page.goto()`` to complete.
    extra_wait_min / extra_wait_max:
        Bounds (seconds) for the randomised pause after network-idle that
        gives late-arriving responses time to be buffered.
    """

    def __init__(
        self,
        *,
        headless: bool = DEFAULT_HEADLESS,
        page_timeout_ms: int = DEFAULT_PAGE_TIMEOUT_MS,
        extra_wait_min: float = DEFAULT_EXTRA_WAIT_MIN,
        extra_wait_max: float = DEFAULT_EXTRA_WAIT_MAX,
    ) -> None:
        self.headless = headless
        self.page_timeout_ms = page_timeout_ms
        self.extra_wait_min = extra_wait_min
        self.extra_wait_max = extra_wait_max

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def search(
        self, query: SearchQuery
    ) -> AsyncGenerator[tuple[str, RawPayload], None]:
        """Yield ``(url, payload)`` for each intercepted Airbnb API response.

        Yields
        ------
        tuple[str, RawPayload]
            * ``url`` — the full URL of the intercepted API response.
            * ``payload`` — the response body parsed from JSON into a ``dict``.
        """
        captured = await self._run_browser(query)
        for url, payload in captured:
            yield url, payload

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_browser(
        self, query: SearchQuery
    ) -> list[tuple[str, RawPayload]]:
        """Drive Chromium, intercept matching responses, and return results."""
        # Collect response objects synchronously so we avoid scheduling async
        # tasks inside the event listener (cleaner lifecycle management).
        pending_responses: list[tuple[str, Response]] = []

        def _on_response(response: Response) -> None:
            """Synchronous listener: stash matching responses for later parsing."""
            if matches_airbnb_endpoint(response.url):
                pending_responses.append((response.url, response))

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = await context.new_page()

            # Apply stealth patches before any navigation.
            await Stealth().apply_stealth_async(page)

            page.on("response", _on_response)

            search_url = build_search_url(query)
            await page.goto(
                search_url,
                wait_until="networkidle",
                timeout=self.page_timeout_ms,
            )

            # Polite pause — lets any final XHRs land before we read bodies.
            await asyncio.sleep(
                random.uniform(self.extra_wait_min, self.extra_wait_max)
            )

            # Parse response bodies while the browser context is still open.
            captured: list[tuple[str, RawPayload]] = []
            for url, response in pending_responses:
                payload = await _parse_json_response(url, response)
                if payload is not None:
                    captured.append((url, payload))

            await browser.close()

        return captured


# ---------------------------------------------------------------------------
# Module-level pure helpers (testable without a browser)
# ---------------------------------------------------------------------------


def matches_airbnb_endpoint(url: str) -> bool:
    """Return ``True`` if *url* contains any of the monitored API patterns.

    Uses only ``AIRBNB_ENDPOINT_PATTERNS`` from ``scrapers.constants`` —
    no hardcoded strings appear in this function.
    """
    return any(pattern in url for pattern in AIRBNB_ENDPOINT_PATTERNS)


def build_search_url(query: SearchQuery) -> str:
    """Build an Airbnb homes search URL from a ``SearchQuery``.

    Uses only constants imported from ``scrapers.constants``.
    """
    area_encoded = urllib.parse.quote(query.area, safe="")
    path = AIRBNB_SEARCH_PATH.format(area=area_encoded)

    params: dict[str, str] = {}
    if query.checkin is not None:
        params["checkin"] = str(query.checkin)
    if query.checkout is not None:
        params["checkout"] = str(query.checkout)
    if query.guests > 1:
        params["adults"] = str(query.guests)

    qs = urllib.parse.urlencode(params)
    base = f"{AIRBNB_BASE_URL}{path}"
    return f"{base}?{qs}" if qs else base


async def _parse_json_response(
    url: str, response: Response
) -> RawPayload | None:
    """Try to parse *response* as JSON; return ``None`` on any failure."""
    try:
        payload = await response.json()
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    if not payload:
        return None
    return payload
