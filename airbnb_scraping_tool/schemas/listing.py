"""Pydantic schemas for listing data.

``ListingExtraction`` is the schema passed to the LLM (structured outputs).
``Listing`` is the canonical representation used throughout the rest of the app.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ListingExtraction(BaseModel):
    """Output schema for Claude structured extraction."""

    source_listing_id: str = Field(description="Platform-unique identifier for this listing")
    name: str = Field(description="Listing title / property name")
    property_type: str | None = Field(None, description="e.g. 'Entire apartment', 'Private room'")
    lat: float | None = Field(None, description="Latitude")
    lon: float | None = Field(None, description="Longitude")
    address_text: str | None = Field(None, description="Human-readable address or neighbourhood")
    bedrooms: int | None = None
    beds: int | None = None
    baths: float | None = None
    max_guests: int | None = None
    rating: float | None = Field(None, ge=0, le=5)
    review_count: int | None = None
    amenities: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list, description="Image URLs")
    url: str = Field(description="Canonical listing URL")
    host_or_brand: str | None = Field(None, description="Host name or hotel brand")
    nightly_price: float | None = Field(None, description="Nightly price in local currency")
    currency: str | None = Field(None, description="ISO currency code, e.g. 'USD'")
    total_price: float | None = Field(None, description="Total price for the stay including fees")
    fees: dict[str, Any] = Field(default_factory=dict, description="Itemised fee breakdown")
    availability: bool | None = Field(None, description="True if available for the requested dates")


class Listing(BaseModel):
    """Canonical listing record stored in the database."""

    source: Literal["airbnb", "booking"]
    source_listing_id: str
    name: str
    property_type: str | None = None
    lat: float | None = None
    lon: float | None = None
    address_text: str | None = None
    bedrooms: int | None = None
    beds: int | None = None
    baths: float | None = None
    max_guests: int | None = None
    rating: float | None = None
    review_count: int | None = None
    amenities: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    url: str
    host_or_brand: str | None = None
