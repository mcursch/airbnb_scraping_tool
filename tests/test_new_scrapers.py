"""Tests for the new HTML source scrapers + the source registry (offline)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scrapers.base import BlockedError, SearchQuery
from scrapers.expedia import ExpediaScraper
from scrapers.google_hotels import GoogleHotelsScraper
from scrapers.hostelworld import HostelworldScraper
from scrapers.vrbo import VrboScraper
from scrapers import html_base
from scrapers import registry


def _q(**kw) -> SearchQuery:
    base = dict(area="Lisbon, Portugal", sources=["vrbo"])
    base.update(kw)
    return SearchQuery(**base)


# ---------------------------------------------------------------------------
# URL building
# ---------------------------------------------------------------------------


class TestUrlBuilding:
    def test_vrbo_url(self):
        url = VrboScraper().build_url(_q())
        assert url.startswith("https://www.vrbo.com/search?")
        assert "destination=Lisbon" in url

    def test_expedia_url(self):
        url = ExpediaScraper().build_url(_q())
        assert "expedia.com/Hotel-Search" in url
        assert "destination=Lisbon" in url

    def test_google_hotels_url(self):
        url = GoogleHotelsScraper().build_url(_q())
        assert "google.com/travel/search" in url
        assert "hotels+in+Lisbon" in url

    def test_hostelworld_url(self):
        url = HostelworldScraper().build_url(_q())
        assert "hostelworld.com/search" in url
        assert "search_keywords=Lisbon" in url

    def test_dates_and_guests_threaded(self):
        from datetime import date

        url = VrboScraper().build_url(
            _q(checkin=date(2026, 8, 1), checkout=date(2026, 8, 7), guests=3)
        )
        assert "startDate=2026-08-01" in url
        assert "endDate=2026-08-07" in url
        assert "adults=3" in url


# ---------------------------------------------------------------------------
# Block detection
# ---------------------------------------------------------------------------


class TestBlockDetection:
    def test_blocked_status_code(self):
        s = VrboScraper()
        with pytest.raises(BlockedError):
            s._check_for_block(403, "<html>fine</html>", url="u")

    def test_captcha_body(self):
        s = VrboScraper()
        with pytest.raises(BlockedError):
            s._check_for_block(200, "<html>Please complete the reCAPTCHA</html>", url="u")

    def test_clean_passes(self):
        s = VrboScraper()
        s._check_for_block(200, "<html><body>listings</body></html>", url="u")  # no raise


# ---------------------------------------------------------------------------
# Fetch flow
# ---------------------------------------------------------------------------


def _mock_httpx(monkeypatch, *, status=200, text="<html>ok</html>"):
    resp = SimpleNamespace(status_code=status, text=text)
    client = MagicMock()
    client.get.return_value = resp
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(html_base.httpx, "Client", MagicMock(return_value=client))


class TestFetchFlow:
    def test_httpx_success_returns_rawscrape(self, monkeypatch):
        _mock_httpx(monkeypatch, text="<html><body>vrbo listings</body></html>")
        results = VrboScraper().search(_q())
        assert len(results) == 1
        assert results[0].source == "vrbo"
        assert "vrbo listings" in results[0].payload

    def test_escalates_to_playwright_on_block(self, monkeypatch):
        scraper = ExpediaScraper()
        # httpx path raises a block; playwright path (mocked) returns the page.
        monkeypatch.setattr(
            scraper, "_fetch_httpx",
            lambda url: (_ for _ in ()).throw(BlockedError(url=url, reason="HTTP 403")),
        )
        monkeypatch.setattr(scraper, "_fetch_playwright", lambda url: "<html>via pw</html>")
        results = scraper.search(_q(sources=["expedia"]))
        assert results[0].source == "expedia"
        assert "via pw" in results[0].payload


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_normalize_aliases_and_dedup(self):
        assert registry.normalize_sources(["hotels", "booking", "vrbo", "vrbo"]) == [
            "booking",
            "vrbo",
        ]

    def test_normalize_drops_unknown_and_defaults(self):
        assert registry.normalize_sources(["bogus"]) == ["airbnb"]
        assert registry.normalize_sources([]) == ["airbnb"]

    def test_build_scrapers_instantiates_known(self):
        scrapers = registry.build_scrapers(["vrbo", "expedia", "hostelworld"])
        sources = {getattr(s, "SOURCE", None) for s in scrapers}
        assert sources == {"vrbo", "expedia", "hostelworld"}

    def test_build_scrapers_skips_failures(self, monkeypatch):
        real_make = registry._make

        def flaky(key):
            if key == "vrbo":
                raise RuntimeError("boom")
            return real_make(key)

        monkeypatch.setattr(registry, "_make", flaky)
        scrapers = registry.build_scrapers(["vrbo", "expedia"])
        assert {s.SOURCE for s in scrapers} == {"expedia"}
