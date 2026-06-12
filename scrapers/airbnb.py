"""
Airbnb scraper: launches Playwright with stealth, intercepts StaysSearch /
ExploreTabs API responses, captures raw JSON, and persists RawScrape rows.

All fragile endpoint patterns and JSON key-paths live in this module so that
when Airbnb changes its API shape, only one file needs updating (PLAN.md risk
note for Stage 1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — all endpoint / key-path constants belong here
# ---------------------------------------------------------------------------

#: URL substrings that identify Airbnb listing-search API responses worth
#: capturing. Checked via substring match against the full response URL.
CAPTURE_PATTERNS: list[str] = [
    "StaysSearch",
    "ExploreV3",
    "ExploreTabs",
]

# JSON key-paths for the StaysSearch (v3 GraphQL) response shape.
_SS_PATH = ("data", "presentation", "staysSearch", "results", "searchResults")
_SS_PAGINATION_PATH = ("data", "presentation", "staysSearch", "results", "paginationInfo")

# JSON key-paths for the ExploreTabs / ExploreV3 legacy response shape.
_ET_ROOT_KEY = "explore_tabs"
_ET_SECTIONS_KEY = "sections"
_ET_LISTINGS_KEY = "listings"

# ---------------------------------------------------------------------------
# URL filtering helper
# ---------------------------------------------------------------------------


def is_capturable_url(url: str) -> bool:
    """Return ``True`` if *url* matches a known Airbnb listing-search endpoint."""
    return any(pattern in url for pattern in CAPTURE_PATTERNS)


# ---------------------------------------------------------------------------
# Payload parsing helpers
# ---------------------------------------------------------------------------


def _parse_stays_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract listing entries from a StaysSearch v3 GraphQL response.

    Expected shape::

        {
          "data": {
            "presentation": {
              "staysSearch": {
                "results": {
                  "searchResults": [
                    {
                      "listing": { "id": "...", "name": "...", ... },
                      "pricingQuote": { ... }
                    },
                    ...
                  ]
                }
              }
            }
          }
        }

    Returns a list of dicts, each with ``"listing"`` and ``"pricing_quote"``
    keys, or an empty list when the payload doesn't match this shape.
    """
    try:
        node: Any = payload
        for key in _SS_PATH:
            node = node[key]
        search_results: list[dict[str, Any]] = node
    except (KeyError, TypeError):
        return []

    out: list[dict[str, Any]] = []
    for result in search_results:
        listing = result.get("listing")
        if listing:
            out.append(
                {
                    "listing": listing,
                    "pricing_quote": result.get("pricingQuote"),
                }
            )
    return out


def _parse_explore_tabs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract listing entries from an ExploreTabs / ExploreV3 legacy response.

    Expected shape::

        {
          "explore_tabs": [
            {
              "sections": [
                {
                  "listings": [
                    {
                      "listing": { "id": "...", "name": "...", ... },
                      "pricing_quote": { ... }
                    },
                    ...
                  ]
                }
              ]
            }
          ]
        }

    Returns a list of dicts, each with ``"listing"`` and ``"pricing_quote"``
    keys, or an empty list when the payload doesn't match this shape.
    """
    out: list[dict[str, Any]] = []
    for tab in payload.get(_ET_ROOT_KEY, []):
        for section in tab.get(_ET_SECTIONS_KEY, []):
            for item in section.get(_ET_LISTINGS_KEY, []):
                listing = item.get("listing")
                if listing:
                    out.append(
                        {
                            "listing": listing,
                            "pricing_quote": item.get("pricing_quote"),
                        }
                    )
    return out


def parse_airbnb_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Route a captured Airbnb API payload to the correct parser.

    Tries StaysSearch (v3 GraphQL) format first; falls back to
    ExploreTabs / ExploreV3 format.

    Each returned item is a ``dict`` with:

    - ``"listing"`` — the raw listing object from the API (always present).
    - ``"pricing_quote"`` — the raw pricing object (may be ``None``).

    Returns an empty list when neither shape matches.
    """
    results = _parse_stays_search(payload)
    if results:
        logger.debug("Parsed %d listings from StaysSearch payload", len(results))
        return results

    results = _parse_explore_tabs(payload)
    logger.debug("Parsed %d listings from ExploreTabs payload", len(results))
    return results


def get_pagination_info(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the ``paginationInfo`` block from a StaysSearch payload, if present.

    Returns an empty dict for payloads that don't carry pagination metadata
    (e.g. ExploreTabs responses or malformed payloads).
    """
    try:
        node: Any = payload
        for key in _SS_PAGINATION_PATH:
            node = node[key]
        return dict(node)
    except (KeyError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Async scraper (uses Playwright at runtime; bypassed in tests)
# ---------------------------------------------------------------------------


async def _random_delay(lo: float = 1.5, hi: float = 4.0) -> None:
    """Polite randomised delay to reduce scraping fingerprint."""
    await asyncio.sleep(random.uniform(lo, hi))


async def scrape_airbnb(
    query: str,
    *,
    checkin: str | None = None,
    checkout: str | None = None,
    guests: int = 1,
    page_limit: int = 5,
) -> list[dict[str, Any]]:
    """Drive an Airbnb search and return all captured raw listing entries.

    Launches a Playwright browser (Chromium), intercepts matching API
    responses via :func:`is_capturable_url`, and routes each through
    :func:`parse_airbnb_payload`.

    This function requires ``playwright`` to be installed and a Chromium
    browser to be available (``playwright install chromium``).  In tests,
    inject fixtures via the parsing helpers directly — do not call this.

    Args:
        query: Free-text location query, e.g. ``"Lisbon, Portugal"``.
        checkin: ISO 8601 date string, e.g. ``"2024-08-01"``.
        checkout: ISO 8601 date string.
        guests: Number of guests.
        page_limit: Maximum number of result pages to scrape.

    Returns:
        Flat list of raw listing+pricing dicts from :func:`parse_airbnb_payload`.
    """
    # Import Playwright lazily so unit tests don't require it to be installed.
    from playwright.async_api import async_playwright  # noqa: PLC0415

    params = f"?query={query.replace(' ', '+')}"
    if checkin:
        params += f"&checkin={checkin}"
    if checkout:
        params += f"&checkout={checkout}"
    params += f"&adults={guests}"
    start_url = f"https://www.airbnb.com/s/{query.replace(' ', '-')}/homes{params}"

    captured: list[dict[str, Any]] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        async def handle_response(response: Any) -> None:
            if is_capturable_url(response.url):
                try:
                    body = await response.json()
                    entries = parse_airbnb_payload(body)
                    if entries:
                        logger.info(
                            "Captured %d listings from %s",
                            len(entries),
                            response.url,
                        )
                        captured.extend(entries)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to parse response from %s: %s", response.url, exc)

        page.on("response", handle_response)

        pages_fetched = 0
        await page.goto(start_url, wait_until="networkidle")
        pages_fetched += 1
        await _random_delay()

        # Scroll / click "next page" up to page_limit times.
        while pages_fetched < page_limit:
            next_btn = page.locator('[aria-label="Next"]').first
            if not await next_btn.is_visible():
                break
            await next_btn.click()
            await page.wait_for_load_state("networkidle")
            pages_fetched += 1
            await _random_delay()

        await browser.close()

    return captured
