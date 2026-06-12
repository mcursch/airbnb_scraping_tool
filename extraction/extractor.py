"""Extractor interface and result type.

The concrete implementation (calling claude-opus-4-8 via the Anthropic SDK) is
added in Stage 3.  This module defines the protocol that pipeline.py depends on
so that the dedup logic and tests can operate against a typed interface.
"""

from dataclasses import dataclass, field
from typing import Protocol

from db.models import RawScrape


@dataclass
class ExtractionResult:
    """Normalised output from one LLM extraction call."""

    listing_data: dict
    """Dict suitable for passing to ``repo.upsert_listing``."""

    model: str
    """Model identifier used for the call (e.g. ``'claude-opus-4-8'``)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


class Extractor(Protocol):
    """Callable interface for extraction implementations."""

    def extract(self, raw_scrape: RawScrape) -> ExtractionResult:
        """Extract a structured Listing from a raw scrape payload.

        Implementations should call the LLM and return an ExtractionResult.
        Any exception propagates to the caller (pipeline.py handles failures).
        """
        ...
