"""Command-line interface for the Short-Stay Market Scanner."""

from __future__ import annotations

import datetime
import sys

import click

import pipeline
from schemas.models import SearchQuery


@click.group()
def main() -> None:
    """Short-Stay Market Scanner — scrape, extract, and compare short-stay listings."""


@main.command()
@click.argument("area")
@click.option(
    "--checkin",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Check-in date (YYYY-MM-DD).",
)
@click.option(
    "--checkout",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Check-out date (YYYY-MM-DD).",
)
@click.option(
    "--guests",
    type=int,
    default=1,
    show_default=True,
    help="Number of guests.",
)
@click.option(
    "--sources",
    type=click.Choice(["airbnb", "hotels", "all"], case_sensitive=False),
    default="all",
    show_default=True,
    help="Which sources to scrape: airbnb, hotels, or all.",
)
def scan(
    area: str,
    checkin: datetime.datetime | None,
    checkout: datetime.datetime | None,
    guests: int,
    sources: str,
) -> None:
    """Scan AREA for short-stay listings.

    AREA is a free-text location string, e.g. "Lisbon, Portugal" or "New York, NY".
    """
    resolved_sources: list[str]
    if sources == "all":
        resolved_sources = ["airbnb", "hotels"]
    else:
        resolved_sources = [sources]

    query = SearchQuery(
        area=area,
        checkin=checkin.date() if checkin else None,
        checkout=checkout.date() if checkout else None,
        guests=guests,
        sources=resolved_sources,  # type: ignore[arg-type]
    )

    try:
        pipeline.run(query)
    except NotImplementedError as exc:
        click.echo(f"Pipeline stage not yet implemented: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
