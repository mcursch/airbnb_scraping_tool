"""Tests for content-hash deduplication in pipeline.py (LIN-37).

Acceptance criteria verified here
----------------------------------
1. Two pipeline runs with identical payloads (same content_hash):
   - ExtractionLog with a real model is created exactly once.
   - Second run produces an ExtractionLog with status='dedup' and zero tokens.
   - Two ListingSnapshot rows exist after both runs.

2. Two pipeline runs with different payloads (different content_hash):
   - The extractor is called twice (mocked call count == 2).

The extractor is mocked throughout so no real LLM is required.
"""

import hashlib
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base, ExtractionLog, ListingSnapshot, RawScrape, SearchRun
from extraction.extractor import ExtractionResult
from pipeline import process_raw_scrape


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session():
    """In-memory SQLite session with all tables created."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make_run(session: Session, area: str = "Lisbon") -> SearchRun:
    run = SearchRun(area_query=area, sources="airbnb")
    session.add(run)
    session.flush()
    return run


def _make_raw_scrape(session: Session, run: SearchRun, payload: str) -> RawScrape:
    content_hash = hashlib.sha256(payload.encode()).hexdigest()
    scrape = RawScrape(
        run_id=run.id,
        source="airbnb",
        url="https://airbnb.com/rooms/1",
        payload=payload,
        content_hash=content_hash,
    )
    session.add(scrape)
    session.flush()
    return scrape


def _make_extractor(call_count_tracker: list | None = None) -> MagicMock:
    """Return a mock extractor that records calls and returns a fixed result."""
    extractor = MagicMock()
    call_count = call_count_tracker if call_count_tracker is not None else []

    def _extract(raw_scrape: RawScrape) -> ExtractionResult:
        call_count.append(1)
        return ExtractionResult(
            listing_data={
                "source": raw_scrape.source,
                "source_listing_id": "listing-001",
                "name": "Cosy Lisbon flat",
            },
            model="claude-opus-4-8",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=0,
        )

    extractor.extract.side_effect = _extract
    return extractor, call_count


# ---------------------------------------------------------------------------
# Test 1: identical payloads → dedup on second run
# ---------------------------------------------------------------------------


def test_same_payload_second_run_is_dedup(session: Session):
    """Two runs with the same payload: only one real extraction, one dedup entry."""
    payload = '{"listing_id": "1", "name": "Cosy flat"}'
    extractor, calls = _make_extractor()

    # --- Run 1 ---
    run1 = _make_run(session, area="Lisbon run 1")
    scrape1 = _make_raw_scrape(session, run1, payload)
    log1 = process_raw_scrape(session, scrape1, extractor)
    session.commit()

    # --- Run 2 (identical payload, different SearchRun) ---
    run2 = _make_run(session, area="Lisbon run 2")
    scrape2 = _make_raw_scrape(session, run2, payload)
    log2 = process_raw_scrape(session, scrape2, extractor)
    session.commit()

    # Extractor called only once (real LLM skipped on second run).
    assert len(calls) == 1, f"Expected extractor to be called once, got {len(calls)}"

    # First log has real status and non-zero tokens.
    assert log1.status == "extracted"
    assert log1.model == "claude-opus-4-8"
    assert log1.input_tokens == 100
    assert log1.output_tokens == 50

    # Second log is a dedup record with zero token counts.
    assert log2.status == "dedup"
    assert log2.model is None
    assert log2.input_tokens == 0
    assert log2.output_tokens == 0
    assert log2.cache_read_tokens == 0

    # Dedup log still points at the same listing as the original extraction.
    assert log2.listing_id == log1.listing_id


def test_same_payload_produces_two_snapshots(session: Session):
    """Even when dedup fires, both runs produce a ListingSnapshot row."""
    payload = '{"listing_id": "2", "name": "River view apartment"}'
    extractor, _ = _make_extractor()

    run1 = _make_run(session, area="Porto run 1")
    scrape1 = _make_raw_scrape(session, run1, payload)
    process_raw_scrape(session, scrape1, extractor)
    session.commit()

    run2 = _make_run(session, area="Porto run 2")
    scrape2 = _make_raw_scrape(session, run2, payload)
    process_raw_scrape(session, scrape2, extractor)
    session.commit()

    snapshots = session.query(ListingSnapshot).all()
    assert len(snapshots) == 2, f"Expected 2 snapshots, got {len(snapshots)}"

    run_ids = {s.run_id for s in snapshots}
    assert run_ids == {run1.id, run2.id}, "Each run should have its own snapshot"


# ---------------------------------------------------------------------------
# Test 2: different payloads → extractor called twice
# ---------------------------------------------------------------------------


def test_different_payloads_extractor_called_twice(session: Session):
    """Two runs with different payloads: extractor must be invoked for each."""
    payload_a = '{"listing_id": "10", "name": "Sunny studio"}'
    payload_b = '{"listing_id": "11", "name": "Rainy loft"}'

    extractor_a = MagicMock()
    calls: list[int] = []

    def _extract_a(raw_scrape: RawScrape) -> ExtractionResult:
        calls.append(1)
        # Return a different source_listing_id per payload so upsert creates
        # distinct Listing rows rather than colliding on the unique constraint.
        lid = "listing-a" if "Sunny" in raw_scrape.payload else "listing-b"
        return ExtractionResult(
            listing_data={"source": "airbnb", "source_listing_id": lid, "name": "Test"},
            model="claude-opus-4-8",
            input_tokens=200,
            output_tokens=80,
        )

    extractor_a.extract.side_effect = _extract_a

    run1 = _make_run(session, area="Madrid run 1")
    scrape1 = _make_raw_scrape(session, run1, payload_a)
    process_raw_scrape(session, scrape1, extractor_a)
    session.commit()

    run2 = _make_run(session, area="Madrid run 2")
    scrape2 = _make_raw_scrape(session, run2, payload_b)
    process_raw_scrape(session, scrape2, extractor_a)
    session.commit()

    assert len(calls) == 2, f"Extractor should be called twice for different hashes, got {len(calls)}"

    logs = session.query(ExtractionLog).all()
    assert all(log.status == "extracted" for log in logs)
    assert all(log.input_tokens == 200 for log in logs)


# ---------------------------------------------------------------------------
# Additional: dedup log references same listing_id as original
# ---------------------------------------------------------------------------


def test_dedup_log_listing_id_matches_original(session: Session):
    """The listing_id on the dedup ExtractionLog must equal the original one."""
    payload = '{"listing_id": "99", "name": "Castle suite"}'
    extractor, _ = _make_extractor()

    run1 = _make_run(session)
    log1 = process_raw_scrape(session, _make_raw_scrape(session, run1, payload), extractor)
    session.commit()

    run2 = _make_run(session)
    log2 = process_raw_scrape(session, _make_raw_scrape(session, run2, payload), extractor)
    session.commit()

    assert log1.listing_id is not None
    assert log2.listing_id == log1.listing_id
