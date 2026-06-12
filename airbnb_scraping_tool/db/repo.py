"""Repository layer — all database reads and writes go through ``Repo``.

Each method operates within a caller-supplied ``Session`` so that callers
control transaction boundaries (start, commit, rollback).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import ExtractionLog, Listing, ListingSnapshot, RawScrape, SearchRun


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Repo:
    """Thin data-access wrapper.  Pass a ``Session`` on each call."""

    # ------------------------------------------------------------------
    # SearchRun
    # ------------------------------------------------------------------

    def open_run(self, session: Session, area_query: str, **kwargs: Any) -> SearchRun:
        """Create and persist a new SearchRun in 'running' status."""
        run = SearchRun(
            area_query=area_query,
            checkin=kwargs.get("checkin"),
            checkout=kwargs.get("checkout"),
            guests=kwargs.get("guests", 1),
            sources=",".join(kwargs.get("sources", ["airbnb", "booking"])),
            status="running",
        )
        session.add(run)
        session.flush()  # get the PK without committing
        return run

    def close_run(self, session: Session, run_id: int, status: str = "done") -> None:
        """Set ``finished_at`` and ``status`` on a run."""
        run = session.get(SearchRun, run_id)
        if run is None:
            raise ValueError(f"SearchRun {run_id} not found")
        run.finished_at = _utcnow()
        run.status = status
        session.flush()

    def record_run_stats(self, session: Session, run_id: int, stats: dict[str, Any]) -> None:
        """Persist aggregated stats to ``SearchRun.stats``.

        Stats keys (also consumed by Stage 5 dashboard):
          total_listings   – total number of unique listings touched this run
                             (equals new + updated + unchanged)
          new              – listings inserted for the first time
          updated          – existing listings whose data was refreshed
          unchanged        – existing listings whose data was identical (no-op upsert)
          dedup_hits       – raw scrapes skipped because content_hash already existed
          total_tokens     – sum of input + output + cache_read tokens across all LLM calls
          estimated_cost_usd – approximate cost in USD (input $5/MTok, output $25/MTok,
                               cache_read $0.50/MTok for claude-opus-4-8)
        """
        run = session.get(SearchRun, run_id)
        if run is None:
            raise ValueError(f"SearchRun {run_id} not found")
        run.stats = stats
        session.flush()

    def get_run(self, session: Session, run_id: int) -> SearchRun | None:
        return session.get(SearchRun, run_id)

    # ------------------------------------------------------------------
    # RawScrape
    # ------------------------------------------------------------------

    def save_raw_scrape(self, session: Session, run_id: int, source: str, url: str, payload: str, content_hash: str) -> RawScrape:
        """Persist a raw scrape and return the new row."""
        rs = RawScrape(
            run_id=run_id,
            source=source,
            url=url,
            payload=payload,
            content_hash=content_hash,
            status="pending",
        )
        session.add(rs)
        session.flush()
        return rs

    def find_by_hash(self, session: Session, content_hash: str) -> RawScrape | None:
        """Return any existing RawScrape with the given content hash, or None."""
        stmt = select(RawScrape).where(RawScrape.content_hash == content_hash).limit(1)
        return session.scalars(stmt).first()

    def mark_scrape_status(self, session: Session, raw_scrape_id: int, status: str, error: str | None = None) -> None:
        rs = session.get(RawScrape, raw_scrape_id)
        if rs:
            rs.status = status
            rs.error = error
            session.flush()

    # ------------------------------------------------------------------
    # Listing upsert
    # ------------------------------------------------------------------

    def upsert_listing(
        self,
        session: Session,
        source: str,
        source_listing_id: str,
        **fields: Any,
    ) -> tuple[Listing, bool, bool]:
        """Insert or update a listing row.

        Returns:
            (listing, is_new, was_updated)
            ``is_new`` is True when the row was inserted for the first time.
            ``was_updated`` is True when an existing row had at least one field
            changed (i.e. the listing existed but its data changed).
        """
        stmt = select(Listing).where(
            Listing.source == source,
            Listing.source_listing_id == source_listing_id,
        )
        existing = session.scalars(stmt).first()

        # Serialise JSON fields
        if "amenities" in fields and isinstance(fields["amenities"], list):
            fields["amenities"] = json.dumps(fields["amenities"])
        if "images" in fields and isinstance(fields["images"], list):
            fields["images"] = json.dumps(fields["images"])

        if existing is None:
            listing = Listing(source=source, source_listing_id=source_listing_id, **fields)
            session.add(listing)
            session.flush()
            return listing, True, False

        # Check whether anything changed
        was_updated = False
        for key, value in fields.items():
            if hasattr(existing, key) and getattr(existing, key) != value:
                setattr(existing, key, value)
                was_updated = True

        if was_updated:
            existing.last_seen_at = _utcnow()
            session.flush()

        return existing, False, was_updated

    # ------------------------------------------------------------------
    # ListingSnapshot
    # ------------------------------------------------------------------

    def insert_snapshot(
        self,
        session: Session,
        listing_id: int,
        run_id: int,
        nightly_price: float | None = None,
        currency: str | None = None,
        total_price: float | None = None,
        fees: dict[str, Any] | None = None,
        availability: bool | None = None,
    ) -> ListingSnapshot:
        snap = ListingSnapshot(
            listing_id=listing_id,
            run_id=run_id,
            nightly_price=nightly_price,
            currency=currency,
            total_price=total_price,
            fees=json.dumps(fees) if fees else None,
            availability=availability,
        )
        session.add(snap)
        session.flush()
        return snap

    # ------------------------------------------------------------------
    # ExtractionLog
    # ------------------------------------------------------------------

    def log_extraction(
        self,
        session: Session,
        raw_scrape_id: int,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        status: str = "ok",
        error: str | None = None,
    ) -> ExtractionLog:
        log = ExtractionLog(
            raw_scrape_id=raw_scrape_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            status=status,
            error=error,
        )
        session.add(log)
        session.flush()
        return log

    # ------------------------------------------------------------------
    # Purge helpers (Stage 4 / LIN-38)
    # ------------------------------------------------------------------

    def purge_run(self, session: Session, run_id: int) -> dict[str, int]:
        """Delete all snapshots for *run_id*, then remove orphaned listings.

        A listing is considered orphaned when it has no remaining snapshots
        (i.e. it was only ever seen in this run).

        Returns a dict with keys ``snapshots_deleted`` and ``listings_deleted``.
        """
        # 1. Collect listing IDs referenced by this run's snapshots (before deletion)
        listing_ids_stmt = select(ListingSnapshot.listing_id).where(
            ListingSnapshot.run_id == run_id
        )
        listing_ids = list(session.scalars(listing_ids_stmt).all())

        # 2. Delete all snapshots for this run
        del_snaps = delete(ListingSnapshot).where(ListingSnapshot.run_id == run_id)
        snaps_deleted = session.execute(del_snaps).rowcount

        # 3. Find listings that now have zero snapshots (orphaned)
        orphan_ids = []
        for lid in listing_ids:
            remaining = session.scalars(
                select(ListingSnapshot.id).where(ListingSnapshot.listing_id == lid).limit(1)
            ).first()
            if remaining is None:
                orphan_ids.append(lid)

        # 4. Delete orphaned listings
        listings_deleted = 0
        if orphan_ids:
            del_listings = delete(Listing).where(Listing.id.in_(orphan_ids))
            listings_deleted = session.execute(del_listings).rowcount

        session.flush()
        return {"snapshots_deleted": snaps_deleted, "listings_deleted": listings_deleted}

