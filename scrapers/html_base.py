"""Shared base for HTML search-results scrapers.

Vrbo, Expedia/Hotels.com, Google Hotels, and Hostelworld are all server-rendered
(or hydratable) HTML search pages. None needs site-specific parsing here — the
LLM extractor turns the raw page into listings — so each scraper only has to:

1. Build the search URL for a :class:`SearchQuery`.
2. Fetch it, escalating httpx → headless Playwright on a soft block.
3. Raise :class:`BlockedError` when both are blocked, so the pipeline's paid
   fallback can engage or the failure is surfaced cleanly.

A subclass supplies ``SOURCE`` and :meth:`build_url`; everything else is shared.
These sites are heavily bot-protected from a datacenter IP — expect frequent
blocks and rely on the Bright Data fallback for reliable coverage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from scrapers.base import BlockedError, RawScrape, ScrapeProvider, SearchQuery

log = logging.getLogger(__name__)

# Browser-like headers shared across the HTML scrapers.
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

BLOCKED_STATUS_CODES: frozenset[int] = frozenset({401, 403, 405, 429, 503})

# Lower-cased substrings that signal a CAPTCHA / bot-challenge page.
CAPTCHA_PATTERNS: tuple[str, ...] = (
    "px-captcha",
    "perimeterx",
    "datadome",            # Vrbo/Expedia commonly use DataDome
    "distil_identify_cookie",
    "cf-challenge",
    "cf_chl_",
    "awswaf",
    "reportchallengeerror",
    "recaptcha",           # Google
    "g-recaptcha",
    "captcha",
    "are you a human",
    "unusual traffic",     # Google "our systems have detected unusual traffic"
    "automated access",
    "bot detection",
    "access to this page has been denied",
)

PW_TIMEOUT_MS: int = 30_000
PW_SETTLE_MS: int = 2_000


class HtmlSearchScraper(ScrapeProvider):
    """Fetch one search-results page per query, with Playwright escalation."""

    SOURCE: str = "other"

    def __init__(
        self,
        session: Optional[Any] = None,
        *,
        run_id: Optional[int] = None,
    ) -> None:
        self._session = session
        self._run_id = run_id

    # ------------------------------------------------------------------ public
    def search(self, query: SearchQuery) -> list[RawScrape]:
        url = self.build_url(query)
        log.info("%s: fetching %s", self.SOURCE, url)
        raw = self._fetch_page(url)
        return [raw]

    def build_url(self, query: SearchQuery) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    # ------------------------------------------------------------------ fetch
    def _fetch_page(self, url: str) -> RawScrape:
        try:
            html = self._fetch_httpx(url)
            log.debug("%s: httpx ok for %s", self.SOURCE, url)
        except BlockedError as exc:
            log.warning("%s: httpx blocked (%s); escalating to Playwright.", self.SOURCE, exc.reason)
            html = self._fetch_playwright(url)
            log.debug("%s: Playwright ok for %s", self.SOURCE, url)

        return RawScrape(
            source=self.SOURCE,
            url=url,
            payload=html,
            fetched_at=datetime.now(timezone.utc),
            status="pending",
        )

    def _fetch_httpx(self, url: str) -> str:
        with httpx.Client(headers=DEFAULT_HEADERS, follow_redirects=True, timeout=20) as client:
            response = client.get(url)
        self._check_for_block(response.status_code, response.text, url=url)
        return response.text

    def _fetch_playwright(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "playwright is not installed; run: uv run playwright install chromium"
            ) from exc

        extra = {k: v for k, v in DEFAULT_HEADERS.items() if k != "User-Agent"}
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=DEFAULT_HEADERS["User-Agent"], extra_http_headers=extra
                )
                page = context.new_page()
                page.goto(url, timeout=PW_TIMEOUT_MS, wait_until="domcontentloaded")
                page.wait_for_timeout(PW_SETTLE_MS)
                html = page.content()
            finally:
                browser.close()
        self._check_for_block(200, html, url=url)
        return html

    def _check_for_block(self, status_code: int, body: str, *, url: str = "") -> None:
        if status_code in BLOCKED_STATUS_CODES:
            raise BlockedError(url=url, reason=f"HTTP {status_code}")
        low = body.lower()
        for pattern in CAPTCHA_PATTERNS:
            if pattern in low:
                raise BlockedError(url=url, reason=f"CAPTCHA pattern {pattern!r} in body")
