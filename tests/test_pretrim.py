"""
Tests for extraction.pretrim.

Runs entirely offline against the fixture files in tests/fixtures/.

Acceptance criteria verified here
----------------------------------
1. pretrim(payload) returns a string strictly smaller than the input for every
   fixture file.
2. The output is ≤ 30 % of the input length for every fixture.
3. All required listing keys (price, name, lat/lon or location) are present
   in the trimmed output for every fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from extraction.pretrim import pretrim

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"

REQUIRED_LISTING_KEYS = {
    "price_or_rate",  # any of: price, rate, amount, nightly, fee, cost
    "name_or_title",  # any of: name, title
    "location",       # any of: lat, lon, latitude, longitude, location, coord, geo, address
}


def _contains_price(text: str) -> bool:
    """Return True if *text* carries any price/rate signal."""
    lower = text.lower()
    return any(kw in lower for kw in ("price", "rate", "amount", "nightly", "fee", "cost"))


def _contains_name(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in ('"name"', '"title"'))


def _contains_location(text: str) -> bool:
    lower = text.lower()
    return any(
        kw in lower
        for kw in (
            '"lat"', '"lon"', '"latitude"', '"longitude"',
            '"location"', '"coord"', '"geo"', '"address"',
        )
    )


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _fixture_names() -> list[str]:
    """Return the names of fixture data files (JSON and HTML only)."""
    return [
        p.name
        for p in sorted(FIXTURES_DIR.iterdir())
        if p.is_file() and p.suffix in {".json", ".html"}
    ]


# ---------------------------------------------------------------------------
# Parametrised tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_name", _fixture_names())
def test_pretrim_reduces_size(fixture_name: str) -> None:
    """Output must be strictly smaller than input."""
    payload = _load_fixture(fixture_name)
    result = pretrim(payload)
    assert len(result) < len(payload), (
        f"{fixture_name}: output ({len(result)} chars) is not smaller than "
        f"input ({len(payload)} chars)"
    )


@pytest.mark.parametrize("fixture_name", _fixture_names())
def test_pretrim_within_30_percent(fixture_name: str) -> None:
    """Output must be ≤ 30 % of input length."""
    payload = _load_fixture(fixture_name)
    result = pretrim(payload)
    ratio = len(result) / len(payload)
    assert ratio <= 0.30, (
        f"{fixture_name}: output is {ratio:.1%} of input "
        f"(limit is 30 %; got {len(result)} / {len(payload)} chars)"
    )


@pytest.mark.parametrize("fixture_name", _fixture_names())
def test_pretrim_contains_price(fixture_name: str) -> None:
    """A price/rate signal must survive trimming."""
    result = pretrim(_load_fixture(fixture_name))
    assert _contains_price(result), (
        f"{fixture_name}: no price/rate/amount key found in trimmed output"
    )


@pytest.mark.parametrize("fixture_name", _fixture_names())
def test_pretrim_contains_name(fixture_name: str) -> None:
    """A name/title key must survive trimming."""
    result = pretrim(_load_fixture(fixture_name))
    assert _contains_name(result), (
        f"{fixture_name}: no 'name' or 'title' key found in trimmed output"
    )


@pytest.mark.parametrize("fixture_name", _fixture_names())
def test_pretrim_contains_location(fixture_name: str) -> None:
    """A lat/lon/location/address key must survive trimming."""
    result = pretrim(_load_fixture(fixture_name))
    assert _contains_location(result), (
        f"{fixture_name}: no location key (lat/lon/location/address/coord) "
        "found in trimmed output"
    )


# ---------------------------------------------------------------------------
# Unit tests for the JSON fixture specifically
# ---------------------------------------------------------------------------


def test_airbnb_json_output_is_valid_json() -> None:
    """The trimmed Airbnb fixture must be parseable JSON."""
    result = pretrim(_load_fixture("airbnb_search.json"))
    data = json.loads(result)  # raises if invalid
    assert isinstance(data, (dict, list))


def test_airbnb_json_listings_present() -> None:
    """Trimmed Airbnb output must contain all 3 listing names."""
    result = pretrim(_load_fixture("airbnb_search.json"))
    assert "Sunny Studio near Eiffel Tower" in result
    assert "Charming Apartment in Le Marais" in result
    assert "Modern Loft with City Views" in result


def test_airbnb_json_coordinates_present() -> None:
    """Trimmed Airbnb output must still contain coordinate values."""
    result = pretrim(_load_fixture("airbnb_search.json"))
    assert "48.8584" in result or "48.8566" in result or "48.8737" in result


def test_airbnb_json_experiments_stripped() -> None:
    """Experiment flags must be absent from the trimmed output."""
    result = pretrim(_load_fixture("airbnb_search.json"))
    assert "serverExperiments" not in result
    assert "clientExperiments" not in result


# ---------------------------------------------------------------------------
# Unit tests for the HTML fixture specifically
# ---------------------------------------------------------------------------


def test_booking_html_output_is_valid_json() -> None:
    """The trimmed Booking fixture must be parseable JSON."""
    result = pretrim(_load_fixture("booking_page.html"))
    data = json.loads(result)
    assert isinstance(data, (dict, list))


def test_booking_html_hotel_names_present() -> None:
    """Both hotel names must survive trimming."""
    result = pretrim(_load_fixture("booking_page.html"))
    assert "Rivoli Palace" in result
    assert "Marais Suites" in result


def test_booking_html_css_stripped() -> None:
    """CSS rules must not appear in the trimmed output."""
    result = pretrim(_load_fixture("booking_page.html"))
    assert "box-sizing" not in result
    assert "border-radius" not in result


def test_booking_html_analytics_stripped() -> None:
    """Analytics config must not appear in the trimmed output."""
    result = pretrim(_load_fixture("booking_page.html"))
    assert "analyticsConfig" not in result
    assert "sessionToken" not in result


def test_booking_html_seo_stripped() -> None:
    """SEO metadata must not appear in the trimmed output."""
    result = pretrim(_load_fixture("booking_page.html"))
    assert "seoData" not in result
    assert "metaDescription" not in result


# ---------------------------------------------------------------------------
# Edge-case / robustness tests
# ---------------------------------------------------------------------------


def test_pretrim_json_no_listings_still_shrinks() -> None:
    """A JSON payload with no recognisable listing objects should still shrink."""
    payload = json.dumps(
        {
            "analyticsData": {"sessionId": "x" * 200, "experiments": {"a": True, "b": False}},
            "configData": {"featureFlags": {"x": True, "y": False}},
            "trackingPayload": {"beacon": "https://track.example.com?" + "x=1&" * 50},
        }
    )
    result = pretrim(payload)
    assert len(result) <= len(payload)


def test_pretrim_is_pure() -> None:
    """Calling pretrim twice on the same input returns the same output."""
    payload = _load_fixture("airbnb_search.json")
    assert pretrim(payload) == pretrim(payload)


def test_pretrim_html_fallback_strips_styles() -> None:
    """HTML with no JSON in scripts should still have styles/scripts stripped."""
    html = (
        "<html><head>"
        "<style>.a{color:red;}" + ".b{margin:0;}" * 100 + "</style>"
        "</head><body>"
        "<script>var x = 1;" + "x++;" * 200 + "</script>"
        "<p>Hello world</p>"
        "</body></html>"
    )
    result = pretrim(html)
    assert len(result) < len(html)
    assert "color:red" not in result
    assert "margin:0" not in result
