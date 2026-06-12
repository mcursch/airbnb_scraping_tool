"""SQLAlchemy ORM models mirroring the data model in PLAN.md."""

from datetime import datetime

from sqlalchemy import (
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
    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    area_query: Mapped[str] = mapped_column(String, nullable=False)
    checkin: Mapped[str | None] = mapped_column(String, nullable=True)
    checkout: Mapped[str | None] = mapped_column(String, nullable=True)
    guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sources: Mapped[str] = mapped_column(String, nullable=False)  # comma-separated
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")
    stats: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON blob

    raw_scrapes: Mapped[list["RawScrape"]] = relationship(back_populates="run")
    snapshots: Mapped[list["ListingSnapshot"]] = relationship(back_populates="run")


class RawScrape(Base):
    """One raw payload captured from a scrape session.

    content_hash is SHA-256 of the payload and is used for deduplication —
    identical payloads from different runs share the same hash so that the
    extraction step can be skipped on a cache hit.  It is NOT enforced unique
    at the DB level because different SearchRun rows legitimately produce the
    same payload (same listing reappearing in later searches).
    """

    __tablename__ = "raw_scrapes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_runs.id"), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|extracted|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["SearchRun"] = relationship(back_populates="raw_scrapes")
    extraction_logs: Mapped[list["ExtractionLog"]] = relationship(back_populates="raw_scrape")


class Listing(Base):
    """Normalised listing record, keyed by (source, source_listing_id)."""

    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "source_listing_id", name="uq_listing_source_id"),)

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
    amenities: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    images: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    host_or_brand: Mapped[str | None] = mapped_column(String, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    snapshots: Mapped[list["ListingSnapshot"]] = relationship(back_populates="listing")
    extraction_logs: Mapped[list["ExtractionLog"]] = relationship(back_populates="listing")


class ListingSnapshot(Base):
    """Price / availability of a Listing captured during one SearchRun."""

    __tablename__ = "listing_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(Integer, ForeignKey("listings.id"), nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_runs.id"), nullable=False)
    nightly_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    total_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    availability: Mapped[bool | None] = mapped_column(nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    listing: Mapped["Listing"] = relationship(back_populates="snapshots")
    run: Mapped["SearchRun"] = relationship(back_populates="snapshots")


class ExtractionLog(Base):
    """Records the outcome (and token cost) of one extraction attempt.

    listing_id is set on success so that find_extraction_by_content_hash can
    resolve the Listing for a given content_hash without an extra join through
    ListingSnapshot.
    """

    __tablename__ = "extraction_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_scrape_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_scrapes.id"), nullable=False
    )
    listing_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("listings.id"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False)  # extracted|failed|dedup
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_scrape: Mapped["RawScrape"] = relationship(back_populates="extraction_logs")
    listing: Mapped["Listing | None"] = relationship(back_populates="extraction_logs")
