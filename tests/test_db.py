"""Tests for the database models and repo layer (LIN-45)."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from db.models import (
    ExtractionLog,
    Listing,
    ListingSnapshot,
    RawScrape,
    SearchRun,
)
from db.repo import create_all, get_engine


# ---------------------------------------------------------------------------
# Table existence
# ---------------------------------------------------------------------------

EXPECTED_TABLES = {
    "search_runs",
    "raw_scrapes",
    "listings",
    "listing_snapshots",
    "extraction_logs",
}


def test_db_all_tables_exist(db_engine):
    """The in-memory DB must contain all five tables after create_all."""
    inspector = inspect(db_engine)
    assert EXPECTED_TABLES == set(inspector.get_table_names())


# ---------------------------------------------------------------------------
# UNIQUE constraint on Listing(source, source_listing_id)
# ---------------------------------------------------------------------------

def test_db_listing_unique_constraint_present(db_engine):
    """listings must have a UNIQUE constraint on (source, source_listing_id)."""
    inspector = inspect(db_engine)
    unique_constraints = inspector.get_unique_constraints("listings")
    # Collect all column sets covered by unique constraints
    covered = [frozenset(uc["column_names"]) for uc in unique_constraints]
    assert frozenset({"source", "source_listing_id"}) in covered, (
        f"Expected UNIQUE(source, source_listing_id) on listings; found: {unique_constraints}"
    )


def test_db_listing_unique_constraint_enforced(db_engine):
    """Inserting a duplicate (source, source_listing_id) must raise an error."""
    from sqlalchemy.exc import IntegrityError

    with Session(db_engine) as session:
        session.add(Listing(source="airbnb", source_listing_id="abc123"))
        session.commit()

    with pytest.raises(IntegrityError):
        with Session(db_engine) as session:
            session.add(Listing(source="airbnb", source_listing_id="abc123"))
            session.commit()


# ---------------------------------------------------------------------------
# Column presence spot-checks
# ---------------------------------------------------------------------------

def test_db_search_run_columns(db_engine):
    inspector = inspect(db_engine)
    col_names = {c["name"] for c in inspector.get_columns("search_runs")}
    assert {"id", "area_query", "checkin", "checkout", "guests", "sources",
            "started_at", "finished_at", "status", "stats"} <= col_names


def test_db_raw_scrape_columns(db_engine):
    inspector = inspect(db_engine)
    col_names = {c["name"] for c in inspector.get_columns("raw_scrapes")}
    assert {"id", "run_id", "source", "url", "payload", "content_hash",
            "fetched_at", "status", "error"} <= col_names


def test_db_listing_columns(db_engine):
    inspector = inspect(db_engine)
    col_names = {c["name"] for c in inspector.get_columns("listings")}
    assert {"id", "source", "source_listing_id", "name", "property_type",
            "lat", "lon", "address_text", "bedrooms", "beds", "baths",
            "max_guests", "rating", "review_count", "amenities", "images",
            "url", "host_or_brand", "first_seen_at", "last_seen_at"} <= col_names


def test_db_listing_snapshot_columns(db_engine):
    inspector = inspect(db_engine)
    col_names = {c["name"] for c in inspector.get_columns("listing_snapshots")}
    assert {"id", "listing_id", "run_id", "nightly_price", "currency",
            "total_price", "fees", "availability", "captured_at"} <= col_names


def test_db_extraction_log_columns(db_engine):
    inspector = inspect(db_engine)
    col_names = {c["name"] for c in inspector.get_columns("extraction_logs")}
    assert {"id", "raw_scrape_id", "model", "input_tokens", "output_tokens",
            "cache_read_tokens", "status", "error"} <= col_names


# ---------------------------------------------------------------------------
# Basic insert / read round-trip
# ---------------------------------------------------------------------------

def test_db_insert_search_run(db_engine):
    with Session(db_engine) as session:
        run = SearchRun(area_query="Lisbon, Portugal", status="running")
        session.add(run)
        session.commit()
        assert run.id is not None

    with Session(db_engine) as session:
        fetched = session.get(SearchRun, run.id)
        assert fetched is not None
        assert fetched.area_query == "Lisbon, Portugal"


def test_db_content_hash_unique(db_engine):
    """content_hash column on raw_scrapes must be unique."""
    from sqlalchemy.exc import IntegrityError

    with Session(db_engine) as session:
        session.add(RawScrape(source="airbnb", url="https://a.co/1",
                               content_hash="deadbeef", status="pending"))
        session.commit()

    with pytest.raises(IntegrityError):
        with Session(db_engine) as session:
            session.add(RawScrape(source="airbnb", url="https://a.co/2",
                                   content_hash="deadbeef", status="pending"))
            session.commit()
