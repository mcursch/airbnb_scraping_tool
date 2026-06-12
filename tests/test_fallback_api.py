"""Tests for scrapers/fallback_api.py.

All HTTP calls are mocked so the suite runs fully offline.
"""

from __future__ import annotations

import contextlib
from typing import Generator
from unittest.mock import MagicMock

import httpx
import pytest

from errors import ConfigurationError
from schemas.search import SearchQuery
from scrapers.base import RawPayload
from scrapers.fallback_api import FallbackApiProvider


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def query() -> SearchQuery:
    """A minimal search query used across tests."""
    return SearchQuery(area="Lisbon, Portugal", guests=2)


def _mock_http_response(text: str, status_code: int = 200) -> httpx.Response:
    """Build a fake :class:`httpx.Response` backed by *text*.

    A dummy :class:`httpx.Request` is attached so that
    :meth:`httpx.Response.raise_for_status` works without raising
    ``RuntimeError``.
    """
    request = httpx.Request("GET", "https://api.example.com/")
    return httpx.Response(status_code=status_code, text=text, request=request)


@pytest.fixture()
def scraperapi_client() -> httpx.Client:
    """An httpx.Client whose GET always returns a simple HTML page."""
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = _mock_http_response(
        "<html><body>Listing A – €120/night</body></html>"
    )
    return client


@pytest.fixture()
def apify_client() -> httpx.Client:
    """An httpx.Client whose POST always returns a JSON array of items."""
    client = MagicMock(spec=httpx.Client)
    client.post.return_value = _mock_http_response(
        '[{"url": "https://booking.com/hotel/1", "title": "Hotel Lisboa"}]'
    )
    return client


# ── Helper: patch settings ────────────────────────────────────────────────────


@contextlib.contextmanager
def _patch_settings(
    api_key: str | None, provider: str | None = "scraperapi"
) -> Generator[None, None, None]:
    """Context-manager that monkey-patches the settings singleton."""
    from config import settings as _settings

    orig_key = _settings.SCRAPER_API_KEY
    orig_provider = _settings.FALLBACK_PROVIDER
    _settings.SCRAPER_API_KEY = api_key  # type: ignore[assignment]
    _settings.FALLBACK_PROVIDER = provider  # type: ignore[assignment]
    try:
        yield
    finally:
        _settings.SCRAPER_API_KEY = orig_key  # type: ignore[assignment]
        _settings.FALLBACK_PROVIDER = orig_provider  # type: ignore[assignment]


# ── Missing-key error path ────────────────────────────────────────────────────


class TestMissingApiKey:
    def test_raises_configuration_error(self, query: SearchQuery) -> None:
        """search() must raise ConfigurationError when SCRAPER_API_KEY is unset."""
        provider = FallbackApiProvider()
        with _patch_settings(api_key=None):
            with pytest.raises(ConfigurationError) as exc_info:
                provider.search(query)
        assert "SCRAPER_API_KEY" in str(exc_info.value)

    def test_error_message_is_human_readable(self, query: SearchQuery) -> None:
        """The error message must be more informative than a bare key name."""
        provider = FallbackApiProvider()
        with _patch_settings(api_key=None):
            with pytest.raises(ConfigurationError) as exc_info:
                provider.search(query)
        msg = str(exc_info.value)
        # Should name the key AND give some guidance.
        assert "SCRAPER_API_KEY" in msg
        assert len(msg) > len("SCRAPER_API_KEY")

    def test_not_key_error_or_attribute_error(self, query: SearchQuery) -> None:
        """Must not leak a raw KeyError or AttributeError."""
        provider = FallbackApiProvider()
        with _patch_settings(api_key=None):
            with pytest.raises(ConfigurationError):
                provider.search(query)
        # If we get here without KeyError/AttributeError the test passes.


# ── ScraperAPI happy path ─────────────────────────────────────────────────────


class TestScraperAPIHappyPath:
    def test_returns_non_empty_list(
        self, query: SearchQuery, scraperapi_client: httpx.Client
    ) -> None:
        provider = FallbackApiProvider(http_client=scraperapi_client)
        with _patch_settings(api_key="test-key-123", provider="scraperapi"):
            results = provider.search(query)
        assert len(results) > 0

    def test_returns_raw_payload_objects(
        self, query: SearchQuery, scraperapi_client: httpx.Client
    ) -> None:
        provider = FallbackApiProvider(http_client=scraperapi_client)
        with _patch_settings(api_key="test-key-123", provider="scraperapi"):
            results = provider.search(query)
        assert all(isinstance(r, RawPayload) for r in results)

    def test_source_is_fallback_scraperapi(
        self, query: SearchQuery, scraperapi_client: httpx.Client
    ) -> None:
        provider = FallbackApiProvider(http_client=scraperapi_client)
        with _patch_settings(api_key="test-key-123", provider="scraperapi"):
            results = provider.search(query)
        assert all(r.source == "fallback_scraperapi" for r in results)

    def test_payload_is_non_empty(
        self, query: SearchQuery, scraperapi_client: httpx.Client
    ) -> None:
        provider = FallbackApiProvider(http_client=scraperapi_client)
        with _patch_settings(api_key="test-key-123", provider="scraperapi"):
            results = provider.search(query)
        assert all(r.payload for r in results)

    def test_api_key_sent_in_request(
        self, query: SearchQuery, scraperapi_client: httpx.Client
    ) -> None:
        provider = FallbackApiProvider(http_client=scraperapi_client)
        with _patch_settings(api_key="my-secret-key", provider="scraperapi"):
            provider.search(query)
        call_kwargs = scraperapi_client.get.call_args
        params = call_kwargs.kwargs.get("params", {})
        assert params.get("api_key") == "my-secret-key"

    def test_area_included_in_target_url(
        self, query: SearchQuery, scraperapi_client: httpx.Client
    ) -> None:
        provider = FallbackApiProvider(http_client=scraperapi_client)
        with _patch_settings(api_key="test-key-123", provider="scraperapi"):
            provider.search(query)
        call_kwargs = scraperapi_client.get.call_args
        params = call_kwargs.kwargs.get("params", {})
        target_url: str = params.get("url", "")
        # The area should be URL-encoded somewhere in the target URL.
        assert "Lisbon" in target_url or "Lisbon%2C" in target_url


# ── Apify happy path ──────────────────────────────────────────────────────────


class TestApifyHappyPath:
    def test_returns_non_empty_list(
        self, query: SearchQuery, apify_client: httpx.Client
    ) -> None:
        provider = FallbackApiProvider(http_client=apify_client)
        with _patch_settings(api_key="apify-token-xyz", provider="apify"):
            results = provider.search(query)
        assert len(results) > 0

    def test_returns_raw_payload_objects(
        self, query: SearchQuery, apify_client: httpx.Client
    ) -> None:
        provider = FallbackApiProvider(http_client=apify_client)
        with _patch_settings(api_key="apify-token-xyz", provider="apify"):
            results = provider.search(query)
        assert all(isinstance(r, RawPayload) for r in results)

    def test_source_is_fallback_apify(
        self, query: SearchQuery, apify_client: httpx.Client
    ) -> None:
        provider = FallbackApiProvider(http_client=apify_client)
        with _patch_settings(api_key="apify-token-xyz", provider="apify"):
            results = provider.search(query)
        assert all(r.source == "fallback_apify" for r in results)

    def test_payload_contains_json(
        self, query: SearchQuery, apify_client: httpx.Client
    ) -> None:
        import json

        provider = FallbackApiProvider(http_client=apify_client)
        with _patch_settings(api_key="apify-token-xyz", provider="apify"):
            results = provider.search(query)
        # Payload should be valid JSON (the serialised dataset items).
        for r in results:
            parsed = json.loads(r.payload)
            assert isinstance(parsed, list)


# ── Unknown provider ──────────────────────────────────────────────────────────


class TestUnknownProvider:
    def test_raises_configuration_error(self, query: SearchQuery) -> None:
        """An unrecognised FALLBACK_PROVIDER must raise ConfigurationError."""
        provider = FallbackApiProvider(http_client=MagicMock(spec=httpx.Client))
        with _patch_settings(api_key="some-key", provider="nonexistent"):  # type: ignore[arg-type]
            with pytest.raises(ConfigurationError):
                provider.search(query)


# ── HTTP error propagation ────────────────────────────────────────────────────


class TestHttpErrors:
    def test_scraperapi_http_error_raises_scraper_error(
        self, query: SearchQuery
    ) -> None:
        from errors import ScraperError

        client = MagicMock(spec=httpx.Client)
        client.get.return_value = _mock_http_response("Forbidden", status_code=403)
        client.get.return_value.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "403",
                request=MagicMock(),
                response=client.get.return_value,
            )
        )
        provider = FallbackApiProvider(http_client=client)
        with _patch_settings(api_key="test-key", provider="scraperapi"):
            with pytest.raises(ScraperError):
                provider.search(query)
