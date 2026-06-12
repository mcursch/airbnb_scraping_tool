"""Repository helpers: upserts and content-hash deduplication queries."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db.models import ExtractionLog, Listing, ListingSnapshot, RawScrape


# ---------------------------------------------------------------------------
# Content-hash deduplication
# ---------------------------------------------------------------------------


def find_extraction_by_content_hash(session: Session, content_hash: str) -> int | None:
    """Return the listing_id of a previously successful extraction for this payload.

    Looks for any ExtractionLog whose associated RawScrape carries the given
    ``content_hash`` and whose own status is ``'extracted'``.  Returns the
    ``listing_id`` so the caller can reuse the existing Listing without
    invoking the LLM again.  Returns ``None`` when no such record exists.
    """
    row = (
        session.query(ExtractionLog.listing_id)
        .join(RawScrape, ExtractionLog.raw_scrape_id == RawScrape.id)
        .filter(
            RawScrape.content_hash == content_hash,
            ExtractionLog.status == "extracted",
            ExtractionLog.listing_id.isnot(None),
        )
        .first()
    )
    return row[0] if row is not None else None


# ---------------------------------------------------------------------------
# Listing upsert
# ---------------------------------------------------------------------------


def upsert_listing(session: Session, data: dict) -> Listing:
    """Insert or update a Listing keyed by (source, source_listing_id).

    ``data`` must contain at minimum ``source`` and ``source_listing_id``.
    All other keys are treated as field updates.
    """
    source = data["source"]
    source_listing_id = data["source_listing_id"]

    listing = (
        session.query(Listing)
        .filter_by(source=source, source_listing_id=source_listing_id)
        .first()
    )
    if listing is None:
        listing = Listing(**data)
        session.add(listing)
    else:
        for key, value in data.items():
            if key not in ("id", "first_seen_at"):
                setattr(listing, key, value)
        listing.last_seen_at = datetime.now(timezone.utc)

    session.flush()  # populate listing.id without committing
    return listing


# ---------------------------------------------------------------------------
# Snapshot insertion
# ---------------------------------------------------------------------------


def insert_snapshot(
    session: Session,
    listing_id: int,
    run_id: int,
    *,
    nightly_price: float | None = None,
    currency: str | None = None,
    total_price: float | None = None,
    fees: str | None = None,
    availability: bool | None = None,
) -> ListingSnapshot:
    """Append a new price/availability snapshot for a listing in a given run."""
    snapshot = ListingSnapshot(
        listing_id=listing_id,
        run_id=run_id,
        nightly_price=nightly_price,
        currency=currency,
        total_price=total_price,
        fees=fees,
        availability=availability,
        captured_at=datetime.now(timezone.utc),
    )
    session.add(snapshot)
    session.flush()
    return snapshot
