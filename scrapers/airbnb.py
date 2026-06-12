"""Airbnb scraper — Playwright-based, network-intercept strategy.

Design
------
Instead of parsing rendered HTML (fragile), we intercept the internal
Airbnb StaysSearch / StaysPdpSections API calls that the page makes.
Those JSON responses contain structured listing data that is far easier
to work with than the rendered DOM.

Pagination
----------
Airbnb uses cursor-based pagination.  After capturing a page's response
we inspect it for a ``paginationCursor`` / ``nextPageCursor`` value.  If
found we inject it into the next request via URL query-parameter
manipulation; otherwise we fall back to clicking the ``aria-label="Next"``
pagination button.

Rate limiting
-------------
Between every page we sleep for a random duration within the
[rate_limit_min_seconds, rate_limit_max_seconds] window (both read from
``config.py``).  The actual sleep duration is logged so the caller (and
tests) can verify it.

Persistence
-----------
Each captured payload is immediately persisted as a ``RawScrape`` row
(status ``pending``) via ``db/repo.py`` *before* we attempt the next
page.  A mid-run kill therefore leaves all already-fetched rows intact.

Selectors / endpoint patterns are isolated in the ``_CONSTANTS`` block at
the top of this file so a site-change only requires editing one place.
"""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.parse
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Page, Response, sync_playwright

from config import settings
from db.repo import create_raw_scrape
from scrapers.base import RawPayload, ScrapeProvider, SearchQuery

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── Constants (change only here when Airbnb updates its API) ─────────────────

# URL fragments that identify search-result API responses we want to capture.
_INTERCEPT_PATTERNS: tuple[str, ...] = (
    "StaysSearch",
    "explore_tabs",
    "ExploreSearch",
    "api/v3/Stays",
)

# The Airbnb search base URL.
_SEARCH_BASE = "https://www.airbnb.com/s/{area}/homes"

# Pagination cursor keys found in Airbnb API responses (tried in order).
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

# User-agent to send (reduces bot-detection false positives).
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_search_response(url: str) -> bool:
    """Return True if *url* looks like an Airbnb search-results API call."""
    return any(pat in url for pat in _INTERCEPT_PATTERNS)


def _extract_cursor(payload_text: str) -> str | None:
    """Try to extract a next-page cursor from a JSON payload string."""
    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError:
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


def _build_search_url(query: SearchQuery) -> str:
    """Construct the Airbnb search URL for *query*."""
    area_slug = urllib.parse.quote(query.area)
    url = _SEARCH_BASE.format(area=area_slug)
    params: dict[str, str] = {}
    if query.checkin:
        params["checkin"] = query.checkin
    if query.checkout:
        params["checkout"] = query.checkout
    if query.guests and query.guests > 1:
        params["adults"] = str(query.guests)
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    return url


def _polite_sleep(page_index: int) -> float:
    """Sleep a random duration between pages and return the actual seconds slept."""
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


# ── Scraper ───────────────────────────────────────────────────────────────────

class AirbnbScraper(ScrapeProvider):
    """Playwright-based Airbnb search scraper."""

    source = "airbnb"

    def __init__(self, max_pages: int | None = None) -> None:
        self._max_pages = max_pages if max_pages is not None else settings.max_pages

    # ── public interface ──────────────────────────────────────────────────────

    def search(
        self,
        query: SearchQuery,
        session: "Session",
        run_id: int | None = None,
    ) -> list[RawPayload]:
        """Drive an Airbnb search and return all captured payloads.

        Payloads are persisted to the DB immediately as they are captured
        (crash-safe).  Pagination continues up to ``max_pages`` pages.
        """
        captured: list[RawPayload] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=settings.browser_headless)
            context = browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            try:
                captured = self._run_search(page, query, session, run_id)
            finally:
                context.close()
                browser.close()

        return captured

    # ── private helpers ───────────────────────────────────────────────────────

    def _run_search(
        self,
        page: Page,
        query: SearchQuery,
        session: "Session",
        run_id: int | None,
    ) -> list[RawPayload]:
        captured: list[RawPayload] = []
        page_num = 1
        next_cursor: str | None = None

        url = _build_search_url(query)
        log.info("Airbnb search start — area=%r  max_pages=%d  url=%s", query.area, self._max_pages, url)

        while page_num <= self._max_pages:
            log.info("Fetching page %d of %d  url=%s", page_num, self._max_pages, url)

            payload_text, intercepted_url = self._load_and_capture(page, url)

            if payload_text is None:
                log.warning("Page %d: no matching API response intercepted; stopping.", page_num)
                break

            # --- persist immediately (crash-safe) ---
            raw = create_raw_scrape(
                session,
                source=self.source,
                url=intercepted_url or url,
                payload=payload_text,
                run_id=run_id,
                status="pending",
                page_number=page_num,
            )
            if raw is None:
                log.info("Page %d: duplicate payload (hash already in DB); skipping.", page_num)
            else:
                log.info("Page %d: persisted RawScrape id=%d", page_num, raw.id)
                captured.append(
                    RawPayload(
                        source=self.source,
                        url=intercepted_url or url,
                        payload=payload_text,
                        page_number=page_num,
                    )
                )

            if page_num >= self._max_pages:
                log.info("Reached max_pages=%d; stopping pagination.", self._max_pages)
                break

            # --- inter-page delay ---
            _polite_sleep(page_num + 1)

            # --- advance to next page ---
            next_cursor = _extract_cursor(payload_text)
            url = self._next_page_url(page, url, next_cursor, page_num)
            if url is None:
                log.info("No next page found after page %d; stopping.", page_num)
                break

            page_num += 1

        log.info("Airbnb search complete — %d page(s) captured.", len(captured))
        return captured

    def _load_and_capture(
        self, page: Page, url: str
    ) -> tuple[str | None, str | None]:
        """Navigate to *url*, intercept the first matching API response,
        and return (payload_text, intercepted_url)."""
        captured_payload: list[str] = []
        captured_url: list[str] = []

        def _on_response(response: Response) -> None:
            if captured_payload:
                return  # already got one
            if not _is_search_response(response.url):
                return
            try:
                text = response.text()
                if text and len(text) > 100:  # sanity-check non-empty
                    captured_payload.append(text)
                    captured_url.append(response.url)
                    log.debug("Intercepted response from %s (%d bytes)", response.url, len(text))
            except Exception as exc:  # noqa: BLE001
                log.debug("Could not read response body from %s: %s", response.url, exc)

        page.on("response", _on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=settings.page_load_timeout_ms)
        except Exception as exc:  # noqa: BLE001
            log.warning("Navigation to %s raised: %s", url, exc)
        finally:
            page.remove_listener("response", _on_response)

        if captured_payload:
            return captured_payload[0], captured_url[0]
        return None, None

    def _next_page_url(
        self,
        page: Page,
        current_url: str,
        cursor: str | None,
        current_page: int,
    ) -> str | None:
        """Return the URL for the next page, or None if none exists.

        Strategy (in order):
        1. If a pagination cursor was found in the API response, inject it
           into the current URL.
        2. Otherwise, look for a "Next" button in the rendered page and
           extract its href (or click it).
        """
        # Strategy 1: cursor-based
        if cursor:
            parsed = urllib.parse.urlparse(current_url)
            params = dict(urllib.parse.parse_qsl(parsed.query))
            params["cursor"] = cursor
            params["section_offset"] = str(current_page)
            new_query = urllib.parse.urlencode(params)
            next_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
            log.debug("Cursor-based next page URL: %s", next_url)
            return next_url

        # Strategy 2: DOM next-button
        for selector in _NEXT_BTN_SELECTORS:
            try:
                element = page.query_selector(selector)
                if element is None:
                    continue
                # Prefer href (avoids triggering a click and double-navigation)
                href = element.get_attribute("href")
                if href:
                    if href.startswith("/"):
                        href = "https://www.airbnb.com" + href
                    log.debug("Next-button href found: %s", href)
                    return href
                # Fall back to clicking
                element.click()
                page.wait_for_load_state("networkidle", timeout=settings.page_load_timeout_ms)
                log.debug("Clicked next button; new URL: %s", page.url)
                return page.url
            except Exception as exc:  # noqa: BLE001
                log.debug("Selector %r failed: %s", selector, exc)
                continue

        log.info("No pagination cursor or next-button found; search complete.")
        return None
