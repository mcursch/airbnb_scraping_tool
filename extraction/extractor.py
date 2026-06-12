"""
Synchronous Claude extraction pipeline.

extract_listings()
------------------
Iterates over a list of RawScrape ORM rows, calls the Anthropic API with a
cached system prompt and a per-record volatile payload, writes an ExtractionLog
row for every record (success *or* failure), and marks RawScrape.status
accordingly.  Exceptions from any single record are caught and logged so the
pipeline always continues to the next record.

Prompt caching
--------------
The frozen system prompt block carries ``cache_control: {type: ephemeral}``.
The Anthropic SDK sends ``cache-write`` headers on the first call and
``cache-read`` on subsequent calls, which is reflected in
``usage.cache_read_input_tokens``.

SDK retries
-----------
The Anthropic client is created with ``max_retries=3`` so transient network
errors and 529 overload responses are retried automatically before the
per-record except block fires.
"""
from __future__ import annotations

import anthropic
from sqlalchemy.orm import Session

from db.models import ExtractionLog, RawScrape
from extraction.pretrim import pretrim
from schemas.listing import ListingExtraction

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """\
You are an expert data-extraction assistant specialising in short-stay
accommodation listings (Airbnb, Booking.com, hotels, etc.).

Given raw scraped content from a listing page or search-results page, extract
every listing you can find and return them as structured data matching the
provided schema.

Rules:
- Extract only information that is explicitly present in the content.
- If a field is not present, omit it (leave it null / empty).
- source_listing_id must be the platform's own unique identifier for the
  listing (numeric ID, slug, etc.), not a URL.
- Prices should be numeric values without currency symbols.
- amenities and images should be flat lists of strings.
- It is valid to return an empty listings array if no listings are found.\
"""

# Module-level singleton; overridden in tests by passing `client=` kwarg.
_default_client: anthropic.Anthropic | None = None


def _get_default_client() -> anthropic.Anthropic:
    global _default_client
    if _default_client is None:
        _default_client = anthropic.Anthropic(max_retries=3)
    return _default_client


def extract_listings(
    raw_scrapes: list[RawScrape],
    session: Session,
    *,
    client: anthropic.Anthropic | None = None,
) -> list[ListingExtraction]:
    """Extract listings from *raw_scrapes* using Claude.

    Parameters
    ----------
    raw_scrapes:
        ORM rows to process.  Each must already be persisted (has an ``id``).
    session:
        Active SQLAlchemy session.  The function flushes after each record and
        commits once at the end.
    client:
        Anthropic client to use.  Defaults to the module-level singleton
        (created with ``max_retries=3``).  Pass a mock/stub in tests.

    Returns
    -------
    list[ListingExtraction]
        Successful extraction results in the same order as the successful
        records (failed records are omitted from the return value).
    """
    if client is None:
        client = _get_default_client()

    results: list[ListingExtraction] = []

    for raw_scrape in raw_scrapes:
        try:
            trimmed = pretrim(raw_scrape.payload or "")

            response = client.messages.parse(
                model=MODEL,
                max_tokens=4096,
                # Frozen system prompt — marked for prompt caching.
                # The SDK sends cache-write headers on the first request and
                # cache-read on subsequent requests with identical content.
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                # Per-record volatile payload placed AFTER the cache breakpoint.
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Extract all listings from this scraped content "
                            f"(source: {raw_scrape.source}):\n\n{trimmed}"
                        ),
                    }
                ],
                response_format=ListingExtraction,
            )

            extraction: ListingExtraction = response.parsed
            usage = response.usage

            log = ExtractionLog(
                raw_scrape_id=raw_scrape.id,
                model=MODEL,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
                status="success",
            )
            session.add(log)
            raw_scrape.status = "extracted"
            session.flush()

            results.append(extraction)

        except Exception as exc:  # noqa: BLE001
            raw_scrape.status = "failed"
            log = ExtractionLog(
                raw_scrape_id=raw_scrape.id,
                model=MODEL,
                status="failed",
                error=str(exc),
            )
            session.add(log)
            session.flush()
            # Continue to the next record — never let one failure abort the batch.

    session.commit()
    return results
