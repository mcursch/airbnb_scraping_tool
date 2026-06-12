"""Tests for the detail panel component and its supporting repo/model helpers."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base, Listing, ListingSnapshot, get_engine
from db.repo import (
    get_latest_snapshot,
    get_listing,
    get_listing_with_latest_snapshot,
    insert_snapshot,
    upsert_listing,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def in_memory_engine():
    """Create a fresh in-memory SQLite engine with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def session(in_memory_engine):
    with Session(in_memory_engine) as s:
        yield s


@pytest.fixture()
def sample_listing(session) -> Listing:
    amenities = ["WiFi", "Kitchen", "Parking", "Pool", "Air conditioning"]
    listing = upsert_listing(
        session,
        {
            "source": "airbnb",
            "source_listing_id": "abc123",
            "name": "Sunny Beachfront Studio",
            "property_type": "Entire apartment",
            "address_text": "Lisbon, Portugal",
            "bedrooms": 1,
            "beds": 2,
            "baths": 1.0,
            "max_guests": 4,
            "rating": 4.8,
            "review_count": 123,
            "amenities": json.dumps(amenities),
            "url": "https://www.airbnb.com/rooms/abc123",
            "host_or_brand": "Maria",
            "first_seen_at": datetime(2024, 1, 1),
            "last_seen_at": datetime(2024, 1, 1),
        },
    )
    session.commit()
    return listing


@pytest.fixture()
def sample_snapshot(session, sample_listing) -> ListingSnapshot:
    snapshot = insert_snapshot(
        session,
        listing_id=sample_listing.id,
        run_id=None,
        snapshot_data={
            "nightly_price": 95.0,
            "currency": "€",
            "total_price": 310.0,
            "fees": json.dumps({"cleaning_fee": 45.0, "service_fee": 30.0}),
            "availability": True,
            "captured_at": datetime(2024, 6, 1),
        },
    )
    session.commit()
    return snapshot


# ---------------------------------------------------------------------------
# db/models tests
# ---------------------------------------------------------------------------


class TestListingModel:
    def test_amenities_list_parses_json(self, sample_listing):
        result = sample_listing.amenities_list
        assert isinstance(result, list)
        assert "WiFi" in result
        assert "Pool" in result

    def test_amenities_list_empty_when_null(self):
        listing = Listing(
            source="airbnb",
            source_listing_id="x",
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        assert listing.amenities_list == []

    def test_amenities_list_empty_on_invalid_json(self):
        listing = Listing(
            source="airbnb",
            source_listing_id="x",
            amenities="not-valid-json",
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        assert listing.amenities_list == []

    def test_amenities_list_empty_when_json_is_not_a_list(self):
        listing = Listing(
            source="airbnb",
            source_listing_id="x",
            amenities=json.dumps({"foo": "bar"}),
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        assert listing.amenities_list == []

    def test_latest_snapshot_returns_most_recent(self, session, sample_listing):
        older = insert_snapshot(
            session,
            sample_listing.id,
            None,
            {
                "nightly_price": 80.0,
                "currency": "€",
                "total_price": 260.0,
                "fees": None,
                "availability": True,
                "captured_at": datetime(2024, 1, 1),
            },
        )
        newer = insert_snapshot(
            session,
            sample_listing.id,
            None,
            {
                "nightly_price": 100.0,
                "currency": "€",
                "total_price": 320.0,
                "fees": None,
                "availability": True,
                "captured_at": datetime(2024, 6, 1),
            },
        )
        session.commit()
        session.expire_all()
        listing = get_listing(session, sample_listing.id)
        assert listing is not None
        assert listing.latest_snapshot is not None
        assert listing.latest_snapshot.nightly_price == 100.0

    def test_latest_snapshot_none_when_no_snapshots(self):
        listing = Listing(
            source="airbnb",
            source_listing_id="empty",
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        assert listing.latest_snapshot is None


class TestListingSnapshotModel:
    def test_fees_dict_parses_json(self, sample_snapshot):
        fees = sample_snapshot.fees_dict
        assert isinstance(fees, dict)
        assert fees["cleaning_fee"] == 45.0
        assert fees["service_fee"] == 30.0

    def test_fees_dict_empty_when_null(self):
        snap = ListingSnapshot(
            listing_id=1,
            captured_at=datetime.utcnow(),
        )
        assert snap.fees_dict == {}

    def test_fees_dict_empty_on_invalid_json(self):
        snap = ListingSnapshot(
            listing_id=1,
            fees="bad-json",
            captured_at=datetime.utcnow(),
        )
        assert snap.fees_dict == {}


# ---------------------------------------------------------------------------
# db/repo tests
# ---------------------------------------------------------------------------


class TestRepo:
    def test_get_listing_returns_listing(self, session, sample_listing):
        result = get_listing(session, sample_listing.id)
        assert result is not None
        assert result.name == "Sunny Beachfront Studio"

    def test_get_listing_returns_none_for_unknown_id(self, session):
        assert get_listing(session, 99999) is None

    def test_get_latest_snapshot(self, session, sample_listing, sample_snapshot):
        result = get_latest_snapshot(session, sample_listing.id)
        assert result is not None
        assert result.nightly_price == 95.0

    def test_get_latest_snapshot_returns_none_when_no_snapshots(self, session, sample_listing):
        assert get_latest_snapshot(session, sample_listing.id) is None

    def test_get_latest_snapshot_picks_newest(self, session, sample_listing):
        insert_snapshot(
            session,
            sample_listing.id,
            None,
            {
                "nightly_price": 50.0,
                "currency": "€",
                "total_price": 150.0,
                "fees": None,
                "availability": True,
                "captured_at": datetime(2023, 1, 1),
            },
        )
        insert_snapshot(
            session,
            sample_listing.id,
            None,
            {
                "nightly_price": 120.0,
                "currency": "€",
                "total_price": 400.0,
                "fees": None,
                "availability": True,
                "captured_at": datetime(2025, 1, 1),
            },
        )
        session.commit()
        result = get_latest_snapshot(session, sample_listing.id)
        assert result is not None
        assert result.nightly_price == 120.0

    def test_get_listing_with_latest_snapshot_returns_pair(
        self, session, sample_listing, sample_snapshot
    ):
        result = get_listing_with_latest_snapshot(session, sample_listing.id)
        assert result is not None
        listing, snapshot = result
        assert listing.id == sample_listing.id
        assert snapshot is not None
        assert snapshot.nightly_price == 95.0

    def test_get_listing_with_latest_snapshot_returns_none_missing(self, session):
        assert get_listing_with_latest_snapshot(session, 99999) is None

    def test_upsert_listing_creates_new(self, session):
        data = {
            "source": "booking",
            "source_listing_id": "hotel42",
            "name": "Hotel Palace",
            "first_seen_at": datetime.utcnow(),
            "last_seen_at": datetime.utcnow(),
        }
        listing = upsert_listing(session, data)
        session.commit()
        assert listing.id is not None
        assert listing.name == "Hotel Palace"

    def test_upsert_listing_updates_existing(self, session, sample_listing):
        data = {
            "source": "airbnb",
            "source_listing_id": "abc123",
            "name": "Updated Name",
            "first_seen_at": sample_listing.first_seen_at,
            "last_seen_at": datetime.utcnow(),
        }
        updated = upsert_listing(session, data)
        session.commit()
        assert updated.id == sample_listing.id
        assert updated.name == "Updated Name"


# ---------------------------------------------------------------------------
# detail_panel rendering tests (mocked Streamlit)
# ---------------------------------------------------------------------------


class TestDetailPanel:
    """Test the render_detail_panel function with Streamlit mocked out."""

    def _mock_st(self):
        """Return a MagicMock that replaces the streamlit module."""
        mock = MagicMock()
        # st.columns must return a pair of context managers
        col = MagicMock()
        col.__enter__ = MagicMock(return_value=col)
        col.__exit__ = MagicMock(return_value=False)
        mock.columns.return_value = (col, col)
        return mock

    def test_renders_listing_details(self, session, sample_listing, sample_snapshot):
        import dashboard.components.detail_panel as dp

        mock_st = self._mock_st()
        with patch.object(dp, "st", mock_st):
            dp.render_detail_panel(listing_id=sample_listing.id, session=session)

        # subheader called with the listing name
        mock_st.subheader.assert_any_call("Sunny Beachfront Studio")

    def test_warns_on_missing_listing(self, session):
        import dashboard.components.detail_panel as dp

        mock_st = self._mock_st()
        with patch.object(dp, "st", mock_st):
            dp.render_detail_panel(listing_id=99999, session=session)

        mock_st.warning.assert_called_once()
        assert "99999" in mock_st.warning.call_args[0][0]

    def test_renders_amenities(self, session, sample_listing, sample_snapshot):
        import dashboard.components.detail_panel as dp

        mock_st = self._mock_st()
        with patch.object(dp, "st", mock_st):
            dp.render_detail_panel(listing_id=sample_listing.id, session=session)

        # _render_amenities calls st.markdown("**✅ Amenities**")
        amenities_calls = [
            call
            for call in mock_st.markdown.call_args_list
            if "Amenities" in str(call)
        ]
        assert amenities_calls, "Expected amenities header to be rendered"

    def test_no_amenities_section_when_empty(self, session):
        """When amenities is null/empty the section should be skipped."""
        listing = upsert_listing(
            session,
            {
                "source": "airbnb",
                "source_listing_id": "no-amenities",
                "name": "Bare Bones",
                "amenities": None,
                "first_seen_at": datetime.utcnow(),
                "last_seen_at": datetime.utcnow(),
            },
        )
        session.commit()

        import dashboard.components.detail_panel as dp

        mock_st = self._mock_st()
        with patch.object(dp, "st", mock_st):
            dp.render_detail_panel(listing_id=listing.id, session=session)

        amenities_calls = [
            call
            for call in mock_st.markdown.call_args_list
            if "Amenities" in str(call)
        ]
        assert not amenities_calls, "Amenities section should be absent when field is empty"

    def test_renders_price_from_snapshot(self, session, sample_listing, sample_snapshot):
        import dashboard.components.detail_panel as dp

        mock_st = self._mock_st()
        with patch.object(dp, "st", mock_st):
            dp.render_detail_panel(listing_id=sample_listing.id, session=session)

        price_calls = [
            call
            for call in mock_st.markdown.call_args_list
            if "95" in str(call) or "310" in str(call)
        ]
        assert price_calls, "Expected nightly or total price to appear in rendered output"

    def test_renders_url_link(self, session, sample_listing, sample_snapshot):
        import dashboard.components.detail_panel as dp

        mock_st = self._mock_st()
        with patch.object(dp, "st", mock_st):
            dp.render_detail_panel(listing_id=sample_listing.id, session=session)

        url_calls = [
            call
            for call in mock_st.markdown.call_args_list
            if "airbnb.com/rooms/abc123" in str(call)
        ]
        assert url_calls, "Expected listing URL to be rendered as a link"

    def test_renders_no_price_message_without_snapshot(self, session, sample_listing):
        """When no snapshot exists, the panel should still render (no crash)."""
        import dashboard.components.detail_panel as dp

        mock_st = self._mock_st()
        with patch.object(dp, "st", mock_st):
            dp.render_detail_panel(listing_id=sample_listing.id, session=session)

        price_calls = [
            call
            for call in mock_st.markdown.call_args_list
            if "No pricing data" in str(call)
        ]
        assert price_calls, "Expected 'No pricing data' message when snapshot is absent"
