"""SQLAlchemy ORM models.

Mirrors the Pydantic schemas closely so the two layers stay in sync.
Keep column types portable (no Postgres-only types) to allow a painless
switch from SQLite to Postgres later.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from config import settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SearchRun(Base):
    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    area_query: Mapped[str] = mapped_column(String(512))
    checkin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    checkout: Mapped[str | None] = mapped_column(String(10), nullable=True)
    guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources: Mapped[str] = mapped_column(String(256))  # comma-separated
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    stats: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON blob

    raw_scrapes: Mapped[list[RawScrape]] = relationship("RawScrape", back_populates="run")


class RawScrape(Base):
    """One intercepted API payload from a scraper run.

    Persisted immediately when captured so data survives mid-run crashes.
    """

    __tablename__ = "raw_scrapes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("search_runs.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(64))          # 'airbnb', 'booking', …
    url: Mapped[str] = mapped_column(String(2048))
    payload: Mapped[str] = mapped_column(Text)               # raw JSON string
    content_hash: Mapped[str] = mapped_column(String(64))    # SHA-256 hex
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_raw_scrapes_content_hash"),
    )

    run: Mapped[SearchRun | None] = relationship("SearchRun", back_populates="raw_scrapes")

    @staticmethod
    def compute_hash(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64))
    source_listing_id: Mapped[str] = mapped_column(String(256))
    name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    property_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lat: Mapped[float | None] = mapped_column(nullable=True)
    lon: Mapped[float | None] = mapped_column(nullable=True)
    address_text: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baths: Mapped[float | None] = mapped_column(nullable=True)
    max_guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amenities: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON
    images: Mapped[str | None] = mapped_column(Text, nullable=True)      # JSON
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    host_or_brand: Mapped[str | None] = mapped_column(String(512), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("source", "source_listing_id", name="uq_listing_source_id"),
    )


# ── Engine / session factory ──────────────────────────────────────────────────

def _make_engine(url: str | None = None):
    db_url = url or settings.database_url
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
    )
    # Enable WAL mode for SQLite so reads don't block writes mid-run.
    if db_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_wal(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA synchronous=NORMAL")

    Base.metadata.create_all(engine)
    return engine


def make_session_factory(url: str | None = None):
    engine = _make_engine(url)
    return sessionmaker(bind=engine, expire_on_commit=False)
