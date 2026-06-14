"""Pydantic v2 schemas for the Short-Stay Market Scanner."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums / literals
# ---------------------------------------------------------------------------

RawScrapeStatus = Literal["pending", "extracted", "failed"]
SearchRunStatus = Literal["running", "completed", "failed"]
ExtractionStatus = Literal["success", "failed"]
Source = Literal["airbnb", "booking", "other"]


# ---------------------------------------------------------------------------
# SearchQuery — user-facing input for a new search
# ---------------------------------------------------------------------------


class SearchQuery(BaseModel):
    """Input parameters for a single short-stay market scan."""

    area: str = Field(..., min_length=1, description="Free-text area / city query")
    checkin: Optional[date] = Field(None, description="Check-in date (ISO 8601)")
    checkout: Optional[date] = Field(None, description="Check-out date (ISO 8601)")
    guests: int = Field(1, ge=1, description="Number of guests (≥1)")
    sources: list[Source] = Field(
        default_factory=lambda: ["airbnb"],
        description="Scraping sources to include",
    )
    page_limit: int = Field(5, ge=1, description="Max pages to fetch per source")

    @model_validator(mode="after")
    def checkout_after_checkin(self) -> "SearchQuery":
        if self.checkin is not None and self.checkout is not None:
            if self.checkout <= self.checkin:
                raise ValueError("checkout must be after checkin")
        return self


# ---------------------------------------------------------------------------
# RawPayload — Pydantic representation of a scraped raw record (RawScrape)
# ---------------------------------------------------------------------------


class RawPayload(BaseModel):
    """A single raw scraped payload captured before LLM extraction."""

    id: Optional[int] = Field(None, description="DB primary key (None before persistence)")
    run_id: int = Field(..., description="Foreign key to SearchRun.id")
    source: Source = Field(..., description="Scraping source identifier")
    url: str = Field(..., min_length=1, description="URL that was fetched")
    payload: str = Field(..., description="Raw text / JSON payload")
    content_hash: str = Field(..., description="SHA-256 hex digest of payload (unique)")
    fetched_at: datetime = Field(..., description="UTC timestamp of fetch")
    status: RawScrapeStatus = Field("pending", description="Processing status")
    error: Optional[str] = Field(None, description="Error message if status='failed'")


# ---------------------------------------------------------------------------
# Listing — normalised, deduplicated listing record
# ---------------------------------------------------------------------------


class Listing(BaseModel):
    """Normalised listing entity.  Unique on (source, source_listing_id)."""

    id: Optional[int] = Field(None, description="DB primary key")
    source: Source = Field(..., description="Platform the listing came from")
    source_listing_id: str = Field(..., min_length=1, description="ID as used by the source platform")
    name: str = Field(..., min_length=1, description="Listing title / name")
    property_type: Optional[str] = Field(None, description="e.g. 'Entire apartment', 'Hotel room'")
    lat: Optional[float] = Field(None, ge=-90.0, le=90.0, description="Latitude")
    lon: Optional[float] = Field(None, ge=-180.0, le=180.0, description="Longitude")
    address_text: Optional[str] = Field(None, description="Human-readable address")
    bedrooms: Optional[int] = Field(None, ge=0, description="Number of bedrooms")
    beds: Optional[int] = Field(None, ge=0, description="Number of beds")
    baths: Optional[float] = Field(None, ge=0.0, description="Number of bathrooms")
    max_guests: Optional[int] = Field(None, ge=1, description="Maximum occupancy")
    rating: Optional[float] = Field(None, ge=0.0, le=5.0, description="Aggregate rating (0–5)")
    review_count: Optional[int] = Field(None, ge=0, description="Total number of reviews")
    amenities: list[str] = Field(default_factory=list, description="List of amenity strings")
    images: list[str] = Field(default_factory=list, description="List of image URLs")
    url: Optional[str] = Field(None, description="Canonical listing URL")
    host_or_brand: Optional[str] = Field(None, description="Host name or hotel brand")
    first_seen_at: datetime = Field(..., description="UTC timestamp when first scraped")
    last_seen_at: datetime = Field(..., description="UTC timestamp of most-recent scrape")

    @model_validator(mode="after")
    def last_seen_not_before_first_seen(self) -> "Listing":
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("last_seen_at must not be before first_seen_at")
        return self


# ---------------------------------------------------------------------------
# ListingSnapshot — price / availability at a specific search run
# ---------------------------------------------------------------------------


class ListingSnapshot(BaseModel):
    """Price and availability captured for a Listing during one SearchRun."""

    id: Optional[int] = Field(None, description="DB primary key")
    listing_id: int = Field(..., description="Foreign key to Listing.id")
    run_id: int = Field(..., description="Foreign key to SearchRun.id")
    nightly_price: Optional[float] = Field(None, ge=0.0, description="Per-night price")
    currency: Optional[str] = Field(None, min_length=3, max_length=3, description="ISO 4217 currency code")
    total_price: Optional[float] = Field(None, ge=0.0, description="Total trip price including fees")
    fees: dict[str, Any] = Field(default_factory=dict, description="Itemised fees (cleaning, service, …)")
    availability: Optional[bool] = Field(None, description="True if available for the queried dates")
    captured_at: datetime = Field(..., description="UTC timestamp when snapshot was taken")


# ---------------------------------------------------------------------------
# SearchRun — a single execution of the scanner
# ---------------------------------------------------------------------------


class SearchRun(BaseModel):
    """Metadata for one scan job."""

    id: Optional[int] = Field(None, description="DB primary key")
    area_query: str = Field(..., min_length=1, description="The area string that was searched")
    checkin: Optional[date] = Field(None)
    checkout: Optional[date] = Field(None)
    guests: Optional[int] = Field(None, ge=1)
    sources: list[Source] = Field(default_factory=list, description="Sources that were scraped")
    started_at: datetime = Field(..., description="UTC timestamp when the run started")
    finished_at: Optional[datetime] = Field(None, description="UTC timestamp when the run ended")
    status: SearchRunStatus = Field("running", description="Current status of the run")
    stats: dict[str, Any] = Field(default_factory=dict, description="Aggregated run statistics")

    @model_validator(mode="after")
    def finished_after_started(self) -> "SearchRun":
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not be before started_at")
        return self


# ---------------------------------------------------------------------------
# ExtractionLog — LLM extraction metadata per RawPayload
# ---------------------------------------------------------------------------


class ExtractionLog(BaseModel):
    """Records token usage and outcome for one LLM extraction call."""

    id: Optional[int] = Field(None, description="DB primary key")
    raw_scrape_id: int = Field(..., description="Foreign key to RawPayload/RawScrape.id")
    model: str = Field(..., min_length=1, description="Model identifier used (e.g. 'claude-opus-4-8')")
    input_tokens: int = Field(..., ge=0, description="Prompt tokens consumed")
    output_tokens: int = Field(..., ge=0, description="Completion tokens consumed")
    cache_read_tokens: int = Field(0, ge=0, description="Tokens served from the prompt cache")
    status: ExtractionStatus = Field(..., description="'success' or 'failed'")
    error: Optional[str] = Field(None, description="Error detail when status='failed'")
