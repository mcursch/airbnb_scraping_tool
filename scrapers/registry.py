"""Central source → scraper registry.

One place to map a source identifier to its scraper class, used by both the
pipeline (`run_search`) and the CLI (`_build_scrapers`). Each scraper is
imported lazily so a missing optional dependency (e.g. Playwright) only skips
that one source instead of breaking startup.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Canonical source identifiers (also what the dashboard / CLI accept).
VALID_SOURCES: tuple[str, ...] = (
    "airbnb",
    "booking",
    "vrbo",
    "expedia",
    "google_hotels",
    "hostelworld",
)

# Friendly aliases → canonical identifier.
_ALIASES: dict[str, str] = {"hotels": "booking"}


def normalize_sources(sources: list[str] | None) -> list[str]:
    """Map aliases to canonical ids, drop unknowns/dupes, default to airbnb."""
    out: list[str] = []
    for s in sources or []:
        key = _ALIASES.get(s, s)
        if key in VALID_SOURCES and key not in out:
            out.append(key)
    return out or ["airbnb"]


def _make(key: str) -> Any:
    if key == "airbnb":
        from scrapers.airbnb import AirbnbScraper

        return AirbnbScraper()
    if key == "booking":
        from scrapers.booking import BookingScraper

        return BookingScraper()
    if key == "vrbo":
        from scrapers.vrbo import VrboScraper

        return VrboScraper()
    if key == "expedia":
        from scrapers.expedia import ExpediaScraper

        return ExpediaScraper()
    if key == "google_hotels":
        from scrapers.google_hotels import GoogleHotelsScraper

        return GoogleHotelsScraper()
    if key == "hostelworld":
        from scrapers.hostelworld import HostelworldScraper

        return HostelworldScraper()
    raise ValueError(f"Unknown source {key!r}")


def build_scrapers(sources: list[str] | None) -> list[Any]:
    """Instantiate scrapers for *sources*; skip any that fail to construct."""
    scrapers: list[Any] = []
    for key in normalize_sources(sources):
        try:
            scrapers.append(_make(key))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scraper for %r unavailable; skipping (%s).", key, exc)
    return scrapers
