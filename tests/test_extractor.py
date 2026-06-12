"""
Tests for extraction/extractor.py

All tests run offline — the Anthropic client is monkeypatched with a
lightweight stub so no real API calls are made.
"""
from __future__ import annotations

import inspect
import json
import pathlib
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base, ExtractionLog, RawScrape
from extraction.extractor import extract_listings
from schemas.listing import ExtractedListing, ListingExtraction

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


def _make_extracted_listing(**kwargs: Any) -> ExtractedListing:
    defaults: dict[str, Any] = {
        "source_listing_id": "12345678",
        "name": "Cozy Studio in Lisbon Historic Centre",
        "property_type": "Entire studio",
        "lat": 38.7128,
        "lon": -9.1393,
        "address_text": "Lisbon, Portugal",
        "bedrooms": 0,
        "beds": 1,
        "baths": 1.0,
        "max_guests": 2,
        "rating": 4.87,
        "review_count": 156,
        "amenities": ["Wifi", "Kitchen", "Air conditioning"],
        "images": ["https://a0.muscache.com/im/pictures/12345678/photo1.jpg"],
        "url": "https://www.airbnb.com/rooms/12345678",
        "host_or_brand": "Maria",
        "nightly_price": 75.0,
        "currency": "USD",
        "total_price": 650.0,
    }
    defaults.update(kwargs)
    return ExtractedListing(**defaults)


def _make_mock_response(
    cache_read_input_tokens: int = 0,
    listings: list[ExtractedListing] | None = None,
) -> MagicMock:
    """Build a fake Anthropic ParsedMessage response."""
    if listings is None:
        listings = [_make_extracted_listing()]

    mock_usage = MagicMock()
    mock_usage.input_tokens = 320
    mock_usage.output_tokens = 180
    mock_usage.cache_read_input_tokens = cache_read_input_tokens
    mock_usage.cache_creation_input_tokens = 0 if cache_read_input_tokens > 0 else 320

    mock_response = MagicMock()
    mock_response.parsed = ListingExtraction(listings=listings)
    mock_response.usage = mock_usage
    return mock_response


def _make_mock_client(
    side_effects: list[Any] | None = None,
    default_cache_read: int = 0,
) -> MagicMock:
    """Return a mock Anthropic client whose messages.parse() is wired up."""
    mock_client = MagicMock()
    if side_effects is not None:
        mock_client.messages.parse.side_effect = side_effects
    else:
        mock_client.messages.parse.return_value = _make_mock_response(
            cache_read_input_tokens=default_cache_read
        )
    return mock_client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """In-memory SQLite session; all tables created fresh for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def airbnb_raw_scrape(db_session: Session) -> RawScrape:
    """A RawScrape row backed by the sample_airbnb.json fixture."""
    scrape = RawScrape(
        source="airbnb",
        url="https://www.airbnb.com/rooms/12345678",
        payload=_load_fixture("sample_airbnb.json"),
        status="pending",
    )
    db_session.add(scrape)
    db_session.flush()  # assign id without committing
    return scrape


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExtractListingsSuccess:
    """Happy-path: valid fixture payload → valid Listing objects, success log."""

    def test_returns_listing_extraction_objects(
        self, db_session: Session, airbnb_raw_scrape: RawScrape
    ) -> None:
        mock_client = _make_mock_client()
        results = extract_listings([airbnb_raw_scrape], db_session, client=mock_client)

        assert len(results) == 1
        extraction = results[0]
        assert isinstance(extraction, ListingExtraction)
        assert len(extraction.listings) == 1

    def test_extracted_listing_has_valid_fields(
        self, db_session: Session, airbnb_raw_scrape: RawScrape
    ) -> None:
        mock_client = _make_mock_client()
        results = extract_listings([airbnb_raw_scrape], db_session, client=mock_client)

        listing = results[0].listings[0]
        assert listing.source_listing_id == "12345678"
        assert listing.name == "Cozy Studio in Lisbon Historic Centre"
        assert listing.rating == 4.87
        assert listing.nightly_price == 75.0
        assert listing.currency == "USD"

    def test_raw_scrape_status_updated_to_extracted(
        self, db_session: Session, airbnb_raw_scrape: RawScrape
    ) -> None:
        mock_client = _make_mock_client()
        extract_listings([airbnb_raw_scrape], db_session, client=mock_client)

        db_session.refresh(airbnb_raw_scrape)
        assert airbnb_raw_scrape.status == "extracted"

    def test_extraction_log_written_with_token_counts(
        self, db_session: Session, airbnb_raw_scrape: RawScrape
    ) -> None:
        mock_client = _make_mock_client()
        extract_listings([airbnb_raw_scrape], db_session, client=mock_client)

        logs = db_session.query(ExtractionLog).all()
        assert len(logs) == 1
        log = logs[0]
        assert log.raw_scrape_id == airbnb_raw_scrape.id
        assert log.status == "success"
        assert log.input_tokens == 320
        assert log.output_tokens == 180
        assert log.error is None

    def test_api_called_with_system_prompt_and_user_payload(
        self, db_session: Session, airbnb_raw_scrape: RawScrape
    ) -> None:
        mock_client = _make_mock_client()
        extract_listings([airbnb_raw_scrape], db_session, client=mock_client)

        call_kwargs = mock_client.messages.parse.call_args
        # System prompt block must be a list containing a dict with cache_control
        system_arg = call_kwargs.kwargs.get("system") or call_kwargs.args[0]
        # Access via kwargs since we call with keyword args
        system_arg = mock_client.messages.parse.call_args.kwargs["system"]
        assert isinstance(system_arg, list)
        assert len(system_arg) == 1
        block = system_arg[0]
        assert block["type"] == "text"
        assert block["cache_control"] == {"type": "ephemeral"}

        messages_arg = mock_client.messages.parse.call_args.kwargs["messages"]
        assert messages_arg[0]["role"] == "user"
        assert "airbnb" in messages_arg[0]["content"]


class TestPromptCaching:
    """Second call must record cache_read_input_tokens > 0 in ExtractionLog."""

    def test_second_call_records_cache_read_tokens(self, db_session: Session) -> None:
        payload = _load_fixture("sample_airbnb.json")

        scrape1 = RawScrape(source="airbnb", payload=payload, status="pending")
        scrape2 = RawScrape(source="airbnb", payload=payload, status="pending")
        db_session.add_all([scrape1, scrape2])
        db_session.flush()

        mock_client = _make_mock_client(
            side_effects=[
                _make_mock_response(cache_read_input_tokens=0),    # first call
                _make_mock_response(cache_read_input_tokens=512),   # second call
            ]
        )

        extract_listings([scrape1], db_session, client=mock_client)
        extract_listings([scrape2], db_session, client=mock_client)

        logs = db_session.query(ExtractionLog).order_by(ExtractionLog.id).all()
        assert len(logs) == 2
        assert logs[0].cache_read_input_tokens == 0
        assert logs[1].cache_read_input_tokens == 512

    def test_cache_read_tokens_stored_on_success_log(self, db_session: Session) -> None:
        payload = _load_fixture("sample_airbnb.json")
        scrape = RawScrape(source="airbnb", payload=payload, status="pending")
        db_session.add(scrape)
        db_session.flush()

        mock_client = _make_mock_client(default_cache_read=1024)
        extract_listings([scrape], db_session, client=mock_client)

        log = db_session.query(ExtractionLog).one()
        assert log.cache_read_input_tokens == 1024


class TestErrorHandling:
    """Corrupted / empty payloads must not raise; they must record failed logs."""

    def test_empty_payload_records_failed_log(self, db_session: Session) -> None:
        scrape = RawScrape(source="airbnb", payload="", status="pending")
        db_session.add(scrape)
        db_session.flush()

        mock_client = _make_mock_client()
        # Must not raise:
        results = extract_listings([scrape], db_session, client=mock_client)

        assert results == []
        db_session.refresh(scrape)
        assert scrape.status == "failed"

        logs = db_session.query(ExtractionLog).all()
        assert len(logs) == 1
        assert logs[0].status == "failed"
        assert logs[0].error  # non-empty error string

    def test_blank_payload_records_failed_log(self, db_session: Session) -> None:
        scrape = RawScrape(source="airbnb", payload="   \n\t  ", status="pending")
        db_session.add(scrape)
        db_session.flush()

        mock_client = _make_mock_client()
        results = extract_listings([scrape], db_session, client=mock_client)

        assert results == []
        db_session.refresh(scrape)
        assert scrape.status == "failed"

    def test_api_exception_records_failed_log(self, db_session: Session) -> None:
        """An Anthropic API error must be caught per-record, not propagated."""
        payload = _load_fixture("sample_airbnb.json")
        scrape = RawScrape(source="airbnb", payload=payload, status="pending")
        db_session.add(scrape)
        db_session.flush()

        mock_client = _make_mock_client(
            side_effects=[RuntimeError("Simulated API failure")]
        )
        # Must not raise:
        results = extract_listings([scrape], db_session, client=mock_client)

        assert results == []
        db_session.refresh(scrape)
        assert scrape.status == "failed"

        log = db_session.query(ExtractionLog).one()
        assert log.status == "failed"
        assert "Simulated API failure" in log.error

    def test_failed_record_does_not_abort_batch(self, db_session: Session) -> None:
        """A failure on one record must not prevent subsequent records from processing."""
        good_payload = _load_fixture("sample_airbnb.json")

        bad_scrape = RawScrape(source="airbnb", payload="", status="pending")
        good_scrape = RawScrape(source="airbnb", payload=good_payload, status="pending")
        db_session.add_all([bad_scrape, good_scrape])
        db_session.flush()

        mock_client = _make_mock_client()
        results = extract_listings([bad_scrape, good_scrape], db_session, client=mock_client)

        # Only the good record should be returned
        assert len(results) == 1
        assert results[0].listings[0].source_listing_id == "12345678"

        db_session.refresh(bad_scrape)
        db_session.refresh(good_scrape)
        assert bad_scrape.status == "failed"
        assert good_scrape.status == "extracted"

        logs = db_session.query(ExtractionLog).order_by(ExtractionLog.id).all()
        assert len(logs) == 2
        assert logs[0].status == "failed"
        assert logs[1].status == "success"

    def test_none_payload_records_failed_log(self, db_session: Session) -> None:
        scrape = RawScrape(source="airbnb", payload=None, status="pending")
        db_session.add(scrape)
        db_session.flush()

        mock_client = _make_mock_client()
        results = extract_listings([scrape], db_session, client=mock_client)

        assert results == []
        db_session.refresh(scrape)
        assert scrape.status == "failed"


class TestSDKSignature:
    """Smoke-test that guards against future SDK renames of output_format."""

    def test_messages_parse_accepts_output_format(self) -> None:
        """Confirm the real SDK's messages.parse() accepts output_format kwarg."""
        import anthropic

        client = anthropic.Anthropic(api_key="smoke-test-key")
        assert "output_format" in inspect.signature(client.messages.parse).parameters, (
            "anthropic SDK no longer has output_format in messages.parse(); "
            "check for a renamed parameter and update extractor.py"
        )


class TestPretrim:
    """Unit tests for the pretrim helper (indirectly via extract_listings)."""

    def test_json_payload_is_compacted(self, db_session: Session) -> None:
        """Pretty-printed JSON should be compacted before hitting the API."""
        pretty_json = json.dumps({"id": "99", "name": "Test"}, indent=4)
        scrape = RawScrape(source="airbnb", payload=pretty_json, status="pending")
        db_session.add(scrape)
        db_session.flush()

        mock_client = _make_mock_client()
        extract_listings([scrape], db_session, client=mock_client)

        user_content = mock_client.messages.parse.call_args.kwargs["messages"][0]["content"]
        # The JSON portion (after the prefix) should be compact — no indented lines.
        # The prefix adds a :\n\n but the JSON payload itself must not have newlines.
        json_part = user_content.split("\n\n", 1)[-1]
        assert "\n" not in json_part
        assert '{"id":"99"' in json_part or '"id":"99"' in json_part
