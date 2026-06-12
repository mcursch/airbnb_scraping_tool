"""SQLAlchemy ORM models for the Short-Stay Market Scanner."""

from datetime import datetime

import hashlib
import json

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

    @property
    def amenities_list(self) -> list:
        """Return ``amenities`` parsed as a Python list, or ``[]`` on failure."""
        if not self.amenities:
            return []
        try:
            raw = self.amenities if isinstance(self.amenities, str) else json.dumps(self.amenities)
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

    @property
    def latest_snapshot(self):
        """Return the most-recently-captured :class:`ListingSnapshot`, or ``None``."""
        snaps = list(self.snapshots)  # triggers lazy-load when session is active
        if not snaps:
            return None
        with_dates = [s for s in snaps if s.captured_at is not None]
        if with_dates:
            return max(with_dates, key=lambda s: s.captured_at)
        return snaps[-1]


class ListingSnapshot(Base):
    """Price and availability captured per search run for a given listing.

    A new row is always inserted (even if the listing already existed) so that
    price history is preserved for future analysis.
    """

    __tablename__ = "listing_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    run_id = Column(Integer, ForeignKey("search_runs.id"), nullable=True)
    nightly_price = Column(Float)
    currency = Column(String)
    total_price = Column(Float)
    fees = Column(JSON)
    availability = Column(String)
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    listing = relationship("Listing", back_populates="snapshots")
    run = relationship("SearchRun", back_populates="snapshots")

    @property
    def fees_dict(self) -> dict:
        """Return ``fees`` parsed as a Python dict, or ``{}`` on failure."""
        if not self.fees:
            return {}
        try:
            raw = self.fees if isinstance(self.fees, str) else json.dumps(self.fees)
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}


class RawScrape(Base):
    """Raw payload captured by a scraper before extraction."""

    __tablename__ = "raw_scrapes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("search_runs.id"), nullable=True)
    source = Column(String, nullable=False)
    url = Column(String)
    payload = Column(Text)
    content_hash = Column(String, index=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String, default="pending", nullable=False)
    error = Column(Text)
    page_number = Column(Integer, nullable=True)

    @staticmethod
    def compute_hash(payload: str) -> str:
        """Return SHA-256 hex digest of *payload*."""
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ExtractionLog(Base):
    """Token usage and outcome for a single LLM extraction call."""

    __tablename__ = "extraction_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_scrape_id = Column(Integer, ForeignKey("raw_scrapes.id"), nullable=False)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=True)
    model = Column(String)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    cache_read_tokens = Column(Integer)
    status = Column(String)
    error = Column(Text)

    @property
    def cache_read_input_tokens(self) -> int | None:
        """Alias for ``cache_read_tokens`` matching the Anthropic SDK field name."""
        return self.cache_read_tokens
