"""Pipeline orchestrator — acquire → extract → store.

Stage implementations (scrapers, extraction, persistence) are filled in later
stages. This module exposes `run_search()` which the dashboard calls in a
background thread; it accepts a `progress_callback` so the UI can advance a
progress bar without polling.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from db.repo import create_search_run, finish_search_run


@dataclass
class SearchQuery:
    area: str
    checkin: date | None = None
    checkout: date | None = None
    guests: int = 1
    sources: str = "both"  # "airbnb" | "hotels" | "both"


@dataclass
class PipelineResult:
    run_id: int
    status: str
    stats: dict = field(default_factory=dict)
    error: str | None = None


def run_search(
    query: SearchQuery,
    progress_callback: Callable[[float, str], None] | None = None,
) -> PipelineResult:
    """Run the full pipeline for *query*.

    Parameters
    ----------
    query:
        The search parameters supplied by the user.
    progress_callback:
        Optional callable that receives ``(fraction: float, message: str)``.
        The dashboard passes an ``st.progress``-backed function here.

    Returns
    -------
    PipelineResult
        Contains the ``run_id`` that uniquely identifies this search run in the
        database.
    """

    def _notify(fraction: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(fraction, message)

    # --- 1. Persist the SearchRun row immediately ----------------------------
    _notify(0.0, "Creating search run…")
    checkin_str = query.checkin.isoformat() if query.checkin else None
    checkout_str = query.checkout.isoformat() if query.checkout else None

    run_id = create_search_run(
        area_query=query.area,
        checkin=checkin_str,
        checkout=checkout_str,
        guests=query.guests,
        sources=query.sources,
    )

    try:
        # --- 2. Acquire (scrapers — stub) ------------------------------------
        _notify(0.1, "Starting scrapers…")
        # TODO (Stage 1/2): invoke airbnb.py / booking.py scrapers here
        time.sleep(0.5)  # simulate network latency during development

        _notify(0.4, "Acquiring listings…")
        time.sleep(0.5)

        # --- 3. Extract (LLM — stub) -----------------------------------------
        _notify(0.6, "Extracting listing data via LLM…")
        # TODO (Stage 3): call extraction/extractor.py
        time.sleep(0.5)

        # --- 4. Store (upsert — stub) -----------------------------------------
        _notify(0.85, "Storing results…")
        # TODO (Stage 4): upsert Listing + ListingSnapshot rows
        time.sleep(0.2)

        # --- Done -------------------------------------------------------------
        stats = {"listings_found": 0, "raw_scrapes": 0, "extracted": 0}
        finish_search_run(run_id, status="done", stats=stats)
        _notify(1.0, "Done.")

        return PipelineResult(run_id=run_id, status="done", stats=stats)

    except Exception as exc:  # noqa: BLE001
        finish_search_run(run_id, status="failed")
        _notify(1.0, f"Pipeline failed: {exc}")
        return PipelineResult(run_id=run_id, status="failed", error=str(exc))
