"""SQLAlchemy ORM models."""
from __future__ import annotations

import functools
import json
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from config import settings


class Base(DeclarativeBase):
    pass


class SearchRun(Base):
    __tablename__ = "search_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    area_query: Mapped[str] = mapped_column(String(500), nullable=False)
    checkin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    checkout: Mapped[str | None] = mapped_column(String(10), nullable=True)
    guests: Mapped[int] = mapped_column(Integer, default=1)
    sources: Mapped[str] = mapped_column(String(100), default="both")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class RawScrape(Base):
    __tablename__ = "raw_scrape"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Listing(Base):
    __tablename__ = "listing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_listing_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    property_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lat: Mapped[float | None] = mapped_column(nullable=True)
    lon: Mapped[float | None] = mapped_column(nullable=True)
    address_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baths: Mapped[float | None] = mapped_column(nullable=True)
    max_guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amenities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    images: Mapped[list | None] = mapped_column(JSON, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    host_or_brand: Mapped[str | None] = mapped_column(String(300), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    nightly_price: Mapped[float | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    total_price: Mapped[float | None] = mapped_column(nullable=True)
    fees: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    availability: Mapped[bool | None] = mapped_column(nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ExtractionLog(Base):
    __tablename__ = "extraction_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_scrape_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


@functools.lru_cache(maxsize=None)
def get_engine():
    """Return the shared Engine singleton; creates tables on first call only."""
    engine = create_engine(settings.database_url, echo=False)
    Base.metadata.create_all(engine)
    return engine


def get_session() -> Session:
    return Session(get_engine())
