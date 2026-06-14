"""Tests for the reason-and-act enrichment agent (fully offline; client mocked)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from enrichment.agent import (
    EnrichmentAgent,
    EnrichmentResult,
    IMPORTANT_FIELDS,
    SNAPSHOT_FIELDS,
    _coerce,
    missing_important_fields,
)
from schemas.listing import ExtractedListing


# ---------------------------------------------------------------------------
# Mock Anthropic client
# ---------------------------------------------------------------------------


def _block(**kw):
    return SimpleNamespace(**kw)


def _usage(inp=100, out=50, cache=0, searches=0):
    server = SimpleNamespace(web_search_requests=searches)
    return SimpleNamespace(
        input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache,
        server_tool_use=server,
    )


class _MockMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


class _MockClient:
    def __init__(self, responses):
        self.messages = _MockMessages(responses)


def _submit_response(fields, *, searches=1):
    block = _block(type="tool_use", name="submit_enrichment", input={"fields": fields})
    return SimpleNamespace(content=[block], usage=_usage(searches=searches), stop_reason="tool_use")


def _partial_listing(**overrides) -> ExtractedListing:
    base = dict(source_listing_id="L1", name="Test Flat", nightly_price=100.0)
    base.update(overrides)
    return ExtractedListing(**base)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_missing_fields_detected(self):
        listing = _partial_listing(rating=4.8, neighborhood="Chiado")
        gaps = missing_important_fields(listing)
        assert "rating" not in gaps
        assert "neighborhood" not in gaps
        assert "cleaning_fee" in gaps
        assert "host_is_superhost" in gaps

    def test_coerce_types(self):
        assert _coerce("host_is_superhost", "true") is True
        assert _coerce("host_is_superhost", "no") is False
        assert _coerce("host_response_rate", "95%") == 95
        assert _coerce("minimum_nights", "2 nights") == 2
        assert _coerce("cleaning_fee", "$45.50") == 45.5
        assert _coerce("neighborhood", " Chiado ") == "Chiado"
        assert _coerce("cleaning_fee", "") is None

    def test_snapshot_fields_subset(self):
        assert SNAPSHOT_FIELDS <= set(IMPORTANT_FIELDS)


# ---------------------------------------------------------------------------
# Agent behaviour
# ---------------------------------------------------------------------------


class TestEnrichmentAgent:
    def test_no_gaps_skips_client(self):
        """A fully-populated listing returns no_gaps without calling the LLM."""
        full = {name: _filler(name) for name in IMPORTANT_FIELDS}
        listing = _partial_listing(**full)
        agent = EnrichmentAgent(client=None)  # must not be called
        res = agent.enrich(listing, source="airbnb")
        assert res.status == "no_gaps"
        assert res.filled == {}

    def test_fills_fields_and_records_provenance(self):
        fields = [
            {"field": "neighborhood", "value": "Chiado", "confidence": 0.9,
             "source_url": "https://x/1", "reasoning": "listing page"},
            {"field": "host_is_superhost", "value": "true", "confidence": 0.8,
             "source_url": "https://x/2", "reasoning": "badge"},
            {"field": "cleaning_fee", "value": "$50", "confidence": 0.7,
             "source_url": "https://x/3", "reasoning": "checkout"},
        ]
        client = _MockClient([_submit_response(fields, searches=2)])
        agent = EnrichmentAgent(client=client, model="claude-sonnet-4-6")

        listing = _partial_listing()
        res = agent.enrich(listing, source="airbnb")

        assert res.status == "enriched"
        assert res.filled["neighborhood"] == "Chiado"
        assert res.filled["host_is_superhost"] is True
        assert res.filled["cleaning_fee"] == 50.0
        assert res.provenance["neighborhood"]["source_url"] == "https://x/1"
        assert res.web_search_count == 2
        assert res.total_tokens > 0
        assert res.estimated_cost_usd > 0

    def test_pause_turn_then_submit(self):
        """A pause_turn response is continued; the next submit is captured."""
        pause = SimpleNamespace(
            content=[_block(type="text", text="searching…")],
            usage=_usage(searches=1), stop_reason="pause_turn",
        )
        submit = _submit_response(
            [{"field": "rating", "value": "4.7", "confidence": 0.9,
              "source_url": "https://x", "reasoning": "reviews"}],
            searches=1,
        )
        client = _MockClient([pause, submit])
        agent = EnrichmentAgent(client=client)

        res = agent.enrich(_partial_listing(), source="airbnb")
        assert client.messages.calls == 2
        assert res.filled["rating"] == 4.7
        assert res.web_search_count == 2  # accumulated across both calls

    def test_end_turn_without_submit_is_no_gaps(self):
        end = SimpleNamespace(
            content=[_block(type="text", text="couldn't find anything")],
            usage=_usage(), stop_reason="end_turn",
        )
        client = _MockClient([end])
        agent = EnrichmentAgent(client=client)
        res = agent.enrich(_partial_listing(), source="airbnb")
        assert res.status == "no_gaps"
        assert res.filled == {}

    def test_exception_returns_failed(self):
        class Boom:
            class messages:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("api down")

        agent = EnrichmentAgent(client=Boom())
        res = agent.enrich(_partial_listing(), source="airbnb")
        assert res.status == "failed"
        assert "api down" in (res.error or "")

    def test_unknown_field_ignored(self):
        client = _MockClient([_submit_response(
            [{"field": "not_a_field", "value": "x", "confidence": 1.0,
              "source_url": "u", "reasoning": "r"}]
        )])
        agent = EnrichmentAgent(client=client)
        res = agent.enrich(_partial_listing(), source="airbnb")
        assert res.filled == {}


def _filler(name: str):
    """Produce a non-empty value of the right type to mark a field 'present'."""
    from enrichment.agent import _python_type

    t = _python_type(name)
    if t is bool:
        return True
    if t is int:
        return 1
    if t is float:
        return 1.0
    return "x"
