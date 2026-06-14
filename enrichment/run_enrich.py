"""Enrich already-stored listings for a completed run (on-demand).

Used by the dashboard's "Enrich missing fields" button: after a scrape, the
user reviews the data, then triggers web-research enrichment over the run's
gappy listings. This reconstructs an ``ExtractedListing`` from each stored
Listing+Snapshot, runs the :class:`EnrichmentAgent`, and writes the filled
values (and provenance) back to the database.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import config as config_mod
from enrichment.agent import (
    EnrichmentAgent,
    SNAPSHOT_FIELDS,
    missing_important_fields,
)
from schemas.listing import ExtractedListing

logger = logging.getLogger(__name__)

# Fields we never try to rebuild from the DB (shape mismatch / not needed for
# gap detection): snapshot ``fees`` is a {name: amount} dict, not list[FeeItem].
_SKIP_REBUILD = {"fees"}


def _to_extracted(listing: Any, snap: Any) -> ExtractedListing:
    """Reconstruct an ExtractedListing from a stored Listing + ListingSnapshot."""
    data: dict[str, Any] = {
        "source_listing_id": listing.source_listing_id or str(listing.id),
        "name": listing.name or "Unknown",
    }
    for field in ExtractedListing.model_fields:
        if field in data or field in _SKIP_REBUILD:
            continue
        # Snapshot holds per-stay/pricing fields; Listing holds the rest.
        val = getattr(snap, field, None)
        if val is None:
            val = getattr(listing, field, None)
        if val is not None:
            data[field] = val
    return ExtractedListing(**data)


def enrich_run(
    run_id: int,
    *,
    model: str | None = None,
    max_listings: int | None = None,
    min_gaps: int | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Enrich the gappiest listings of *run_id* in place.

    Returns a summary dict: ``selected``, ``processed``, ``enriched_count``,
    ``searches``, ``tokens``, ``cost_usd``.
    """
    import anthropic

    from db.models import Listing, ListingSnapshot, SessionLocal

    settings = config_mod.settings
    max_listings = max_listings or settings.enrich_max_listings
    min_gaps = settings.enrich_min_gaps if min_gaps is None else min_gaps

    # Fast-fail client: a single stubborn listing must not hang the UI. No
    # retries (a retry would double a timed-out call), so each request is bounded
    # to one timeout; combined with the agent's max_loops + wall-clock budget,
    # each listing resolves in ~2 minutes worst-case before moving on.
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key, max_retries=0, timeout=110.0
    )
    agent = EnrichmentAgent(client=client, model=model or settings.enrich_model)

    summary: dict[str, Any] = {
        "selected": 0, "processed": 0, "enriched_count": 0,
        "searches": 0, "tokens": 0, "cost_usd": 0.0,
    }

    def _progress(frac: float, msg: str) -> None:
        if progress_callback is not None:
            try:
                progress_callback(frac, msg)
            except Exception:  # noqa: BLE001
                pass

    sess = SessionLocal()
    try:
        pairs = (
            sess.query(Listing, ListingSnapshot)
            .join(ListingSnapshot, ListingSnapshot.listing_id == Listing.id)
            .filter(ListingSnapshot.run_id == run_id)
            .all()
        )

        # Rank by gap count; keep only listings with enough gaps to be worth it.
        candidates = []
        for listing, snap in pairs:
            try:
                ex = _to_extracted(listing, snap)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping listing %s: rebuild failed (%s)", listing.id, exc)
                continue
            gap_n = len(missing_important_fields(ex))
            if gap_n >= min_gaps:
                candidates.append((listing, snap, ex, gap_n))
        candidates.sort(key=lambda c: c[3], reverse=True)
        selected = candidates[:max_listings]
        summary["selected"] = len(selected)

        total = len(selected) or 1
        for i, (listing, snap, ex, _gap_n) in enumerate(selected):
            _progress(i / total, f"Researching “{(listing.name or '?')[:40]}”…")
            res = agent.enrich(ex, source=listing.source)
            summary["processed"] += 1
            summary["searches"] += res.web_search_count
            summary["tokens"] += res.total_tokens
            summary["cost_usd"] += res.estimated_cost_usd

            for field, value in res.filled.items():
                if field in SNAPSHOT_FIELDS:
                    if hasattr(snap, field):
                        setattr(snap, field, value)
                elif hasattr(listing, field):
                    setattr(listing, field, value)

            merged = dict(listing.enrichment or {})
            merged.update(res.provenance)
            listing.enrichment = merged or None
            listing.enrichment_status = res.status
            if res.filled:
                summary["enriched_count"] += 1
            sess.commit()

        _progress(1.0, "Enrichment complete.")
        return summary
    finally:
        sess.close()
