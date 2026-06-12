"""Pipeline orchestration: acquire → extract → store.

This module contains the per-RawScrape processing logic including content-hash
deduplication so that identical payloads from repeat scrapes skip the LLM.

Dedup flow
----------
Before dispatching a RawScrape to the extractor the pipeline checks whether
any *previous* extraction for the same ``content_hash`` already succeeded.  On
a cache hit it:

  1. Reuses the existing ``listing_id`` without calling the LLM.
  2. Still inserts a new ``ListingSnapshot`` so the run has a price record.
  3. Records an ``ExtractionLog`` with ``status='dedup'`` and zero token counts.

On a cache miss it calls the extractor normally, upserts the Listing, inserts a
snapshot, and records real token usage with ``status='extracted'``.
"""

from sqlalchemy.orm import Session

from db.models import ExtractionLog, RawScrape
from db.repo import find_extraction_by_content_hash, insert_snapshot, upsert_listing
from extraction.extractor import Extractor


def process_raw_scrape(
    session: Session,
    raw_scrape: RawScrape,
    extractor: Extractor,
) -> ExtractionLog:
    """Process one RawScrape through the extract-and-store pipeline.

    Parameters
    ----------
    session:
        An active SQLAlchemy session.  The caller is responsible for calling
        ``session.commit()`` after this function returns (or wrapping multiple
        calls in a transaction).
    raw_scrape:
        The RawScrape row to process.  Its ``run_id`` is used as the
        ``run_id`` for the new ``ListingSnapshot``.
    extractor:
        An object implementing the ``Extractor`` protocol.  In production this
        wraps the Anthropic SDK; in tests it is mocked.

    Returns
    -------
    ExtractionLog
        The newly created log row (not yet committed).
    """
    run_id = raw_scrape.run_id

    # ------------------------------------------------------------------
    # Content-hash dedup check
    # ------------------------------------------------------------------
    existing_listing_id = find_extraction_by_content_hash(session, raw_scrape.content_hash)

    if existing_listing_id is not None:
        # Cache hit: reuse the existing Listing without calling the LLM.
        insert_snapshot(session, listing_id=existing_listing_id, run_id=run_id)

        log = ExtractionLog(
            raw_scrape_id=raw_scrape.id,
            listing_id=existing_listing_id,
            model=None,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            status="dedup",
        )
        session.add(log)
        raw_scrape.status = "extracted"
        session.flush()
        return log

    # ------------------------------------------------------------------
    # Normal extraction path
    # ------------------------------------------------------------------
    result = extractor.extract(raw_scrape)

    listing = upsert_listing(session, result.listing_data)

    insert_snapshot(session, listing_id=listing.id, run_id=run_id)

    log = ExtractionLog(
        raw_scrape_id=raw_scrape.id,
        listing_id=listing.id,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        status="extracted",
    )
    session.add(log)
    raw_scrape.status = "extracted"
    session.flush()
    return log
