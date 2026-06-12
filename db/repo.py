"""Repository layer — upserts, queries, and snapshot helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from db.models import Listing, ListingSnapshot, SearchRun


# ---------------------------------------------------------------------------
# Listing queries
# ---------------------------------------------------------------------------


def get_listing(session: Session, listing_id: int) -> Listing | None:
    """Return the Listing with *listing_id*, eagerly loading its snapshots."""
    stmt = (
        select(Listing)
        .where(Listing.id == listing_id)
        .options(selectinload(Listing.snapshots))
    )
    return session.execute(stmt).scalar_one_or_none()


def get_latest_snapshot(
    session: Session, listing_id: int
) -> ListingSnapshot | None:
    """Return the most-recently captured ListingSnapshot for *listing_id*."""
    stmt = (
        select(ListingSnapshot)
        .where(ListingSnapshot.listing_id == listing_id)
        .order_by(ListingSnapshot.captured_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_listing_with_latest_snapshot(
    session: Session, listing_id: int
) -> tuple[Listing, ListingSnapshot | None] | None:
    """
    Convenience function used by the detail panel.

    Returns ``(listing, snapshot)`` or ``None`` if the listing does not exist.
    *snapshot* may itself be ``None`` when no snapshot has been recorded yet.
    """
    listing = get_listing(session, listing_id)
    if listing is None:
        return None
    snapshot = get_latest_snapshot(session, listing_id)
    return listing, snapshot


def list_listings(
    session: Session,
    *,
    source: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Listing]:
    """Return listings, optionally filtered by *source*."""
    stmt = select(Listing).order_by(Listing.last_seen_at.desc()).limit(limit).offset(offset)
    if source:
        stmt = stmt.where(Listing.source == source)
    return list(session.execute(stmt).scalars())


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------


def upsert_listing(session: Session, data: dict[str, Any]) -> Listing:
    """
    Insert or update a Listing keyed by ``(source, source_listing_id)``.

    *data* should be a dict of column values (excluding ``id``).
    Returns the persisted ``Listing`` instance.
    """
    stmt = select(Listing).where(
        Listing.source == data["source"],
        Listing.source_listing_id == data["source_listing_id"],
    )
    listing = session.execute(stmt).scalar_one_or_none()
    if listing is None:
        listing = Listing(**data)
        session.add(listing)
    else:
        for key, value in data.items():
            setattr(listing, key, value)
        listing.last_seen_at = datetime.utcnow()
    session.flush()
    return listing


def insert_snapshot(
    session: Session, listing_id: int, run_id: int | None, snapshot_data: dict[str, Any]
) -> ListingSnapshot:
    """Create and persist a new ListingSnapshot."""
    snapshot = ListingSnapshot(
        listing_id=listing_id,
        run_id=run_id,
        **snapshot_data,
    )
    session.add(snapshot)
    session.flush()
    return snapshot
