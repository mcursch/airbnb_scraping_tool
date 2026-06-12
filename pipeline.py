"""Acquire → Extract → Store pipeline.

Orchestrates a full search run:

1. **Acquire** – each registered ``ScrapeProvider`` fetches raw pages for the
   query.  Payloads are persisted immediately so a crash later loses no data.
2. **Extract** – for each raw scrape the ``Extractor`` calls the LLM to
   produce a structured ``ListingExtraction``.  Content-hash dedup skips
   payloads whose hash already exists in the database (no LLM call, no cost).
3. **Store** – each extracted listing is upserted and a ``ListingSnapshot``
   (price/availability at this moment) is written.
4. **Close** – the ``SearchRun`` is closed with ``finished_at`` and aggregated
   stats written to ``SearchRun.stats``.

Stats keys (also read by the Stage 5 dashboard):
    total_listings    – total unique listings touched this run
                        (equals new + updated + unchanged)
    new               – listings inserted for the first time
    updated           – existing listings whose fields were refreshed
    unchanged         – existing listings whose data was identical (no-op upsert)
    dedup_hits        – raw scrapes skipped because content_hash already existed
    total_tokens      – cumulative input + output + cache_read tokens
    estimated_cost_usd – approximate USD cost at claude-opus-4-8 pricing
"""

from __future__ import annotations

import logging
from typing import Any

from airbnb_scraping_tool.db.models import SessionLocal, init_db
from airbnb_scraping_tool.db.repo import Repo
from airbnb_scraping_tool.extraction.extractor import Extractor
from airbnb_scraping_tool.schemas import SearchQuery
from airbnb_scraping_tool.scrapers.base import ScrapeProvider

logger = logging.getLogger(__name__)


class Pipeline:
    """Stateless orchestrator; creates its own DB session per ``run()`` call."""

    def __init__(
        self,
        scrapers: list[ScrapeProvider],
        extractor: Extractor,
        repo: Repo | None = None,
    ) -> None:
        self.scrapers = scrapers
        self.extractor = extractor
        self.repo = repo or Repo()

    def run(self, query: SearchQuery) -> int:
        """Execute a full pipeline run and return the ``SearchRun.id``.

        The run is always closed (even on partial failure) so the dashboard
        can show progress and cost.
        """
        init_db()  # no-op if tables already exist

        # Accumulated stats — see module docstring for key definitions
        stats: dict[str, Any] = {
            "total_listings": 0,
            "new": 0,
            "updated": 0,
            "unchanged": 0,
            "dedup_hits": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
        }

        with SessionLocal() as session:
            run = self.repo.open_run(
                session,
                area_query=query.area,
                checkin=str(query.checkin) if query.checkin else None,
                checkout=str(query.checkout) if query.checkout else None,
                guests=query.guests,
                sources=list(query.sources),
            )
            run_id = run.id
            session.commit()

            try:
                run_id = self._execute(session, run_id, query, stats)
                self.repo.close_run(session, run_id, status="done")
            except Exception:
                logger.exception("Pipeline failed for run %s", run_id)
                self.repo.close_run(session, run_id, status="failed")
                raise
            finally:
                self.repo.record_run_stats(session, run_id, stats)
                session.commit()

        return run_id

    def _execute(
        self,
        session: Any,
        run_id: int,
        query: SearchQuery,
        stats: dict[str, Any],
    ) -> int:
        """Inner implementation — separated so the outer method can always close."""

        for scraper in self.scrapers:
            if scraper.source not in query.sources:
                logger.debug("Skipping scraper %s (not in query sources)", scraper.source)
                continue

            logger.info("Acquiring from %s for '%s'", scraper.source, query.area)
            try:
                payloads = scraper.search(query)
            except Exception:
                logger.exception("Scraper %s failed", scraper.source)
                continue

            for raw in payloads:
                self._process_payload(session, run_id, raw, stats)

        return run_id

    def _process_payload(
        self,
        session: Any,
        run_id: int,
        raw: Any,
        stats: dict[str, Any],
    ) -> None:
        """Persist one raw payload, extract it (unless deduped), and store the listing."""
        from airbnb_scraping_tool.db.models import RawScrape

        # --- Dedup check ---------------------------------------------------
        existing = self.repo.find_by_hash(session, raw.content_hash)
        if existing is not None:
            logger.debug("Dedup hit for hash %s", raw.content_hash[:12])
            stats["dedup_hits"] += 1
            # Still record a scrape row so audits can trace the run
            rs = self.repo.save_raw_scrape(
                session, run_id, raw.source, raw.url, raw.payload, raw.content_hash
            )
            self.repo.mark_scrape_status(session, rs.id, "deduped")
            session.flush()
            return

        # --- Persist raw scrape -------------------------------------------
        rs = self.repo.save_raw_scrape(
            session, run_id, raw.source, raw.url, raw.payload, raw.content_hash
        )

        # --- Extract -------------------------------------------------------
        result = self.extractor.extract(raw.source, raw.url, raw.payload)

        # Record extraction log regardless of success/failure
        self.repo.log_extraction(
            session,
            raw_scrape_id=rs.id,
            model=self.extractor.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            status=result.status,
            error=result.error,
        )

        # Accumulate token stats
        stats["total_tokens"] += result.total_tokens
        stats["estimated_cost_usd"] += result.estimated_cost_usd

        if result.status == "failed" or result.listing is None:
            self.repo.mark_scrape_status(session, rs.id, "failed", error=result.error)
            session.flush()
            return

        self.repo.mark_scrape_status(session, rs.id, "extracted")

        # --- Store ---------------------------------------------------------
        extracted = result.listing

        listing_fields = {
            "name": extracted.name,
            "property_type": extracted.property_type,
            "lat": extracted.lat,
            "lon": extracted.lon,
            "address_text": extracted.address_text,
            "bedrooms": extracted.bedrooms,
            "beds": extracted.beds,
            "baths": extracted.baths,
            "max_guests": extracted.max_guests,
            "rating": extracted.rating,
            "review_count": extracted.review_count,
            "amenities": extracted.amenities,
            "images": extracted.images,
            "url": extracted.url,
            "host_or_brand": extracted.host_or_brand,
        }

        listing, is_new, was_updated = self.repo.upsert_listing(
            session,
            source=raw.source,
            source_listing_id=extracted.source_listing_id,
            **listing_fields,
        )

        self.repo.insert_snapshot(
            session,
            listing_id=listing.id,
            run_id=run_id,
            nightly_price=extracted.nightly_price,
            currency=extracted.currency,
            total_price=extracted.total_price,
            fees=extracted.fees or None,
            availability=extracted.availability,
        )

        # Accumulate listing stats
        # total_listings == new + updated + unchanged
        stats["total_listings"] += 1
        if is_new:
            stats["new"] += 1
        elif was_updated:
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1

        session.flush()
