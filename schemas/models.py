"""Pydantic schemas shared across the pipeline."""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    """Parameters that define a single scan request."""

    area: str
    checkin: datetime.date | None = None
    checkout: datetime.date | None = None
    guests: int = 1
    sources: list[Literal["airbnb", "hotels"]] = ["airbnb", "hotels"]


class RawPayload(BaseModel):
    """Raw scraped payload before extraction/normalisation."""

    source: str
    url: str
    payload: str  # raw text/JSON content
    fetched_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
