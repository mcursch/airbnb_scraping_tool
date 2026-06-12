"""Results-table component.

Queries :mod:`db.repo` for Listing + ListingSnapshot rows for a given
``run_id``, applies optional filter parameters, and returns a sorted
:class:`pandas.DataFrame` ready for display.

Public API
----------
load_run_df(run_id, engine=None) -> pd.DataFrame
    Load *all* rows for a run (no filters applied).

filter_df(df, *, price_min, price_max, min_rating, property_types, sources) -> pd.DataFrame
    Apply in-memory filters to a previously-loaded DataFrame.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd
from sqlalchemy import Engine

import db.repo as repo
from db.models import engine as _default_engine

# Columns guaranteed in the returned DataFrame (order defines display order).
DISPLAY_COLUMNS: list[str] = [
    "name",
    "source",
    "nightly_price",
    "rating",
    "property_type",
    "url",
    "currency",
    "review_count",
    "bedrooms",
    "beds",
    "host_or_brand",
    "address_text",
]


def load_run_df(run_id: int, engine: Engine | None = None) -> pd.DataFrame:
    """Return a DataFrame of all Listing+ListingSnapshot rows for *run_id*.

    The DataFrame is sorted by ``nightly_price`` ascending (nulls last).
    Columns are ordered per :data:`DISPLAY_COLUMNS` (extra columns follow).

    Parameters
    ----------
    run_id:
        Primary key of the ``search_runs`` row.
    engine:
        SQLAlchemy engine to use.  Defaults to the module-level engine
        configured via the ``DATABASE_URL`` environment variable.
    """
    if engine is None:
        engine = _default_engine

    df = repo.get_listings_for_run(run_id, engine)

    if df.empty:
        return pd.DataFrame(columns=DISPLAY_COLUMNS)

    # Sort: nightly_price ascending, nulls last.
    df = df.sort_values("nightly_price", ascending=True, na_position="last")

    # Re-order columns so the required ones come first.
    ordered = [c for c in DISPLAY_COLUMNS if c in df.columns]
    rest = [c for c in df.columns if c not in DISPLAY_COLUMNS]
    df = df[ordered + rest].reset_index(drop=True)

    return df


def filter_df(
    df: pd.DataFrame,
    *,
    price_min: float = 0.0,
    price_max: float = 1_000_000.0,
    min_rating: float = 0.0,
    property_types: Sequence[str] | None = None,
    sources: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Apply filter parameters to *df* and return the filtered result.

    All parameters are optional.  Passing ``None`` (or the default) for
    ``property_types`` / ``sources`` means *no filtering* on that column.

    Parameters
    ----------
    df:
        DataFrame produced by :func:`load_run_df`.
    price_min:
        Include only rows with ``nightly_price >= price_min``.
        Rows where ``nightly_price`` is NaN are **excluded** when a price
        filter is explicitly set (i.e. when ``price_min > 0`` or
        ``price_max < 1_000_000``).
    price_max:
        Include only rows with ``nightly_price <= price_max``.
    min_rating:
        Include only rows with ``rating >= min_rating`` (NaN rows kept
        unless ``min_rating > 0``).
    property_types:
        If given, keep only rows whose ``property_type`` is in this list.
    sources:
        If given, keep only rows whose ``source`` is in this list.
    """
    if df.empty:
        return df

    mask = pd.Series(True, index=df.index)

    # --- price filter ---
    if "nightly_price" in df.columns:
        price_mask = (
            df["nightly_price"].between(price_min, price_max, inclusive="both")
        )
        # Rows with NaN price: keep them only when no price filter is active.
        price_filter_active = price_min > 0.0 or price_max < 1_000_000.0
        if price_filter_active:
            price_mask = price_mask & df["nightly_price"].notna()
        else:
            price_mask = price_mask | df["nightly_price"].isna()
        mask = mask & price_mask

    # --- rating filter ---
    if min_rating > 0.0 and "rating" in df.columns:
        mask = mask & (df["rating"] >= min_rating)

    # --- property_type filter ---
    if property_types is not None and len(property_types) > 0 and "property_type" in df.columns:
        mask = mask & df["property_type"].isin(property_types)

    # --- source filter ---
    if sources is not None and len(sources) > 0 and "source" in df.columns:
        mask = mask & df["source"].isin(sources)

    return df[mask].reset_index(drop=True)
