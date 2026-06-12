"""Unit tests for dashboard/components/map_view.py.

Streamlit and pydeck calls are patched out so tests run without a real server.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from dashboard.components.map_view import render_map


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def full_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "name": ["Listing A", "Listing B", "Listing C"],
            "nightly_price": [100.0, 85.5, None],
            "currency": ["EUR", "EUR", "EUR"],
            "source": ["airbnb", "booking", "airbnb"],
            "lat": [38.72, 38.73, 38.71],
            "lon": [-9.14, -9.13, -9.15],
        }
    )


@pytest.fixture()
def no_coords_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "name": ["Listing X"],
            "nightly_price": [120.0],
        }
    )


@pytest.fixture()
def all_null_coords_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "name": ["Listing X", "Listing Y"],
            "nightly_price": [100.0, 200.0],
            "lat": [None, None],
            "lon": [None, None],
        }
    )


@pytest.fixture()
def mixed_null_coords_df() -> pd.DataFrame:
    """Some listings have coords, some do not."""
    return pd.DataFrame(
        {
            "name": ["Has coords", "No coords", "Has coords 2"],
            "nightly_price": [80.0, 90.0, 110.0],
            "currency": ["EUR", "EUR", "EUR"],
            "lat": [38.72, None, 38.74],
            "lon": [-9.14, None, -9.12],
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRenderMap:
    def test_warns_when_lat_lon_missing(self, no_coords_df):
        with patch("streamlit.warning") as mock_warn:
            render_map(no_coords_df)
            mock_warn.assert_called_once()

    def test_info_when_all_coords_null(self, all_null_coords_df):
        with (
            patch("streamlit.info") as mock_info,
            patch("streamlit.caption"),
        ):
            render_map(all_null_coords_df)
            mock_info.assert_called_once()

    def test_renders_pydeck_when_available(self, full_df):
        """When pydeck is importable, st.pydeck_chart should be called."""
        mock_deck_instance = MagicMock()
        mock_pdk = MagicMock()
        mock_pdk.Layer.return_value = MagicMock()
        mock_pdk.ViewState.return_value = MagicMock()
        mock_pdk.Deck.return_value = mock_deck_instance

        with (
            patch.dict("sys.modules", {"pydeck": mock_pdk}),
            patch("streamlit.pydeck_chart") as mock_pydeck_chart,
            patch("streamlit.caption"),
        ):
            render_map(full_df)
            mock_pydeck_chart.assert_called_once_with(mock_deck_instance)

    def test_falls_back_to_st_map_when_pydeck_unavailable(self, full_df):
        """When pydeck raises ImportError, st.map is used instead."""
        import sys
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "pydeck":
                raise ImportError("pydeck not installed")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=mock_import),
            patch("streamlit.map") as mock_st_map,
            patch("streamlit.caption"),
            patch("streamlit.dataframe"),
        ):
            render_map(full_df)
            mock_st_map.assert_called_once()

    def test_only_non_null_coords_passed_to_map(self, mixed_null_coords_df):
        """Rows with null lat/lon are dropped; only 2 of 3 rows have coords."""
        mock_pdk = MagicMock()
        mock_pdk.Deck.return_value = MagicMock()
        captured_data = []

        def capture_layer(layer_type, data, **kwargs):
            captured_data.append(data)
            return MagicMock()

        mock_pdk.Layer.side_effect = capture_layer

        with (
            patch.dict("sys.modules", {"pydeck": mock_pdk}),
            patch("streamlit.pydeck_chart"),
            patch("streamlit.caption"),
        ):
            render_map(mixed_null_coords_df)

        assert captured_data, "pydeck.Layer should have been called"
        layer_df = captured_data[0]
        assert len(layer_df) == 2, "Only rows with valid coords should appear in the layer"
        assert layer_df["lat"].notna().all()
        assert layer_df["lon"].notna().all()

    def test_caption_shows_correct_count(self, mixed_null_coords_df):
        mock_pdk = MagicMock()
        mock_pdk.Deck.return_value = MagicMock()

        with (
            patch.dict("sys.modules", {"pydeck": mock_pdk}),
            patch("streamlit.pydeck_chart"),
            patch("streamlit.caption") as mock_caption,
        ):
            render_map(mixed_null_coords_df)

        mock_caption.assert_called_once()
        caption_text: str = mock_caption.call_args[0][0]
        assert "2" in caption_text, "Caption should mention the 2 listings with coords"
