"""Offline unit tests for scrapers/airbnb.py.

These tests exercise the capture, cursor-extraction, persistence, and
rate-limiting logic without touching the network or a real browser.
The Playwright ``Page`` object is mocked where needed.
"""

from __future__ import annotations

import json
import time
import unittest.mock as mock
from pathlib import Path

import pytest

from db.repo import create_raw_scrape, get_raw_scrapes
from scrapers.airbnb import (
    AirbnbScraper,
    _build_search_url,
    _extract_cursor,
    _is_search_response,
    _polite_sleep,
)
from scrapers.base import SearchQuery

FIXTURES = Path(__file__).parent / "fixtures"


# ── URL helpers ───────────────────────────────────────────────────────────────

class TestBuildSearchUrl:
    def test_basic_area(self):
        q = SearchQuery(area="Lisbon, Portugal")
        url = _build_search_url(q)
        assert "airbnb.com" in url
        assert "Lisbon" in url

    def test_dates_included(self):
        q = SearchQuery(area="Paris", checkin="2025-07-01", checkout="2025-07-07")
        url = _build_search_url(q)
        assert "checkin=2025-07-01" in url
        assert "checkout=2025-07-07" in url

    def test_guests_omitted_when_one(self):
        q = SearchQuery(area="Rome", guests=1)
        url = _build_search_url(q)
        assert "adults" not in url

    def test_guests_included_when_more_than_one(self):
        q = SearchQuery(area="Rome", guests=3)
        url = _build_search_url(q)
        assert "adults=3" in url


# ── Response pattern matching ─────────────────────────────────────────────────

class TestIsSearchResponse:
    def test_stays_search_url(self):
        assert _is_search_response("https://www.airbnb.com/api/v3/StaysSearch?operationName=StaysSearch")

    def test_explore_tabs_url(self):
        assert _is_search_response("https://www.airbnb.com/api/v2/explore_tabs?key=abc")

    def test_unrelated_url(self):
        assert not _is_search_response("https://www.airbnb.com/rooms/12345")

    def test_image_url(self):
        assert not _is_search_response("https://a0.muscache.com/im/pictures/123.jpg")


# ── Cursor extraction ─────────────────────────────────────────────────────────

class TestExtractCursor:
    def test_finds_paginationCursor(self, page1_payload):
        cursor = _extract_cursor(page1_payload)
        assert cursor == "CURSOR_PAGE_2_ABCDEF"

    def test_returns_none_when_null(self, page2_payload):
        cursor = _extract_cursor(page2_payload)
        assert cursor is None

    def test_returns_none_for_invalid_json(self):
        assert _extract_cursor("not json {{") is None

    def test_returns_none_for_empty(self):
        assert _extract_cursor("{}") is None

    def test_nested_cursor(self):
        payload = json.dumps({
            "wrapper": {"inner": {"nextPageCursor": "XYZ123"}}
        })
        assert _extract_cursor(payload) == "XYZ123"


# ── DB persistence ────────────────────────────────────────────────────────────

class TestCreateRawScrape:
    def test_persists_pending_row(self, in_memory_session, page1_payload):
        row = create_raw_scrape(
            in_memory_session,
            source="airbnb",
            url="https://www.airbnb.com/api/v3/StaysSearch",
            payload=page1_payload,
            page_number=1,
        )
        assert row is not None
        assert row.id is not None
        assert row.status == "pending"
        assert row.page_number == 1

    def test_duplicate_hash_returns_none(self, in_memory_session, page1_payload):
        create_raw_scrape(
            in_memory_session,
            source="airbnb",
            url="https://www.airbnb.com/api/v3/StaysSearch",
            payload=page1_payload,
        )
        second = create_raw_scrape(
            in_memory_session,
            source="airbnb",
            url="https://www.airbnb.com/api/v3/StaysSearch",
            payload=page1_payload,
        )
        assert second is None

    def test_two_distinct_pages_persist(self, in_memory_session, page1_payload, page2_payload):
        r1 = create_raw_scrape(
            in_memory_session, source="airbnb",
            url="https://airbnb.com/api/v3/StaysSearch", payload=page1_payload, page_number=1,
        )
        r2 = create_raw_scrape(
            in_memory_session, source="airbnb",
            url="https://airbnb.com/api/v3/StaysSearch", payload=page2_payload, page_number=2,
        )
        rows = get_raw_scrapes(in_memory_session)
        assert len(rows) == 2
        assert r1.id != r2.id
        assert rows[0].page_number == 1
        assert rows[1].page_number == 2

    def test_run_id_linked(self, in_memory_session, page1_payload):
        from db.repo import create_search_run

        run = create_search_run(in_memory_session, area_query="Lisbon", sources=["airbnb"])
        row = create_raw_scrape(
            in_memory_session,
            source="airbnb",
            url="https://airbnb.com/api/v3/StaysSearch",
            payload=page1_payload,
            run_id=run.id,
        )
        assert row.run_id == run.id
        rows = get_raw_scrapes(in_memory_session, run_id=run.id)
        assert len(rows) == 1


# ── Rate-limiting sleep ───────────────────────────────────────────────────────

class TestPoliteSleep:
    def test_sleeps_within_configured_window(self):
        """Sleep duration must be within [min, max] from config."""
        from config import settings

        start = time.monotonic()
        duration = _polite_sleep(page_index=2)
        elapsed = time.monotonic() - start

        assert settings.rate_limit_min_seconds <= duration <= settings.rate_limit_max_seconds
        # Wall clock should be >= the returned duration (minus tiny scheduling jitter)
        assert elapsed >= duration - 0.05

    def test_sleep_is_logged(self, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="scrapers.airbnb"):
            _polite_sleep(page_index=3)
        assert any("sleep" in r.message.lower() for r in caplog.records)


# ── AirbnbScraper integration (mocked browser) ───────────────────────────────

class TestAirbnbScraperMocked:
    """Test the pagination + persistence loop without launching a real browser."""

    def _make_scraper(self) -> AirbnbScraper:
        return AirbnbScraper(max_pages=2)

    def test_two_pages_produce_two_rows(
        self, in_memory_session, page1_payload, page2_payload, monkeypatch
    ):
        """Simulate a 2-page run using monkeypatched _load_and_capture."""
        scraper = self._make_scraper()
        payloads = [
            (page1_payload, "https://airbnb.com/api/v3/StaysSearch?p=1"),
            (page2_payload, "https://airbnb.com/api/v3/StaysSearch?p=2"),
        ]
        call_count = {"n": 0}

        def fake_load(page, url):
            idx = call_count["n"]
            call_count["n"] += 1
            return payloads[idx] if idx < len(payloads) else (None, None)

        def fake_next_page(page, current_url, cursor, current_page):
            # Cursor from page1_payload should be detected
            if cursor:
                return f"https://airbnb.com/api/v3/StaysSearch?cursor={cursor}"
            return None

        def fake_sleep(page_index):
            # Instant in tests but still exercise the code path
            return 0.001

        monkeypatch.setattr(scraper, "_load_and_capture", fake_load)
        monkeypatch.setattr(scraper, "_next_page_url", fake_next_page)
        monkeypatch.setattr("scrapers.airbnb._polite_sleep", fake_sleep)

        query = SearchQuery(area="Lisbon, Portugal")
        results = scraper._run_search(
            page=mock.MagicMock(),
            query=query,
            session=in_memory_session,
            run_id=None,
        )

        assert len(results) == 2
        rows = get_raw_scrapes(in_memory_session)
        assert len(rows) == 2
        assert rows[0].page_number == 1
        assert rows[1].page_number == 2
        assert rows[0].status == "pending"
        assert rows[1].status == "pending"

    def test_stops_at_max_pages(
        self, in_memory_session, page1_payload, monkeypatch
    ):
        """With max_pages=1 only one row should be persisted."""
        scraper = AirbnbScraper(max_pages=1)

        monkeypatch.setattr(
            scraper, "_load_and_capture",
            lambda page, url: (page1_payload, "https://airbnb.com/api/v3/StaysSearch"),
        )
        monkeypatch.setattr(
            scraper, "_next_page_url",
            lambda *a, **kw: "https://airbnb.com/api/v3/StaysSearch?cursor=X",
        )
        monkeypatch.setattr("scrapers.airbnb._polite_sleep", lambda _: 0.001)

        results = scraper._run_search(
            page=mock.MagicMock(),
            query=SearchQuery(area="Lisbon"),
            session=in_memory_session,
            run_id=None,
        )
        assert len(results) == 1
        assert len(get_raw_scrapes(in_memory_session)) == 1

    def test_duplicate_payload_not_double_counted(
        self, in_memory_session, page1_payload, monkeypatch
    ):
        """If both pages return the same payload, the duplicate is skipped."""
        scraper = AirbnbScraper(max_pages=2)
        call_count = {"n": 0}

        def fake_load(page, url):
            call_count["n"] += 1
            return (page1_payload, "https://airbnb.com/api/v3/StaysSearch")

        monkeypatch.setattr(scraper, "_load_and_capture", fake_load)
        monkeypatch.setattr(
            scraper, "_next_page_url",
            lambda *a, **kw: "https://airbnb.com/api/v3/StaysSearch?cursor=X" if a[3] < 2 else None,
        )
        monkeypatch.setattr("scrapers.airbnb._polite_sleep", lambda _: 0.001)

        results = scraper._run_search(
            page=mock.MagicMock(),
            query=SearchQuery(area="Lisbon"),
            session=in_memory_session,
            run_id=None,
        )
        # Only 1 unique payload should be in captured list and DB
        assert len(results) == 1
        assert len(get_raw_scrapes(in_memory_session)) == 1

    def test_missing_payload_stops_loop(self, in_memory_session, monkeypatch):
        """If the first page yields no intercepted response, stop immediately."""
        scraper = AirbnbScraper(max_pages=3)
        monkeypatch.setattr(scraper, "_load_and_capture", lambda page, url: (None, None))
        monkeypatch.setattr("scrapers.airbnb._polite_sleep", lambda _: 0.001)

        results = scraper._run_search(
            page=mock.MagicMock(),
            query=SearchQuery(area="Nowhere"),
            session=in_memory_session,
            run_id=None,
        )
        assert results == []
        assert get_raw_scrapes(in_memory_session) == []

    def test_sleep_called_between_pages(
        self, in_memory_session, page1_payload, page2_payload, monkeypatch
    ):
        """_polite_sleep must be called exactly (max_pages - 1) times."""
        scraper = AirbnbScraper(max_pages=2)
        payloads = iter([
            (page1_payload, "https://airbnb.com/api/v3/StaysSearch?p=1"),
            (page2_payload, "https://airbnb.com/api/v3/StaysSearch?p=2"),
        ])

        monkeypatch.setattr(scraper, "_load_and_capture", lambda page, url: next(payloads))
        monkeypatch.setattr(
            scraper, "_next_page_url",
            lambda page, url, cursor, n: f"https://airbnb.com?cursor=X" if n < 2 else None,
        )

        sleep_calls: list[int] = []

        def recording_sleep(page_index: int) -> float:
            sleep_calls.append(page_index)
            return 0.001

        monkeypatch.setattr("scrapers.airbnb._polite_sleep", recording_sleep)

        scraper._run_search(
            page=mock.MagicMock(),
            query=SearchQuery(area="Lisbon"),
            session=in_memory_session,
            run_id=None,
        )
        # 2 pages → 1 sleep (before page 2, after capturing page 1)
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 2  # page_index = next page number
