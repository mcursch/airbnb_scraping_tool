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
        assert inspect.isasyncgenfunction(scraper.search)


# ---------------------------------------------------------------------------
# Live integration test (network required — skipped unless AIRBNB_LIVE_TEST=1)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AIRBNB_LIVE_TEST") != "1",
    reason="Live network test — set AIRBNB_LIVE_TEST=1 to enable",
)
@pytest.mark.asyncio
async def test_live_search_returns_payloads() -> None:
    """
    Smoke-test: AirbnbScraper.search() against a real query must yield at
    least one non-empty dict payload without raising.

    Run with: AIRBNB_LIVE_TEST=1 pytest tests/test_airbnb.py::test_live_search_returns_payloads -v
    """
    scraper = AirbnbScraper(headless=True)
    query = SearchQuery(area="Lisbon, Portugal", guests=2)

    payloads: list[tuple[str, dict]] = []
    async for url, payload in scraper.search(query):
        payloads.append((url, payload))

    assert len(payloads) >= 1, "Expected at least one intercepted API payload"

    for url, payload in payloads:
        assert isinstance(url, str) and url.startswith("https://")
        assert isinstance(payload, dict) and len(payload) > 0
        # Payloads must be dicts (parsed JSON), never raw HTML strings.
        assert not isinstance(payload, str)
