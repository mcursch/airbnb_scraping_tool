"""Results page — Streamlit multi-page app entry point.

Sidebar controls
----------------
* Price-range slider (min / max derived from the current run's data)
* Minimum rating slider (0.0 – 5.0)
* Property-type multi-select (populated from the current run)
* Source multi-select (populated from the current run)

Main area
---------
* ``st.dataframe`` with the filtered, sorted results
* ``st.download_button`` that exports the current view as CSV
"""

from __future__ import annotations

import io

import streamlit as st

from dashboard.components.results_table import DISPLAY_COLUMNS, filter_df, load_run_df
from db.models import engine as _default_engine
from db.repo import create_all, list_run_ids

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Results",
    page_icon="🏠",
    layout="wide",
)

# Ensure tables exist (no-op if they already do).
create_all(_default_engine)

# ---------------------------------------------------------------------------
# Run selector
# ---------------------------------------------------------------------------
run_ids = list_run_ids(_default_engine)

if not run_ids:
    st.title("Results")
    st.info("No search runs found. Run a scan first to see results here.")
    st.stop()

st.title("Results")

selected_run_id: int = st.selectbox(
    "Search run",
    options=run_ids,
    format_func=lambda rid: f"Run #{rid}",
)

# ---------------------------------------------------------------------------
# Load data for the selected run (cached by run_id so reruns are fast)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Loading results…")
def _load(run_id: int):  # type: ignore[return]
    return load_run_df(run_id)


full_df = _load(selected_run_id)

if full_df.empty:
    st.warning(f"No listings found for run #{selected_run_id}.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar filters (derived from the *full* unfiltered data)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    # --- Price range ---
    raw_prices = full_df["nightly_price"].dropna()
    price_floor = float(raw_prices.min()) if not raw_prices.empty else 0.0
    price_ceiling = float(raw_prices.max()) if not raw_prices.empty else 1_000.0

    # Ensure we always have a valid range even if all prices are identical.
    if price_ceiling <= price_floor:
        price_ceiling = price_floor + 1.0

    price_min, price_max = st.slider(
        "Nightly price (£)",
        min_value=price_floor,
        max_value=price_ceiling,
        value=(price_floor, price_ceiling),
        step=1.0,
    )

    # --- Minimum rating ---
    min_rating: float = st.slider(
        "Minimum rating",
        min_value=0.0,
        max_value=5.0,
        value=0.0,
        step=0.1,
    )

    # --- Property type multi-select ---
    all_types: list[str] = sorted(
        full_df["property_type"].dropna().unique().tolist()
    )
    selected_types: list[str] = st.multiselect(
        "Property type",
        options=all_types,
        default=all_types,
        placeholder="All types",
    )

    # --- Source multi-select ---
    all_sources: list[str] = sorted(
        full_df["source"].dropna().unique().tolist()
    )
    selected_sources: list[str] = st.multiselect(
        "Source",
        options=all_sources,
        default=all_sources,
        placeholder="All sources",
    )

# ---------------------------------------------------------------------------
# Apply filters
# ---------------------------------------------------------------------------
filtered_df = filter_df(
    full_df,
    price_min=price_min,
    price_max=price_max,
    min_rating=min_rating,
    property_types=selected_types if selected_types != all_types else None,
    sources=selected_sources if selected_sources != all_sources else None,
)

# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------
col1, col2, col3 = st.columns(3)
col1.metric("Listings shown", len(filtered_df))
col2.metric(
    "Avg nightly price",
    f"£{filtered_df['nightly_price'].mean():.0f}"
    if not filtered_df["nightly_price"].dropna().empty
    else "—",
)
col3.metric(
    "Avg rating",
    f"{filtered_df['rating'].mean():.2f}"
    if not filtered_df["rating"].dropna().empty
    else "—",
)

# ---------------------------------------------------------------------------
# Results table
# ---------------------------------------------------------------------------

# Only show DISPLAY_COLUMNS that exist in the filtered DataFrame.
display_cols = [c for c in DISPLAY_COLUMNS if c in filtered_df.columns]
st.dataframe(
    filtered_df[display_cols],
    use_container_width=True,
    hide_index=True,
)

# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
csv_buffer = io.StringIO()
filtered_df[display_cols].to_csv(csv_buffer, index=False)
csv_bytes = csv_buffer.getvalue().encode("utf-8")

st.download_button(
    label="⬇️ Download as CSV",
    data=csv_bytes,
    file_name=f"results_run_{selected_run_id}.csv",
    mime="text/csv",
)
