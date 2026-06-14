"""Acquire → extract → store pipeline.

This module orchestrates the three main stages of the Short-Stay Market
Scanner.  All stages (acquire, extract, store) are fully wired.

Public API:
  - ``SessionLocal``    — re-exported from db.models
  - ``init_db``         — re-exported from db.models
  - ``Pipeline``        — full acquire→extract→store orchestration class
  - ``PipelineResult``  — result type returned by ``run_search``
  - ``run_search``      — high-level entry point used by the dashboard
  - ``run_acquire``     — acquire-only helper (backward compatibility)
  - ``process_raw_scrape`` — content-hash dedup helper (uses flat db.models)
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import config as config_mod
from scrapers.base import BlockedError, ScrapeProvider, SearchQuery as FlatSearchQuery
from db.models import SessionLocal, init_db  # noqa: F401 — re-exported
from db.repo import Repo
from schemas.models import RawPayload, SearchQuery

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PipelineResult — result type returned by run_search
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Outcome of a ``run_search`` call.

    Attributes
    ----------
    status:
        ``"done"`` on success, ``"failed"`` on error.
    run_id:
        The ``SearchRun.id`` of the completed run, or ``None`` on failure.
    error:
        Human-readable error message when ``status == "failed"``, else ``None``.
    """

    status: str
    run_id: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# run_search — high-level entry point used by the dashboard
# ---------------------------------------------------------------------------


def run_search(
    query: SearchQuery,
    progress_callback: Callable[[float, str], None] | None = None,
    model: str | None = None,
    enrich: bool = False,
    enrich_model: str | None = None,
) -> PipelineResult:
    """Run the full pipeline for *query* and return a :class:`PipelineResult`.

    This is the high-level entry point used by the Streamlit dashboard and
    other callers that want a simple fire-and-forget interface.  It
    constructs scrapers and an extractor from the application configuration,
    delegates to :class:`Pipeline`, and wraps the outcome in a
    :class:`PipelineResult`.

    Parameters
    ----------
    query:
        Search parameters (area, sources, guests, dates).
    progress_callback:
        Optional ``(fraction: float, message: str) -> None`` callable invoked
        at key milestones so callers can show a progress bar.  ``fraction``
        is in ``[0.0, 1.0]``.

    Returns
    -------
    PipelineResult
        ``status="done"`` with the ``run_id`` on success; ``status="failed"``
        with an ``error`` string on any exception.
    """

    def _progress(fraction: float, message: str) -> None:
        if progress_callback is not None:
            try:
                progress_callback(fraction, message)
            except Exception:  # noqa: BLE001
                pass

    _progress(0.05, "Building scrapers…")

    # Build scrapers lazily so missing optional dependencies don't crash the
    # import; each scraper is silently skipped when its package is absent.
    scrapers: list[Any] = []
    sources = list(query.sources) if query.sources else ["airbnb"]
    if "airbnb" in sources:
        try:
            from scrapers.airbnb import AirbnbScraper  # type: ignore[import]
            scrapers.append(AirbnbScraper())
        except (ImportError, Exception):  # noqa: BLE001
            logger.warning("Airbnb scraper not available; skipping.")

    if "booking" in sources or "hotels" in sources:
        try:
            from scrapers.booking import BookingScraper  # type: ignore[import]
            scrapers.append(BookingScraper())
        except (ImportError, Exception):  # noqa: BLE001
            logger.warning("Booking.com scraper not available; skipping.")

    _progress(0.10, "Building extractor…")

    try:
        from extraction.provider import Extractor
        import anthropic

        client = anthropic.Anthropic(
            api_key=config_mod.settings.anthropic_api_key,
            max_retries=3,
        )
        extractor = Extractor(client=client, model=model or config_mod.settings.llm_model)
    except Exception as exc:  # noqa: BLE001
        return PipelineResult(status="failed", error=f"Failed to build extractor: {exc}")

    # Build the paid-scraping fallback when an API key is configured. It
    # auto-engages inside Pipeline.run whenever a primary scraper is blocked or
    # returns nothing (the FALLBACK_PROVIDER setting — scraperapi | apify |
    # brightdata — selects the backend; default Bright Data zone via BRIGHTDATA_ZONE).
    fallback: Any | None = None
    if config_mod.settings.SCRAPER_API_KEY:
        try:
            from scrapers.fallback_api import FallbackApiProvider

            fallback = FallbackApiProvider()
            _progress(0.13, "Paid fallback ready (auto-engages if blocked)…")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallback provider unavailable (%s); continuing without it.", exc)

    # Build the reason-and-act enrichment agent when requested. It reuses the
    # same Anthropic client and fills missing fields via web search/fetch.
    enricher: Any | None = None
    if enrich:
        try:
            from enrichment.agent import EnrichmentAgent

            enricher = EnrichmentAgent(
                client=client,
                model=enrich_model or config_mod.settings.enrich_model,
            )
            _progress(0.14, "Enrichment agent ready…")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Enrichment agent unavailable (%s); continuing without it.", exc)

    _progress(0.15, "Starting pipeline…")

    pipeline = Pipeline(
        scrapers=scrapers, extractor=extractor, fallback=fallback, enricher=enricher
    )

    try:
        run_id = pipeline.run(query)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline raised an unexpected exception")
        return PipelineResult(status="failed", error=str(exc))

    _progress(1.0, "Done.")
    return PipelineResult(status="done", run_id=run_id)


# ---------------------------------------------------------------------------
# run_acquire — kept for backward compatibility with test_pipeline_fallback.py
# ---------------------------------------------------------------------------


def run_acquire(
    query: str,
    providers: Sequence[ScrapeProvider],
    fallback_provider: ScrapeProvider | None = None,
) -> list:
    """Run the acquire stage for *query* across every provider in *providers*.

    For each provider the method calls ``provider.search(SearchQuery(area=query))``
    and accumulates the returned :class:`~scrapers.base.RawScrape` objects.
    When a provider raises :exc:`~scrapers.base.BlockedError` the method
    behaves as follows:

    * **Fallback configured** (``fallback_provider`` is not ``None``): the same
      *query* is submitted to ``fallback_provider.search()`` and its results
      are included in the returned list.  A structured ``INFO`` message is
      logged so that run statistics can record the event.
    * **No fallback configured**: a ``WARNING``-level structured log entry is
      emitted with the provider name and block reason.  The source is skipped
      gracefully; no exception propagates to the caller.

    Any exception *other* than :exc:`~scrapers.base.BlockedError` is not
    caught and propagates normally -- unexpected errors should not be silenced.

    Args:
        query: Human-readable location / keyword string (e.g.
            ``"Lisbon, Portugal"``).
        providers: Ordered sequence of primary scrape providers to try.
        fallback_provider: Optional paid scraping-API provider used when a
            primary provider is blocked.  ``None`` means no fallback is
            configured.

    Returns:
        Flat list of :class:`~scrapers.base.RawScrape` objects collected from
        all providers (and the fallback where used).
    """
    search_query = FlatSearchQuery(area=query)
    results: list = []

    for provider in providers:
        provider_name = type(provider).__name__
        try:
            raw = provider.search(search_query)
            results.extend(raw)
            logger.debug(
                "Provider %s returned %d payload(s) for query %r",
                provider_name,
                len(raw),
                query,
            )
        except BlockedError as exc:
            if fallback_provider is not None:
                fallback_name = type(fallback_provider).__name__
                logger.info(
                    "Provider %s blocked (%s); retrying query %r via %s",
                    provider_name,
                    exc,
                    query,
                    fallback_name,
                    extra={
                        "event": "fallback_engaged",
                        "blocked_provider": provider_name,
                        "fallback_provider": fallback_name,
                        "query": query,
                    },
                )
                raw = fallback_provider.search(search_query)
                results.extend(raw)
                logger.debug(
                    "Fallback provider %s returned %d payload(s)",
                    fallback_name,
                    len(raw),
                )
            else:
                logger.warning(
                    "Provider %s blocked and no fallback configured; skipping source",
                    provider_name,
                    extra={
                        "event": "provider_skipped",
                        "blocked_provider": provider_name,
                        "reason": str(exc),
                        "query": query,
                    },
                )

    return results


# ---------------------------------------------------------------------------
# process_raw_scrape — content-hash dedup helper (uses flat db.models)
# ---------------------------------------------------------------------------


def process_raw_scrape(session: Any, raw_scrape: Any, extractor: Any) -> Any:
    """Process a single RawScrape with content-hash deduplication.

    Uses the ``db.models`` ORM classes.
    The caller is responsible for flushing/committing the session.

    Algorithm:
        1. Look for an existing ExtractionLog (status='extracted') linked to any
           RawScrape with the same ``content_hash``.
        2. **Dedup path**: create a zero-cost ExtractionLog with status='dedup'
           pointing at the same listing, plus a new ListingSnapshot for this run.
        3. **New path**: call ``extractor.extract(raw_scrape)``, upsert the
           returned listing, create a ListingSnapshot and an ExtractionLog.

    Returns:
        The ExtractionLog instance for this scrape (flushed, not committed).
    """
    from db.models import ExtractionLog, Listing, ListingSnapshot, RawScrape as DBRawScrape

    # ------------------------------------------------------------------
    # Check for existing extraction with the same content_hash (dedup)
    # ------------------------------------------------------------------
    existing_log: Any = (
        session.query(ExtractionLog)
        .join(DBRawScrape, ExtractionLog.raw_scrape_id == DBRawScrape.id)
        .filter(DBRawScrape.content_hash == raw_scrape.content_hash)
        .filter(ExtractionLog.status == "extracted")
        .first()
    )

    if existing_log is not None:
        # Dedup: zero-cost log pointing at the same listing
        dedup_log = ExtractionLog(
            raw_scrape_id=raw_scrape.id,
            listing_id=existing_log.listing_id,
            model=None,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            status="dedup",
        )
        session.add(dedup_log)
        session.flush()

        # Still insert a ListingSnapshot so every run has its own row
        if existing_log.listing_id is not None:
            snap = ListingSnapshot(
                listing_id=existing_log.listing_id,
                run_id=raw_scrape.run_id,
            )
            session.add(snap)
            session.flush()

        return dedup_log

    # ------------------------------------------------------------------
    # New content hash: call the extractor
    # ------------------------------------------------------------------
    result = extractor.extract(raw_scrape)

    # Upsert the Listing from listing_data
    ld: dict[str, Any] = result.listing_data
    source = ld.get("source", raw_scrape.source)
    source_listing_id = ld.get("source_listing_id")
    name = ld.get("name")

    listing = None
    if source_listing_id:
        existing_listing = (
            session.query(Listing)
            .filter_by(source=source, source_listing_id=source_listing_id)
            .first()
        )
        if existing_listing is None:
            listing = Listing(
                source=source,
                source_listing_id=source_listing_id,
                name=name,
                url=ld.get("url"),
            )
            session.add(listing)
        else:
            listing = existing_listing
            if name:
                listing.name = name
        session.flush()

    listing_id = listing.id if listing is not None else None

    # Create ExtractionLog
    log = ExtractionLog(
        raw_scrape_id=raw_scrape.id,
        listing_id=listing_id,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        status="extracted",
    )
    session.add(log)
    session.flush()

    # Create ListingSnapshot for this run
    if listing_id is not None:
        snap = ListingSnapshot(
            listing_id=listing_id,
            run_id=raw_scrape.run_id,
        )
        session.add(snap)
        session.flush()

    return log


# ---------------------------------------------------------------------------
# Pipeline class — uses db.models
# ---------------------------------------------------------------------------


class Pipeline:
    """End-to-end scan pipeline: acquire → extract → store.

    Parameters
    ----------
    scrapers:
        Sequence of scraper objects whose ``search(query)`` method returns
        a list of ``RawPayload`` objects.
    extractor:
        Extraction object whose ``extract(source, url, payload)`` method
        returns an ``ExtractionResult``.
    repo:
        Optional ``Repo`` instance.  Defaults to a fresh ``Repo()``.
    session_factory:
        Optional ``sessionmaker``-like callable.  When omitted the pipeline
        reads the module-level ``SessionLocal`` at call time so that tests
        can monkey-patch it.
    """

    def __init__(
        self,
        scrapers: Sequence[Any],
        extractor: Any,
        repo: Repo | None = None,
        session_factory: Any | None = None,
        fallback: Any | None = None,
        enricher: Any | None = None,
    ) -> None:
        self._scrapers = scrapers
        self._extractor = extractor
        self._repo = repo if repo is not None else Repo()
        self._session_factory = session_factory
        self._fallback = fallback
        self._enricher = enricher
        self._cancel_flag = threading.Event()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session(self) -> Any:
        """Return a new session from the configured factory."""
        pipeline_mod = sys.modules[__name__]
        factory = self._session_factory or pipeline_mod.SessionLocal
        return factory()

    @staticmethod
    def _write_log(log_file: Any, level: str, msg: str, **extra: Any) -> None:
        """Append a JSON-lines log entry to *log_file*."""
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "msg": msg,
        }
        entry.update(extra)
        log_file.write(json.dumps(entry) + "\n")
        log_file.flush()

    def _run_enrichment(
        self,
        sess: Any,
        candidates: list[tuple[int, Any, Any, str]],
        stats: dict[str, Any],
        lf: Any,
    ) -> None:
        """Fill missing important fields on gappy listings via the enricher.

        Gated for cost: only listings missing at least ``enrich_min_gaps``
        important fields are enriched, and at most ``enrich_max_listings`` per
        run (the gappiest first). Each filled value is written to the Listing
        (or its ListingSnapshot for per-stay pricing) and the full provenance —
        value, confidence, source URL, reasoning — is stored on
        ``Listing.enrichment``.
        """
        from db.models import Listing as DBListing
        from enrichment.agent import (
            IMPORTANT_FIELDS,
            SNAPSHOT_FIELDS,
            missing_important_fields,
        )

        min_gaps = config_mod.settings.enrich_min_gaps
        max_listings = config_mod.settings.enrich_max_listings

        ranked = sorted(
            candidates,
            key=lambda c: len(missing_important_fields(c[2])),
            reverse=True,
        )
        selected = [c for c in ranked if len(missing_important_fields(c[2])) >= min_gaps][
            :max_listings
        ]

        if not selected:
            self._write_log(lf, "INFO", "Enrichment: no listings met the gap threshold",
                            min_gaps=min_gaps, candidates=len(candidates))
            return

        self._write_log(lf, "INFO", "Enrichment pass starting",
                        selected=len(selected), candidates=len(candidates))

        for listing_id, snap, listing_ex, source in selected:
            if self._cancel_flag.is_set():
                break
            try:
                res = self._enricher.enrich(listing_ex, source=source)
            except Exception as exc:  # noqa: BLE001
                self._write_log(lf, "ERROR", "Enrichment errored",
                                listing_id=listing_id, error=str(exc))
                continue

            stats["enrich_tokens"] += res.total_tokens
            stats["enrich_searches"] += res.web_search_count
            stats["enrich_cost_usd"] += res.estimated_cost_usd

            db_listing = sess.get(DBListing, listing_id)
            if db_listing is None:
                continue

            for fname, value in res.filled.items():
                if fname in SNAPSHOT_FIELDS:
                    if snap is not None and hasattr(snap, fname):
                        setattr(snap, fname, value)
                elif hasattr(db_listing, fname):
                    setattr(db_listing, fname, value)

            db_listing.enrichment = res.provenance or None
            db_listing.enrichment_status = res.status
            if res.filled:
                stats["enriched_count"] += 1
            sess.flush()

            self._write_log(lf, "INFO", "Listing enriched",
                            listing_id=listing_id, status=res.status,
                            filled=list(res.filled.keys()),
                            searches=res.web_search_count)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        query: SearchQuery,
        *,
        dry_run: bool = False,
        no_extract: bool = False,
    ) -> int | None:
        """Execute the full pipeline for *query*.

        Parameters
        ----------
        query:
            Search parameters (area, sources, guests, dates).
        dry_run:
            When ``True``, collect payloads but write nothing to the database.
            Returns ``None`` and creates a ``dry-<timestamp>.jsonl`` log file.
        no_extract:
            When ``True``, persist RawScrape rows with ``status='pending'``
            but skip LLM extraction and produce no ListingSnapshot rows.

        Returns
        -------
        int | None
            The ``SearchRun.id`` of the completed run, or ``None`` for dry runs.
        """
        pipeline_mod = sys.modules[__name__]
        pipeline_mod.init_db()

        log_dir = Path(config_mod.settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # Dry-run path — no DB writes
        # ------------------------------------------------------------------
        if dry_run:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            log_path = log_dir / f"dry-{ts}.jsonl"
            with open(log_path, "w", encoding="utf-8") as lf:
                self._write_log(lf, "INFO", "Dry run started", area=query.area)
                for scraper in self._scrapers:
                    payloads: list[RawPayload] = scraper.search(query)
                    for p in payloads:
                        self._write_log(lf, "DEBUG", "Would process payload", url=p.url)
                self._write_log(lf, "INFO", "Dry run complete")
            return None

        # ------------------------------------------------------------------
        # Normal run — acquire → (extract) → store
        # ------------------------------------------------------------------
        from db.models import (
            RawScrape as AirbnbRawScrape,
        )

        self._cancel_flag.clear()

        # Install a SIGINT handler that sets the cancel flag instead of raising.
        # Restore the original handler when we're done.
        orig_handler = signal.getsignal(signal.SIGINT)

        def _sigint_handler(sig: int, frame: Any) -> None:  # noqa: ANN001
            self._cancel_flag.set()

        try:
            signal.signal(signal.SIGINT, _sigint_handler)
        except (ValueError, OSError):
            # Not the main thread; skip signal handling.
            pass

        sess = self._get_session()
        run_id: int | None = None
        cancelled = False

        try:
            # Open a SearchRun
            run = self._repo.open_run(
                sess,
                area_query=query.area,
                sources=list(query.sources),
                guests=query.guests,
                checkin=str(query.checkin) if query.checkin else None,
                checkout=str(query.checkout) if query.checkout else None,
            )
            sess.flush()
            run_id = run.id

            log_path = log_dir / f"{run_id}.jsonl"

            stats: dict[str, Any] = {
                "total_listings": 0,
                "new": 0,
                "updated": 0,
                "dedup_hits": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "fallback_engaged": False,
                "enriched_count": 0,
                "enrich_tokens": 0,
                "enrich_searches": 0,
                "enrich_cost_usd": 0.0,
            }

            # (listing_id, snapshot, extracted) tuples for the optional
            # post-extraction enrichment pass.
            enrich_candidates: list[tuple[int, Any, Any]] = []

            seen_hashes: set[str] = set()

            with open(log_path, "w", encoding="utf-8") as lf:
                self._write_log(lf, "INFO", "Run started", run_id=run_id, area=query.area)

                # Collect payloads from all scrapers. A primary scraper can fail
                # two ways: an explicit BlockedError, or — the common Airbnb
                # symptom from a datacenter IP — a silent empty result. Either
                # way, engage the configured paid fallback (e.g. Bright Data Web
                # Unlocker) once so the run still yields data. The fallback is
                # only attempted when one is configured (SCRAPER_API_KEY set).
                all_payloads: list[RawPayload] = []
                fallback_attempted = False
                for scraper in self._scrapers:
                    scraper_name = type(scraper).__name__
                    blocked_reason: str | None = None
                    payloads: list[RawPayload] = []
                    try:
                        payloads = list(scraper.search(query))
                    except BlockedError as exc:
                        blocked_reason = exc.reason
                        self._write_log(
                            lf, "WARNING", "Scraper blocked",
                            scraper=scraper_name, reason=exc.reason,
                        )
                    except Exception as exc:
                        # An unexpected (non-block) failure — record and move on;
                        # we can't tell whether the fallback would fare better.
                        self._write_log(
                            lf, "ERROR", "Scraper failed",
                            scraper=scraper_name, error=str(exc),
                        )
                        continue

                    if payloads:
                        all_payloads.extend(payloads)
                        continue

                    # No payloads — blocked or empty. Try the fallback once.
                    if self._fallback is None or fallback_attempted:
                        if blocked_reason is None:
                            self._write_log(
                                lf, "WARNING", "Scraper returned no payloads; "
                                "no fallback available",
                                scraper=scraper_name,
                            )
                        continue

                    fallback_attempted = True
                    reason = blocked_reason or "no payloads returned"
                    fb_name = type(self._fallback).__name__
                    self._write_log(
                        lf, "INFO", "Engaging paid fallback provider",
                        blocked_scraper=scraper_name, reason=reason, fallback=fb_name,
                    )
                    try:
                        fb_payloads = list(self._fallback.search(query))
                        stats["fallback_engaged"] = True
                        all_payloads.extend(fb_payloads)
                        self._write_log(
                            lf, "INFO", "Fallback provider returned payloads",
                            fallback=fb_name, count=len(fb_payloads),
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._write_log(
                            lf, "ERROR", "Fallback provider failed",
                            fallback=fb_name, error=str(exc),
                        )

                for payload in all_payloads:
                    # Check cancel flag at the start of each iteration
                    if self._cancel_flag.is_set():
                        cancelled = True
                        break

                    content_hash = payload.content_hash

                    # Intra-run dedup via Python set
                    if content_hash in seen_hashes:
                        stats["dedup_hits"] += 1
                        self._write_log(lf, "DEBUG", "Dedup hit (intra-run)", url=payload.url)
                        continue

                    seen_hashes.add(content_hash)

                    # Persist the raw scrape
                    raw_scrape = AirbnbRawScrape(
                        run_id=run_id,
                        source=payload.source,
                        url=payload.url,
                        payload=payload.payload,
                        content_hash=content_hash,
                        status="pending",
                    )
                    sess.add(raw_scrape)
                    sess.flush()

                    if no_extract:
                        continue

                    # LLM extraction — one API call per scraped page can yield
                    # many listings (search-results pages); upsert each.
                    result = self._extractor.extract(payload.source, payload.url, payload.payload)
                    model = getattr(self._extractor, "model", config_mod.settings.llm_model)

                    if result.status != "ok" or not result.listings:
                        self._write_log(
                            lf, "WARNING", "Extraction failed or empty",
                            url=payload.url, error=result.error,
                        )
                        # Record the failed call so run-history cost accounting
                        # (get_all_runs_with_cost) sees every extraction attempt.
                        self._repo.log_extraction(
                            sess,
                            raw_scrape_id=raw_scrape.id,
                            model=model,
                            input_tokens=result.input_tokens,
                            output_tokens=result.output_tokens,
                            cache_read_tokens=result.cache_read_tokens,
                            status="failed",
                            error=result.error,
                        )
                        raw_scrape.status = "failed"
                        stats["total_tokens"] += result.total_tokens
                        stats["estimated_cost_usd"] += result.estimated_cost_usd
                        sess.flush()
                        continue

                    for listing_ex in result.listings:
                        # Upsert the normalised Listing (static attributes,
                        # including the expanded host/trust, location, and
                        # policy fields).
                        listing, is_new, was_updated = self._repo.upsert_listing(
                            sess,
                            source=payload.source,
                            source_listing_id=listing_ex.source_listing_id,
                            name=listing_ex.name,
                            url=listing_ex.url,
                            property_type=listing_ex.property_type,
                            lat=listing_ex.lat,
                            lon=listing_ex.lon,
                            address_text=listing_ex.address_text,
                            bedrooms=listing_ex.bedrooms,
                            beds=listing_ex.beds,
                            baths=listing_ex.baths,
                            max_guests=listing_ex.max_guests,
                            rating=listing_ex.rating,
                            review_count=listing_ex.review_count,
                            amenities=listing_ex.amenities,
                            images=listing_ex.images,
                            host_or_brand=listing_ex.host_or_brand,
                            # Host & trust signals
                            host_is_superhost=listing_ex.host_is_superhost,
                            host_response_rate=listing_ex.host_response_rate,
                            host_response_time=listing_ex.host_response_time,
                            years_hosting=listing_ex.years_hosting,
                            rating_cleanliness=listing_ex.rating_cleanliness,
                            rating_location=listing_ex.rating_location,
                            rating_value=listing_ex.rating_value,
                            license_number=listing_ex.license_number,
                            # Location precision
                            neighborhood=listing_ex.neighborhood,
                            distance_to_center_km=listing_ex.distance_to_center_km,
                            # Policies & rules
                            cancellation_policy=listing_ex.cancellation_policy,
                            checkin_time=listing_ex.checkin_time,
                            checkout_time=listing_ex.checkout_time,
                            instant_book=listing_ex.instant_book,
                            pets_allowed=listing_ex.pets_allowed,
                            smoking_allowed=listing_ex.smoking_allowed,
                            events_allowed=listing_ex.events_allowed,
                        )

                        # Insert price/availability snapshot. Fees are stored as
                        # a {name: amount} dict (the storage/dashboard contract);
                        # the LLM emits a closed list of FeeItem for grammar safety.
                        fees_dict = (
                            {f.name: f.amount for f in listing_ex.fees}
                            if listing_ex.fees
                            else None
                        )
                        snap = self._repo.insert_snapshot(
                            sess,
                            listing_id=listing.id,
                            run_id=run_id,
                            nightly_price=listing_ex.nightly_price,
                            currency=listing_ex.currency,
                            total_price=listing_ex.total_price,
                            fees=fees_dict,
                            availability=listing_ex.availability,
                            # Pricing breakdown (per-stay)
                            cleaning_fee=listing_ex.cleaning_fee,
                            service_fee=listing_ex.service_fee,
                            taxes=listing_ex.taxes,
                            deposit=listing_ex.deposit,
                            weekly_discount_pct=listing_ex.weekly_discount_pct,
                            monthly_discount_pct=listing_ex.monthly_discount_pct,
                            minimum_nights=listing_ex.minimum_nights,
                        )

                        if self._enricher is not None:
                            enrich_candidates.append(
                                (listing.id, snap, listing_ex, payload.source)
                            )

                        stats["total_listings"] += 1
                        if is_new:
                            stats["new"] += 1
                        elif was_updated:
                            stats["updated"] += 1

                        self._write_log(
                            lf, "INFO", "Listing extracted",
                            url=payload.url,
                            source_listing_id=listing_ex.source_listing_id,
                            is_new=is_new, was_updated=was_updated,
                        )

                    raw_scrape.status = "extracted"
                    # One ExtractionLog per extraction call feeds run-history cost.
                    self._repo.log_extraction(
                        sess,
                        raw_scrape_id=raw_scrape.id,
                        model=model,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        cache_read_tokens=result.cache_read_tokens,
                        status="ok",
                    )
                    sess.flush()

                    # Token usage is per extraction call (one call per page).
                    stats["total_tokens"] += result.total_tokens
                    stats["estimated_cost_usd"] += result.estimated_cost_usd

                # Optional reason-and-act enrichment pass over listings with gaps.
                if self._enricher is not None and not cancelled and enrich_candidates:
                    self._run_enrichment(sess, enrich_candidates, stats, lf)

                # Close the run
                final_status = "cancelled" if cancelled else "done"
                # Mirror total_listings as listing_count for the run-history page
                # (get_all_runs_with_cost reads stats["listing_count"]).
                stats["listing_count"] = stats["total_listings"]
                self._repo.record_run_stats(sess, run_id, stats)
                self._repo.close_run(sess, run_id, status=final_status)
                sess.commit()

                self._write_log(
                    lf, "INFO", "Run complete",
                    run_id=run_id, status=final_status, **stats,
                )

            return run_id

        except Exception:
            try:
                sess.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                sess.close()
            except Exception:
                pass
            try:
                signal.signal(signal.SIGINT, orig_handler)
            except (ValueError, OSError, UnboundLocalError):
                pass
