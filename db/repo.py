"""Repository layer: queries and upserts for Short-Stay Market Scanner."""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from db.models import Base, Listing, ListingSnapshot, SearchRun


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def create_all(engine: Engine) -> None:
    """Create all tables if they don't exist yet."""
    Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Search-run queries
# ---------------------------------------------------------------------------

def list_run_ids(engine: Engine) -> list[int]:
    """Return all SearchRun ids, most-recent first."""
    with Session(engine) as session:
        rows = session.execute(
            select(SearchRun.id).order_by(SearchRun.started_at.desc())
        ).all()
        return [r[0] for r in rows]


def get_run(run_id: int, engine: Engine) -> SearchRun | None:
    """Fetch a single SearchRun by primary key."""
    with Session(engine) as session:
        return session.get(SearchRun, run_id)


# ---------------------------------------------------------------------------
# Listing + snapshot queries
# ---------------------------------------------------------------------------

_DISPLAY_COLUMNS = [
    "name",
    "source",
    "nightly_price",
    "currency",
    "rating",
    "review_count",
    "property_type",
    "bedrooms",
    "beds",
    "url",
    "host_or_brand",
    "address_text",
    "snapshot_id",
    "listing_id",
]


def get_listings_for_run(run_id: int, engine: Engine) -> pd.DataFrame:
    """Return a DataFrame joining Listing + ListingSnapshot for *run_id*.

    Columns always present (even when empty):
        name, source, nightly_price, currency, rating, review_count,
        property_type, bedrooms, beds, url, host_or_brand, address_text,
        snapshot_id, listing_id
    """
    stmt = (
        select(
            Listing.id.label("listing_id"),
            Listing.name,
            Listing.source,
            Listing.property_type,
            Listing.rating,
            Listing.review_count,
            Listing.url,
            Listing.host_or_brand,
            Listing.address_text,
            Listing.bedrooms,
            Listing.beds,
            ListingSnapshot.id.label("snapshot_id"),
            ListingSnapshot.nightly_price,
            ListingSnapshot.currency,
            ListingSnapshot.total_price,
        )
        .join(ListingSnapshot, ListingSnapshot.listing_id == Listing.id)
        .where(ListingSnapshot.run_id == run_id)
    )

    with Session(engine) as session:
        rows: list[Any] = session.execute(stmt).all()

    if not rows:
        return pd.DataFrame(columns=_DISPLAY_COLUMNS)

    df = pd.DataFrame(rows, columns=[col for col in rows[0]._fields])
    # Ensure all expected columns exist
    for col in _DISPLAY_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df


# ---------------------------------------------------------------------------
# Upsert helpers (used by the pipeline — stubs for now)
# ---------------------------------------------------------------------------

def upsert_listing(listing_data: dict[str, Any], engine: Engine) -> int:
    """Insert or update a Listing; return its primary key."""
    with Session(engine) as session:
        existing = session.execute(
            select(Listing).where(
                Listing.source == listing_data["source"],
                Listing.source_listing_id == listing_data["source_listing_id"],
            )
        ).scalar_one_or_none()

        if existing is None:
            obj = Listing(**listing_data)
            session.add(obj)
        else:
            for k, v in listing_data.items():
                setattr(existing, k, v)
            obj = existing

        session.commit()
        session.refresh(obj)
        return obj.id


def insert_snapshot(snapshot_data: dict[str, Any], engine: Engine) -> int:
    """Insert a ListingSnapshot; return its primary key."""
    with Session(engine) as session:
        obj = ListingSnapshot(**snapshot_data)
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj.id
