"""SQLAlchemy ORM models for the Short-Stay Market Scanner."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SearchRun(Base):
    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_query: Mapped[str] = mapped_column(Text, nullable=False)
    checkin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    checkout: Mapped[str | None] = mapped_column(String(10), nullable=True)
    guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    raw_scrapes: Mapped[list[RawScrape]] = relationship("RawScrape", back_populates="run")


class RawScrape(Base):
    __tablename__ = "raw_scrapes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("search_runs.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[SearchRun | None] = relationship("SearchRun", back_populates="raw_scrapes")
    extraction_logs: Mapped[list[ExtractionLog]] = relationship(
        "ExtractionLog", back_populates="raw_scrape"
    )


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "source_listing_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_listing_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    property_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    address_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baths: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amenities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    images: Mapped[list | None] = mapped_column(JSON, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    host_or_brand: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    snapshots: Mapped[list[ListingSnapshot]] = relationship(
        "ListingSnapshot", back_populates="listing"
    )


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
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    total_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    availability: Mapped[bool | None] = mapped_column(nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    listing: Mapped[Listing] = relationship("Listing", back_populates="snapshots")


class ExtractionLog(Base):
    __tablename__ = "extraction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_scrape_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_scrapes.id"), nullable=False
    )
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="success")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    raw_scrape: Mapped[RawScrape] = relationship(
        "RawScrape", back_populates="extraction_logs"
    )
