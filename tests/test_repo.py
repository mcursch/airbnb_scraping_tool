"""Tests for the db/repo.py write-path methods.

All tests use an in-memory SQLite database to remain self-contained and fast.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from db.models import Base, Listing, ListingSnapshot, SearchRun
from db.repo import (
    close_search_run,
    create_listing_snapshot,
    create_search_run,
    record_run_stats,
    upsert_listing,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    """In-memory SQLite engine with all tables created fresh per test."""
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    """Provide a session that is rolled back after each test."""
    with Session(engine) as sess:
        yield sess
        sess.rollback()


@pytest.fixture()
def search_run(session) -> SearchRun:
    """A persisted SearchRun available to tests that need one."""
    run = create_search_run(session, area_query="Lisbon, Portugal", sources=["airbnb"])
    session.commit()
    return run


def _make_listing(**overrides) -> Listing:
    """Return a minimal Listing instance (not yet added to any session)."""
    defaults = dict(
        source="airbnb",
        source_listing_id="abc123",
        name="Sunny Studio",
        rating=4.8,
        review_count=42,
        nightly_price=None,  # snapshot field, not on Listing
    )
    defaults.update(overrides)
    # ``nightly_price`` doesn't belong on Listing — drop it if callers forgot.
    defaults.pop("nightly_price", None)
    return Listing(**defaults)


# ---------------------------------------------------------------------------
# upsert_listing
# ---------------------------------------------------------------------------


class TestUpsertListing:
    def test_insert_creates_one_row(self, session):
        listing = _make_listing()
        upsert_listing(session, listing)
        session.commit()

        count = session.execute(select(func.count()).select_from(Listing)).scalar_one()
        assert count == 1

    def test_upsert_twice_yields_exactly_one_row(self, session):
        """Upserting the same (source, source_listing_id) twice must not duplicate."""
        listing1 = _make_listing(name="Version A")
        upsert_listing(session, listing1)
        session.commit()

        listing2 = _make_listing(name="Version B")  # same source/source_listing_id
        upsert_listing(session, listing2)
        session.commit()

        count = session.execute(select(func.count()).select_from(Listing)).scalar_one()
        assert count == 1

    def test_upsert_updates_last_seen_at(self, session):
        """last_seen_at must be refreshed on the second upsert."""
        listing1 = _make_listing()
        row1 = upsert_listing(session, listing1)
        session.commit()
        first_seen = row1.first_seen_at
        last_seen_after_insert = row1.last_seen_at

        # Small sleep so the clock advances before the second upsert.
        time.sleep(0.05)

        listing2 = _make_listing(name="Updated Name")
        row2 = upsert_listing(session, listing2)
        session.commit()

        assert row2.last_seen_at > last_seen_after_insert, (
            "last_seen_at should have been bumped on the second upsert"
        )

    def test_upsert_preserves_first_seen_at(self, session):
        """first_seen_at must never change after the initial insert."""
        listing1 = _make_listing()
        row1 = upsert_listing(session, listing1)
        session.commit()
        original_first_seen = row1.first_seen_at

        time.sleep(0.05)

        listing2 = _make_listing(name="Updated Name")
        row2 = upsert_listing(session, listing2)
        session.commit()

        assert row2.first_seen_at == original_first_seen

    def test_upsert_updates_mutable_field(self, session):
        """A mutable field (e.g. name) must be overwritten on the second upsert."""
        upsert_listing(session, _make_listing(name="Old Name"))
        session.commit()

        upsert_listing(session, _make_listing(name="New Name"))
        session.commit()

        row = session.execute(select(Listing)).scalar_one()
        assert row.name == "New Name"

    def test_upsert_returns_listing_instance(self, session):
        result = upsert_listing(session, _make_listing())
        assert isinstance(result, Listing)
        assert result.id is not None


# ---------------------------------------------------------------------------
# create_listing_snapshot
# ---------------------------------------------------------------------------


class TestCreateListingSnapshot:
    def test_snapshot_is_inserted(self, session, search_run):
        listing = upsert_listing(session, _make_listing())
        session.commit()

        snap = ListingSnapshot(listing_id=listing.id, run_id=search_run.id, nightly_price=99.0)
        create_listing_snapshot(session, snap)
        session.commit()

        count = session.execute(
            select(func.count()).select_from(ListingSnapshot)
        ).scalar_one()
        assert count == 1

    def test_two_snapshots_for_same_listing_different_runs(self, session):
        """Each run must produce its own snapshot row — no dedup."""
        listing = upsert_listing(session, _make_listing())
        session.commit()

        run1 = create_search_run(session, area_query="Lisbon")
        session.commit()
        run2 = create_search_run(session, area_query="Lisbon")
        session.commit()

        create_listing_snapshot(
            session,
            ListingSnapshot(listing_id=listing.id, run_id=run1.id, nightly_price=100.0),
        )
        create_listing_snapshot(
            session,
            ListingSnapshot(listing_id=listing.id, run_id=run2.id, nightly_price=110.0),
        )
        session.commit()

        count = session.execute(
            select(func.count()).select_from(ListingSnapshot)
        ).scalar_one()
        assert count == 2

    def test_snapshot_captured_at_defaults_to_now(self, session, search_run):
        listing = upsert_listing(session, _make_listing())
        session.commit()

        before = datetime.utcnow()
        snap = ListingSnapshot(listing_id=listing.id, run_id=search_run.id)
        result = create_listing_snapshot(session, snap)
        session.commit()
        after = datetime.utcnow()

        assert result.captured_at is not None
        assert before <= result.captured_at <= after

    def test_snapshot_returns_instance_with_id(self, session, search_run):
        listing = upsert_listing(session, _make_listing())
        session.commit()

        snap = ListingSnapshot(listing_id=listing.id, run_id=search_run.id)
        result = create_listing_snapshot(session, snap)
        assert isinstance(result, ListingSnapshot)
        assert result.id is not None


# ---------------------------------------------------------------------------
# record_run_stats / close_search_run
# ---------------------------------------------------------------------------


class TestRecordRunStats:
    def test_finished_at_is_set(self, session, search_run):
        assert search_run.finished_at is None

        before = datetime.utcnow()
        record_run_stats(session, search_run, stats={"listings_found": 5})
        session.commit()
        after = datetime.utcnow()

        assert search_run.finished_at is not None
        assert before <= search_run.finished_at <= after

    def test_stats_json_contains_expected_keys(self, session, search_run):
        expected_stats = {
            "listings_found": 10,
            "snapshots_created": 10,
            "extraction_errors": 0,
        }
        record_run_stats(session, search_run, stats=expected_stats)
        session.commit()

        # Re-fetch to confirm persistence.
        session.expire(search_run)
        refreshed = session.get(SearchRun, search_run.id)
        assert refreshed.stats == expected_stats
        for key in expected_stats:
            assert key in refreshed.stats

    def test_status_set_to_completed(self, session, search_run):
        assert search_run.status == "running"
        record_run_stats(session, search_run, stats={})
        session.commit()
        assert search_run.status == "completed"

    def test_custom_finished_at_is_respected(self, session, search_run):
        custom_ts = datetime(2025, 1, 15, 12, 0, 0)
        record_run_stats(session, search_run, stats={}, finished_at=custom_ts)
        session.commit()
        assert search_run.finished_at == custom_ts

    def test_returns_search_run_instance(self, session, search_run):
        result = record_run_stats(session, search_run, stats={"ok": True})
        assert isinstance(result, SearchRun)
        assert result is search_run


class TestCreateSearchRun:
    def test_creates_run_with_running_status(self, session):
        run = create_search_run(session, area_query="Porto")
        session.commit()
        assert run.status == "running"
        assert run.id is not None
        assert run.area_query == "Porto"
        assert run.finished_at is None

    def test_started_at_defaults_to_now(self, session):
        before = datetime.utcnow()
        run = create_search_run(session, area_query="Porto")
        session.commit()
        after = datetime.utcnow()
        assert before <= run.started_at <= after


class TestCloseSearchRun:
    def test_close_sets_finished_at(self, session):
        run = create_search_run(session, area_query="Porto")
        session.commit()

        before = datetime.utcnow()
        close_search_run(session, run, stats={"listings_found": 3})
        session.commit()
        after = datetime.utcnow()

        assert before <= run.finished_at <= after
        assert run.stats == {"listings_found": 3}

    def test_close_with_no_stats_defaults_to_empty_dict(self, session):
        run = create_search_run(session, area_query="Porto")
        session.commit()
        close_search_run(session, run)
        session.commit()
        assert run.stats == {}
