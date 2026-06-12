"""SQLAlchemy ORM models mirroring the Pydantic schemas defined in PLAN.md."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SearchRun(Base):
    """Records a single user-initiated search."""

    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    area_query: Mapped[str] = mapped_column(String, nullable=False)
    checkin: Mapped[str | None] = mapped_column(String, nullable=True)
    checkout: Mapped[str | None] = mapped_column(String, nullable=True)
    guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    raw_scrapes: Mapped[list["RawScrape"]] = relationship(
        "RawScrape", back_populates="run"
    )
    snapshots: Mapped[list["ListingSnapshot"]] = relationship(
        "ListingSnapshot", back_populates="run"
    )


class RawScrape(Base):
    """Raw scraped payload, persisted immediately so no data is lost on crash."""

    __tablename__ = "raw_scrapes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("search_runs.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["SearchRun | None"] = relationship("SearchRun", back_populates="raw_scrapes")
    extraction_logs: Mapped[list["ExtractionLog"]] = relationship(
        "ExtractionLog", back_populates="raw_scrape"
    )


class Listing(Base):
    """Canonical listing record, deduped on (source, source_listing_id)."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_listing_id", name="uq_listing_source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_listing_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    property_type: Mapped[str | None] = mapped_column(String, nullable=True)
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
    host_or_brand: Mapped[str | None] = mapped_column(String, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    snapshots: Mapped[list["ListingSnapshot"]] = relationship(
        "ListingSnapshot", back_populates="listing"
    )


class ListingSnapshot(Base):
    """Price/availability at the time of a specific search run."""

    __tablename__ = "listing_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id"), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("search_runs.id"), nullable=False
    )
    nightly_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    total_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    availability: Mapped[bool | None] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    listing: Mapped["Listing"] = relationship("Listing", back_populates="snapshots")
    run: Mapped["SearchRun"] = relationship("SearchRun", back_populates="snapshots")


class ExtractionLog(Base):
    """Records LLM extraction metadata for each raw scrape processed."""

    __tablename__ = "extraction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_scrape_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_scrapes.id"), nullable=False
    )
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="success")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_scrape: Mapped["RawScrape"] = relationship(
        "RawScrape", back_populates="extraction_logs"
    )
