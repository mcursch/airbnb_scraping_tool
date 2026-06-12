"""Offline unit tests for the Airbnb scraper module.

These tests never touch the network: they verify URL construction, endpoint
pattern matching, JSON parsing, and the ``SearchQuery`` schema.  Network-level
integration tests (requiring a live browser) are kept separate and skipped in
CI unless the ``AIRBNB_LIVE_TEST`` environment variable is set.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from scrapers.airbnb import (
    AirbnbScraper,
    _parse_json_response,
    build_search_url,
    matches_airbnb_endpoint,
)
from scrapers.constants import AIRBNB_BASE_URL, AIRBNB_ENDPOINT_PATTERNS
from schemas.search_query import SearchQuery

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# SearchQuery schema
# ---------------------------------------------------------------------------


class TestSearchQuery:
    def test_minimal_construction(self) -> None:
        q = SearchQuery(area="Lisbon")
        assert q.area == "Lisbon"
        assert q.guests == 1
        assert q.checkin is None
        assert q.checkout is None

    def test_full_construction(self) -> None:
        q = SearchQuery(
            area="Paris, France",
            checkin=date(2025, 7, 1),
            checkout=date(2025, 7, 8),
            guests=2,
        )
        assert q.guests == 2
        assert q.checkin < q.checkout  # type: ignore[operator]

    def test_invalid_dates_raise(self) -> None:
        with pytest.raises(Exception, match="checkout must be"):
            SearchQuery(
                area="Rome",
                checkin=date(2025, 7, 8),
                checkout=date(2025, 7, 1),
            )

    def test_same_day_dates_raise(self) -> None:
        with pytest.raises(Exception):
            SearchQuery(
                area="Rome",
                checkin=date(2025, 7, 1),
                checkout=date(2025, 7, 1),
            )

    def test_guests_must_be_positive(self) -> None:
        with pytest.raises(Exception):
            SearchQuery(area="Berlin", guests=0)


# ---------------------------------------------------------------------------
# matches_airbnb_endpoint — pattern matching
# ---------------------------------------------------------------------------


class TestMatchesAirbnbEndpoint:
    """Verifies that the pattern predicate uses constants, not hard-coded values."""

    # Every pattern in the constants tuple must produce a match.
    @pytest.mark.parametrize("pattern", list(AIRBNB_ENDPOINT_PATTERNS))
    def test_each_constant_matches(self, pattern: str) -> None:
        url = f"https://www.airbnb.com/api/v3/{pattern}?operationName=foo&locale=en"
        assert matches_airbnb_endpoint(url) is True

    def test_stays_search_url(self) -> None:
        url = "https://www.airbnb.com/api/v3/StaysSearch?operationName=StaysSearch"
        assert matches_airbnb_endpoint(url) is True

    def test_explore_tabs_url(self) -> None:
        url = "https://www.airbnb.com/api/v2/explore_tabs?version=1.8.3&section_offset=0"
        assert matches_airbnb_endpoint(url) is True

    def test_homepage_does_not_match(self) -> None:
        assert matches_airbnb_endpoint("https://www.airbnb.com/") is False

    def test_search_page_does_not_match(self) -> None:
        assert matches_airbnb_endpoint("https://www.airbnb.com/s/Lisbon/homes") is False

    def test_cdn_image_does_not_match(self) -> None:
        url = "https://a0.muscache.com/pictures/12345/photo.jpg"
        assert matches_airbnb_endpoint(url) is False

    def test_font_resource_does_not_match(self) -> None:
        assert matches_airbnb_endpoint("https://fonts.googleapis.com/css2?family=Cereal") is False

    def test_js_bundle_does_not_match(self) -> None:
        assert matches_airbnb_endpoint("https://www.airbnb.com/static/js/main.chunk.js") is False


# ---------------------------------------------------------------------------
# build_search_url — URL construction
# ---------------------------------------------------------------------------


class TestBuildSearchUrl:
    def test_minimal_query(self) -> None:
        q = SearchQuery(area="Lisbon")
        url = build_search_url(q)
        assert url.startswith(AIRBNB_BASE_URL)
        assert "Lisbon" in url
        assert "homes" in url
        # No date params when not provided
        assert "checkin" not in url
        assert "checkout" not in url

    def test_url_contains_dates_when_provided(self) -> None:
        q = SearchQuery(
            area="Paris",
            checkin=date(2025, 8, 1),
            checkout=date(2025, 8, 7),
        )
        url = build_search_url(q)
        assert "checkin=2025-08-01" in url
        assert "checkout=2025-08-07" in url

    def test_guests_param_included_when_more_than_one(self) -> None:
        q = SearchQuery(area="London", guests=3)
        url = build_search_url(q)
        assert "adults=3" in url

    def test_guests_param_omitted_for_default(self) -> None:
        q = SearchQuery(area="London", guests=1)
        url = build_search_url(q)
        assert "adults" not in url

    def test_area_with_spaces_is_encoded(self) -> None:
        q = SearchQuery(area="New York")
        url = build_search_url(q)
        # URL-encoded space should appear; raw space should not
        assert " " not in url
        assert "New%20York" in url or "New+York" in url

    def test_area_with_comma_is_encoded(self) -> None:
        q = SearchQuery(area="Lisbon, Portugal")
        url = build_search_url(q)
        assert " " not in url

    def test_returned_url_starts_with_airbnb_base(self) -> None:
        q = SearchQuery(area="Tokyo")
        assert build_search_url(q).startswith(AIRBNB_BASE_URL)


# ---------------------------------------------------------------------------
# _parse_json_response — body parsing helper
# ---------------------------------------------------------------------------


class TestParseJsonResponse:
    """Tests for the async JSON parsing helper using a mock Response object."""

    @pytest.fixture
    def mock_response(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_valid_json_dict_returned(self, mock_response: MagicMock) -> None:
        payload = {"data": {"listings": []}, "metadata": {"total": 0}}
        mock_response.json = AsyncMock(return_value=payload)
        result = await _parse_json_response("https://example.com/api", mock_response)
        assert result == payload

    @pytest.mark.asyncio
    async def test_empty_dict_returns_none(self, mock_response: MagicMock) -> None:
        mock_response.json = AsyncMock(return_value={})
        result = await _parse_json_response("https://example.com/api", mock_response)
        assert result is None

    @pytest.mark.asyncio
    async def test_json_list_returns_none(self, mock_response: MagicMock) -> None:
        # Only dict payloads are valid; lists are ignored.
        mock_response.json = AsyncMock(return_value=[1, 2, 3])
        result = await _parse_json_response("https://example.com/api", mock_response)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_json_body_returns_none(self, mock_response: MagicMock) -> None:
        mock_response.json = AsyncMock(side_effect=Exception("not JSON"))
        result = await _parse_json_response("https://example.com/api", mock_response)
        assert result is None

    @pytest.mark.asyncio
    async def test_fixture_payload_is_valid(self, mock_response: MagicMock) -> None:
        """Round-trip a real-shaped fixture through the helper."""
        fixture_path = FIXTURES_DIR / "stays_search_response.json"
        raw = json.loads(fixture_path.read_text())
        mock_response.json = AsyncMock(return_value=raw)
        result = await _parse_json_response(
            "https://www.airbnb.com/api/v3/StaysSearch", mock_response
        )
        assert isinstance(result, dict)
        assert "data" in result


# ---------------------------------------------------------------------------
# AirbnbScraper — instantiation and interface
# ---------------------------------------------------------------------------


class TestAirbnbScraperInstantiation:
    def test_default_construction(self) -> None:
        scraper = AirbnbScraper()
        assert scraper.headless is True

    def test_custom_headless_flag(self) -> None:
        scraper = AirbnbScraper(headless=False)
        assert scraper.headless is False

    def test_is_scrape_provider_subclass(self) -> None:
        from scrapers.base import ScrapeProvider

        assert issubclass(AirbnbScraper, ScrapeProvider)

    def test_search_method_exists(self) -> None:
        import inspect

        scraper = AirbnbScraper()
        assert callable(scraper.search)
        # search() must be a plain synchronous method, not an async generator,
        # so that it conforms to the ScrapeProvider interface (LIN-157).
        assert not inspect.isasyncgenfunction(scraper.search)
        assert not inspect.iscoroutinefunction(scraper.search)

    def test_search_returns_list_not_coroutine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LIN-157: search() must return a list, never a coroutine/async-generator.

        Monkeypatch _run_browser so we never touch a real browser; just verify
        the synchronous wrapper converts the result to a list correctly.
        """
        import asyncio
        from scrapers.base import RawScrape

        scraper = AirbnbScraper()

        async def _fake_run_browser(query: object) -> list:
            return []

        monkeypatch.setattr(scraper, "_run_browser", _fake_run_browser)

        query = SearchQuery(area="Test City")
        result = scraper.search(query)

        # Must be a plain list, not a coroutine or async generator.
        assert isinstance(result, list)
        assert not asyncio.iscoroutine(result)

    def test_search_result_items_are_raw_scrapes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LIN-157: each item in the list returned by search() is a RawScrape."""
        from scrapers.base import RawScrape

        scraper = AirbnbScraper()

        async def _fake_run_browser(query: object) -> list:
            return [
                ("https://www.airbnb.com/api/v3/StaysSearch?q=1", {"data": {"listings": []}}),
            ]

        monkeypatch.setattr(scraper, "_run_browser", _fake_run_browser)

        query = SearchQuery(area="Lisbon, Portugal")
        results = scraper.search(query)

        assert len(results) == 1
        record = results[0]
        assert isinstance(record, RawScrape)
        assert record.source == "airbnb"
        assert record.url == "https://www.airbnb.com/api/v3/StaysSearch?q=1"
        assert record.payload == '{"data": {"listings": []}}'
        assert record.content_hash  # non-empty hash


# ---------------------------------------------------------------------------
# Regression: LIN-145 — scrapers.airbnb must import without error, and
# scrapers.base must export RawPayload
# ---------------------------------------------------------------------------


class TestImports:
    """Guard against import regressions in scrapers.airbnb and scrapers.base."""

    def test_scrapers_airbnb_importable(self) -> None:
        """scrapers.airbnb must be importable without raising ImportError."""
        import importlib

        mod = importlib.import_module("scrapers.airbnb")
        assert mod is not None

    def test_scrapers_base_exports_raw_payload(self) -> None:
        """scrapers.base must export RawPayload (re-export alias added in LIN-144)."""
        from scrapers.base import RawPayload  # noqa: F401

        assert RawPayload is not None

    def test_airbnb_raw_payload_is_dict(self) -> None:
        """scrapers.airbnb.RawPayload must be dict (JSON objects from Airbnb API)."""
        from scrapers import airbnb as _mod

        assert _mod.RawPayload is dict


# ---------------------------------------------------------------------------
# Live integration test (network required — skipped unless AIRBNB_LIVE_TEST=1)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AIRBNB_LIVE_TEST") != "1",
    reason="Live network test — set AIRBNB_LIVE_TEST=1 to enable",
)
def test_live_search_returns_payloads() -> None:
    """
    Smoke-test: AirbnbScraper.search() against a real query must return at
    least one non-empty RawScrape payload without raising.

    search() is now synchronous (LIN-157), so no ``async for`` is needed.

    Run with: AIRBNB_LIVE_TEST=1 pytest tests/test_airbnb.py::test_live_search_returns_payloads -v
    """
    from scrapers.base import RawScrape

    scraper = AirbnbScraper(headless=True)
    query = SearchQuery(area="Lisbon, Portugal", guests=2)

    results = scraper.search(query)

    assert isinstance(results, list), "search() must return a list"
    assert len(results) >= 1, "Expected at least one intercepted API payload"

    for record in results:
        assert isinstance(record, RawScrape)
        assert isinstance(record.url, str) and record.url.startswith("https://")
        assert isinstance(record.payload, str) and len(record.payload) > 0
        assert record.source == "airbnb"
