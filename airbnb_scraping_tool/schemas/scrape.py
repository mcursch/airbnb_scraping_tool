"""Schemas for search queries and raw scraped payloads."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel


class SearchQuery(BaseModel):
    """Parameters for a single search request."""

    area: str
    checkin: date | None = None
    checkout: date | None = None
    guests: int = 1
    sources: list[Literal["airbnb", "booking"]] = ["airbnb", "booking"]


class RawPayload(BaseModel):
    """A raw scraped payload before LLM extraction."""

    source: Literal["airbnb", "booking"]
    url: str
    payload: str  # raw HTML or JSON string
    content_hash: str  # sha256 hex of payload
