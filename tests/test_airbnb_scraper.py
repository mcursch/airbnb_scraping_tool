"""
Offline unit tests for scrapers/airbnb.py.

All tests load pre-recorded fixture files from tests/fixtures/airbnb/ and
exercise the payload-capture and parsing logic without any network access or
Playwright involvement.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scrapers.airbnb import (
    CAPTURE_PATTERNS,
    get_pagination_info,
    is_capturable_url,
    parse_airbnb_payload,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "airbnb"


def load_fixture(filename: str) -> dict[str, Any]:
    """Load a JSON fixture file from tests/fixtures/airbnb/."""
    path = FIXTURES_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures (pytest)
# ---------------------------------------------------------------------------


@pytest.fixture()
def stays_search_lisbon() -> dict[str, Any]:
    return load_fixture("stays_search_lisbon.json")


@pytest.fixture()
def explore_tabs_barcelona() -> dict[str, Any]:
    return load_fixture("explore_tabs_barcelona.json")


@pytest.fixture()
def stays_search_tokyo() -> dict[str, Any]:
    return load_fixture("stays_search_tokyo.json")


# ---------------------------------------------------------------------------
# Tests: fixture files exist and are valid JSON
# ---------------------------------------------------------------------------


class TestFixtureFiles:
    """Sanity checks that fixture files are present and parseable."""

    @pytest.mark.parametrize(
        "filename",
        [
            "stays_search_lisbon.json",
            "explore_tabs_barcelona.json",
            "stays_search_tokyo.json",
        ],
    )
    def test_fixture_file_exists(self, filename: str) -> None:
        assert (FIXTURES_DIR / filename).is_file(), (
            f"Fixture file {filename!r} is missing from {FIXTURES_DIR}"
        )

    @pytest.mark.parametrize(
        "filename",
        [
            "stays_search_lisbon.json",
            "explore_tabs_barcelona.json",
            "stays_search_tokyo.json",
        ],
    )
    def test_fixture_is_valid_json(self, filename: str) -> None:
        data = load_fixture(filename)
        assert isinstance(data, dict), "Top-level fixture value must be a JSON object"

    def test_at_least_two_fixture_files(self) -> None:
        fixture_files = list(FIXTURES_DIR.glob("*.json"))
        assert len(fixture_files) >= 2, (
            f"Expected ≥2 fixture files, found {len(fixture_files)}"
        )


# ---------------------------------------------------------------------------
# Tests: is_capturable_url
# ---------------------------------------------------------------------------


class TestIsCapturable:
    """Tests for the URL-filtering predicate."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.airbnb.com/api/v3/StaysSearch?operationName=StaysSearch",
            "https://www.airbnb.com/api/v3/ExploreV3?currency=USD",
            "https://www.airbnb.com/api/v2/explore_tabs?ExploreTabs=1",
        ],
    )
    def test_matching_urls_are_capturable(self, url: str) -> None:
        assert is_capturable_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.airbnb.com/api/v3/PdpPlatformSections",
            "https://www.airbnb.com/s/Lisbon--Portugal/homes",
            "https://a0.muscache.com/im/pictures/photo.jpg",
            "https://www.google.com/",
        ],
    )
    def test_non_matching_urls_are_not_capturable(self, url: str) -> None:
        assert is_capturable_url(url) is False

    def test_capture_patterns_non_empty(self) -> None:
        assert len(CAPTURE_PATTERNS) > 0


# ---------------------------------------------------------------------------
# Tests: parse_airbnb_payload — StaysSearch format (Lisbon fixture)
# ---------------------------------------------------------------------------


class TestParseStaysSearch:
    """parse_airbnb_payload against the StaysSearch fixture format."""

    def test_returns_non_empty_list(self, stays_search_lisbon: dict[str, Any]) -> None:
        results = parse_airbnb_payload(stays_search_lisbon)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_each_result_has_listing_key(
        self, stays_search_lisbon: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(stays_search_lisbon)
        for item in results:
            assert "listing" in item, f"Missing 'listing' key in result: {item!r}"

    def test_each_listing_has_id_and_name(
        self, stays_search_lisbon: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(stays_search_lisbon)
        for item in results:
            listing = item["listing"]
            assert "id" in listing, f"Listing missing 'id': {listing!r}"
            assert "name" in listing, f"Listing missing 'name': {listing!r}"
            assert listing["id"], "Listing 'id' must not be empty"
            assert listing["name"], "Listing 'name' must not be empty"

    def test_pricing_quote_present(self, stays_search_lisbon: dict[str, Any]) -> None:
        results = parse_airbnb_payload(stays_search_lisbon)
        # At least one result should carry pricing data.
        assert any(
            item.get("pricing_quote") is not None for item in results
        ), "Expected at least one result with a pricing_quote"

    def test_expected_listing_ids(self, stays_search_lisbon: dict[str, Any]) -> None:
        results = parse_airbnb_payload(stays_search_lisbon)
        ids = {item["listing"]["id"] for item in results}
        assert ids == {"48921034", "52304871", "61840293"}

    def test_all_listings_have_location(
        self, stays_search_lisbon: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(stays_search_lisbon)
        for item in results:
            listing = item["listing"]
            assert "lat" in listing and "lng" in listing, (
                f"Listing {listing.get('id')!r} is missing lat/lng"
            )

    def test_tokyo_fixture_returns_correct_count(
        self, stays_search_tokyo: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(stays_search_tokyo)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Tests: parse_airbnb_payload — ExploreTabs format (Barcelona fixture)
# ---------------------------------------------------------------------------


class TestParseExploreTabs:
    """parse_airbnb_payload against the ExploreTabs fixture format."""

    def test_returns_non_empty_list(
        self, explore_tabs_barcelona: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(explore_tabs_barcelona)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_each_result_has_listing_key(
        self, explore_tabs_barcelona: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(explore_tabs_barcelona)
        for item in results:
            assert "listing" in item, f"Missing 'listing' key in result: {item!r}"

    def test_each_listing_has_id_and_name(
        self, explore_tabs_barcelona: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(explore_tabs_barcelona)
        for item in results:
            listing = item["listing"]
            assert "id" in listing, f"Listing missing 'id': {listing!r}"
            assert "name" in listing, f"Listing missing 'name': {listing!r}"

    def test_expected_listing_ids(
        self, explore_tabs_barcelona: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(explore_tabs_barcelona)
        ids = {item["listing"]["id"] for item in results}
        assert ids == {"34567890", "29104583", "73920156"}

    def test_pricing_quote_present(
        self, explore_tabs_barcelona: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(explore_tabs_barcelona)
        assert any(item.get("pricing_quote") is not None for item in results)

    def test_pricing_rate_currency(
        self, explore_tabs_barcelona: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(explore_tabs_barcelona)
        for item in results:
            pq = item.get("pricing_quote")
            if pq and pq.get("rate"):
                assert pq["rate"]["currency"] == "USD"

    def test_room_type_category_present(
        self, explore_tabs_barcelona: dict[str, Any]
    ) -> None:
        results = parse_airbnb_payload(explore_tabs_barcelona)
        for item in results:
            listing = item["listing"]
            assert "room_type_category" in listing, (
                f"Listing {listing.get('id')!r} missing 'room_type_category'"
            )


# ---------------------------------------------------------------------------
# Tests: get_pagination_info
# ---------------------------------------------------------------------------


class TestGetPaginationInfo:
    """Tests for pagination metadata extraction."""

    def test_returns_dict_for_stays_search(
        self, stays_search_lisbon: dict[str, Any]
    ) -> None:
        info = get_pagination_info(stays_search_lisbon)
        assert isinstance(info, dict)

    def test_pagination_has_next_cursor(
        self, stays_search_lisbon: dict[str, Any]
    ) -> None:
        info = get_pagination_info(stays_search_lisbon)
        assert "nextCursor" in info
        assert info["nextCursor"]

    def test_pagination_has_next_page_flag(
        self, stays_search_lisbon: dict[str, Any]
    ) -> None:
        info = get_pagination_info(stays_search_lisbon)
        assert "hasNextPage" in info
        assert info["hasNextPage"] is True

    def test_returns_empty_dict_for_explore_tabs(
        self, explore_tabs_barcelona: dict[str, Any]
    ) -> None:
        """ExploreTabs payloads don't use the StaysSearch pagination shape."""
        info = get_pagination_info(explore_tabs_barcelona)
        assert info == {}

    def test_returns_empty_dict_for_empty_payload(self) -> None:
        info = get_pagination_info({})
        assert info == {}

    def test_returns_empty_dict_for_malformed_payload(self) -> None:
        info = get_pagination_info({"data": {"presentation": None}})
        assert info == {}


# ---------------------------------------------------------------------------
# Tests: edge cases and malformed payloads
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Robustness tests against unusual or malformed inputs."""

    def test_empty_dict_returns_empty_list(self) -> None:
        assert parse_airbnb_payload({}) == []

    def test_unknown_shape_returns_empty_list(self) -> None:
        assert parse_airbnb_payload({"foo": "bar", "baz": [1, 2, 3]}) == []

    def test_stays_search_with_no_search_results(self) -> None:
        payload = {
            "data": {
                "presentation": {
                    "staysSearch": {
                        "results": {
                            "searchResults": [],
                            "paginationInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        }
        assert parse_airbnb_payload(payload) == []

    def test_explore_tabs_with_empty_sections(self) -> None:
        payload = {
            "explore_tabs": [
                {"tab_name": "homes", "sections": []}
            ]
        }
        assert parse_airbnb_payload(payload) == []

    def test_explore_tabs_listing_without_listing_key_is_skipped(self) -> None:
        payload = {
            "explore_tabs": [
                {
                    "tab_name": "homes",
                    "sections": [
                        {
                            "listings": [
                                {"pricing_quote": {"rate": {"amount": 50.0}}},
                                {
                                    "listing": {"id": "999", "name": "Valid"},
                                    "pricing_quote": None,
                                },
                            ]
                        }
                    ],
                }
            ]
        }
        results = parse_airbnb_payload(payload)
        assert len(results) == 1
        assert results[0]["listing"]["id"] == "999"

    def test_stays_search_listing_without_listing_key_is_skipped(self) -> None:
        payload = {
            "data": {
                "presentation": {
                    "staysSearch": {
                        "results": {
                            "searchResults": [
                                {"pricingQuote": {"rate": {"amount": 50.0}}},
                                {
                                    "listing": {"id": "888", "name": "Good"},
                                    "pricingQuote": None,
                                },
                            ]
                        }
                    }
                }
            }
        }
        results = parse_airbnb_payload(payload)
        assert len(results) == 1
        assert results[0]["listing"]["id"] == "888"

    def test_deeply_nested_none_does_not_crash(self) -> None:
        payload = {"data": {"presentation": {"staysSearch": None}}}
        assert parse_airbnb_payload(payload) == []
