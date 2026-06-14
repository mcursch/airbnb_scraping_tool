"""Seed a preset "demo run" of real, findable hotels with blank fields.

This makes the enrichment feature demoable reliably: the scrape step is replaced
by deterministic seed data (no live anti-bot / fallback fragility), and only the
enrichment — the thing being shown — runs live. The seeded hotels are real and
web-findable, so the Enrich button fills their blanks (rating, reviews, brand,
superhost, check-in time, cancellation policy, neighbourhood) with citations.

Each hotel has only name / address / property_type / nightly_price filled;
everything else is intentionally left blank so the fill is visible.
"""

from __future__ import annotations

from datetime import datetime, timezone

DEMO_AREA = "Lisbon, Portugal — Demo"

# Real Lisbon hotels (web-findable → enrichment reliably fills their details).
DEMO_HOTELS: list[dict] = [
    {
        "name": "Four Seasons Hotel Ritz Lisbon",
        "address_text": "Rua Rodrigo da Fonseca 88, Lisbon, Portugal",
        "nightly_price": 650.0,
    },
    {
        "name": "Tivoli Avenida Liberdade Lisboa",
        "address_text": "Avenida da Liberdade 185, Lisbon, Portugal",
        "nightly_price": 380.0,
    },
    {
        "name": "Pestana Palace Lisboa",
        "address_text": "Rua Jau 54, Lisbon, Portugal",
        "nightly_price": 320.0,
    },
    {
        "name": "Bairro Alto Hotel",
        "address_text": "Praça Luís de Camões 2, Lisbon, Portugal",
        "nightly_price": 450.0,
    },
    {
        "name": "Memmo Alfama Hotel",
        "address_text": "Travessa Merceeiras 27, Lisbon, Portugal",
        "nightly_price": 260.0,
    },
]

_DEMO_ID_PREFIX = "demo-"


def seed_demo_run(*, replace_existing: bool = True) -> int:
    """Create the preset demo run and return its ``run_id``.

    When *replace_existing* is True (default), any prior demo run and its demo
    listings/snapshots are removed first so the demo always starts clean and
    re-runnable.
    """
    # Imported at call time so tests can monkey-patch SessionLocal / init_db.
    from db.models import (
        Listing,
        ListingSnapshot,
        SearchRun,
        SessionLocal,
        init_db,
    )

    init_db()
    sess = SessionLocal()
    try:
        if replace_existing:
            demo_listings = (
                sess.query(Listing)
                .filter(Listing.source_listing_id.like(f"{_DEMO_ID_PREFIX}%"))
                .all()
            )
            demo_ids = [listing.id for listing in demo_listings]
            if demo_ids:
                sess.query(ListingSnapshot).filter(
                    ListingSnapshot.listing_id.in_(demo_ids)
                ).delete(synchronize_session=False)
            sess.query(Listing).filter(
                Listing.source_listing_id.like(f"{_DEMO_ID_PREFIX}%")
            ).delete(synchronize_session=False)
            sess.query(SearchRun).filter(
                SearchRun.area_query == DEMO_AREA
            ).delete(synchronize_session=False)
            # Commit + expire so deleted rows leave the identity map before we
            # insert fresh listings (avoids identity-map collision warnings).
            sess.commit()
            sess.expire_all()

        now = datetime.now(timezone.utc)
        n = len(DEMO_HOTELS)
        run = SearchRun(
            area_query=DEMO_AREA,
            sources=["booking"],
            guests=2,
            started_at=now,
            finished_at=now,
            status="done",
            stats={"total_listings": n, "listing_count": n, "new": n},
        )
        sess.add(run)
        sess.flush()

        for i, hotel in enumerate(DEMO_HOTELS, start=1):
            listing = Listing(
                source="booking",
                source_listing_id=f"{_DEMO_ID_PREFIX}{i}",
                name=hotel["name"],
                address_text=hotel["address_text"],
                property_type="Hotel",
                # Everything else intentionally blank — these are the enrich
                # targets: rating, review_count, host_or_brand, host_is_superhost,
                # checkin_time, cancellation_policy, neighborhood, …
            )
            sess.add(listing)
            sess.flush()
            sess.add(
                ListingSnapshot(
                    listing_id=listing.id,
                    run_id=run.id,
                    nightly_price=hotel["nightly_price"],
                    currency="USD",
                )
            )

        sess.commit()
        return run.id
    finally:
        sess.close()
