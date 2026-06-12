"""Acquire → extract → store pipeline.

This module orchestrates the three main stages of the Short-Stay Market
Scanner.  The extraction and store stages are stubs pending later tasks;
the acquire stage (``run_acquire``) is fully wired and handles the
``BlockedError`` fallback logic described in PLAN.md Stage 2.
"""

from __future__ import annotations

import logging
from typing import Sequence

from scrapers.base import BlockedError, RawPayload, ScrapeProvider

logger = logging.getLogger(__name__)


def run_acquire(
    query: str,
    providers: Sequence[ScrapeProvider],
    fallback_provider: ScrapeProvider | None = None,
) -> list[RawPayload]:
    """Run the acquire stage for *query* across every provider in *providers*.

    For each provider the method calls ``provider.search(query)`` and
    accumulates the returned :class:`~scrapers.base.RawPayload` objects.
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
    caught and propagates normally — unexpected errors should not be silenced.

    Args:
        query: Human-readable location / keyword string (e.g.
            ``"Lisbon, Portugal"``).
        providers: Ordered sequence of primary scrape providers to try.
        fallback_provider: Optional paid scraping-API provider used when a
            primary provider is blocked.  ``None`` means no fallback is
            configured.

    Returns:
        Flat list of :class:`~scrapers.base.RawPayload` objects collected from
        all providers (and the fallback where used).  Ready to be persisted as
        ``RawScrape`` database rows.
    """
    results: list[RawPayload] = []

    for provider in providers:
        provider_name = type(provider).__name__
        try:
            raw = provider.search(query)
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
                raw = fallback_provider.search(query)
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
