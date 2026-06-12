"""SQLAlchemy ORM models for Short-Stay Market Scanner."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///scanner.db")


def get_engine(url: str = DATABASE_URL):
    """Create (or return a cached) SQLAlchemy engine for *url*."""
    return create_engine(url, echo=False)


# Module-level default engine – import from here for the shared DB.
engine = get_engine()


class Base(DeclarativeBase):
    pass


class SearchRun(Base):
    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    area_query: Mapped[str] = mapped_column(String(255))
    checkin: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    checkout: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    guests: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sources: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    stats: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    snapshots: Mapped[list[ListingSnapshot]] = relationship(back_populates="run")


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50))
    source_listing_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(500))
    property_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    address_text: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    bedrooms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    beds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    baths: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_guests: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    review_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    amenities: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    images: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    host_or_brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    snapshots: Mapped[list[ListingSnapshot]] = relationship(back_populates="listing")

    __table_args__ = (UniqueConstraint("source", "source_listing_id"),)


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    run_id: Mapped[int] = mapped_column(ForeignKey("search_runs.id"))
    nightly_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    total_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fees: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    availability: Mapped[Optional[bool]] = mapped_column(nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    listing: Mapped[Listing] = relationship(back_populates="snapshots")
    run: Mapped[SearchRun] = relationship(back_populates="snapshots")
