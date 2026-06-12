"""Integration tests for the BlockedError fallback wiring in pipeline.run_acquire.

All tests run offline with no real API keys and no network access.  They use
lightweight mock providers built on the ``ScrapeProvider`` ABC.

Test matrix
-----------
test_fallback_engaged_on_blocked_error
    Primary scraper always raises BlockedError; a fallback provider IS
    configured.  Asserts that the fallback's payloads are returned and that
    the fallback's ``search`` method was called exactly once with the original
    query.

test_no_fallback_warning_and_no_exception
    Primary scraper always raises BlockedError; NO fallback provider is
    configured.  Asserts that a WARNING-level log entry is emitted by the
    ``pipeline`` logger and that the function completes without raising,
    returning an empty list.
"""

from __future__ import annotations

import logging

import pytest

from pipeline import run_acquire
from scrapers.base import BlockedError, RawScrape, ScrapeProvider, SearchQuery


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class AlwaysBlockedScraper(ScrapeProvider):
    """A booking-style scraper stub that always raises ``BlockedError``.

    Simulates the scenario where bot-detection triggers on every request,
    which is the primary motivation for the fallback mechanism.
    """

    def __init__(self, reason: str = "bot detection triggered") -> None:
        self._reason = reason

    def search(self, query: SearchQuery) -> list[RawScrape]:  # noqa: ARG002
        raise BlockedError(url="", reason=self._reason)


class MockFallbackProvider(ScrapeProvider):
    """A fallback provider stub that returns a fixed list of payloads.

    Records every ``query`` argument passed to ``search`` so tests can assert
    on the call count and the forwarded query string.
    """

    def __init__(self, payloads: list[RawScrape] | None = None) -> None:
        self.calls: list[SearchQuery] = []
        self._payloads: list[RawScrape] = payloads or [
            RawScrape(
                source="fallback",
                url="https://api.example.com/results/1",
                payload='{"title": "Cosy Studio", "price": 85}',
            ),
            RawScrape(
                source="fallback",
                url="https://api.example.com/results/2",
                payload='{"title": "City Centre Flat", "price": 120}',
            ),
        ]

    def search(self, query: SearchQuery) -> list[RawScrape]:
        self.calls.append(query)
        return self._payloads


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFallbackEngaged:
    """Fallback IS configured — the pipeline should use it on BlockedError."""

    def test_returns_fallback_payloads(self) -> None:
        """Results contain exactly the payloads supplied by the fallback."""
        scraper = AlwaysBlockedScraper()
        fallback = MockFallbackProvider()

        results = run_acquire(
            "Lisbon, Portugal",
            providers=[scraper],
            fallback_provider=fallback,
        )

        assert len(results) == 2
        assert all(r.source == "fallback" for r in results)

    def test_fallback_called_with_original_query(self) -> None:
        """The fallback receives a SearchQuery whose area matches the original string."""
        query = "Tokyo, Japan"
        scraper = AlwaysBlockedScraper()
        fallback = MockFallbackProvider()

        run_acquire(query, providers=[scraper], fallback_provider=fallback)

        assert len(fallback.calls) == 1
        assert fallback.calls[0].area == query

    def test_fallback_called_once_per_blocked_provider(self) -> None:
        """With two blocked primary scrapers, the fallback is called twice."""
        fallback = MockFallbackProvider()
        providers = [AlwaysBlockedScraper(), AlwaysBlockedScraper()]

        results = run_acquire(
            "Barcelona, Spain",
            providers=providers,
            fallback_provider=fallback,
        )

        assert len(fallback.calls) == 2
        # Each blocked provider triggers one fallback call → 2 × 2 payloads
        assert len(results) == 4

    def test_unblocked_provider_results_included(self) -> None:
        """Results from an unblocked primary provider are not discarded."""

        class GoodScraper(ScrapeProvider):
            def search(self, query: SearchQuery) -> list[RawScrape]:  # noqa: ARG002
                return [RawScrape(source="direct", url="https://direct.example.com", payload="{}")]

        fallback = MockFallbackProvider()
        providers = [GoodScraper(), AlwaysBlockedScraper()]

        results = run_acquire("Amsterdam, NL", providers=providers, fallback_provider=fallback)

        sources = {r.source for r in results}
        assert "direct" in sources
        assert "fallback" in sources


class TestNoFallbackDegradation:
    """Fallback is NOT configured — the pipeline must degrade gracefully."""

    def test_does_not_raise(self) -> None:
        """No exception escapes when a provider is blocked and no fallback exists."""
        scraper = AlwaysBlockedScraper()

        # Must complete without raising
        results = run_acquire("Berlin, Germany", providers=[scraper], fallback_provider=None)

        assert results == []

    def test_emits_warning_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """A WARNING entry is written to the ``pipeline`` logger."""
        scraper = AlwaysBlockedScraper(reason="HTTP 403")

        with caplog.at_level(logging.WARNING, logger="pipeline"):
            run_acquire("Paris, France", providers=[scraper], fallback_provider=None)

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) >= 1

    def test_warning_mentions_provider_or_fallback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The warning log message references the blocked situation."""
        scraper = AlwaysBlockedScraper()

        with caplog.at_level(logging.WARNING, logger="pipeline"):
            run_acquire("Rome, Italy", providers=[scraper], fallback_provider=None)

        warning_text = " ".join(r.message for r in caplog.records if r.levelno == logging.WARNING)
        # The message should hint at what happened
        assert any(
            keyword in warning_text.lower()
            for keyword in ("blocked", "fallback", "skipping", "skip")
        )

    def test_multiple_blocked_providers_all_warned(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Each blocked provider generates its own warning entry."""
        providers = [AlwaysBlockedScraper(), AlwaysBlockedScraper()]

        with caplog.at_level(logging.WARNING, logger="pipeline"):
            run_acquire("Madrid, Spain", providers=providers, fallback_provider=None)

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 2

    def test_unblocked_provider_still_returns_results(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A working provider's results survive even when another provider is blocked."""

        class GoodScraper(ScrapeProvider):
            def search(self, query: SearchQuery) -> list[RawScrape]:  # noqa: ARG002
                return [RawScrape(source="direct", url="https://ok.example.com", payload="{}")]

        providers = [GoodScraper(), AlwaysBlockedScraper()]

        with caplog.at_level(logging.WARNING, logger="pipeline"):
            results = run_acquire("Vienna, Austria", providers=providers, fallback_provider=None)

        assert len(results) == 1
        assert results[0].source == "direct"
        assert any(r.levelno == logging.WARNING for r in caplog.records)
