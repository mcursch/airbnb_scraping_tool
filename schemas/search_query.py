from datetime import date

from pydantic import BaseModel, Field, model_validator


class SearchQuery(BaseModel):
    """Parameters for a short-stay property search."""

    area: str = Field(..., description="Area / location to search (e.g. 'Lisbon, Portugal')")
    checkin: date | None = Field(None, description="Check-in date (YYYY-MM-DD)")
    checkout: date | None = Field(None, description="Check-out date (YYYY-MM-DD)")
    guests: int = Field(1, ge=1, description="Number of guests")

    @model_validator(mode="after")
    def _validate_dates(self) -> "SearchQuery":
        if self.checkin and self.checkout and self.checkin >= self.checkout:
            raise ValueError("checkout must be strictly after checkin")
        return self
