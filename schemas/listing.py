"""
Pydantic schemas used by the LLM extraction pipeline.

ListingExtraction is the response_format passed to client.messages.parse().
ExtractedListing mirrors the Listing DB model fields that Claude can populate
from raw scraped content.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class FeeItem(BaseModel):
    """A single itemised fee. A closed schema (no open-ended dict) so the
    structured-output grammar compiles — `dict[str, Any]` triggers
    'Schema is too complex' / 'Grammar compilation timed out' from the API."""

    name: str = Field(..., description="Fee name, e.g. 'cleaning fee', 'service fee'")
    amount: float | None = Field(None, description="Fee amount")
    currency: str | None = Field(None, description="ISO 4217 currency code")


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
    fees: list[FeeItem] = Field(
        default_factory=list, description="Itemised fees (cleaning, service, etc.)"
    )
    availability: bool | None = Field(
        None, description="True if available for the requested dates"
    )

    # ── Host & trust signals ────────────────────────────────────────────────
    host_is_superhost: bool | None = Field(
        None, description="True if the host is an Airbnb Superhost (or equivalent badge)"
    )
    host_response_rate: int | None = Field(
        None, description="Host response rate as a percentage 0–100"
    )
    host_response_time: str | None = Field(
        None, description="Host response time, e.g. 'within an hour', 'within a day'"
    )
    years_hosting: int | None = Field(
        None, description="How many years the host has been hosting"
    )
    rating_cleanliness: float | None = Field(
        None, description="Cleanliness sub-rating on a 0–5 scale"
    )
    rating_location: float | None = Field(
        None, description="Location sub-rating on a 0–5 scale"
    )
    rating_value: float | None = Field(
        None, description="Value-for-money sub-rating on a 0–5 scale"
    )
    license_number: str | None = Field(
        None, description="Rental licence / registration number where shown"
    )

    # ── Pricing breakdown (per-stay; complements the `fees` list) ────────────
    cleaning_fee: float | None = Field(None, description="Cleaning fee amount")
    service_fee: float | None = Field(None, description="Service fee amount")
    taxes: float | None = Field(None, description="Taxes / occupancy fees amount")
    deposit: float | None = Field(None, description="Security/damage deposit amount")
    weekly_discount_pct: float | None = Field(
        None, description="Weekly-stay discount as a percentage 0–100"
    )
    monthly_discount_pct: float | None = Field(
        None, description="Monthly-stay discount as a percentage 0–100"
    )
    minimum_nights: int | None = Field(
        None, description="Minimum number of nights required to book"
    )

    # ── Location precision ──────────────────────────────────────────────────
    neighborhood: str | None = Field(
        None, description="Neighbourhood / district name within the city"
    )
    distance_to_center_km: float | None = Field(
        None, description="Distance to the city centre in kilometres"
    )

    # ── Policies & rules ────────────────────────────────────────────────────
    cancellation_policy: str | None = Field(
        None, description="Cancellation policy, e.g. 'Flexible', 'Moderate', 'Strict'"
    )
    checkin_time: str | None = Field(
        None, description="Check-in time or window, e.g. '15:00' or 'After 3:00 PM'"
    )
    checkout_time: str | None = Field(
        None, description="Check-out time, e.g. '11:00' or 'Before 11:00 AM'"
    )
    instant_book: bool | None = Field(
        None, description="True if the listing can be booked instantly (no host approval)"
    )
    pets_allowed: bool | None = Field(None, description="True if pets are allowed")
    smoking_allowed: bool | None = Field(None, description="True if smoking is allowed")
    events_allowed: bool | None = Field(
        None, description="True if parties/events are allowed"
    )


class ListingExtraction(BaseModel):
    """Top-level response schema returned by Claude for a single scraped page."""

    listings: list[ExtractedListing] = Field(
        ...,
        description=(
            "All individual listings found in the scraped content. "
            "May be one (detail page) or many (search-results page)."
        ),
    )
