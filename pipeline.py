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

Flags:
    dry_run    – acquire only, no DB writes; exits cleanly with no rows.
    no_extract – persist ``RawScrape`` rows with ``status='pending'`` but skip
                 LLM extraction and produce zero ``ExtractionLog`` rows.

Stats keys (also read by the Stage 5 dashboard):
    total_listings    – total unique listings touched this run
    new               – listings inserted for the first time
    updated           – existing listings whose fields were refreshed
    dedup_hits        – raw scrapes skipped because content_hash already existed
    total_tokens      – cumulative input + output + cache_read tokens
    estimated_cost_usd – approximate USD cost at claude-opus-4-8 pricing

Structured logging:
    Each run writes JSON-lines to ``logs/<run_id>.jsonl`` (or
    ``logs/dry-<uuid>.jsonl`` for dry runs).  Every line contains at minimum
    ``ts``, ``level``, and ``msg`` keys.

SIGINT handling:
    A SIGINT handler is installed for the duration of the run.  It sets a
    cancellation flag so the pipeline finishes the current record, persists all
    completed work, marks the run ``status='cancelled'``, and exits cleanly.
    The original SIGINT handler is always restored before ``run()`` returns.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import signal
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from airbnb_scraping_tool.db.repo import Repo
from airbnb_scraping_tool.extraction.extractor import Extractor
from airbnb_scraping_tool.schemas import SearchQuery
from airbnb_scraping_tool.scrapers.base import ScrapeProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON-lines formatter
# ---------------------------------------------------------------------------


class _JsonLinesFormatter(logging.Formatter):
    """Emit one JSON object per log record with at least ``ts``, ``level``, ``msg``."""

    _EXTRA_KEYS = frozenset({"run_id", "source", "url", "stage", "status", "record_idx"})

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        for key in self._EXTRA_KEYS:
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def _setup_run_logger(log_id: str | int, log_dir: str) -> tuple[logging.Logger, logging.FileHandler]:
    """Create a dedicated logger that writes JSON-lines to ``log_dir/<log_id>.jsonl``."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / f"{log_id}.jsonl"

    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(_JsonLinesFormatter())
    handler.setLevel(logging.DEBUG)

    run_logger = logging.getLogger(f"pipeline.run.{log_id}")
    run_logger.setLevel(logging.DEBUG)
    run_logger.addHandler(handler)
    run_logger.propagate = False  # don't double-emit to root logger
    return run_logger, handler


# ---------------------------------------------------------------------------
# Retry / timeout helpers
# ---------------------------------------------------------------------------


def _call_with_timeout_and_retry(
    fn: Any,
    args: tuple,
    kwargs: dict,
    timeout: float,
    max_retries: int,
    label: str,
    run_logger: logging.Logger,
) -> Any:
    """Call *fn* with a per-attempt timeout; retry up to *max_retries* times.

    Raises the last exception if all attempts are exhausted.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            last_exc = exc
            # Don't wait=True — that would block until the thread finishes,
            # making the timeout completely unenforced.
            pool.shutdown(wait=False)
            run_logger.warning(
                "%s timed out after %.1fs (attempt %d/%d)",
                label,
                timeout,
                attempt + 1,
                max_retries + 1,
            )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            pool.shutdown(wait=False)
            run_logger.warning(
                "%s raised %s (attempt %d/%d): %s",
                label,
                type(exc).__name__,
                attempt + 1,
                max_retries + 1,
                exc,
            )
        if attempt < max_retries:
            backoff = min(2.0 ** attempt, 10.0)
            time.sleep(backoff)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline:
    """Stateless orchestrator; creates its own DB session per ``run()`` call.

    Parameters
    ----------
    session_factory:
        Optional callable that returns a new ``Session``.  When provided,
        ``init_db()`` is *not* called automatically (the caller is expected to
        have created the schema already).  This is the primary seam for tests.
    """

    def __init__(
        self,
        scrapers: list[ScrapeProvider],
        extractor: Extractor,
        repo: Repo | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self.scrapers = scrapers
        self.extractor = extractor
        self.repo = repo or Repo()
        self._session_factory = session_factory
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Session factory
    # ------------------------------------------------------------------

    def _make_session(self) -> Any:
        if self._session_factory is not None:
            return self._session_factory()
        from airbnb_scraping_tool.db.models import SessionLocal

        return SessionLocal()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        query: SearchQuery,
        dry_run: bool = False,
        no_extract: bool = False,
    ) -> int | None:
        """Execute a full pipeline run and return the ``SearchRun.id``.

        Returns ``None`` in *dry_run* mode (no DB rows created).

        The run is always closed (even on partial failure) so the dashboard
        can show progress and cost.
        """
        # Reset cancel flag in case the object is reused
        self._cancel_event.clear()

        # Install SIGINT handler — only valid from the main thread; silently
        # skip on worker threads (e.g. during tests that drive us directly).
        orig_sigint = signal.getsignal(signal.SIGINT)
        _installed = False
        if threading.current_thread() is threading.main_thread():
            def _sigint_handler(signum: int, frame: Any) -> None:  # noqa: ANN001
                self._cancel_event.set()
                # Restore immediately so a second Ctrl-C uses the default behaviour.
                signal.signal(signal.SIGINT, orig_sigint)

            signal.signal(signal.SIGINT, _sigint_handler)
            _installed = True

        # ----------------------------------------------------------------
        # Dry-run: acquire payloads, do not touch the database at all.
        # ----------------------------------------------------------------
        if dry_run:
            log_id = f"dry-{uuid.uuid4().hex[:8]}"
            from config import settings

            run_logger, handler = _setup_run_logger(log_id, settings.log_dir)
            try:
                run_logger.info("Dry run started", extra={"stage": "init", "run_id": log_id})
                self._execute_dry(query, run_logger)
                run_logger.info("Dry run finished", extra={"stage": "done", "run_id": log_id})
            finally:
                handler.close()
                run_logger.removeHandler(handler)
                if _installed:
                    signal.signal(signal.SIGINT, orig_sigint)
            return None

        # ----------------------------------------------------------------
        # Normal (non-dry) run
        # ----------------------------------------------------------------
        if self._session_factory is None:
            # Only import / call init_db when using the default session factory
            from airbnb_scraping_tool.db.models import SessionLocal, init_db

            init_db()

        stats: dict[str, Any] = {
            "total_listings": 0,
            "new": 0,
            "updated": 0,
            "dedup_hits": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
        }

        with self._make_session() as session:
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

        from config import settings

        run_logger, handler = _setup_run_logger(run_id, settings.log_dir)
        run_logger.info(
            "Run started",
            extra={"run_id": run_id, "stage": "init"},
        )

        try:
            with self._make_session() as session:
                try:
                    self._execute(session, run_id, query, stats, no_extract, run_logger)

                    final_status = "cancelled" if self._cancel_event.is_set() else "done"
                    self.repo.close_run(session, run_id, status=final_status)
                except Exception:
                    logger.exception("Pipeline failed for run %s", run_id)
                    run_logger.exception("Pipeline failed", extra={"run_id": run_id, "stage": "error"})
                    self.repo.close_run(session, run_id, status="failed")
                    raise
                finally:
                    self.repo.record_run_stats(session, run_id, stats)
                    session.commit()

            run_logger.info(
                "Run finished",
                extra={
                    "run_id": run_id,
                    "stage": "done",
                    "status": "cancelled" if self._cancel_event.is_set() else "done",
                },
            )
        finally:
            handler.close()
            run_logger.removeHandler(handler)
            if _installed:
                signal.signal(signal.SIGINT, orig_sigint)

        return run_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute_dry(self, query: SearchQuery, run_logger: logging.Logger) -> None:
        """Dry-run: call scrapers but discard all results without any DB writes."""
        for scraper in self.scrapers:
            if self._cancel_event.is_set():
                break
            if scraper.source not in query.sources:
                run_logger.debug("Skipping scraper %s (not in sources)", scraper.source)
                continue

            run_logger.info(
                "Acquiring (dry) from %s for '%s'",
                scraper.source,
                query.area,
                extra={"stage": "acquire", "source": scraper.source},
            )
            from config import settings

            try:
                _call_with_timeout_and_retry(
                    scraper.search,
                    (query,),
                    {},
                    timeout=settings.scraper_timeout,
                    max_retries=settings.scraper_max_retries,
                    label=f"scraper:{scraper.source}",
                    run_logger=run_logger,
                )
            except Exception:  # noqa: BLE001
                run_logger.exception("Scraper %s failed (dry)", scraper.source)

    def _execute(
        self,
        session: Any,
        run_id: int,
        query: SearchQuery,
        stats: dict[str, Any],
        no_extract: bool,
        run_logger: logging.Logger,
    ) -> None:
        """Inner implementation — separated so the outer method can always close."""
        from config import settings

        for scraper in self.scrapers:
            if self._cancel_event.is_set():
                run_logger.info(
                    "Cancellation requested; skipping remaining scrapers",
                    extra={"run_id": run_id, "stage": "acquire"},
                )
                break

            if scraper.source not in query.sources:
                logger.debug("Skipping scraper %s (not in query sources)", scraper.source)
                continue

            run_logger.info(
                "Stage: acquire from %s for '%s'",
                scraper.source,
                query.area,
                extra={"run_id": run_id, "stage": "acquire", "source": scraper.source},
            )
            logger.info("Acquiring from %s for '%s'", scraper.source, query.area)

            try:
                payloads = _call_with_timeout_and_retry(
                    scraper.search,
                    (query,),
                    {},
                    timeout=settings.scraper_timeout,
                    max_retries=settings.scraper_max_retries,
                    label=f"scraper:{scraper.source}",
                    run_logger=run_logger,
                )
            except Exception:
                logger.exception("Scraper %s failed", scraper.source)
                run_logger.exception(
                    "Scraper %s failed after retries",
                    scraper.source,
                    extra={"run_id": run_id, "source": scraper.source},
                )
                continue

            for idx, raw in enumerate(payloads):
                if self._cancel_event.is_set():
                    run_logger.info(
                        "Cancellation requested; stopping after record %d",
                        idx,
                        extra={"run_id": run_id, "stage": "acquire"},
                    )
                    break
                self._process_payload(
                    session, run_id, raw, stats, no_extract, run_logger, settings
                )

    def _process_payload(
        self,
        session: Any,
        run_id: int,
        raw: Any,
        stats: dict[str, Any],
        no_extract: bool,
        run_logger: logging.Logger,
        settings: Any,
    ) -> None:
        """Persist one raw payload, extract it (unless deduped or no_extract), and store."""
        from airbnb_scraping_tool.db.models import RawScrape  # noqa: F401 — referenced via repo

        # --- Dedup check ---------------------------------------------------
        existing = self.repo.find_by_hash(session, raw.content_hash)
        if existing is not None:
            logger.debug("Dedup hit for hash %s", raw.content_hash[:12])
            run_logger.debug(
                "Dedup hit; skipping",
                extra={"run_id": run_id, "source": raw.source, "url": raw.url},
            )
            stats["dedup_hits"] += 1
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
        run_logger.info(
            "Saved raw scrape",
            extra={"run_id": run_id, "source": raw.source, "url": raw.url},
        )

        # --- No-extract mode: leave RawScrape with status='pending' --------
        if no_extract:
            # Status stays 'pending' — do not call extractor, do not create
            # ExtractionLog, Listing, or ListingSnapshot rows.
            session.flush()
            run_logger.debug(
                "no-extract mode: skipping LLM for %s",
                raw.url,
                extra={"run_id": run_id, "url": raw.url},
            )
            return

        # --- Extract -------------------------------------------------------
        run_logger.info(
            "Stage: extract",
            extra={"run_id": run_id, "stage": "extract", "source": raw.source, "url": raw.url},
        )

        try:
            result = _call_with_timeout_and_retry(
                self.extractor.extract,
                (raw.source, raw.url, raw.payload),
                {},
                timeout=settings.extractor_timeout,
                max_retries=settings.extractor_max_retries,
                label=f"extractor:{raw.url}",
                run_logger=run_logger,
            )
        except Exception as exc:  # noqa: BLE001
            run_logger.error(
                "Extraction failed after retries: %s",
                exc,
                extra={"run_id": run_id, "url": raw.url},
            )
            self.repo.log_extraction(
                session,
                raw_scrape_id=rs.id,
                model=self.extractor.model,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                status="failed",
                error=str(exc),
            )
            self.repo.mark_scrape_status(session, rs.id, "failed", error=str(exc))
            session.flush()
            return

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

        stats["total_tokens"] += result.total_tokens
        stats["estimated_cost_usd"] += result.estimated_cost_usd

        if result.status == "failed" or result.listing is None:
            run_logger.warning(
                "Extraction returned failed status for %s",
                raw.url,
                extra={"run_id": run_id, "url": raw.url},
            )
            self.repo.mark_scrape_status(session, rs.id, "failed", error=result.error)
            session.flush()
            return

        self.repo.mark_scrape_status(session, rs.id, "extracted")

        # --- Store ---------------------------------------------------------
        run_logger.info(
            "Stage: store",
            extra={"run_id": run_id, "stage": "store", "source": raw.source, "url": raw.url},
        )

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

        stats["total_listings"] += 1
        if is_new:
            stats["new"] += 1
        elif was_updated:
            stats["updated"] += 1

        run_logger.info(
            "Record stored: listing_id=%s is_new=%s",
            listing.id,
            is_new,
            extra={"run_id": run_id, "source": raw.source, "url": raw.url},
        )

        session.flush()
