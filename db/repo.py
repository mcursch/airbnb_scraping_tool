"""Repository layer — all write-path helpers for the Short-Stay Market Scanner.

Each function accepts and returns SQLAlchemy model instances defined in
``db/models.py``.  Callers are responsible for session lifecycle (begin /
commit / rollback).  Functions call ``session.flush()`` so that generated
primary keys are available on return without requiring the caller to commit
immediately.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from db.models import Listing, ListingSnapshot, SearchRun

# ---------------------------------------------------------------------------
# Fields that may change between scrapes and should be refreshed on upsert.
# ``first_seen_at`` is intentionally excluded — it is set once and preserved.
# ---------------------------------------------------------------------------
_MUTABLE_LISTING_FIELDS: tuple[str, ...] = (
    "name",
    "property_type",
    "lat",
    "lon",
    "address_text",
    "bedrooms",
    "beds",
    "baths",
    "max_guests",
    "rating",
    "review_count",
    "amenities",
    "images",
    "url",
    "host_or_brand",
)


# ---------------------------------------------------------------------------
# SearchRun helpers
# ---------------------------------------------------------------------------


def create_search_run(
    session: Session,
    area_query: str,
    *,
    checkin: str | None = None,
    checkout: str | None = None,
    guests: int | None = None,
    sources: list[str] | None = None,
) -> SearchRun:
    """Insert a new :class:`SearchRun` and return it (flushed, not committed).

    ``started_at`` is set to the current UTC time; ``status`` is ``"running"``.
    """
    run = SearchRun(
        area_query=area_query,
        checkin=checkin,
        checkout=checkout,
        guests=guests,
        sources=sources,
        started_at=datetime.utcnow(),
        status="running",
    )
    session.add(run)
    session.flush()
    return run


def close_search_run(
    session: Session,
    run: SearchRun,
    stats: dict[str, Any] | None = None,
) -> SearchRun:
    """Convenience wrapper: finalise *run* with optional *stats*.

    Delegates to :func:`record_run_stats`.
    """
    return record_run_stats(session, run, stats=stats or {})


def record_run_stats(
    session: Session,
    run: SearchRun,
    stats: dict[str, Any],
    finished_at: datetime | None = None,
) -> SearchRun:
    """Update ``SearchRun.stats`` and ``SearchRun.finished_at``.

    Sets ``status`` to ``"completed"`` and stamps ``finished_at`` with the
    current UTC time unless *finished_at* is provided explicitly.

    Returns the mutated *run* instance (flushed, not committed).
    """
    run.stats = stats
    run.finished_at = finished_at if finished_at is not None else datetime.utcnow()
    run.status = "completed"
    session.flush()
    return run


# ---------------------------------------------------------------------------
# Listing helpers
# ---------------------------------------------------------------------------


def upsert_listing(session: Session, listing: Listing) -> Listing:
    """Insert or update a :class:`Listing` keyed on ``(source, source_listing_id)``.

    * **Insert path** — the row does not yet exist: ``first_seen_at`` and
      ``last_seen_at`` are set to the current UTC time and the object is added
      to the session.
    * **Update path** — a row already exists: all :data:`_MUTABLE_LISTING_FIELDS`
      are copied from *listing* onto the existing row, and ``last_seen_at`` is
      refreshed to the current UTC time.  ``first_seen_at`` is *not* changed.

    Returns the persisted :class:`Listing` instance (flushed, not committed).
    """
    now = datetime.utcnow()

    existing: Listing | None = (
        session.query(Listing)
        .filter_by(source=listing.source, source_listing_id=listing.source_listing_id)
        .first()
    )

    if existing is None:
        listing.first_seen_at = now
        listing.last_seen_at = now
        session.add(listing)
        session.flush()
        return listing

    # Update path: refresh mutable fields and bump last_seen_at.
    for field in _MUTABLE_LISTING_FIELDS:
        incoming = getattr(listing, field, None)
        if incoming is not None:
            setattr(existing, field, incoming)
    existing.last_seen_at = now
    session.flush()
    return existing


# ---------------------------------------------------------------------------
# ListingSnapshot helpers
# ---------------------------------------------------------------------------


def create_listing_snapshot(
    session: Session,
    snapshot: ListingSnapshot,
) -> ListingSnapshot:
    """Insert a :class:`ListingSnapshot` row unconditionally.

    A snapshot is created on every search run regardless of whether the parent
    listing was new or already existed.  ``captured_at`` defaults to the
    current UTC time when not already set on *snapshot*.

    Returns the inserted instance (flushed, not committed).
    """
    if snapshot.captured_at is None:
        snapshot.captured_at = datetime.utcnow()
    session.add(snapshot)
    session.flush()
    return snapshot
