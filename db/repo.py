"""Repository layer — thin wrappers over the SQLAlchemy session.

All public functions accept a SQLAlchemy Session and a data dict / ORM
object so they can be used from any context (CLI, pipeline, tests).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from db.models import RawScrape, SearchRun

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ── SearchRun ─────────────────────────────────────────────────────────────────

def create_search_run(
    session: "Session",
    *,
    area_query: str,
    sources: list[str],
    checkin: str | None = None,
    checkout: str | None = None,
    guests: int | None = None,
) -> SearchRun:
    run = SearchRun(
        area_query=area_query,
        sources=",".join(sources),
        checkin=checkin,
        checkout=checkout,
        guests=guests,
        status="running",
    )
    session.add(run)
    session.commit()
    return run


def finish_search_run(
    session: "Session",
    run: SearchRun,
    *,
    status: str = "done",
    stats: str | None = None,
) -> SearchRun:
    run.status = status
    run.finished_at = datetime.now(timezone.utc)
    run.stats = stats
    session.commit()
    return run


# ── RawScrape ─────────────────────────────────────────────────────────────────

def create_raw_scrape(
    session: "Session",
    *,
    source: str,
    url: str,
    payload: str,
    run_id: int | None = None,
    status: str = "pending",
    page_number: int | None = None,
) -> RawScrape | None:
    """Persist a raw captured payload.

    Returns the saved ``RawScrape`` row, or ``None`` if the content hash
    already exists in the database (idempotent duplicate skip).
    """
    content_hash = RawScrape.compute_hash(payload)
    row = RawScrape(
        run_id=run_id,
        source=source,
        url=url,
        payload=payload,
        content_hash=content_hash,
        status=status,
        page_number=page_number,
    )
    session.add(row)
    try:
        session.commit()
        return row
    except IntegrityError:
        session.rollback()
        return None  # duplicate hash — same payload seen before


def get_raw_scrapes(
    session: "Session",
    *,
    run_id: int | None = None,
    source: str | None = None,
    status: str | None = None,
) -> list[RawScrape]:
    q = session.query(RawScrape)
    if run_id is not None:
        q = q.filter(RawScrape.run_id == run_id)
    if source is not None:
        q = q.filter(RawScrape.source == source)
    if status is not None:
        q = q.filter(RawScrape.status == status)
    return q.order_by(RawScrape.id).all()
