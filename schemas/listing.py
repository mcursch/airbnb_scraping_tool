"""
Pydantic schemas used by the LLM extraction pipeline.

ListingExtraction is the response_format passed to client.messages.parse().
ExtractedListing mirrors the Listing DB model fields that Claude can populate
from raw scraped content.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedListing(BaseModel):
    """A single short-stay listing extracted by Claude from raw scraped content."""

    source_listing_id: str = Field(
        ..., description="Unique listing ID from the source platform (e.g. Airbnb room ID)"
    )
    name: str = Field(..., description="Listing title / display name")
    property_type: str | None = Field(
        None, description="Type of property: apartment, house, hotel room, studio, etc."
    )
    lat: float | None = Field(None, description="Latitude (WGS-84)")
    lon: float | None = Field(None, description="Longitude (WGS-84)")
    address_text: str | None = Field(
        None, description="Human-readable address or neighbourhood/city"
    )
    bedrooms: int | None = Field(None, description="Number of bedrooms (0 = studio)")
    beds: int | None = Field(None, description="Number of beds")
    baths: float | None = Field(None, description="Number of bathrooms (0.5 = shared)")
    max_guests: int | None = Field(None, description="Maximum number of guests allowed")
    rating: float | None = Field(None, description="Average guest rating on a 0–5 scale")
    review_count: int | None = Field(None, description="Total number of guest reviews")
    amenities: list[str] = Field(
        default_factory=list, description="List of amenities (WiFi, Kitchen, etc.)"
    )
    images: list[str] = Field(
        default_factory=list, description="List of image URLs"
    )
    url: str | None = Field(None, description="Direct URL to the listing page")
    host_or_brand: str | None = Field(
        None, description="Host name (Airbnb) or hotel brand (hotels)"
    )
    nightly_price: float | None = Field(None, description="Nightly price before fees")
    currency: str | None = Field(None, description="ISO 4217 currency code, e.g. USD")
    total_price: float | None = Field(None, description="Total price for the requested stay")


class ListingExtraction(BaseModel):
    """Top-level response schema returned by Claude for a single scraped page."""

    listings: list[ExtractedListing] = Field(
        ...,
        description=(
            "All individual listings found in the scraped content. "
            "May be one (detail page) or many (search-results page)."
        ),
    )
