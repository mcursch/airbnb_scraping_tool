from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class SearchRun(Base):
    """One user-initiated scan for an area/date combination."""

    __tablename__ = "search_runs"

    id = Column(Integer, primary_key=True)
    area_query = Column(String, nullable=False)
    checkin = Column(String)
    checkout = Column(String)
    guests = Column(Integer)
    sources = Column(String)  # comma-separated: 'airbnb,booking'
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
    status = Column(String, default="pending")  # pending|running|done|failed
    stats = Column(JSON)  # {"listing_count": N, ...}

    raw_scrapes = relationship("RawScrape", back_populates="run")


class RawScrape(Base):
    """One scraped page payload; status tracks extraction progress."""

    __tablename__ = "raw_scrapes"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("search_runs.id"), nullable=False)
    source = Column(String, nullable=False)  # 'airbnb'|'booking'|...
    url = Column(String)
    payload = Column(Text)
    content_hash = Column(String, unique=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="pending")  # pending|extracted|failed
    error = Column(Text)

    run = relationship("SearchRun", back_populates="raw_scrapes")
    extraction_logs = relationship("ExtractionLog", back_populates="raw_scrape")


class ExtractionLog(Base):
    """LLM extraction attempt for one RawScrape; records token usage."""

    __tablename__ = "extraction_logs"

    id = Column(Integer, primary_key=True)
    raw_scrape_id = Column(Integer, ForeignKey("raw_scrapes.id"), nullable=False)
    model = Column(String)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cache_read_tokens = Column(Integer, default=0)
    status = Column(String)  # ok|failed
    error = Column(Text)

    raw_scrape = relationship("RawScrape", back_populates="extraction_logs")
