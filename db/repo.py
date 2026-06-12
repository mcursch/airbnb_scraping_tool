"""Repository layer — upserts and queries."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db.models import SearchRun, get_session


def create_search_run(
    area_query: str,
    checkin: str | None,
    checkout: str | None,
    guests: int,
    sources: str,
) -> int:
    """Insert a new SearchRun row and return its integer primary-key id."""
    with get_session() as session:
        run = SearchRun(
            area_query=area_query,
            checkin=checkin,
            checkout=checkout,
            guests=guests,
            sources=sources,
            status="running",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
        return run_id


def finish_search_run(run_id: int, status: str = "done", stats: dict | None = None) -> None:
    """Mark a SearchRun as finished."""
    with get_session() as session:
        run = session.get(SearchRun, run_id)
        if run is None:
            return
        run.finished_at = datetime.now(timezone.utc)
        run.status = status
        if stats is not None:
            run.stats = stats
        session.commit()


def list_search_runs(limit: int = 50) -> list[dict]:
    """Return recent SearchRun rows as plain dicts for display."""
    with get_session() as session:
        runs = (
            session.query(SearchRun)
            .order_by(SearchRun.started_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "area_query": r.area_query,
                "checkin": r.checkin,
                "checkout": r.checkout,
                "guests": r.guests,
                "sources": r.sources,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "status": r.status,
                "stats": r.stats,
            }
            for r in runs
        ]
