"""Search-query schema shared across all scraper providers."""

from __future__ import annotations

import datetime

from pydantic import BaseModel


class SearchQuery(BaseModel):
    """Describes a user's search request passed to every ScrapeProvider."""

    area: str
    checkin: datetime.date | None = None
    checkout: datetime.date | None = None
    guests: int = 1
