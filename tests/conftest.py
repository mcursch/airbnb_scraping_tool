"""Shared pytest fixtures for Short-Stay Market Scanner tests."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base, Listing, ListingSnapshot, SearchRun


@pytest.fixture()
def db_engine():
    """In-memory SQLite engine with all tables created and seed data loaded."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # --- Search run ---
        run = SearchRun(
            id=1,
            area_query="Lisbon, Portugal",
            checkin="2025-07-01",
            checkout="2025-07-07",
            guests=2,
            sources="airbnb,booking",
            started_at=datetime(2025, 6, 1, 10, 0, 0),
            finished_at=datetime(2025, 6, 1, 10, 5, 0),
            status="finished",
        )
        session.add(run)
        session.flush()

        # --- Listings ---
        listings = [
            Listing(
                source="airbnb",
                source_listing_id="ab-1",
                name="Cosy Studio in Alfama",
                property_type="apartment",
                rating=4.8,
                review_count=120,
                url="https://airbnb.com/rooms/1",
                host_or_brand="Alice",
            ),
            Listing(
                source="airbnb",
                source_listing_id="ab-2",
                name="Sunny Loft near Bairro Alto",
                property_type="loft",
                rating=4.5,
                review_count=80,
                url="https://airbnb.com/rooms/2",
                host_or_brand="Bob",
            ),
            Listing(
                source="booking",
                source_listing_id="bk-1",
                name="Hotel Lisboa Central",
                property_type="hotel",
                rating=4.2,
                review_count=500,
                url="https://booking.com/hotel/1",
                host_or_brand="Lisboa Central Hotels",
            ),
            Listing(
                source="booking",
                source_listing_id="bk-2",
                name="Budget Inn Baixa",
                property_type="hotel",
                rating=3.5,
                review_count=200,
                url="https://booking.com/hotel/2",
                host_or_brand="Budget Stays",
            ),
            Listing(
                source="airbnb",
                source_listing_id="ab-3",
                name="Luxury Villa with Pool",
                property_type="villa",
                rating=4.9,
                review_count=45,
                url="https://airbnb.com/rooms/3",
                host_or_brand="Carlos",
            ),
        ]
        session.add_all(listings)
        session.flush()

        # --- Snapshots tied to run 1 ---
        prices = [80.0, 120.0, 150.0, 55.0, 350.0]
        for listing, price in zip(listings, prices):
            session.add(
                ListingSnapshot(
                    listing_id=listing.id,
                    run_id=run.id,
                    nightly_price=price,
                    currency="EUR",
                    total_price=price * 6,
                    captured_at=datetime(2025, 6, 1, 10, 3, 0),
                )
            )

        session.commit()

    return engine
