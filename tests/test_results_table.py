"""Tests for dashboard/components/results_table.py.

Covers:
* Required columns are present after loading
* Price-range filter reduces rows to only those within the range
* Source filter hides non-matching rows
* Rating filter hides low-rated rows
* Property-type filter works
* CSV export produces valid output with correct headers
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from dashboard.components.results_table import (
    DISPLAY_COLUMNS,
    filter_df,
    load_run_df,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {"name", "source", "nightly_price", "rating", "property_type", "url"}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


class TestLoadRunDf:
    def test_returns_dataframe(self, db_engine):
        df = load_run_df(1, engine=db_engine)
        assert isinstance(df, pd.DataFrame)

    def test_required_columns_present(self, db_engine):
        df = load_run_df(1, engine=db_engine)
        assert REQUIRED_COLUMNS.issubset(set(df.columns)), (
            f"Missing columns: {REQUIRED_COLUMNS - set(df.columns)}"
        )

    def test_display_columns_order(self, db_engine):
        df = load_run_df(1, engine=db_engine)
        actual_leading = list(df.columns[: len(DISPLAY_COLUMNS)])
        expected_leading = [c for c in DISPLAY_COLUMNS if c in df.columns]
        assert actual_leading == expected_leading

    def test_all_seeded_rows_returned(self, db_engine):
        df = load_run_df(1, engine=db_engine)
        assert len(df) == 5

    def test_sorted_by_price_ascending(self, db_engine):
        df = load_run_df(1, engine=db_engine)
        prices = df["nightly_price"].dropna().tolist()
        assert prices == sorted(prices), "Rows should be sorted by nightly_price ascending"

    def test_nonexistent_run_returns_empty(self, db_engine):
        df = load_run_df(999, engine=db_engine)
        assert df.empty


# ---------------------------------------------------------------------------
# Filtering — price range
# ---------------------------------------------------------------------------


class TestPriceFilter:
    def test_filter_reduces_rows(self, db_engine):
        """Adjusting the price-range slider reduces visible rows."""
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, price_min=100.0, price_max=200.0)
        assert len(filtered) < len(full)

    def test_all_rows_within_range(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        price_min, price_max = 100.0, 200.0
        filtered = filter_df(full, price_min=price_min, price_max=price_max)
        assert not filtered.empty
        prices = filtered["nightly_price"].dropna()
        assert (prices >= price_min).all()
        assert (prices <= price_max).all()

    def test_no_rows_outside_tight_range(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, price_min=1000.0, price_max=2000.0)
        assert filtered.empty

    def test_boundary_prices_included(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        # price of 80.0 exists in seed data
        filtered = filter_df(full, price_min=80.0, price_max=80.0)
        assert len(filtered) == 1
        assert filtered.iloc[0]["nightly_price"] == 80.0


# ---------------------------------------------------------------------------
# Filtering — source
# ---------------------------------------------------------------------------


class TestSourceFilter:
    def test_airbnb_only_hides_non_airbnb(self, db_engine):
        """Selecting only 'airbnb' hides all non-Airbnb rows."""
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, sources=["airbnb"])
        assert not filtered.empty
        assert set(filtered["source"].unique()) == {"airbnb"}

    def test_booking_only(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, sources=["booking"])
        assert set(filtered["source"].unique()) == {"booking"}

    def test_multiple_sources_allowed(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, sources=["airbnb", "booking"])
        assert len(filtered) == len(full)

    def test_unknown_source_returns_empty(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, sources=["vrbo"])
        assert filtered.empty


# ---------------------------------------------------------------------------
# Filtering — rating
# ---------------------------------------------------------------------------


class TestRatingFilter:
    def test_min_rating_excludes_low_rated(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, min_rating=4.5)
        assert (filtered["rating"] >= 4.5).all()

    def test_zero_rating_keeps_all(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, min_rating=0.0)
        assert len(filtered) == len(full)


# ---------------------------------------------------------------------------
# Filtering — property type
# ---------------------------------------------------------------------------


class TestPropertyTypeFilter:
    def test_single_type_filter(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, property_types=["hotel"])
        assert not filtered.empty
        assert set(filtered["property_type"].unique()) == {"hotel"}

    def test_multiple_types(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, property_types=["apartment", "villa"])
        assert set(filtered["property_type"].unique()) == {"apartment", "villa"}


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------


class TestCombinedFilters:
    def test_price_and_source_combined(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, price_min=70.0, price_max=130.0, sources=["airbnb"])
        assert not filtered.empty
        assert (filtered["source"] == "airbnb").all()
        assert (filtered["nightly_price"] >= 70.0).all()
        assert (filtered["nightly_price"] <= 130.0).all()


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


class TestCsvExport:
    def test_csv_has_correct_headers(self, db_engine):
        """CSV headers must match the displayed columns."""
        full = load_run_df(1, engine=db_engine)
        display_cols = [c for c in DISPLAY_COLUMNS if c in full.columns]
        buf = io.StringIO()
        full[display_cols].to_csv(buf, index=False)
        buf.seek(0)
        header_line = buf.readline().strip()
        csv_headers = header_line.split(",")
        assert csv_headers == display_cols

    def test_csv_is_parseable(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        csv_bytes = full.to_csv(index=False).encode("utf-8")
        re_loaded = pd.read_csv(io.BytesIO(csv_bytes))
        assert len(re_loaded) == len(full)

    def test_filtered_csv_matches_filtered_df(self, db_engine):
        full = load_run_df(1, engine=db_engine)
        filtered = filter_df(full, sources=["airbnb"])
        csv_bytes = filtered.to_csv(index=False).encode("utf-8")
        re_loaded = pd.read_csv(io.BytesIO(csv_bytes))
        assert len(re_loaded) == len(filtered)
        assert set(re_loaded["source"].unique()) == {"airbnb"}
