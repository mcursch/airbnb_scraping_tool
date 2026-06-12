"""SQLAlchemy ORM models for the Short-Stay Market Scanner."""

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class SearchRun(Base):
    """Represents a single scan initiated by the user."""

    __tablename__ = "search_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    area_query = Column(String, nullable=False)
    checkin = Column(String)
    checkout = Column(String)
    guests = Column(Integer)
    sources = Column(JSON)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime)
    status = Column(String, default="running", nullable=False)
    stats = Column(JSON)

    snapshots = relationship("ListingSnapshot", back_populates="run")


class Listing(Base):
    """A normalised, deduplicated listing record.

    Keyed on (source, source_listing_id) via a unique constraint so that
    repeated scrapes update rather than duplicate.
    """

    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source", "source_listing_id", name="uq_listing_source"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)
    source_listing_id = Column(String, nullable=False)
    name = Column(String)
    property_type = Column(String)
    lat = Column(Float)
    lon = Column(Float)
    address_text = Column(String)
    bedrooms = Column(Integer)
    beds = Column(Integer)
    baths = Column(Float)
    max_guests = Column(Integer)
    rating = Column(Float)
    review_count = Column(Integer)
    amenities = Column(JSON)
    images = Column(JSON)
    url = Column(String)
    host_or_brand = Column(String)
    first_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    snapshots = relationship("ListingSnapshot", back_populates="listing")


class ListingSnapshot(Base):
    """Price and availability captured per search run for a given listing.

    A new row is always inserted (even if the listing already existed) so that
    price history is preserved for future analysis.
    """

    __tablename__ = "listing_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    run_id = Column(Integer, ForeignKey("search_runs.id"), nullable=False)
    nightly_price = Column(Float)
    currency = Column(String)
    total_price = Column(Float)
    fees = Column(JSON)
    availability = Column(String)
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    listing = relationship("Listing", back_populates="snapshots")
    run = relationship("SearchRun", back_populates="snapshots")


class RawScrape(Base):
    """Raw payload captured by a scraper before extraction."""

    __tablename__ = "raw_scrapes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("search_runs.id"), nullable=False)
    source = Column(String, nullable=False)
    url = Column(String)
    payload = Column(Text)
    content_hash = Column(String, unique=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String, default="pending", nullable=False)
    error = Column(Text)


class ExtractionLog(Base):
    """Token usage and outcome for a single LLM extraction call."""

    __tablename__ = "extraction_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_scrape_id = Column(Integer, ForeignKey("raw_scrapes.id"), nullable=False)
    model = Column(String)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    cache_read_tokens = Column(Integer)
    status = Column(String)
    error = Column(Text)
