"""
Message Batches API path for bulk extraction runs.

batch_extract()
---------------
When the number of raw scrapes exceeds the configurable ``batch_threshold``
(default 20, read from config.py), this function submits all pre-trimmed
payloads as a **single** Anthropic Message Batches request, polls the batch
until it reaches ``processing_status == "ended"``, then ingests each result
using the same ExtractionLog and RawScrape-status logic as the synchronous
path.

Below the threshold the function transparently delegates to
``extraction.extractor.extract_listings`` so callers can always use
``batch_extract`` regardless of the expected record count.

Batch request format
--------------------
Each ``BatchMessageRequest`` uses the record's database ``id`` as its
``custom_id`` so results can be matched back to the originating row without
relying on list position (results may arrive in arbitrary order).

The frozen system prompt is cached with ``cache_control: {type: ephemeral}``
just like the synchronous path; the per-record payload is appended after
the cache breakpoint.

Result ingestion
----------------
Succeeded results have their response content parsed from JSON into a
``ListingExtraction`` Pydantic model.  Errored or expired results are
recorded as failed ``ExtractionLog`` rows and the corresponding
``RawScrape.status`` is set to ``"failed"``, matching the synchronous
error-handling contract.
"""
from __future__ import annotations

import json
import time
from typing import Any

import anthropic
from sqlalchemy.orm import Session

from config import settings
from db.models import ExtractionLog, RawScrape
from extraction.extractor import SYSTEM_PROMPT, _get_default_client, extract_listings
from extraction.pretrim import pretrim
from schemas.listing import ListingExtraction

MODEL = "claude-opus-4-8"

# Seconds to wait between polls when the batch is still processing.
_POLL_INTERVAL_SECONDS = 60


def batch_extract(
    raw_scrapes: list[RawScrape],
    session: Session,
    *,
    threshold: int | None = None,
    client: anthropic.Anthropic | None = None,
) -> list[ListingExtraction]:
    """Extract listings, using the Message Batches API when above *threshold*.

    Parameters
    ----------
    raw_scrapes:
        ORM rows to process.  Each must already be persisted (has an ``id``).
    session:
        Active SQLAlchemy session.  Flushed after each record result is
        ingested; committed once at the end.
    threshold:
        Maximum number of records to process synchronously.  When
        ``len(raw_scrapes) > threshold`` the Batches API is used instead.
        Defaults to ``settings.batch_threshold`` (20).
    client:
        Anthropic client to use.  Defaults to the extractor module's
        singleton (created with ``max_retries=3``).  Pass a mock in tests.

    Returns
    -------
    list[ListingExtraction]
        Successful extraction results (failed records are omitted).
    """
    if threshold is None:
        threshold = settings.batch_threshold

    if len(raw_scrapes) <= threshold:
        # Below threshold — delegate to the synchronous pipeline unchanged.
        return extract_listings(raw_scrapes, session, client=client)

    if client is None:
        client = _get_default_client()

    return _batch_extract_via_api(raw_scrapes, session, client)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_batch_requests(raw_scrapes: list[RawScrape]) -> list[dict[str, Any]]:
    """Build the list of request dicts for the batch, one per raw scrape.

    Records whose payload cannot be pre-trimmed are silently excluded from
    the batch request list; the caller is responsible for handling the gap.
    Returns a tuple of (request_list, skipped_ids) but the current
    implementation just builds the request list and lets the ingestion step
    deal with missing results by matching custom_ids.
    """
    requests: list[dict[str, Any]] = []
    for raw_scrape in raw_scrapes:
        try:
            trimmed = pretrim(raw_scrape.payload or "")
        except ValueError:
            # Pretrim failed — will be handled in _ingest_results as a missing result.
            continue

        requests.append(
            {
                "custom_id": str(raw_scrape.id),
                "params": {
                    "model": MODEL,
                    "max_tokens": 4096,
                    "system": [
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Extract all listings from this scraped content "
                                f"(source: {raw_scrape.source}):\n\n{trimmed}"
                            ),
                        }
                    ],
                },
            }
        )
    return requests


def _batch_extract_via_api(
    raw_scrapes: list[RawScrape],
    session: Session,
    client: anthropic.Anthropic,
) -> list[ListingExtraction]:
    """Submit a batch, poll until done, ingest results."""
    # Build a lookup of id → RawScrape for fast result matching.
    scrape_by_id: dict[str, RawScrape] = {str(rs.id): rs for rs in raw_scrapes}

    # --- Build and submit the batch ---
    batch_requests = _build_batch_requests(raw_scrapes)

    # Records that failed pretrim have no entry in batch_requests; mark them
    # failed now so they aren't left in "pending" after ingestion.
    submitted_ids = {req["custom_id"] for req in batch_requests}
    for raw_scrape in raw_scrapes:
        if str(raw_scrape.id) not in submitted_ids:
            raw_scrape.status = "failed"
            log = ExtractionLog(
                raw_scrape_id=raw_scrape.id,
                model=MODEL,
                status="failed",
                error="Payload failed pretrim; excluded from batch",
            )
            session.add(log)
            session.flush()

    batch = client.beta.messages.batches.create(requests=batch_requests)

    # --- Poll until complete ---
    while batch.processing_status != "ended":
        time.sleep(_POLL_INTERVAL_SECONDS)
        batch = client.beta.messages.batches.retrieve(batch.id)

    # --- Ingest results ---
    results: list[ListingExtraction] = []
    seen_ids: set[str] = set()

    for result in client.beta.messages.batches.results(batch.id):
        custom_id: str = result.custom_id
        seen_ids.add(custom_id)
        raw_scrape = scrape_by_id.get(custom_id)
        if raw_scrape is None:
            continue  # Shouldn't happen; ignore stale IDs

        result_type: str = result.result.type

        if result_type == "succeeded":
            try:
                message = result.result.message
                # Extract the text content and parse it as ListingExtraction JSON.
                text_content = _extract_text(message)
                extraction = ListingExtraction.model_validate_json(text_content)

                usage = getattr(message, "usage", None)
                log = ExtractionLog(
                    raw_scrape_id=raw_scrape.id,
                    model=MODEL,
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

        else:
            # "errored" or "expired"
            error_detail: str
            if result_type == "errored":
                error_obj = getattr(result.result, "error", None)
                error_detail = str(error_obj) if error_obj else f"Batch result type: {result_type}"
            else:
                error_detail = f"Batch result type: {result_type}"

            raw_scrape.status = "failed"
            log = ExtractionLog(
                raw_scrape_id=raw_scrape.id,
                model=MODEL,
                status="failed",
                error=error_detail,
            )
            session.add(log)
            session.flush()

    # Any submitted IDs that got no result at all → mark failed.
    for cid in submitted_ids - seen_ids:
        raw_scrape = scrape_by_id.get(cid)
        if raw_scrape is not None:
            raw_scrape.status = "failed"
            log = ExtractionLog(
                raw_scrape_id=raw_scrape.id,
                model=MODEL,
                status="failed",
                error="No result returned by batch API for this record",
            )
            session.add(log)
            session.flush()

    session.commit()
    return results


def _extract_text(message: Any) -> str:
    """Pull the text string out of a batch result message.

    Handles both:
    - SDK objects with a ``content`` attribute (list of blocks)
    - Plain dicts (e.g. from test mocks)
    """
    content = getattr(message, "content", None)
    if content is None:
        raise ValueError("Batch result message has no content")

    for block in content:
        # SDK content block object
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type == "text":
            text = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else None
            )
            if text is not None:
                return text

    raise ValueError("No text block found in batch result message content")
