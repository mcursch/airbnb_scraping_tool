"""Unit tests for dashboard/components/charts.py.

These tests validate chart logic (bin counts, source-comparison guards) without
launching a real Streamlit server.  Streamlit display calls are patched out.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from dashboard.components.charts import (
    _infer_currency,
    render_price_histogram,
    render_source_comparison,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def two_source_df() -> pd.DataFrame:
    """DataFrame with both 'airbnb' and 'booking' sources."""
    return pd.DataFrame(
        {
            "nightly_price": [80, 120, 95, 200, 55, 310, 75, 140],
            "source": ["airbnb", "airbnb", "airbnb", "airbnb", "booking", "booking", "booking", "booking"],
            "currency": ["EUR"] * 8,
        }
    )


@pytest.fixture()
def single_source_df() -> pd.DataFrame:
    """DataFrame with only one source."""
    return pd.DataFrame(
        {
            "nightly_price": [60, 90, 110],
            "source": ["airbnb", "airbnb", "airbnb"],
            "currency": ["USD"] * 3,
        }
    )


@pytest.fixture()
def no_price_df() -> pd.DataFrame:
    return pd.DataFrame({"source": ["airbnb", "booking"]})


@pytest.fixture()
def all_null_prices_df() -> pd.DataFrame:
    return pd.DataFrame(
        {"nightly_price": [None, None], "source": ["airbnb", "booking"]}
    )


# ---------------------------------------------------------------------------
# _infer_currency
# ---------------------------------------------------------------------------

class TestInferCurrency:
    def test_returns_most_common_currency(self):
        df = pd.DataFrame({"currency": ["EUR", "EUR", "USD"]})
        assert _infer_currency(df) == "EUR"

    def test_defaults_to_usd_when_column_missing(self):
        df = pd.DataFrame({"price": [1, 2]})
        assert _infer_currency(df) == "USD"

    def test_defaults_to_usd_when_all_null(self):
        df = pd.DataFrame({"currency": [None, None]})
        assert _infer_currency(df) == "USD"


# ---------------------------------------------------------------------------
# render_price_histogram
# ---------------------------------------------------------------------------

class TestRenderPriceHistogram:
    def test_renders_without_error(self, two_source_df):
        """Chart renders when data is valid."""
        with (
            patch("streamlit.altair_chart") as mock_chart,
            patch("streamlit.caption"),
        ):
            render_price_histogram(two_source_df)
            mock_chart.assert_called_once()

    def test_warns_on_missing_price_column(self, no_price_df):
        with patch("streamlit.warning") as mock_warn:
            render_price_histogram(no_price_df)
            mock_warn.assert_called_once()
            assert "nightly_price" in mock_warn.call_args[0][0]

    def test_info_on_all_null_prices(self, all_null_prices_df):
        with patch("streamlit.info") as mock_info:
            render_price_histogram(all_null_prices_df)
            mock_info.assert_called_once()

    def test_info_on_empty_df(self):
        with patch("streamlit.info") as mock_info:
            render_price_histogram(pd.DataFrame({"nightly_price": pd.Series([], dtype=float)}))
            mock_info.assert_called_once()

    def test_bin_counts_match_data(self, two_source_df):
        """The Altair chart is constructed from the same data as the DataFrame."""
        captured_charts = []

        def capture(chart, **kwargs):
            captured_charts.append(chart)

        with patch("streamlit.altair_chart", side_effect=capture):
            render_price_histogram(two_source_df)

        assert len(captured_charts) == 1
        chart = captured_charts[0]
        # Altair chart data should contain the same number of price rows.
        chart_data = chart.data
        assert len(chart_data) == len(two_source_df["nightly_price"].dropna())


# ---------------------------------------------------------------------------
# render_source_comparison
# ---------------------------------------------------------------------------

class TestRenderSourceComparison:
    def test_renders_with_two_sources(self, two_source_df):
        with (
            patch("streamlit.altair_chart") as mock_chart,
        ):
            render_source_comparison(two_source_df)
            mock_chart.assert_called_once()

    def test_info_with_single_source(self, single_source_df):
        with patch("streamlit.info") as mock_info:
            render_source_comparison(single_source_df)
            mock_info.assert_called_once()
            assert "airbnb" in mock_info.call_args[0][0]

    def test_warns_missing_source_column(self):
        df = pd.DataFrame({"nightly_price": [100, 200]})
        with patch("streamlit.warning") as mock_warn:
            render_source_comparison(df)
            mock_warn.assert_called_once()

    def test_warns_missing_price_column(self):
        df = pd.DataFrame({"source": ["airbnb", "booking"]})
        with patch("streamlit.warning") as mock_warn:
            render_source_comparison(df)
            mock_warn.assert_called_once()

    def test_info_on_all_null_prices(self, all_null_prices_df):
        with patch("streamlit.info") as mock_info:
            render_source_comparison(all_null_prices_df)
            mock_info.assert_called_once()

    def test_both_sources_appear_in_chart_data(self, two_source_df):
        """Chart data contains both sources when two are present."""
        captured = []

        def capture(chart, **kwargs):
            captured.append(chart)

        with patch("streamlit.altair_chart", side_effect=capture):
            render_source_comparison(two_source_df)

        assert captured, "altair_chart should have been called"
        chart_data = captured[0].data
        assert set(chart_data["source"].unique()) == {"airbnb", "booking"}
