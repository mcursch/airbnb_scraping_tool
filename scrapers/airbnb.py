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
import json
import logging
import random
import time
import urllib.parse
from typing import TYPE_CHECKING, Any

try:
    from playwright.async_api import (
        Response,
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

# Stealth is an optional enhancement — the browser still runs without it (just
# at higher bot-detection risk). Do NOT couple its availability to playwright's.
try:
    from playwright_stealth import Stealth
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False

try:
    from playwright.sync_api import sync_playwright as _sync_playwright
except ImportError:
    _sync_playwright = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from playwright.async_api import Response
    from playwright.sync_api import Page

from scrapers.base import RawScrape as BaseRawScrape, ScrapeProvider, SearchQuery as BaseSearchQuery

# RawPayload is a parsed-JSON dict from an Airbnb API response.
RawPayload = dict
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
from config import settings
from db.repo import create_raw_scrape

log = logging.getLogger(__name__)

# Cursor keys searched (in order) inside Airbnb API response JSON.
_CURSOR_KEYS: tuple[str, ...] = (
    "paginationCursor",
    "nextPageCursor",
    "cursor",
)

# Selectors for the "Next" pagination button (tried in order).
_NEXT_BTN_SELECTORS: tuple[str, ...] = (
    '[aria-label="Next"]',
    'a[data-testid="pagination-next"]',
    'button[data-testid="pagination-next"]',
    'a[aria-label="Next"]',
)


# ---------------------------------------------------------------------------
# Module-level sync helper functions (used by sync pagination flow)
# ---------------------------------------------------------------------------


def _is_search_response(url: str) -> bool:
    """Return True if *url* looks like an Airbnb search-results API endpoint."""
    return any(pat in url for pat in AIRBNB_ENDPOINT_PATTERNS)


def _extract_cursor(payload_text: str) -> str | None:
    """Try to extract a next-page cursor string from a JSON payload."""
    try:
        data = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return None

    def _search(obj: Any) -> str | None:
        if isinstance(obj, dict):
            for key in _CURSOR_KEYS:
                if key in obj and isinstance(obj[key], str) and obj[key]:
                    return obj[key]
            for v in obj.values():
                result = _search(v)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = _search(item)
                if result:
                    return result
        return None

    return _search(data)


def _build_search_url(query: BaseSearchQuery) -> str:
    """Build an Airbnb homes search URL from a :class:`SearchQuery`."""
    area_encoded = urllib.parse.quote(query.area, safe="")
    path = AIRBNB_SEARCH_PATH.format(area=area_encoded)

    params: dict[str, str] = {}
    if query.checkin is not None:
        params["checkin"] = str(query.checkin)
    if query.checkout is not None:
        params["checkout"] = str(query.checkout)
    if query.guests and query.guests > 1:
        params["adults"] = str(query.guests)

    qs = urllib.parse.urlencode(params)
    base = f"{AIRBNB_BASE_URL}{path}"
    return f"{base}?{qs}" if qs else base


def _polite_sleep(page_index: int) -> float:
    """Sleep a random duration between page fetches; return seconds slept."""
    lo = settings.rate_limit_min_seconds
    hi = settings.rate_limit_max_seconds
    duration = random.uniform(lo, hi)
    log.info(
        "Rate-limit sleep before page %d: sleeping %.2fs (window %.1f–%.1fs)",
        page_index,
        duration,
        lo,
        hi,
    )
    time.sleep(duration)
    return duration


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
        max_pages: int | None = None,
        *,
        headless: bool = DEFAULT_HEADLESS,
        page_timeout_ms: int = DEFAULT_PAGE_TIMEOUT_MS,
        extra_wait_min: float = DEFAULT_EXTRA_WAIT_MIN,
        extra_wait_max: float = DEFAULT_EXTRA_WAIT_MAX,
    ) -> None:
        self._max_pages = max_pages if max_pages is not None else settings.max_pages
        self.headless = headless
        self.page_timeout_ms = page_timeout_ms
        self.extra_wait_min = extra_wait_min
        self.extra_wait_max = extra_wait_max

    SOURCE = "airbnb"

    # ------------------------------------------------------------------
    # Public interface (synchronous, conforming to ScrapeProvider)
    # ------------------------------------------------------------------

    def fallback_url(self, query: SearchQuery) -> str | None:
        """Airbnb search URL for the paid fallback (Web Unlocker) to fetch."""
        return build_search_url(query)

    def search(self, query: SearchQuery) -> list[BaseRawScrape]:
        """Execute a search and return raw page payloads as :class:`RawScrape` records.

        Runs the async :meth:`_run_browser` coroutine via :func:`asyncio.run`
        so that this method conforms to the synchronous
        :class:`~scrapers.base.ScrapeProvider` interface.  Callers receive a
        plain list and never a coroutine object or async generator.

        Parameters
        ----------
        query:
            Describes the area, dates, and guest count to search for.

        Returns
        -------
        list[RawScrape]
            One record per intercepted Airbnb API response.
        """
        captured = asyncio.run(self._run_browser(query))
        results: list[BaseRawScrape] = []
        for url, payload in captured:
            results.append(
                BaseRawScrape(
                    source="airbnb",
                    url=url,
                    payload=json.dumps(payload),
                )
            )
        return results

    # ------------------------------------------------------------------
    # Sync pagination flow (used by tests via monkeypatching)
    # ------------------------------------------------------------------

    def _run_search(
        self,
        page: Any,
        query: BaseSearchQuery,
        session: Any,
        run_id: int | None,
    ) -> list[Any]:
        """Drive a synchronous search over up to ``_max_pages`` pages.

        Each captured payload is persisted immediately via :func:`create_raw_scrape`.
        Returns the list of persisted :class:`~db.models.RawScrape` instances.
        """
        captured: list[Any] = []
        page_num = 1
        url = _build_search_url(query)

        while page_num <= self._max_pages:
            payload_text, intercepted_url = self._load_and_capture(page, url)

            if payload_text is None:
                log.warning("Page %d: no matching response intercepted; stopping.", page_num)
                break

            raw = create_raw_scrape(
                session,
                source="airbnb",
                url=intercepted_url or url,
                payload=payload_text,
                run_id=run_id,
                status="pending",
                page_number=page_num,
            )
            if raw is None:
                log.info("Page %d: duplicate payload skipped.", page_num)
            else:
                captured.append(raw)

            if page_num >= self._max_pages:
                break

            _polite_sleep(page_num + 1)

            cursor = _extract_cursor(payload_text)
            url = self._next_page_url(page, url, cursor, page_num)
            if url is None:
                break

            page_num += 1

        return captured

    def _load_and_capture(
        self, page: Any, url: str
    ) -> tuple[str | None, str | None]:
        """Navigate to *url* using a sync Playwright page and capture the first
        matching API response.  Returns ``(payload_text, intercepted_url)``.

        This default implementation uses ``sync_playwright``; override or
        monkeypatch for tests.
        """
        if _sync_playwright is None:
            raise RuntimeError("playwright is not installed")
        captured_payload: list[str] = []
        captured_url: list[str] = []

        def _on_response(response: Any) -> None:
            if captured_payload:
                return
            if not _is_search_response(response.url):
                return
            try:
                text = response.text()
                if text and len(text) > 100:
                    captured_payload.append(text)
                    captured_url.append(response.url)
            except Exception:  # noqa: BLE001
                pass

        page.on("response", _on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=self.page_timeout_ms)
        except Exception as exc:  # noqa: BLE001
            log.warning("Navigation to %s raised: %s", url, exc)
        finally:
            try:
                page.remove_listener("response", _on_response)
            except Exception:  # noqa: BLE001
                pass

        if captured_payload:
            return captured_payload[0], captured_url[0]
        return None, None

    def _next_page_url(
        self,
        page: Any,
        current_url: str,
        cursor: str | None,
        current_page: int,
    ) -> str | None:
        """Return the URL for the next page, or None if pagination is exhausted."""
        if cursor:
            parsed = urllib.parse.urlparse(current_url)
            params = dict(urllib.parse.parse_qsl(parsed.query))
            params["cursor"] = cursor
            params["section_offset"] = str(current_page)
            new_query = urllib.parse.urlencode(params)
            return urllib.parse.urlunparse(parsed._replace(query=new_query))

        for selector in _NEXT_BTN_SELECTORS:
            try:
                element = page.query_selector(selector)
                if element is None:
                    continue
                href = element.get_attribute("href")
                if href:
                    if href.startswith("/"):
                        href = f"{AIRBNB_BASE_URL}{href}"
                    return href
                element.click()
                page.wait_for_load_state("networkidle", timeout=self.page_timeout_ms)
                return page.url
            except Exception:  # noqa: BLE001
                continue

        return None

    # ------------------------------------------------------------------
    # Internal async helpers
    # ------------------------------------------------------------------

    async def _run_browser(
        self, query: SearchQuery
    ) -> list[tuple[str, RawPayload]]:
        """Drive Chromium, intercept matching responses, and return results."""
        if not _PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("playwright is not installed; cannot run browser")

        # Collect response objects synchronously so we avoid scheduling async
        # tasks inside the event listener (cleaner lifecycle management).
        pending_responses: list[tuple[str, Any]] = []

        def _on_response(response: Any) -> None:
            """Synchronous listener: stash matching responses for later parsing."""
            if matches_airbnb_endpoint(response.url):
                pending_responses.append((response.url, response))

        async with async_playwright() as playwright:  # type: ignore[name-defined]
            browser = await playwright.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = await context.new_page()

            # Apply stealth patches before any navigation (optional).
            if _STEALTH_AVAILABLE:
                await Stealth().apply_stealth_async(page)  # type: ignore[name-defined]
            else:
                log.warning(
                    "playwright-stealth not installed; running without stealth "
                    "patches (higher bot-detection risk). `pip install playwright-stealth`."
                )

            page.on("response", _on_response)

            search_url = build_search_url(query)
            # "networkidle" is unreliable on Airbnb's SPA (it keeps long-lived
            # connections open, so the event often never fires). Wait for the
            # DOM instead, and if even that times out, still harvest whatever
            # API responses were captured during the attempt rather than failing
            # the whole scrape.
            try:
                await page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=self.page_timeout_ms,
                )
            except PlaywrightTimeoutError:  # type: ignore[name-defined]
                log.warning(
                    "Airbnb navigation timed out (%dms); harvesting any captured "
                    "responses.", self.page_timeout_ms,
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
