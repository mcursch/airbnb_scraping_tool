"""
Command-line interface for the Short-Stay Market Scanner.

Usage
-----
    uv run python cli.py scan "Lisbon, Portugal" [options]

Options
-------
--checkin    Check-in date (YYYY-MM-DD)
--checkout   Check-out date (YYYY-MM-DD)
--guests     Number of guests
--sources    Comma-separated list of sources (airbnb, booking)
--batch      Force the Message Batches API path regardless of record count
--no-extract Skip LLM extraction; only persist raw scrapes
--dry-run    Print what would be done without actually running
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from config import settings
from db.models import Base, RawScrape
from extraction.batch import batch_extract


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scanner",
        description="Short-Stay Market Scanner — scrape, extract, and compare short-stay listings",
    )

    sub = parser.add_subparsers(dest="command")

    scan = sub.add_parser("scan", help="Run a new search")
    scan.add_argument("area", help='Area to search, e.g. "Lisbon, Portugal"')
    scan.add_argument("--checkin", metavar="YYYY-MM-DD", help="Check-in date")
    scan.add_argument("--checkout", metavar="YYYY-MM-DD", help="Check-out date")
    scan.add_argument("--guests", type=int, metavar="N", help="Number of guests")
    scan.add_argument(
        "--sources",
        default="airbnb",
        help="Comma-separated sources: airbnb, booking (default: airbnb)",
    )
    scan.add_argument(
        "--batch",
        action="store_true",
        help=(
            "Force the Anthropic Message Batches API path for extraction, "
            "regardless of the configured batch_threshold"
        ),
    )
    scan.add_argument(
        "--no-extract",
        action="store_true",
        dest="no_extract",
        help="Skip LLM extraction; only persist raw scrapes",
    )
    scan.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Print what would be done without actually running",
    )

    return parser


def run(argv: list[str] | None = None) -> int:
    """Entry point; returns an exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "scan":
        return _cmd_scan(args)

    parser.print_help()
    return 1


def _cmd_scan(args: argparse.Namespace) -> int:
    """Execute a scan command."""
    if args.dry_run:
        print(f"[dry-run] Would scan: {args.area!r}")
        print(f"[dry-run] Sources: {args.sources}")
        if args.checkin:
            print(f"[dry-run] Check-in:  {args.checkin}")
        if args.checkout:
            print(f"[dry-run] Check-out: {args.checkout}")
        if args.guests:
            print(f"[dry-run] Guests:    {args.guests}")
        batch_mode = "forced (--batch flag)" if args.batch else f"auto (threshold={settings.batch_threshold})"
        print(f"[dry-run] Batch mode: {batch_mode}")
        return 0

    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # In a real pipeline, raw_scrapes would be populated by the scraper layer.
        # Here we run extraction over any pending scrapes already in the DB.
        pending = (
            session.query(RawScrape).filter(RawScrape.status == "pending").all()
        )

        if not pending:
            print("No pending raw scrapes found.")
            return 0

        if args.no_extract:
            print(f"--no-extract set; skipping extraction for {len(pending)} scrapes.")
            return 0

        # When --batch is set, force the batch path by passing threshold=0,
        # which guarantees len(pending) > 0 > threshold.
        threshold = 0 if args.batch else None

        print(
            f"Extracting {len(pending)} record(s) "
            f"({'batch' if args.batch else 'auto'} mode) …"
        )
        results = batch_extract(pending, session, threshold=threshold)
        print(f"Extraction complete: {len(results)} successful result(s).")

    return 0


if __name__ == "__main__":
    sys.exit(run())
