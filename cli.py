"""Command-line interface for the Short-Stay Market Scanner.

Commands:
    scan          Run a full acquire→extract→store pipeline for a search query.
    purge-run     Delete all snapshots and orphaned listings for a past run.
    vacuum        Run VACUUM on the SQLite database to reclaim free pages.
"""

from __future__ import annotations

import sys
import types
from datetime import date

import click
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from airbnb_scraping_tool.db.models import SessionLocal, init_db
from airbnb_scraping_tool.db.repo import Repo
from db.models import Base, RawScrape
from extraction.batch import batch_extract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise click.BadParameter(f"Expected ISO date YYYY-MM-DD, got '{value}'")


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """Short-Stay Market Scanner."""


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def _cmd_scan(args: types.SimpleNamespace) -> None:
    """Core scan-command logic, broken out for testability.

    Queries pending RawScrape rows from the database and runs extraction via
    ``batch_extract``.  When ``args.batch`` is ``True`` the Batches API is
    forced by passing ``threshold=0``.
    """
    from config import settings

    engine = create_engine(settings.db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        pending = (
            session.query(RawScrape)
            .filter(RawScrape.status == "pending")
            .all()
        )

        if not pending:
            return

        threshold = 0 if getattr(args, "batch", False) else None
        batch_extract(pending, session, threshold=threshold)


@cli.command()
@click.argument("area")
@click.option("--checkin", default=None, help="Check-in date (YYYY-MM-DD)")
@click.option("--checkout", default=None, help="Check-out date (YYYY-MM-DD)")
@click.option("--guests", default=1, show_default=True, help="Number of guests")
@click.option(
    "--sources",
    default="airbnb,booking",
    show_default=True,
    help="Comma-separated list of sources: airbnb,booking",
)
@click.option("--no-extract", is_flag=True, default=False, help="Acquire only; skip LLM extraction")
@click.option("--batch", is_flag=True, default=False, help="Force Message Batches API for extraction")
@click.option("--dry-run", is_flag=True, default=False, help="Collect payloads but write nothing to the database")
def scan(
    area: str,
    checkin: str | None,
    checkout: str | None,
    guests: int,
    sources: str,
    no_extract: bool,
    batch: bool,
    dry_run: bool,
) -> None:
    """Run a full market scan for AREA.

    Example:
        python cli.py scan "Lisbon, Portugal" --checkin 2025-08-01 --checkout 2025-08-07 --guests 2
    """
    if dry_run:
        from airbnb_scraping_tool.extraction.extractor import Extractor
        from airbnb_scraping_tool.schemas import SearchQuery
        from pipeline import Pipeline

        sources_list = [s.strip() for s in sources.split(",") if s.strip() in ("airbnb", "booking")]
        scrapers = _build_scrapers(sources_list)
        extractor = Extractor(client=None)
        query = SearchQuery(area=area, sources=sources_list or ["airbnb"])  # type: ignore[arg-type]
        pipeline = Pipeline(scrapers=scrapers, extractor=extractor)
        pipeline.run(query, dry_run=True)
        click.echo("Dry run complete")
        return

    args = types.SimpleNamespace(
        area=area,
        checkin=checkin,
        checkout=checkout,
        guests=guests,
        sources=sources,
        no_extract=no_extract,
        batch=batch,
        dry_run=False,
    )
    _cmd_scan(args)


def _build_scrapers(sources: list[str]):  # noqa: ANN201
    """Return scraper instances for the requested sources.

    Real scrapers (Playwright, httpx) are only constructed here; stubs are
    injected by tests instead of calling this function.
    """
    scrapers = []
    # Import real scrapers lazily so the CLI starts fast even if Playwright
    # is not installed.
    if "airbnb" in sources:
        try:
            from airbnb_scraping_tool.scrapers.airbnb import AirbnbScraper  # type: ignore[import]
            scrapers.append(AirbnbScraper())
        except ImportError:
            click.echo("Warning: Airbnb scraper not available (install playwright).", err=True)

    if "booking" in sources:
        try:
            from airbnb_scraping_tool.scrapers.booking import BookingScraper  # type: ignore[import]
            scrapers.append(BookingScraper())
        except ImportError:
            click.echo("Warning: Booking.com scraper not available.", err=True)

    return scrapers


# ---------------------------------------------------------------------------
# purge-run
# ---------------------------------------------------------------------------


@cli.command("purge-run")
@click.argument("run_id", type=int)
def purge_run(run_id: int) -> None:
    """Delete all snapshots and orphaned listings for RUN_ID.

    A listing is considered orphaned when the run being purged is the only run
    in which it ever appeared (i.e. it has no remaining snapshots after the
    purge).

    Example:
        python cli.py purge-run 42
    """
    init_db()
    repo = Repo()
    with SessionLocal() as session:
        counts = repo.purge_run(session, run_id)
        session.commit()

    click.echo(
        f"Purged run {run_id}: "
        f"{counts['snapshots_deleted']} snapshot(s) deleted, "
        f"{counts['listings_deleted']} orphaned listing(s) deleted."
    )


# ---------------------------------------------------------------------------
# vacuum
# ---------------------------------------------------------------------------


@cli.command()
def vacuum() -> None:
    """Run VACUUM on the SQLite database to reclaim free pages.

    Safe to run at any time; briefly locks the database file.

    Example:
        python cli.py vacuum
    """
    init_db()
    repo = Repo()
    # VACUUM cannot run inside a transaction; use autocommit-style connection.
    from sqlalchemy import create_engine, text

    from config import settings

    engine = create_engine(
        settings.db_url,
        connect_args={"check_same_thread": False},
        isolation_level=None,  # autocommit — required for VACUUM on SQLite
    )
    with engine.connect() as conn:
        conn.execute(text("VACUUM"))

    click.echo("VACUUM complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    cli()
