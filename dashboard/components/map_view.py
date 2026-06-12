"""Map view component for the Short-Stay Market Scanner dashboard.

Renders listing lat/lon coordinates with tooltips showing name and nightly price.
Uses pydeck when available for interactive tooltips; falls back to st.map otherwise.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def render_map(df: pd.DataFrame) -> None:
    """Render a map with one marker per listing that has non-null lat/lon values.

    Args:
        df: DataFrame containing at minimum ``lat``, ``lon``, ``name``, and
            ``nightly_price`` columns.  Rows where either ``lat`` or ``lon``
            is null are silently dropped before rendering.
    """
    required_cols = {"lat", "lon"}
    missing = required_cols - set(df.columns)
    if missing:
        st.warning(f"Map cannot be rendered: DataFrame is missing columns {missing}.")
        return

    map_df = df.dropna(subset=["lat", "lon"]).copy()

    if map_df.empty:
        st.info("No listings with location data available for the current filters.")
        return

    # Ensure numeric types for coordinates.
    map_df["lat"] = pd.to_numeric(map_df["lat"], errors="coerce")
    map_df["lon"] = pd.to_numeric(map_df["lon"], errors="coerce")
    map_df = map_df.dropna(subset=["lat", "lon"])

    if map_df.empty:
        st.info("No listings with valid numeric coordinates for the current filters.")
        return

    st.caption(f"Showing {len(map_df):,} listings with location data.")

    try:
        _render_pydeck(map_df)
    except Exception:  # pydeck not installed or rendering failed — fall back gracefully
        _render_st_map(map_df)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _tooltip_text(row: pd.Series) -> str:
    name = row.get("name", "—")
    price = row.get("nightly_price")
    currency = row.get("currency", "USD")
    if price is not None and not pd.isna(price):
        price_str = f"{currency} {price:,.0f}/night"
    else:
        price_str = "Price N/A"
    return f"{name}\n{price_str}"


def _render_pydeck(map_df: pd.DataFrame) -> None:
    """Render an interactive pydeck ScatterplotLayer map."""
    import pydeck as pdk  # noqa: PLC0415 — optional dependency

    # Build tooltip-friendly columns.
    tooltip_df = map_df.copy()
    tooltip_df["_name"] = tooltip_df.get("name", pd.Series("", index=tooltip_df.index)).fillna("—")

    if "nightly_price" in tooltip_df.columns:
        currency = tooltip_df.get("currency", pd.Series("USD", index=tooltip_df.index)).fillna("USD")
        tooltip_df["_price_label"] = (
            currency.astype(str)
            + " "
            + tooltip_df["nightly_price"].map(lambda p: f"{p:,.0f}" if pd.notna(p) else "N/A")
            + "/night"
        )
    else:
        tooltip_df["_price_label"] = "Price N/A"

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=tooltip_df,
        get_position=["lon", "lat"],
        get_radius=200,
        radius_min_pixels=6,
        radius_max_pixels=20,
        get_fill_color=[255, 75, 75, 200],
        pickable=True,
        auto_highlight=True,
    )

    center_lat = float(tooltip_df["lat"].mean())
    center_lon = float(tooltip_df["lon"].mean())

    view_state = pdk.ViewState(
        latitude=center_lat,
        longitude=center_lon,
        zoom=11,
        pitch=0,
    )

    tooltip = {
        "html": "<b>{_name}</b><br/>{_price_label}",
        "style": {
            "backgroundColor": "steelblue",
            "color": "white",
            "padding": "6px 10px",
            "border-radius": "4px",
        },
    }

    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=view_state,
        tooltip=tooltip,
        map_style="mapbox://styles/mapbox/light-v10",
    )

    st.pydeck_chart(deck)


def _render_st_map(map_df: pd.DataFrame) -> None:
    """Render a basic st.map as a fallback when pydeck is unavailable."""
    st.map(map_df[["lat", "lon"]])

    # Show a basic legend/table so users can still hover-inspect listings.
    if "name" in map_df.columns or "nightly_price" in map_df.columns:
        display_cols = [c for c in ["name", "nightly_price", "currency", "source"] if c in map_df.columns]
        st.dataframe(
            map_df[display_cols].reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
        )
