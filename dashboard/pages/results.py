"""Results page — displays scraped listings for the selected search run.

Renders:
- Filter controls (price range, rating, source, property type)
- Results table with sort support and CSV export
- Map view of listing locations
- Price histogram and source comparison charts
- Detail panel for an individually selected listing
"""
from __future__ import annotations

import sys
import os

import streamlit as st

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from dashboard.components.results_table import DISPLAY_COLUMNS, filter_df, load_run_df  # noqa: E402
from dashboard.components.map_view import render_map  # noqa: E402
from dashboard.components.charts import render_price_histogram, render_source_comparison  # noqa: E402
from dashboard.components.detail_panel import render_detail_panel  # noqa: E402


def render() -> None:
    st.title("📋 Results")

    run_id = st.session_state.get("last_run_id")
    if run_id is None:
        st.info("No search run selected yet.  Go to **Search** and run a query first.")
        return

    st.write(f"Showing results for **Run ID: {run_id}**.")

    # ── Load data ─────────────────────────────────────────────────────────────
    df = load_run_df(run_id)

    if df.empty:
        st.info("No listings found for this run.  Try running a new search.")
        return

    # ── Filters ───────────────────────────────────────────────────────────────
    with st.expander("🔧 Filters", expanded=False):
        col_left, col_right = st.columns(2)

        with col_left:
            price_vals = df["nightly_price"].dropna() if "nightly_price" in df.columns else None
            if price_vals is not None and not price_vals.empty:
                p_min = float(price_vals.min())
                p_max = float(price_vals.max())
                # Ensure distinct min/max so slider is valid even with a single value.
                if p_min == p_max:
                    p_max = p_min + 1.0
                price_range = st.slider(
                    "Nightly price",
                    min_value=p_min,
                    max_value=p_max,
                    value=(p_min, p_max),
                    step=1.0,
                )
            else:
                price_range = (0.0, 1_000_000.0)

            min_rating = st.slider(
                "Minimum rating",
                min_value=0.0,
                max_value=5.0,
                value=0.0,
                step=0.1,
            )

        with col_right:
            available_sources = (
                sorted(df["source"].dropna().unique().tolist())
                if "source" in df.columns
                else []
            )
            selected_sources = st.multiselect(
                "Sources",
                options=available_sources,
                default=available_sources,
            )

            available_types = (
                sorted(df["property_type"].dropna().unique().tolist())
                if "property_type" in df.columns
                else []
            )
            selected_types = st.multiselect(
                "Property types",
                options=available_types,
                default=available_types,
            )

    # Apply filters — pass None instead of empty list to skip that filter.
    filtered_df = filter_df(
        df,
        price_min=price_range[0],
        price_max=price_range[1],
        min_rating=min_rating,
        sources=selected_sources if selected_sources else None,
        property_types=selected_types if selected_types else None,
    )

    st.write(f"**{len(filtered_df):,}** of **{len(df):,}** listings shown.")

    # ── Results table ─────────────────────────────────────────────────────────
    display_cols = [c for c in DISPLAY_COLUMNS if c in filtered_df.columns]
    st.dataframe(filtered_df[display_cols], use_container_width=True, hide_index=True)

    # ── CSV export ────────────────────────────────────────────────────────────
    csv_bytes = filtered_df[display_cols].to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇ Download CSV",
        data=csv_bytes,
        file_name=f"run_{run_id}_listings.csv",
        mime="text/csv",
    )

    st.divider()

    # ── Map / Chart tabs ──────────────────────────────────────────────────────
    tab_map, tab_hist, tab_compare = st.tabs(
        ["🗺 Map", "💰 Price Histogram", "📊 Source Comparison"]
    )

    with tab_map:
        render_map(filtered_df)

    with tab_hist:
        render_price_histogram(filtered_df)

    with tab_compare:
        render_source_comparison(filtered_df)

    # ── Detail panel ──────────────────────────────────────────────────────────
    if "id" in filtered_df.columns and not filtered_df.empty:
        st.divider()
        st.subheader("🔍 Listing Detail")

        name_col = "name" if "name" in filtered_df.columns else None
        listing_options: dict[str, int] = {}
        for _, row in filtered_df.iterrows():
            lid = int(row["id"])
            label = (str(row[name_col]) if name_col and row[name_col] else None) or f"Listing #{lid}"
            # Deduplicate labels in case multiple rows share a name.
            unique_label = label
            suffix = 1
            while unique_label in listing_options:
                suffix += 1
                unique_label = f"{label} ({suffix})"
            listing_options[unique_label] = lid

        selected_label = st.selectbox(
            "Select a listing to inspect:",
            options=list(listing_options.keys()),
        )

        if selected_label:
            listing_id = listing_options[selected_label]
            from sqlalchemy.orm import sessionmaker as _sessionmaker  # noqa: PLC0415
            from db.repo import get_engine as _get_engine  # noqa: PLC0415

            _engine = _get_engine()
            _Session = _sessionmaker(bind=_engine)
            with _Session() as session:
                render_detail_panel(listing_id=listing_id, session=session)


render()
