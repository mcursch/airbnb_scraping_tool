"""Extractor interface, result type, and synchronous extraction entry-point.

``SYSTEM_PROMPT``, ``_get_default_client``, and ``extract_listings`` provide
the concrete implementation (calling claude-opus-4-8 via the Anthropic SDK).
The ``ExtractionResult`` dataclass and ``Extractor`` Protocol are kept for
backwards compatibility with pipeline.py.
"""

from __future__ import annotations

import anthropic
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from config import settings
from db.models import ExtractionLog, RawScrape
from extraction.pretrim import pretrim
# Reuse the schema-embedded, JSON-only system prompt and JSON extractor from the
# provider module so both extraction paths stay in lock-step. batch.py imports
# SYSTEM_PROMPT from here, so this keeps the Batches path aligned too.
from extraction.provider import SYSTEM_PROMPT, _extract_json
from schemas.listing import ListingExtraction

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL = "claude-opus-4-8"

# ---------------------------------------------------------------------------
# Default client factory
# ---------------------------------------------------------------------------


def _get_default_client() -> anthropic.Anthropic:
    """Return an Anthropic client configured from application settings."""
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3)


# ---------------------------------------------------------------------------
# Synchronous extraction entry-point
# ---------------------------------------------------------------------------


def extract_listings(
    raw_scrapes: list[RawScrape],
    session: Session,
    *,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
) -> list[ListingExtraction]:
    """Extract structured listings from a list of raw scrapes.

    For each ``RawScrape``:

    * Empty / blank / ``None`` payloads are marked ``status='failed'`` and a
      failed ``ExtractionLog`` row is written; the record is skipped.
    * Valid payloads are pre-trimmed then sent to the LLM.  On success the
      scrape is marked ``'extracted'`` and a success log is written.
    * Any exception from the API is caught per-record; a failed log is written
      and processing continues with the next record.

    ``session.flush()`` is called after each record; a single
    ``session.commit()`` is issued at the end.

    Returns
    -------
    list[ListingExtraction]
        Successful extraction results only (failed records are omitted).
    """
    if client is None:
        client = _get_default_client()
    if model is None:
        model = settings.llm_model

    results: list[ListingExtraction] = []

    for raw_scrape in raw_scrapes:
        payload = raw_scrape.payload

        # ------------------------------------------------------------------
        # Guard: reject empty / blank / None payloads before calling the LLM
        # ------------------------------------------------------------------
        if not payload or not payload.strip():
            raw_scrape.status = "failed"
            log = ExtractionLog(
                raw_scrape_id=raw_scrape.id,
                model=model,
                status="failed",
                error="Empty or blank payload; skipping extraction",
            )
            session.add(log)
            session.flush()
            continue

        # ------------------------------------------------------------------
        # Happy path: pre-trim → LLM call → persist
        # ------------------------------------------------------------------
        try:
            trimmed = pretrim(payload)

            # JSON mode (not grammar-constrained structured output): our listing
            # schema is too rich for constrained decoding ("Schema is too
            # complex" / "Grammar compilation timed out"). Ask for JSON and
            # validate with Pydantic instead.
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Extract all listings from this scraped content "
                            f"(source: {raw_scrape.source}):\n\n{trimmed}"
                        ),
                    }
                ],
            )

            usage = response.usage
            text = "".join(b.text for b in response.content if b.type == "text")
            parsed = ListingExtraction.model_validate_json(_extract_json(text))

            log = ExtractionLog(
                raw_scrape_id=raw_scrape.id,
                model=model,
                input_tokens=getattr(usage, "input_tokens", None) if usage else None,
                output_tokens=getattr(usage, "output_tokens", None) if usage else None,
                cache_read_tokens=(
                    getattr(usage, "cache_read_input_tokens", None) if usage else None
                ),
                status="success",
            )
            session.add(log)
            raw_scrape.status = "extracted"
            session.flush()
            results.append(parsed)

        except Exception as exc:  # noqa: BLE001
            raw_scrape.status = "failed"
            log = ExtractionLog(
                raw_scrape_id=raw_scrape.id,
                model=model,
                status="failed",
                error=str(exc),
            )
            session.add(log)
            session.flush()

    session.commit()
    return results


# ---------------------------------------------------------------------------
# Legacy types kept for pipeline.py compatibility
# ---------------------------------------------------------------------------


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
