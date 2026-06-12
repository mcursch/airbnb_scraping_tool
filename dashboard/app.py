"""Streamlit dashboard — Short-Stay Market Scanner.

Run with:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
import sys

# Ensure the project root is on sys.path when running via `streamlit run`.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.detail_panel import render_detail_panel
from db.models import Listing, get_engine
from db.repo import list_listings

# ---------------------------------------------------------------------------
# Engine / session factory (cached at app level)
# ---------------------------------------------------------------------------

DB_URL = os.getenv("DATABASE_URL", "sqlite:///market_scanner.db")


@st.cache_resource
def _get_engine():
    return get_engine(DB_URL)


# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Short-Stay Market Scanner",
    page_icon="🏠",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

page = st.sidebar.selectbox("Page", ["Results", "About"])

if page == "About":
    st.title("Short-Stay Market Scanner")
    st.markdown(
        "Scrapes Airbnb and hotel listings, normalises payloads via Claude, "
        "and presents them here. Run `python cli.py scan --help` to get started."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Results page
# ---------------------------------------------------------------------------

st.title("🏠 Results")

engine = _get_engine()

with Session(engine) as session:
    listings: list[Listing] = list_listings(session, limit=500)

    if not listings:
        st.info(
            "No listings found. Run a search from the CLI or seed the database, "
            "then refresh this page."
        )
        st.stop()

    # ------------------------------------------------------------------ build dataframe
    rows = []
    for l in listings:
        rows.append(
            {
                "id": l.id,
                "Name": l.name or f"#{l.id}",
                "Source": l.source,
                "Type": l.property_type or "",
                "Address": l.address_text or "",
                "Beds": l.beds,
                "Bedrooms": l.bedrooms,
                "Baths": l.baths,
                "Rating": l.rating,
                "Reviews": l.review_count,
                "URL": l.url or "",
            }
        )

    df = pd.DataFrame(rows)

    # ------------------------------------------------------------------ filters (sidebar)
    st.sidebar.header("Filters")

    sources = sorted(df["Source"].unique().tolist())
    selected_sources = st.sidebar.multiselect("Source", sources, default=sources)

    types = sorted(t for t in df["Type"].unique().tolist() if t)
    selected_types = st.sidebar.multiselect("Property type", types, default=types)

    min_rating = st.sidebar.slider("Minimum rating", 0.0, 5.0, 0.0, step=0.5)

    # Apply filters
    mask = df["Source"].isin(selected_sources)
    if selected_types:
        mask &= df["Type"].isin(selected_types) | (df["Type"] == "")
    mask &= df["Rating"].fillna(0) >= min_rating
    df_filtered = df[mask].reset_index(drop=True)

    if df_filtered.empty:
        st.warning("No listings match the current filters.")
        st.stop()

    # ------------------------------------------------------------------ two-column layout
    table_col, detail_col = st.columns([3, 2], gap="large")

    with table_col:
        st.subheader(f"{len(df_filtered):,} listing{'s' if len(df_filtered) != 1 else ''}")

        # st.dataframe with row-selection (Streamlit ≥ 1.35)
        display_cols = ["Name", "Source", "Type", "Beds", "Rating", "Reviews", "Address"]
        event = st.dataframe(
            df_filtered[display_cols],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="results_table",
        )

        selected_rows = event.selection.rows if event and event.selection else []  # type: ignore[union-attr]

    with detail_col:
        if selected_rows:
            selected_idx = selected_rows[0]
            listing_id = int(df_filtered.loc[selected_idx, "id"])
            st.subheader("Detail")
            render_detail_panel(listing_id=listing_id, session=session)
        else:
            st.info("👈 Select a row in the table to view listing details.")
