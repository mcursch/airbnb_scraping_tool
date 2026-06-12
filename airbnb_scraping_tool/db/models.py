"""SQLAlchemy ORM models.

Tables mirror the Pydantic schemas in ``schemas/`` but are the canonical
persistence layer.  The engine is created here so every module that needs a
session can import ``engine`` / ``SessionLocal`` from one place.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from config import settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False},
    echo=False,
)

# Enable WAL mode for better concurrent read performance on SQLite
@event.listens_for(engine, "connect")
def _set_wal(dbapi_conn, _connection_record):  # noqa: ANN001
    dbapi_conn.execute("PRAGMA journal_mode=WAL")


SessionLocal: sessionmaker[Session] = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SearchRun(Base):
    """One end-to-end search initiated by the user."""

    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    area_query: Mapped[str] = mapped_column(String, nullable=False)
    checkin: Mapped[str | None] = mapped_column(String, nullable=True)
    checkout: Mapped[str | None] = mapped_column(String, nullable=True)
    guests: Mapped[int] = mapped_column(Integer, default=1)
    sources: Mapped[str] = mapped_column(String, default="airbnb,booking")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")  # running | done | failed
    # JSON blob — see pipeline.py for documented keys
    _stats: Mapped[str | None] = mapped_column("stats", Text, nullable=True)

    raw_scrapes: Mapped[list[RawScrape]] = relationship("RawScrape", back_populates="run")
    snapshots: Mapped[list[ListingSnapshot]] = relationship("ListingSnapshot", back_populates="run")

    @property
    def stats(self) -> dict[str, Any]:
        if self._stats is None:
            return {}
        return json.loads(self._stats)

    @stats.setter
    def stats(self, value: dict[str, Any]) -> None:
        self._stats = json.dumps(value)


class RawScrape(Base):
    """Raw HTML/JSON fetched by a scraper, before LLM extraction."""

    __tablename__ = "raw_scrapes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_runs.id"), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | extracted | failed | deduped
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[SearchRun] = relationship("SearchRun", back_populates="raw_scrapes")
    extraction_logs: Mapped[list[ExtractionLog]] = relationship("ExtractionLog", back_populates="raw_scrape")


class Listing(Base):
    """Canonical listing record — one row per unique (source, source_listing_id)."""

    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "source_listing_id", name="uq_listing_source_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_listing_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
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
    amenities: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    images: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    url: Mapped[str] = mapped_column(String, nullable=False)
    host_or_brand: Mapped[str | None] = mapped_column(String, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    snapshots: Mapped[list[ListingSnapshot]] = relationship("ListingSnapshot", back_populates="listing")


class ListingSnapshot(Base):
    """Price/availability snapshot for a listing at a specific run."""

    __tablename__ = "listing_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id"), nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_runs.id"), nullable=False)
    nightly_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    total_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON object
    availability: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    listing: Mapped[Listing] = relationship("Listing", back_populates="snapshots")
    run: Mapped[SearchRun] = relationship("SearchRun", back_populates="snapshots")


class ExtractionLog(Base):
    """Token usage and outcome for each LLM extraction call."""

    __tablename__ = "extraction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_scrape_id: Mapped[int] = mapped_column(Integer, ForeignKey("raw_scrapes.id"), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="ok")  # ok | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    raw_scrape: Mapped[RawScrape] = relationship("RawScrape", back_populates="extraction_logs")


def init_db() -> None:
    """Create all tables if they do not exist yet."""
    Base.metadata.create_all(engine)
