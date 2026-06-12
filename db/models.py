"""SQLAlchemy ORM models for the Short-Stay Market Scanner."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SearchRun(Base):
    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_query: Mapped[str] = mapped_column(String, nullable=False)
    checkin: Mapped[str | None] = mapped_column(String, nullable=True)
    checkout: Mapped[str | None] = mapped_column(String, nullable=True)
    guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    stats: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    snapshots: Mapped[list[ListingSnapshot]] = relationship(
        "ListingSnapshot", back_populates="run"
    )


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_listing_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    property_type: Mapped[str | None] = mapped_column(String, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    address_text: Mapped[str | None] = mapped_column(String, nullable=True)
    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baths: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amenities: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    images: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    host_or_brand: Mapped[str | None] = mapped_column(String, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("source", "source_listing_id"),)

    snapshots: Mapped[list[ListingSnapshot]] = relationship(
        "ListingSnapshot", back_populates="listing", order_by="ListingSnapshot.captured_at"
    )

    @property
    def amenities_list(self) -> list[str]:
        """Return amenities as a Python list, or empty list if unset/invalid."""
        if not self.amenities:
            return []
        try:
            value = json.loads(self.amenities)
            return value if isinstance(value, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def latest_snapshot(self) -> ListingSnapshot | None:
        """Return the most-recently captured snapshot, or None."""
        if not self.snapshots:
            return None
        return max(self.snapshots, key=lambda s: s.captured_at)


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("search_runs.id"), nullable=True
    )
    nightly_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    total_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON dict
    availability: Mapped[bool | None] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )

    listing: Mapped[Listing] = relationship("Listing", back_populates="snapshots")
    run: Mapped[SearchRun | None] = relationship("SearchRun", back_populates="snapshots")

    @property
    def fees_dict(self) -> dict[str, Any]:
        """Return fees as a Python dict, or empty dict if unset/invalid."""
        if not self.fees:
            return {}
        try:
            value = json.loads(self.fees)
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}


def get_engine(db_url: str = "sqlite:///market_scanner.db"):
    """Create (or reuse) a SQLAlchemy engine and ensure all tables exist."""
    engine = create_engine(db_url, echo=False)
    Base.metadata.create_all(engine)
    return engine
