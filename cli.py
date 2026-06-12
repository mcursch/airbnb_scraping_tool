"""Command-line interface for the Short-Stay Market Scanner.

Usage
-----
    python cli.py scan "Lisbon, Portugal" --sources airbnb --no-extract
    python cli.py scan "Paris, France" --checkin 2025-07-01 --checkout 2025-07-07 --guests 2
"""

from __future__ import annotations

import json
import logging
import sys

import click

from config import settings
from db.models import make_session_factory
from db.repo import create_search_run, finish_search_run, get_raw_scrapes
from scrapers.base import SearchQuery


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


@click.group()
def main() -> None:
    """Short-Stay Market Scanner CLI."""


@main.command()
@click.argument("area")
@click.option("--sources", default="airbnb", show_default=True,
              help="Comma-separated list of sources (airbnb, booking).")
@click.option("--checkin", default=None, help="Check-in date (YYYY-MM-DD).")
@click.option("--checkout", default=None, help="Check-out date (YYYY-MM-DD).")
@click.option("--guests", default=1, show_default=True, help="Number of guests.")
@click.option("--max-pages", default=None, type=int,
              help=f"Max pages per source (default: config.max_pages={settings.max_pages}).")
@click.option("--no-extract", is_flag=True, default=False,
              help="Skip LLM extraction; only acquire raw payloads.")
@click.option("--db-url", default=None, help="Override database URL.")
@click.option("--verbose", "-v", is_flag=True, default=False)
def scan(
    area: str,
    sources: str,
    checkin: str | None,
    checkout: str | None,
    guests: int,
    max_pages: int | None,
    no_extract: bool,
    db_url: str | None,
    verbose: bool,
) -> None:
    """Scan AREA for short-stay listings."""
    _configure_logging(verbose)
    log = logging.getLogger(__name__)

    source_list = [s.strip().lower() for s in sources.split(",") if s.strip()]
    effective_max_pages = max_pages if max_pages is not None else settings.max_pages

    log.info(
        "scan area=%r sources=%s max_pages=%d no_extract=%s",
        area, source_list, effective_max_pages, no_extract,
    )

    session_factory = make_session_factory(db_url)
    session = session_factory()

    run = create_search_run(
        session,
        area_query=area,
        sources=source_list,
        checkin=checkin,
        checkout=checkout,
        guests=guests,
    )
    log.info("Created SearchRun id=%d", run.id)

    query = SearchQuery(area=area, checkin=checkin, checkout=checkout, guests=guests)
    total_captured = 0

    try:
        for source_name in source_list:
            if source_name == "airbnb":
                from scrapers.airbnb import AirbnbScraper

                scraper = AirbnbScraper(max_pages=effective_max_pages)
                payloads = scraper.search(query, session, run_id=run.id)
                total_captured += len(payloads)
                log.info("Airbnb: captured %d page(s).", len(payloads))
            else:
                log.warning("Source %r not yet implemented; skipping.", source_name)

        if not no_extract and total_captured:
            log.info("--no-extract not set but extraction not yet implemented; skipping.")

    except KeyboardInterrupt:
        log.warning("Interrupted by user — already-persisted rows are safe in the DB.")
        finish_search_run(session, run, status="interrupted")
        session.close()
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        log.error("Scan failed: %s", exc, exc_info=True)
        finish_search_run(session, run, status="failed")
        session.close()
        raise SystemExit(1)

    # Summarise
    rows = get_raw_scrapes(session, run_id=run.id)
    stats = {"raw_scrapes": len(rows), "sources": source_list, "max_pages": effective_max_pages}
    finish_search_run(session, run, status="done", stats=json.dumps(stats))
    session.close()

    click.echo(f"Done. {len(rows)} RawScrape row(s) persisted for run id={run.id}.")


if __name__ == "__main__":
    main()
