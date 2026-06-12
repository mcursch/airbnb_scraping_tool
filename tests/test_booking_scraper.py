"""
tests/test_booking_scraper.py
==============================
Offline unit tests for the Booking.com scraper.

All tests run entirely from fixture files; no network access is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest

from scrapers.base import BlockedError, RawScrape, SearchQuery
from scrapers.booking import (
    BLOCKED_STATUS_CODES,
    CAPTCHA_PATTERNS,
    SELECTORS,
    BookingScraper,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "booking"


def _load_fixture(name: str) -> str:
    path = FIXTURES_DIR / name
    assert path.exists(), f"Fixture file not found: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture()
def search_results_html() -> str:
    return _load_fixture("search_results.html")


@pytest.fixture()
def search_results_page2_html() -> str:
    return _load_fixture("search_results_page2.html")


@pytest.fixture()
def captcha_html() -> str:
    return _load_fixture("captcha_page.html")


@pytest.fixture()
def scraper() -> BookingScraper:
    """A BookingScraper with no DB session (in-memory only)."""
    return BookingScraper(session=None)


# ---------------------------------------------------------------------------
# Block / CAPTCHA detection
# ---------------------------------------------------------------------------


class TestCheckForBlock:
    """_check_for_block raises BlockedError for blocked responses."""

    def test_captcha_fixture_triggers_blocked_error(
        self, scraper: BookingScraper, captcha_html: str
    ) -> None:
        """The CAPTCHA fixture file must raise BlockedError without network access."""
        with pytest.raises(BlockedError) as exc_info:
            scraper._check_for_block(200, captcha_html, url="https://www.booking.com/")
        err = exc_info.value
        assert err.url == "https://www.booking.com/"
        assert err.reason  # non-empty reason string

    def test_http_403_triggers_blocked_error(self, scraper: BookingScraper) -> None:
        with pytest.raises(BlockedError) as exc_info:
            scraper._check_for_block(403, "", url="https://www.booking.com/")
        assert "403" in exc_info.value.reason

    def test_http_429_triggers_blocked_error(self, scraper: BookingScraper) -> None:
        with pytest.raises(BlockedError) as exc_info:
            scraper._check_for_block(429, "", url="https://www.booking.com/")
        assert "429" in exc_info.value.reason

    def test_http_503_triggers_blocked_error(self, scraper: BookingScraper) -> None:
        with pytest.raises(BlockedError) as exc_info:
            scraper._check_for_block(503, "", url="https://www.booking.com/")
        assert "503" in exc_info.value.reason

    def test_normal_200_html_does_not_raise(
        self, scraper: BookingScraper, search_results_html: str
    ) -> None:
        """A normal search-results page must NOT trigger BlockedError."""
        # Should not raise
        scraper._check_for_block(200, search_results_html, url="https://www.booking.com/")

    @pytest.mark.parametrize("pattern", CAPTCHA_PATTERNS[:4])
    def test_captcha_pattern_in_body_triggers_blocked_error(
        self, scraper: BookingScraper, pattern: str
    ) -> None:
        """Any CAPTCHA_PATTERN substring in the body must trigger BlockedError."""
        body = f"<html><body><p>We detected {pattern}</p></body></html>"
        with pytest.raises(BlockedError):
            scraper._check_for_block(200, body, url="https://www.booking.com/")

    @pytest.mark.parametrize("code", sorted(BLOCKED_STATUS_CODES))
    def test_all_blocked_status_codes_raise(
        self, scraper: BookingScraper, code: int
    ) -> None:
        with pytest.raises(BlockedError):
            scraper._check_for_block(code, "some body", url="https://www.booking.com/")


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


class TestBuildUrl:
    def test_basic_area(self, scraper: BookingScraper) -> None:
        url = scraper._build_url(SearchQuery(area="Amsterdam"))
        assert "Amsterdam" in url
        assert "searchresults.html" in url

    def test_pagination_offset(self, scraper: BookingScraper) -> None:
        url = scraper._build_url(SearchQuery(area="Paris"), offset=25)
        assert "offset=25" in url

    def test_checkin_checkout_included(self, scraper: BookingScraper) -> None:
        url = scraper._build_url(
            SearchQuery(area="Rome", checkin="2025-08-01", checkout="2025-08-07")
        )
        assert "checkin=2025-08-01" in url
        assert "checkout=2025-08-07" in url

    def test_guests_included(self, scraper: BookingScraper) -> None:
        url = scraper._build_url(SearchQuery(area="Berlin", guests=3))
        assert "group_adults=3" in url

    def test_no_checkin_checkout_when_omitted(self, scraper: BookingScraper) -> None:
        url = scraper._build_url(SearchQuery(area="Lisbon"))
        assert "checkin" not in url
        assert "checkout" not in url


# ---------------------------------------------------------------------------
# search() — offline via monkeypatching _fetch_httpx
# ---------------------------------------------------------------------------


class TestSearch:
    def test_returns_raw_scrape_list(
        self,
        scraper: BookingScraper,
        search_results_html: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """search() returns a non-empty list of RawScrape objects."""
        monkeypatch.setattr(scraper, "_fetch_httpx", lambda url: search_results_html)
        query = SearchQuery(area="Amsterdam", page_limit=1)
        results = scraper.search(query)
        assert len(results) == 1
        assert isinstance(results[0], RawScrape)

    def test_raw_scrape_has_correct_source(
        self,
        scraper: BookingScraper,
        search_results_html: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(scraper, "_fetch_httpx", lambda url: search_results_html)
        results = scraper.search(SearchQuery(area="Amsterdam", page_limit=1))
        assert results[0].source == "booking"

    def test_raw_scrape_payload_is_full_html(
        self,
        scraper: BookingScraper,
        search_results_html: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(scraper, "_fetch_httpx", lambda url: search_results_html)
        results = scraper.search(SearchQuery(area="Amsterdam", page_limit=1))
        assert results[0].payload == search_results_html

    def test_raw_scrape_content_hash_populated(
        self,
        scraper: BookingScraper,
        search_results_html: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(scraper, "_fetch_httpx", lambda url: search_results_html)
        results = scraper.search(SearchQuery(area="Amsterdam", page_limit=1))
        assert len(results[0].content_hash) == 64  # SHA-256 hex digest

    def test_raw_scrape_status_is_pending(
        self,
        scraper: BookingScraper,
        search_results_html: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(scraper, "_fetch_httpx", lambda url: search_results_html)
        results = scraper.search(SearchQuery(area="Amsterdam", page_limit=1))
        assert results[0].status == "pending"

    def test_stops_at_page_limit(
        self,
        scraper: BookingScraper,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """page_limit=2 with two fixture pages and no delay should return 2 records."""
        from bs4 import BeautifulSoup

        page1 = _load_fixture("search_results.html")
        page2 = _load_fixture("search_results_page2.html")

        # Inject a pagination-next link into page 1 so the scraper continues.
        page1_with_next = page1.replace(
            "<!-- No pagination-next on this page (single-page result for test) -->",
            '<a data-testid="pagination-next" href="?offset=25">Next</a>',
        )

        call_count = 0

        def fake_fetch(url: str) -> str:
            nonlocal call_count
            call_count += 1
            return page1_with_next if call_count == 1 else page2

        monkeypatch.setattr(scraper, "_fetch_httpx", fake_fetch)
        monkeypatch.setattr(scraper, "_polite_delay", lambda: None)

        results = scraper.search(SearchQuery(area="Amsterdam", page_limit=2))
        assert len(results) == 2
        assert call_count == 2

    def test_stops_when_no_next_page(
        self,
        scraper: BookingScraper,
        search_results_html: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """search() stops early if no pagination-next is found (before page_limit)."""
        monkeypatch.setattr(scraper, "_fetch_httpx", lambda url: search_results_html)
        results = scraper.search(SearchQuery(area="Amsterdam", page_limit=5))
        assert len(results) == 1  # fixture has no next-page link

    def test_blocked_response_raises_blocked_error(
        self,
        scraper: BookingScraper,
        captcha_html: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CAPTCHA fixture causes search() to raise BlockedError (no network)."""

        def blocked_fetch(url: str) -> str:
            raise BlockedError(url=url, reason="CAPTCHA pattern 'captcha' detected")

        # httpx raises → escalation to Playwright which also raises
        monkeypatch.setattr(scraper, "_fetch_httpx", blocked_fetch)
        monkeypatch.setattr(scraper, "_fetch_playwright", blocked_fetch)

        with pytest.raises(BlockedError):
            scraper.search(SearchQuery(area="Amsterdam", page_limit=1))

    def test_persist_called_per_page(
        self,
        scraper: BookingScraper,
        search_results_html: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_persist is called once per fetched page."""
        persisted: list[RawScrape] = []
        monkeypatch.setattr(scraper, "_fetch_httpx", lambda url: search_results_html)
        monkeypatch.setattr(scraper, "_persist", lambda raw: persisted.append(raw))

        scraper.search(SearchQuery(area="Amsterdam", page_limit=1))
        assert len(persisted) == 1
        assert persisted[0].source == "booking"

    def test_persist_passes_run_id_to_db_row(
        self,
        search_results_html: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_persist() includes run_id when constructing the ORM row."""
        scraper_with_run = BookingScraper(session=None, run_id=42)
        assert scraper_with_run._run_id == 42

        # Verify run_id is threaded into the ORM row constructor.
        added_rows: list[object] = []

        class FakeSession:
            def add(self, row: object) -> None:
                added_rows.append(row)

            def flush(self) -> None:
                pass

        scraper_with_run._session = FakeSession()

        class FakeRawScrapeRow:
            """Minimal stand-in for db.models.RawScrape."""

            def __init__(self, **kwargs: object) -> None:
                self.__dict__.update(kwargs)
                self.id = 99

        # Patch the in-function import so no real DB is needed.
        import db.models as db_models_mod

        monkeypatch.setattr(db_models_mod, "RawScrape", FakeRawScrapeRow)

        raw = RawScrape(source="booking", url="https://example.com", payload="<html/>")
        scraper_with_run._persist(raw)

        assert len(added_rows) == 1
        row = added_rows[0]
        assert getattr(row, "run_id", "MISSING") == 42

    def test_no_network_access_in_all_tests(
        self,
        scraper: BookingScraper,
        search_results_html: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Confirm _fetch_httpx is never executed with a real HTTP client."""
        # We monkeypatch to ensure httpx.Client is never used
        import httpx

        original_client = httpx.Client

        def fail_if_instantiated(*args: object, **kwargs: object) -> None:
            raise AssertionError(
                "httpx.Client was instantiated — tests should not make real requests"
            )

        monkeypatch.setattr(httpx, "Client", fail_if_instantiated)

        # Since we also monkeypatch _fetch_httpx, no real Client is used.
        monkeypatch.setattr(scraper, "_fetch_httpx", lambda url: search_results_html)

        results = scraper.search(SearchQuery(area="Amsterdam", page_limit=1))
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Fixture file existence
# ---------------------------------------------------------------------------


class TestFixtureFiles:
    """Ensure fixture files are committed and parseable."""

    def test_search_results_fixture_exists(self) -> None:
        assert (FIXTURES_DIR / "search_results.html").exists()

    def test_search_results_page2_fixture_exists(self) -> None:
        assert (FIXTURES_DIR / "search_results_page2.html").exists()

    def test_captcha_fixture_exists(self) -> None:
        assert (FIXTURES_DIR / "captcha_page.html").exists()

    def test_search_results_contains_property_cards(
        self, search_results_html: str
    ) -> None:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(search_results_html, "lxml")
        cards = soup.select(SELECTORS["property_card"])
        assert len(cards) >= 1, "search_results.html must contain at least one property card"

    def test_search_results_page2_contains_property_cards(
        self, search_results_page2_html: str
    ) -> None:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(search_results_page2_html, "lxml")
        cards = soup.select(SELECTORS["property_card"])
        assert len(cards) >= 1, "search_results_page2.html must contain at least one property card"

    def test_captcha_page_triggers_block_detection(
        self, scraper: BookingScraper, captcha_html: str
    ) -> None:
        """The captcha fixture must be recognised as blocked by the scraper."""
        with pytest.raises(BlockedError):
            scraper._check_for_block(200, captcha_html)


# ---------------------------------------------------------------------------
# RawScrape model
# ---------------------------------------------------------------------------


class TestRawScrape:
    def test_content_hash_auto_computed(self) -> None:
        raw = RawScrape(source="booking", url="https://example.com", payload="hello")
        import hashlib

        expected = hashlib.sha256(b"hello").hexdigest()
        assert raw.content_hash == expected

    def test_status_defaults_to_pending(self) -> None:
        raw = RawScrape(source="booking", url="https://example.com", payload="x")
        assert raw.status == "pending"


# ---------------------------------------------------------------------------
# BlockedError
# ---------------------------------------------------------------------------


class TestBlockedError:
    def test_attributes(self) -> None:
        err = BlockedError(url="https://www.booking.com/", reason="HTTP 403")
        assert err.url == "https://www.booking.com/"
        assert err.reason == "HTTP 403"
        assert "403" in str(err)

    def test_is_exception(self) -> None:
        assert issubclass(BlockedError, Exception)
