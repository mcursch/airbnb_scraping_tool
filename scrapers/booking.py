"""
scrapers/booking.py
===================
Booking.com scraper implementing :class:`ScrapeProvider`.

Strategy
--------
1. Attempt the request with **httpx** (fast, low overhead).
2. If the response looks like a CAPTCHA or bot-challenge page, escalate to
   a headless **Playwright** browser.
3. If the Playwright response is also blocked, raise :class:`BlockedError` so
   the caller can engage a fallback provider or surface the failure clearly.

All URL patterns, request headers, block-detection patterns, and CSS selectors
live in the **CONSTANTS** section below so future fixes are one-place changes.
"""

from __future__ import annotations

# ============================================================
# CONSTANTS
# ============================================================

# --- Base URL -----------------------------------------------------------
SEARCH_URL: str = "https://www.booking.com/searchresults.html"

# --- Default request headers (mimic a modern Chrome browser) ------------
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xhtml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"macOS"',
}

# --- HTTP status codes that indicate an IP-level block ------------------
BLOCKED_STATUS_CODES: frozenset[int] = frozenset({403, 429, 503})

# --- Substrings (lower-cased) that indicate a CAPTCHA / challenge page --
# Order: most specific first so the BlockedError reason is informative.
CAPTCHA_PATTERNS: tuple[str, ...] = (
    "px-captcha",           # PerimeterX
    "perimeterx",
    "distil_identify_cookie",
    "cf-challenge",         # Cloudflare
    "cf_chl_",
    "captcha",              # generic CAPTCHA
    "are you a human",
    "unusual traffic",
    "automated access",
    "bot detection",
    "robot or human",
)

# --- CSS selectors for Booking.com page structure -----------------------
# Booking.com uses data-testid attributes across most interactive elements.
SELECTORS: dict[str, str] = {
    # Container for each listed property
    "property_card": '[data-testid="property-card"]',
    # Property name inside a card
    "title": '[data-testid="title"]',
    # Displayed price (may include discount)
    "price": '[data-testid="price-and-discounted-price"]',
    # Numeric review score
    "review_score": '[data-testid="review-score"]',
    # Anchor wrapping the property title / link to the property page
    "title_link": 'a[data-testid="title-link"]',
    # "Next page" pagination control; absence means we are on the last page
    "pagination_next": '[data-testid="pagination-next"]',
    # Total result count banner
    "search_results_count": '[data-testid="search-results-count"]',
}

# --- Pagination step: Booking.com returns 25 results per page -----------
RESULTS_PER_PAGE: int = 25

# --- Polite crawl delay range (seconds) between paginated requests ------
MIN_DELAY: float = 1.5
MAX_DELAY: float = 3.5

# --- Playwright navigation timeout (milliseconds) -----------------------
PW_TIMEOUT_MS: int = 30_000

# --- Extra JS-settle wait in Playwright after page load (milliseconds) --
PW_SETTLE_MS: int = 2_000

# ============================================================
# Implementation
# ============================================================

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BlockedError, RawScrape, ScrapeProvider, SearchQuery

log = logging.getLogger(__name__)


class BookingScraper(ScrapeProvider):
    """
    Scraper for Booking.com hotel / short-stay search results.

    Parameters
    ----------
    session:
        Optional SQLAlchemy ``Session``.  When provided, each
        :class:`RawScrape` row is flushed to the database immediately after
        the page is fetched, decoupling acquisition from extraction.
        Pass ``None`` (default) for in-memory-only operation (e.g. in tests).
    max_pages:
        Hard upper bound on pagination, regardless of the query's
        ``page_limit``.  Defaults to 5.
    """

    SOURCE: str = "booking"

    def __init__(
        self,
        session: Optional[Any] = None,
        *,
        max_pages: int = 5,
    ) -> None:
        self._session = session
        self._max_pages = max_pages

    # ------------------------------------------------------------------
    # ScrapeProvider interface
    # ------------------------------------------------------------------

    def search(self, query: SearchQuery) -> list[RawScrape]:
        """
        Fetch Booking.com search results for *query*.

        Paginates up to ``min(max_pages, query.page_limit)`` pages and
        returns one :class:`RawScrape` per fetched page.

        Raises
        ------
        BlockedError
            When a CAPTCHA or block response cannot be overcome (both httpx
            and Playwright escalation have failed).
        """
        results: list[RawScrape] = []
        effective_limit = min(self._max_pages, query.page_limit)
        page = 1
        offset = 0

        while page <= effective_limit:
            url = self._build_url(query, offset=offset)
            log.info("Booking.com: fetching page %d – %s", page, url)

            raw = self._fetch_page(url)
            self._persist(raw)
            results.append(raw)

            # Determine whether a next page exists
            soup = BeautifulSoup(raw.payload, "lxml")
            has_next = soup.select_one(SELECTORS["pagination_next"]) is not None
            if not has_next:
                log.info(
                    "Booking.com: no next-page control found; stopping after page %d.",
                    page,
                )
                break

            page += 1
            offset += RESULTS_PER_PAGE

            if page <= effective_limit:
                self._polite_delay()

        return results

    # ------------------------------------------------------------------
    # Internal: URL construction
    # ------------------------------------------------------------------

    def _build_url(self, query: SearchQuery, *, offset: int = 0) -> str:
        """Build a Booking.com search URL from *query* and *offset*."""
        params: list[tuple[str, str]] = [
            ("ss", query.area),
            ("lang", "en-us"),
            ("offset", str(offset)),
            ("group_adults", str(query.guests)),
            ("no_rooms", "1"),
            ("selected_currency", "USD"),
        ]
        if query.checkin:
            params.append(("checkin", query.checkin))
        if query.checkout:
            params.append(("checkout", query.checkout))

        qs = "&".join(f"{k}={v}" for k, v in params)
        return f"{SEARCH_URL}?{qs}"

    # ------------------------------------------------------------------
    # Internal: page fetching (httpx → Playwright escalation)
    # ------------------------------------------------------------------

    def _fetch_page(self, url: str) -> RawScrape:
        """
        Retrieve *url*, escalating from httpx to Playwright on block.

        Returns a :class:`RawScrape` (not yet persisted).

        Raises
        ------
        BlockedError
            When both httpx and Playwright return a blocked response.
        """
        html: str
        try:
            html = self._fetch_httpx(url)
            log.debug("Booking.com: httpx succeeded for %s", url)
        except BlockedError as exc:
            log.warning(
                "Booking.com: httpx blocked (%s); escalating to Playwright.", exc.reason
            )
            html = self._fetch_playwright(url)
            log.debug("Booking.com: Playwright succeeded for %s", url)

        return RawScrape(
            source=self.SOURCE,
            url=url,
            payload=html,
            fetched_at=datetime.now(timezone.utc),
            status="pending",
        )

    def _fetch_httpx(self, url: str) -> str:
        """
        Perform a synchronous HTTP GET with httpx.

        Raises
        ------
        BlockedError
            On a blocked status code or CAPTCHA body content.
        """
        with httpx.Client(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=20,
        ) as client:
            response = client.get(url)

        self._check_for_block(response.status_code, response.text, url=url)
        return response.text

    def _fetch_playwright(self, url: str) -> str:
        """
        Fetch *url* with a headless Chromium browser via Playwright.

        Raises
        ------
        RuntimeError
            When the ``playwright`` package is not installed.
        BlockedError
            When the rendered page is still blocked.
        """
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed; run: uv run playwright install chromium"
            ) from exc

        extra_headers = {k: v for k, v in DEFAULT_HEADERS.items() if k != "User-Agent"}

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=DEFAULT_HEADERS["User-Agent"],
                    extra_http_headers=extra_headers,
                )
                page = context.new_page()
                page.goto(url, timeout=PW_TIMEOUT_MS, wait_until="domcontentloaded")
                page.wait_for_timeout(PW_SETTLE_MS)
                html = page.content()
            finally:
                browser.close()

        self._check_for_block(200, html, url=url)
        return html

    # ------------------------------------------------------------------
    # Internal: block / CAPTCHA detection
    # ------------------------------------------------------------------

    def _check_for_block(
        self, status_code: int, body: str, *, url: str = ""
    ) -> None:
        """
        Inspect *status_code* and *body* for block / CAPTCHA signals.

        Raises
        ------
        BlockedError
            When any block indicator is found.
        """
        if status_code in BLOCKED_STATUS_CODES:
            raise BlockedError(url=url, reason=f"HTTP {status_code}")

        body_lower = body.lower()
        for pattern in CAPTCHA_PATTERNS:
            if pattern in body_lower:
                raise BlockedError(
                    url=url,
                    reason=f"CAPTCHA pattern {pattern!r} detected in response body",
                )

    # ------------------------------------------------------------------
    # Internal: persistence
    # ------------------------------------------------------------------

    def _persist(self, raw: RawScrape) -> None:
        """
        Flush *raw* to the database when a SQLAlchemy session is available.

        Failures are logged but never re-raised so a transient DB error does
        not abort the ongoing scrape.
        """
        if self._session is None:
            return
        try:
            from db.models import RawScrapeRow  # type: ignore[import]

            row = RawScrapeRow(
                source=raw.source,
                url=raw.url,
                payload=raw.payload,
                content_hash=raw.content_hash,
                fetched_at=raw.fetched_at,
                status=raw.status,
            )
            self._session.add(row)
            self._session.flush()
            log.debug("Persisted RawScrape id=%s url=%s", row.id, raw.url)
        except Exception:
            log.exception("Failed to persist RawScrape for %s", raw.url)

    # ------------------------------------------------------------------
    # Internal: polite crawl delay
    # ------------------------------------------------------------------

    @staticmethod
    def _polite_delay() -> None:
        """Sleep a random interval between paginated requests."""
        delay = random.uniform(MIN_DELAY, MAX_DELAY)
        log.debug("Booking.com: sleeping %.1fs (polite delay)", delay)
        time.sleep(delay)
