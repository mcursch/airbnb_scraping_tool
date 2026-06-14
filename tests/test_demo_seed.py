"""Tests for the preset hotel demo seed (offline, in-memory DB)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import db.models as models_mod
from db.models import Base, Listing, ListingSnapshot, SearchRun


@pytest.fixture()
def in_memory_db(monkeypatch):
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    TestSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    monkeypatch.setattr(models_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(models_mod, "init_db", lambda: None)
    yield TestSession
    eng.dispose()


def test_seed_creates_run_and_hotels(in_memory_db):
    from demo.seed import DEMO_AREA, DEMO_HOTELS, seed_demo_run

    run_id = seed_demo_run()

    with in_memory_db() as s:
        run = s.get(SearchRun, run_id)
        assert run is not None
        assert run.area_query == DEMO_AREA
        assert run.status == "done"

        listings = s.scalars(select(Listing)).all()
        assert len(listings) == len(DEMO_HOTELS)
        # All hotels, with name/address/property_type filled and details blank.
        for lst in listings:
            assert lst.property_type == "Hotel"
            assert lst.name and lst.address_text
            assert lst.source_listing_id.startswith("demo-")
            assert lst.rating is None  # an enrich target, left blank
            assert lst.host_is_superhost is None
            assert lst.cancellation_policy is None

        snaps = s.scalars(
            select(ListingSnapshot).where(ListingSnapshot.run_id == run_id)
        ).all()
        assert len(snaps) == len(DEMO_HOTELS)
        assert all(snap.nightly_price for snap in snaps)


def test_seed_listings_have_enrichable_gaps(in_memory_db):
    """Seeded hotels must have enough gaps to trigger enrichment selection."""
    from demo.seed import seed_demo_run
    from enrichment.run_enrich import _to_extracted
    from enrichment.agent import missing_important_fields

    seed_demo_run()
    with in_memory_db() as s:
        listing, snap = (
            s.query(Listing, ListingSnapshot)
            .join(ListingSnapshot, ListingSnapshot.listing_id == Listing.id)
            .first()
        )
        gaps = missing_important_fields(_to_extracted(listing, snap))

    assert len(gaps) >= 3  # >= enrich_min_gaps default
    # The dashboard-visible, front-loaded fields are among the gaps.
    for field in ("rating", "host_is_superhost", "checkin_time", "cancellation_policy"):
        assert field in gaps


def test_seed_replaces_existing(in_memory_db):
    """Re-seeding does not duplicate listings or runs (idempotent)."""
    from demo.seed import DEMO_AREA, DEMO_HOTELS, seed_demo_run

    seed_demo_run()
    seed_demo_run()  # replace_existing=True by default

    with in_memory_db() as s:
        n_listings = s.scalar(select(func.count()).select_from(Listing))
        n_runs = s.scalar(
            select(func.count()).select_from(SearchRun).where(SearchRun.area_query == DEMO_AREA)
        )

    assert n_listings == len(DEMO_HOTELS)  # not doubled
    assert n_runs == 1


def test_reseed_resets_enriched_state(in_memory_db):
    """Re-seeding wipes any enrichment written to demo listings (clean practice run)."""
    from demo.seed import seed_demo_run

    seed_demo_run()
    # Simulate a prior enrichment having filled fields on a demo listing.
    with in_memory_db() as s:
        lst = s.scalars(select(Listing)).first()
        lst.rating = 4.9
        lst.enrichment_status = "enriched"
        lst.enrichment = {"rating": {"value": 4.9}}
        s.commit()

    seed_demo_run()  # reset

    with in_memory_db() as s:
        for lst in s.scalars(select(Listing)).all():
            assert lst.rating is None
            assert lst.enrichment_status is None
            assert lst.enrichment is None


def test_clear_demo_data(in_memory_db):
    """Cleanup removes all demo runs/listings/snapshots and is idempotent."""
    from demo.seed import DEMO_AREA, DEMO_HOTELS, clear_demo_data, seed_demo_run

    seed_demo_run()
    removed = clear_demo_data()
    assert removed == len(DEMO_HOTELS)

    with in_memory_db() as s:
        assert s.scalar(select(func.count()).select_from(Listing)) == 0
        assert s.scalar(select(func.count()).select_from(ListingSnapshot)) == 0
        assert (
            s.scalar(
                select(func.count()).select_from(SearchRun).where(SearchRun.area_query == DEMO_AREA)
            )
            == 0
        )

    # Second clear is a harmless no-op.
    assert clear_demo_data() == 0
