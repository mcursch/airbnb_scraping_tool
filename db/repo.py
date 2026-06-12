"""Database repository: engine setup and query helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker

from config import settings
from db.models import Base, ExtractionLog, RawScrape, SearchRun

_connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    """Create all tables if they don't exist yet."""
    Base.metadata.create_all(engine)


def get_session() -> Session:
    """Return a new database session (caller is responsible for closing it)."""
    return SessionLocal()


def get_all_runs_with_cost(session: Session) -> list[dict[str, Any]]:
    """Return all SearchRun rows ordered by started_at descending.

    Each row is enriched with aggregated token counts from ExtractionLog and
    an estimated cost using the hardcoded claude-opus-4-8 prices from config.

    Cost formula (matching Anthropic's billing):
        cost = (input_tokens × INPUT_PRICE
                + output_tokens × OUTPUT_PRICE
                + cache_read_tokens × CACHE_READ_PRICE) / 1_000_000

    Runs with no extraction logs produce a cost of $0.00 without error.
    """
    input_price = settings.CLAUDE_OPUS_4_8_INPUT_PRICE_PER_MTOK
    output_price = settings.CLAUDE_OPUS_4_8_OUTPUT_PRICE_PER_MTOK
    cache_read_price = settings.CLAUDE_OPUS_4_8_CACHE_READ_PRICE_PER_MTOK

    rows = (
        session.query(
            SearchRun,
            func.coalesce(func.sum(ExtractionLog.input_tokens), 0).label("total_input"),
            func.coalesce(func.sum(ExtractionLog.output_tokens), 0).label("total_output"),
            func.coalesce(func.sum(ExtractionLog.cache_read_tokens), 0).label("total_cache_read"),
        )
        .outerjoin(RawScrape, RawScrape.run_id == SearchRun.id)
        .outerjoin(ExtractionLog, ExtractionLog.raw_scrape_id == RawScrape.id)
        .group_by(SearchRun.id)
        .order_by(SearchRun.started_at.desc())
        .all()
    )

    results: list[dict[str, Any]] = []
    for run, total_input, total_output, total_cache_read in rows:
        estimated_cost = (
            total_input * input_price
            + total_output * output_price
            + total_cache_read * cache_read_price
        ) / 1_000_000

        listing_count: int = (run.stats or {}).get("listing_count", 0)

        results.append(
            {
                "id": run.id,
                "area_query": run.area_query,
                "started_at": run.started_at,
                "status": run.status,
                "listing_count": listing_count,
                "estimated_cost_usd": estimated_cost,
                # Raw token totals (available to callers that need them)
                "total_input_tokens": int(total_input),
                "total_output_tokens": int(total_output),
                "total_cache_read_tokens": int(total_cache_read),
            }
        )

    return results
