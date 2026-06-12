"""Tests for the run-history cost rollup in db/repo.py."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, ExtractionLog, RawScrape, SearchRun
from db.repo import get_all_runs_with_cost
from config import settings


@pytest.fixture()
def session():
    """In-memory SQLite session, schema created fresh for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()


def _make_run(session, area_query: str, listing_count: int = 0, **kwargs) -> SearchRun:
    run = SearchRun(
        area_query=area_query,
        started_at=kwargs.get("started_at", datetime(2024, 1, 1, 12, 0, 0)),
        status=kwargs.get("status", "done"),
        stats={"listing_count": listing_count},
    )
    session.add(run)
    session.flush()
    return run


def _make_scrape(session, run: SearchRun) -> RawScrape:
    scrape = RawScrape(run_id=run.id, source="airbnb", url="https://airbnb.com/test")
    session.add(scrape)
    session.flush()
    return scrape


def _make_extraction_log(
    session,
    scrape: RawScrape,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
) -> ExtractionLog:
    log = ExtractionLog(
        raw_scrape_id=scrape.id,
        model="claude-opus-4-8",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        status="ok",
    )
    session.add(log)
    session.flush()
    return log


# ── Zero extraction logs ──────────────────────────────────────────────────────

def test_zero_extraction_logs_cost_is_zero(session):
    """A run with no ExtractionLog rows must report $0.00 without raising."""
    _make_run(session, "Lisbon, Portugal", listing_count=0)
    session.commit()

    results = get_all_runs_with_cost(session)

    assert len(results) == 1
    assert results[0]["estimated_cost_usd"] == 0.0


def test_zero_extraction_logs_columns_present(session):
    """All required columns must be present even for a cost-less run."""
    _make_run(session, "Paris, France", listing_count=5, status="done")
    session.commit()

    results = get_all_runs_with_cost(session)
    row = results[0]

    for col in ("area_query", "started_at", "status", "listing_count", "estimated_cost_usd"):
        assert col in row, f"Missing column: {col}"

    assert row["area_query"] == "Paris, France"
    assert row["listing_count"] == 5
    assert row["status"] == "done"


# ── Cost calculation ──────────────────────────────────────────────────────────

def test_cost_calculation_matches_manual(session):
    """estimated_cost_usd must equal the formula within floating-point precision."""
    run = _make_run(session, "Tokyo, Japan")
    scrape = _make_scrape(session, run)
    _make_extraction_log(
        session, scrape,
        input_tokens=10_000,
        output_tokens=2_000,
        cache_read_tokens=5_000,
    )
    session.commit()

    results = get_all_runs_with_cost(session)
    row = results[0]

    input_price = settings.CLAUDE_OPUS_4_8_INPUT_PRICE_PER_MTOK
    output_price = settings.CLAUDE_OPUS_4_8_OUTPUT_PRICE_PER_MTOK
    cache_read_price = settings.CLAUDE_OPUS_4_8_CACHE_READ_PRICE_PER_MTOK

    expected = (
        10_000 * input_price
        + 2_000 * output_price
        - 5_000 * cache_read_price
    ) / 1_000_000

    assert row["estimated_cost_usd"] == pytest.approx(expected, rel=1e-9)


def test_cost_aggregated_across_multiple_scrapes(session):
    """Token counts from multiple ExtractionLog rows must be summed correctly."""
    run = _make_run(session, "Barcelona, Spain")
    scrape1 = _make_scrape(session, run)
    scrape2 = _make_scrape(session, run)

    _make_extraction_log(session, scrape1, input_tokens=1_000, output_tokens=500, cache_read_tokens=200)
    _make_extraction_log(session, scrape2, input_tokens=2_000, output_tokens=800, cache_read_tokens=600)
    session.commit()

    results = get_all_runs_with_cost(session)
    row = results[0]

    assert row["total_input_tokens"] == 3_000
    assert row["total_output_tokens"] == 1_300
    assert row["total_cache_read_tokens"] == 800

    expected = (
        3_000 * settings.CLAUDE_OPUS_4_8_INPUT_PRICE_PER_MTOK
        + 1_300 * settings.CLAUDE_OPUS_4_8_OUTPUT_PRICE_PER_MTOK
        - 800 * settings.CLAUDE_OPUS_4_8_CACHE_READ_PRICE_PER_MTOK
    ) / 1_000_000

    assert row["estimated_cost_usd"] == pytest.approx(expected, rel=1e-9)


# ── Ordering ──────────────────────────────────────────────────────────────────

def test_runs_ordered_by_started_at_descending(session):
    """Runs must be returned newest first."""
    _make_run(session, "Rome, Italy", started_at=datetime(2024, 1, 1))
    _make_run(session, "Milan, Italy", started_at=datetime(2024, 3, 1))
    _make_run(session, "Venice, Italy", started_at=datetime(2024, 2, 1))
    session.commit()

    results = get_all_runs_with_cost(session)

    assert results[0]["area_query"] == "Milan, Italy"
    assert results[1]["area_query"] == "Venice, Italy"
    assert results[2]["area_query"] == "Rome, Italy"


# ── listing_count from stats JSON ─────────────────────────────────────────────

def test_listing_count_read_from_stats_json(session):
    """listing_count must come from the stats JSON field."""
    _make_run(session, "Amsterdam, Netherlands", listing_count=42)
    session.commit()

    results = get_all_runs_with_cost(session)

    assert results[0]["listing_count"] == 42


def test_listing_count_defaults_to_zero_when_stats_is_null(session):
    """A run with no stats JSON must return listing_count=0 without error."""
    run = SearchRun(area_query="Dublin, Ireland", status="pending")
    session.add(run)
    session.commit()

    results = get_all_runs_with_cost(session)

    assert results[0]["listing_count"] == 0
