"""Round-trip unit tests for all Pydantic schemas.

Run with:
    uv run pytest tests/ -k schema
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from schemas import (
    ExtractionLog,
    Listing,
    ListingSnapshot,
    RawPayload,
    SearchQuery,
    SearchRun,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2024, 6, 1, 13, 0, 0, tzinfo=timezone.utc)
CHECKIN = date(2024, 7, 1)
CHECKOUT = date(2024, 7, 5)


# ===========================================================================
# SearchQuery
# ===========================================================================


class TestSearchQuery:
    def test_valid_minimal(self):
        q = SearchQuery(area="Lisbon, Portugal")
        assert q.area == "Lisbon, Portugal"
        assert q.checkin is None
        assert q.guests == 1
        assert q.sources == ["airbnb"]

    def test_valid_full(self):
        q = SearchQuery(
            area="Paris",
            checkin=CHECKIN,
            checkout=CHECKOUT,
            guests=2,
            sources=["airbnb", "booking"],
        )
        assert q.guests == 2
        assert q.checkout == CHECKOUT

    def test_invalid_empty_area(self):
        with pytest.raises(ValidationError):
            SearchQuery(area="")

    def test_invalid_guests_zero(self):
        with pytest.raises(ValidationError):
            SearchQuery(area="Lisbon", guests=0)

    def test_invalid_guests_string(self):
        with pytest.raises(ValidationError):
            SearchQuery(area="Lisbon", guests="two")  # type: ignore[arg-type]

    def test_checkout_before_checkin_raises(self):
        with pytest.raises(ValidationError):
            SearchQuery(
                area="Lisbon",
                checkin=CHECKOUT,
                checkout=CHECKIN,
            )

    def test_checkout_same_as_checkin_raises(self):
        with pytest.raises(ValidationError):
            SearchQuery(area="Lisbon", checkin=CHECKIN, checkout=CHECKIN)

    def test_round_trip_json(self):
        q = SearchQuery(area="Lisbon", checkin=CHECKIN, checkout=CHECKOUT, guests=3)
        restored = SearchQuery.model_validate_json(q.model_dump_json())
        assert restored == q


# ===========================================================================
# RawPayload
# ===========================================================================


class TestRawPayload:
    def _valid_kwargs(self):
        return dict(
            run_id=1,
            source="airbnb",
            url="https://www.airbnb.com/search",
            payload='{"listings": []}',
            content_hash="abc123" * 10 + "ab",  # 62-char dummy
            fetched_at=NOW,
        )

    def test_valid(self):
        rp = RawPayload(**self._valid_kwargs())
        assert rp.status == "pending"
        assert rp.error is None

    def test_invalid_source(self):
        kwargs = self._valid_kwargs()
        kwargs["source"] = "unknown_source"
        with pytest.raises(ValidationError):
            RawPayload(**kwargs)

    def test_invalid_status(self):
        kwargs = self._valid_kwargs()
        kwargs["status"] = "in_progress"
        with pytest.raises(ValidationError):
            RawPayload(**kwargs)

    def test_invalid_run_id_string(self):
        kwargs = self._valid_kwargs()
        kwargs["run_id"] = "one"  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            RawPayload(**kwargs)

    def test_round_trip_json(self):
        rp = RawPayload(**self._valid_kwargs())
        restored = RawPayload.model_validate_json(rp.model_dump_json())
        assert restored == rp


# ===========================================================================
# Listing
# ===========================================================================


class TestListing:
    def _valid_kwargs(self):
        return dict(
            source="airbnb",
            source_listing_id="12345678",
            name="Cosy flat in Alfama",
            lat=38.713,
            lon=-9.134,
            bedrooms=1,
            beds=2,
            baths=1.0,
            max_guests=4,
            rating=4.8,
            review_count=120,
            amenities=["WiFi", "Kitchen"],
            images=["https://example.com/img1.jpg"],
            url="https://www.airbnb.com/rooms/12345678",
            host_or_brand="João",
            first_seen_at=NOW,
            last_seen_at=NOW,
        )

    def test_valid(self):
        listing = Listing(**self._valid_kwargs())
        assert listing.source == "airbnb"
        assert listing.rating == 4.8

    def test_invalid_rating_too_high(self):
        kwargs = self._valid_kwargs()
        kwargs["rating"] = 5.1
        with pytest.raises(ValidationError):
            Listing(**kwargs)

    def test_invalid_lat_out_of_range(self):
        kwargs = self._valid_kwargs()
        kwargs["lat"] = 95.0
        with pytest.raises(ValidationError):
            Listing(**kwargs)

    def test_invalid_rating_string(self):
        kwargs = self._valid_kwargs()
        kwargs["rating"] = "good"  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            Listing(**kwargs)

    def test_invalid_bedrooms_negative(self):
        kwargs = self._valid_kwargs()
        kwargs["bedrooms"] = -1
        with pytest.raises(ValidationError):
            Listing(**kwargs)

    def test_last_seen_before_first_seen_raises(self):
        kwargs = self._valid_kwargs()
        kwargs["last_seen_at"] = datetime(2024, 1, 1, tzinfo=timezone.utc)
        kwargs["first_seen_at"] = datetime(2024, 6, 1, tzinfo=timezone.utc)
        with pytest.raises(ValidationError):
            Listing(**kwargs)

    def test_amenities_defaults_to_empty_list(self):
        kwargs = self._valid_kwargs()
        del kwargs["amenities"]
        listing = Listing(**kwargs)
        assert listing.amenities == []

    def test_round_trip_json(self):
        listing = Listing(**self._valid_kwargs())
        restored = Listing.model_validate_json(listing.model_dump_json())
        assert restored == listing


# ===========================================================================
# ListingSnapshot
# ===========================================================================


class TestListingSnapshot:
    def _valid_kwargs(self):
        return dict(
            listing_id=1,
            run_id=1,
            nightly_price=95.50,
            currency="EUR",
            total_price=477.50,
            fees={"cleaning_fee": 40.0, "service_fee": 37.50},
            availability=True,
            captured_at=NOW,
        )

    def test_valid(self):
        snap = ListingSnapshot(**self._valid_kwargs())
        assert snap.currency == "EUR"
        assert snap.availability is True

    def test_invalid_nightly_price_negative(self):
        kwargs = self._valid_kwargs()
        kwargs["nightly_price"] = -10.0
        with pytest.raises(ValidationError):
            ListingSnapshot(**kwargs)

    def test_invalid_nightly_price_string(self):
        kwargs = self._valid_kwargs()
        kwargs["nightly_price"] = "cheap"  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            ListingSnapshot(**kwargs)

    def test_currency_too_short(self):
        kwargs = self._valid_kwargs()
        kwargs["currency"] = "EU"
        with pytest.raises(ValidationError):
            ListingSnapshot(**kwargs)

    def test_fees_defaults_to_empty_dict(self):
        kwargs = self._valid_kwargs()
        del kwargs["fees"]
        snap = ListingSnapshot(**kwargs)
        assert snap.fees == {}

    def test_round_trip_json(self):
        snap = ListingSnapshot(**self._valid_kwargs())
        restored = ListingSnapshot.model_validate_json(snap.model_dump_json())
        assert restored == snap


# ===========================================================================
# SearchRun
# ===========================================================================


class TestSearchRun:
    def _valid_kwargs(self):
        return dict(
            area_query="Lisbon, Portugal",
            checkin=CHECKIN,
            checkout=CHECKOUT,
            guests=2,
            sources=["airbnb", "booking"],
            started_at=NOW,
            status="running",
        )

    def test_valid_in_progress(self):
        run = SearchRun(**self._valid_kwargs())
        assert run.status == "running"
        assert run.finished_at is None

    def test_valid_completed(self):
        kwargs = self._valid_kwargs()
        kwargs["finished_at"] = LATER
        kwargs["status"] = "completed"
        run = SearchRun(**kwargs)
        assert run.finished_at == LATER

    def test_invalid_status(self):
        kwargs = self._valid_kwargs()
        kwargs["status"] = "pending"
        with pytest.raises(ValidationError):
            SearchRun(**kwargs)

    def test_invalid_guests_string(self):
        kwargs = self._valid_kwargs()
        kwargs["guests"] = "two"  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            SearchRun(**kwargs)

    def test_finished_before_started_raises(self):
        kwargs = self._valid_kwargs()
        kwargs["finished_at"] = datetime(2024, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(ValidationError):
            SearchRun(**kwargs)

    def test_stats_defaults_to_empty_dict(self):
        run = SearchRun(**self._valid_kwargs())
        assert run.stats == {}

    def test_round_trip_json(self):
        kwargs = self._valid_kwargs()
        kwargs["finished_at"] = LATER
        kwargs["status"] = "completed"
        run = SearchRun(**kwargs)
        restored = SearchRun.model_validate_json(run.model_dump_json())
        assert restored == run


# ===========================================================================
# ExtractionLog
# ===========================================================================


class TestExtractionLog:
    def _valid_kwargs(self):
        return dict(
            raw_scrape_id=42,
            model="claude-opus-4-8",
            input_tokens=1500,
            output_tokens=300,
            cache_read_tokens=800,
            status="success",
        )

    def test_valid_success(self):
        log = ExtractionLog(**self._valid_kwargs())
        assert log.status == "success"
        assert log.error is None

    def test_valid_failed_with_error(self):
        kwargs = self._valid_kwargs()
        kwargs["status"] = "failed"
        kwargs["error"] = "Timeout after 30s"
        log = ExtractionLog(**kwargs)
        assert log.error == "Timeout after 30s"

    def test_invalid_status(self):
        kwargs = self._valid_kwargs()
        kwargs["status"] = "partial"
        with pytest.raises(ValidationError):
            ExtractionLog(**kwargs)

    def test_invalid_input_tokens_negative(self):
        kwargs = self._valid_kwargs()
        kwargs["input_tokens"] = -1
        with pytest.raises(ValidationError):
            ExtractionLog(**kwargs)

    def test_invalid_input_tokens_string(self):
        kwargs = self._valid_kwargs()
        kwargs["input_tokens"] = "many"  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            ExtractionLog(**kwargs)

    def test_cache_read_tokens_defaults_to_zero(self):
        kwargs = self._valid_kwargs()
        del kwargs["cache_read_tokens"]
        log = ExtractionLog(**kwargs)
        assert log.cache_read_tokens == 0

    def test_round_trip_json(self):
        log = ExtractionLog(**self._valid_kwargs())
        restored = ExtractionLog.model_validate_json(log.model_dump_json())
        assert restored == log
