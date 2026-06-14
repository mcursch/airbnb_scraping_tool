"""Smoke tests for dashboard/pages/results.py.

These tests exercise the render() function with Streamlit and DB calls mocked
out, so they run without a real Streamlit server or database.

Test coverage:
  (a) No run selected   — session state has no last_run_id → info message shown.
  (b) Empty results     — load_run_df returns an empty DataFrame → info message shown.
  (c) Populated results — load_run_df returns data → download button rendered and
                          render_map / render_price_histogram / render_source_comparison
                          are each called once.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_st(session_state: dict | None = None):
    """Return a MagicMock that stands in for the ``streamlit`` module.

    ``session_state.get`` is wired to look up keys in *session_state*.
    Context-manager-capable mocks are provided for ``expander``, ``columns``,
    and ``tabs``.
    """
    state = session_state or {}
    mock = MagicMock()

    # session_state.get(key) → look up in the supplied dict; default to None.
    mock.session_state.get.side_effect = lambda key, *args: state.get(
        key, args[0] if args else None
    )

    # Make expander, columns, and tabs usable as context managers.
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    mock.expander.return_value = ctx
    mock.columns.return_value = (ctx, ctx)
    mock.tabs.return_value = (ctx, ctx, ctx)

    return mock


def _populated_df() -> pd.DataFrame:
    """Return a small non-empty DataFrame with all DISPLAY_COLUMNS (no 'id').

    Omitting 'id' skips the detail-panel block so the test stays focused on
    the download button and the three chart helpers.
    """
    return pd.DataFrame(
        {
            "name": ["Listing A", "Listing B"],
            "source": ["airbnb", "booking"],
            "nightly_price": [100.0, 150.0],
            "rating": [4.5, 4.8],
            "property_type": ["apartment", "hotel"],
            "url": ["https://example.com/1", "https://example.com/2"],
            "currency": ["USD", "USD"],
            "review_count": [10, 20],
            "bedrooms": [1, 2],
            "beds": [1, 2],
            "host_or_brand": ["Host A", "Brand B"],
            "address_text": ["City A", "City B"],
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResultsPage:
    """Smoke tests for dashboard/pages/results.py::render()."""

    def test_no_runs_shows_info(self) -> None:
        """(a) When there are no runs at all, render() shows an info message and returns."""
        import dashboard.pages.results as results_page

        mock_st = _make_mock_st(session_state={})  # no last_run_id key

        with (
            patch.object(results_page, "st", mock_st),
            patch("dashboard.pages.results.list_search_runs", return_value=[]),
        ):
            results_page.render()

        mock_st.info.assert_called_once()
        message = mock_st.info.call_args[0][0]
        assert "no search runs" in message.lower() or "search" in message.lower()

    def test_empty_dataframe_shows_info(self) -> None:
        """(b) When load_run_df returns an empty DataFrame, an info message is shown."""
        import dashboard.pages.results as results_page

        mock_st = _make_mock_st(session_state={"last_run_id": 1})
        empty_df = pd.DataFrame()

        with (
            patch.object(results_page, "st", mock_st),
            patch(
                "dashboard.pages.results.list_search_runs",
                return_value=[{"id": 1, "area_query": "Lisbon", "stats": {}}],
            ),
            patch("dashboard.pages.results.load_run_df", return_value=empty_df),
        ):
            results_page.render()

        mock_st.info.assert_called()
        all_info_text = " ".join(str(c) for c in mock_st.info.call_args_list)
        assert "no listings" in all_info_text.lower() or "new search" in all_info_text.lower()

    def test_populated_df_renders_download_and_charts(self) -> None:
        """(c) A populated DataFrame triggers the download button and all three charts."""
        import dashboard.pages.results as results_page

        df = _populated_df()
        mock_st = _make_mock_st(session_state={"last_run_id": 42})

        with (
            patch.object(results_page, "st", mock_st),
            patch(
                "dashboard.pages.results.list_search_runs",
                return_value=[{"id": 42, "area_query": "Lisbon", "stats": {"listing_count": 2}}],
            ),
            patch("dashboard.pages.results.load_run_df", return_value=df),
            patch("dashboard.pages.results.filter_df", return_value=df),
            patch("dashboard.pages.results.render_map") as mock_map,
            patch("dashboard.pages.results.render_price_histogram") as mock_hist,
            patch("dashboard.pages.results.render_source_comparison") as mock_compare,
        ):
            results_page.render()

        mock_st.download_button.assert_called_once()
        mock_map.assert_called_once()
        mock_hist.assert_called_once()
        mock_compare.assert_called_once()
