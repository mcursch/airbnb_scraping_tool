"""Tests for the ScrapeProvider ABC and related scaffolding."""

from __future__ import annotations

import pytest

from scrapers.base import ScrapeProvider
from schemas.models import RawPayload, SearchQuery


class TestScrapeProviderIsAbstract:
    def test_cannot_instantiate_directly(self):
        """ScrapeProvider must raise TypeError when instantiated without implementing search()."""
        with pytest.raises(TypeError):
            ScrapeProvider()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self):
        """A subclass that implements search() can be instantiated."""

        class DummyProvider(ScrapeProvider):
            def search(self, query: SearchQuery) -> list[RawPayload]:
                return []

        provider = DummyProvider()
        assert isinstance(provider, ScrapeProvider)

    def test_concrete_subclass_search_returns_list(self):
        """search() on a concrete provider returns a (possibly empty) list."""

        class DummyProvider(ScrapeProvider):
            def search(self, query: SearchQuery) -> list[RawPayload]:
                return []

        query = SearchQuery(area_query="Lisbon, Portugal")
        provider = DummyProvider()
        result = provider.search(query)
        assert result == []


class TestSearchQuery:
    def test_defaults(self):
        q = SearchQuery(area_query="Paris, France")
        assert q.checkin is None
        assert q.checkout is None
        assert q.guests is None
        assert q.sources == ["airbnb"]

    def test_custom_values(self):
        import datetime

        q = SearchQuery(
            area_query="Rome",
            checkin=datetime.date(2026, 7, 1),
            checkout=datetime.date(2026, 7, 7),
            guests=2,
            sources=["airbnb"],
        )
        assert q.area_query == "Rome"
        assert q.guests == 2
        assert q.sources == ["airbnb"]


class TestImports:
    def test_pipeline_importable(self):
        import pipeline  # noqa: F401

    def test_cli_importable(self):
        import cli  # noqa: F401
