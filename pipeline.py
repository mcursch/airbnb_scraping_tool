"""Orchestrates the acquire → extract → store pipeline."""

from __future__ import annotations

from schemas.models import RawPayload, SearchQuery


# ---------------------------------------------------------------------------
# Stage stubs — each will be replaced by a real implementation in later stages
# ---------------------------------------------------------------------------


def acquire(query: SearchQuery) -> list[RawPayload]:
    """Stage 1 — Scrape raw payloads for the given search query.

    Iterates over the requested sources, delegates to the appropriate
    :class:`~scrapers.base.ScrapeProvider`, and persists ``RawScrape`` rows so
    that a crash during extraction never loses fetched data.

    Args:
        query: The search parameters supplied by the caller.

    Returns:
        A list of :class:`~schemas.models.RawPayload` objects ready for extraction.
    """
    raise NotImplementedError("acquire() is not yet implemented")


def extract(payloads: list[RawPayload]) -> list[dict]:
    """Stage 2 — Normalise raw payloads into structured ``Listing`` records.

    Pre-trims HTML/JSON to reduce token cost, then calls the Claude API via
    ``client.messages.parse()`` with the ``Listing`` Pydantic schema to obtain
    guaranteed-valid structured output.

    Args:
        payloads: The raw payloads produced by :func:`acquire`.

    Returns:
        A list of validated listing dicts (will become ``Listing`` Pydantic
        models once the extraction stage is implemented).
    """
    raise NotImplementedError("extract() is not yet implemented")


def store(listings: list[dict]) -> None:
    """Stage 3 — Upsert structured listings into the database.

    Inserts or updates ``Listing`` rows keyed by ``(source, source_listing_id)``,
    records a ``ListingSnapshot`` for this run, and updates ``ExtractionLog``
    rows with token-usage statistics.

    Args:
        listings: Validated listing dicts produced by :func:`extract`.
    """
    raise NotImplementedError("store() is not yet implemented")


# ---------------------------------------------------------------------------
# Top-level entry point called by the CLI
# ---------------------------------------------------------------------------


def run(query: SearchQuery) -> None:
    """Run the full acquire → extract → store pipeline for *query*.

    Args:
        query: The search parameters supplied by the CLI or another caller.
    """
    payloads = acquire(query)
    listings = extract(payloads)
    store(listings)
