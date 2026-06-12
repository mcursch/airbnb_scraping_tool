"""Short-Stay Market Scanner — Streamlit dashboard.

Entry point:  streamlit run dashboard/app.py

Pages
-----
- Search      : launch a new scan (stubbed until pipeline is wired in).
- Results     : filterable table of listings + map view + price charts.
- Run History : past searches and extraction cost summary (stub).
"""

from __future__ import annotations

import datetime
import random

import pandas as pd
import streamlit as st

from dashboard.components.charts import render_price_histogram, render_source_comparison
from dashboard.components.map_view import render_map

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Short-Stay Market Scanner",
    page_icon="🏠",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Seed / demo data helper
# ---------------------------------------------------------------------------

_SOURCES = ["airbnb", "booking"]
_PROPERTY_TYPES = ["Entire apartment", "Private room", "Hotel room", "Entire house", "Studio"]
_NAMES = [
    "Sunny Studio near City Centre",
    "Cosy Apartment with Balcony",
    "Modern Loft — Great Views",
    "Historic District Flat",
    "Beachside Bungalow",
    "Budget Room — Shared Bath",
    "Luxury Suite — Roof Terrace",
    "Quiet Garden Cottage",
    "Designer Penthouse",
    "Central Family Apartment",
]


def _generate_demo_data(n: int = 40, seed: int = 42) -> pd.DataFrame:
    """Return a synthetic DataFrame that exercises all dashboard components."""
    rng = random.Random(seed)

    rows = []
    # Centre around Lisbon, Portugal for a realistic-looking map.
    base_lat, base_lon = 38.717, -9.142

    for i in range(n):
        source = rng.choice(_SOURCES)
        price = round(rng.uniform(40, 350), 2)
        # Inject some null prices and some null coordinates for robustness testing.
        if rng.random() < 0.05:
            price = None  # type: ignore[assignment]
        lat = base_lat + rng.uniform(-0.05, 0.05) if rng.random() > 0.1 else None
        lon = base_lon + rng.uniform(-0.05, 0.05) if lat is not None else None

        rows.append(
            {
                "id": i + 1,
                "source": source,
                "name": rng.choice(_NAMES),
                "property_type": rng.choice(_PROPERTY_TYPES),
                "nightly_price": price,
                "currency": "EUR",
                "rating": round(rng.uniform(3.5, 5.0), 2) if rng.random() > 0.15 else None,
                "review_count": rng.randint(0, 600),
                "bedrooms": rng.randint(0, 4),
                "max_guests": rng.randint(1, 8),
                "lat": lat,
                "lon": lon,
                "url": f"https://example.com/listing/{i + 1}",
                "host_or_brand": f"Host {rng.randint(1, 20)}",
                "first_seen_at": datetime.date.today(),
            }
        )

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

if "listings_df" not in st.session_state:
    st.session_state["listings_df"] = _generate_demo_data()

# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

st.sidebar.title("🏠 Market Scanner")
page = st.sidebar.radio("Navigate", ["Search", "Results", "Run History"])

# ---------------------------------------------------------------------------
# Search page (stub)
# ---------------------------------------------------------------------------

if page == "Search":
    st.title("New Search")
    st.info(
        "The search launcher will trigger the acquire → extract → store pipeline once "
        "the scraper and extraction stages are implemented.  For now, demo data is "
        "pre-loaded on the Results page."
    )

    with st.form("search_form"):
        area = st.text_input("Area", placeholder="e.g. Lisbon, Portugal")
        col1, col2 = st.columns(2)
        checkin = col1.date_input("Check-in", value=datetime.date.today())
        checkout = col2.date_input("Check-out", value=datetime.date.today() + datetime.timedelta(days=3))
        guests = st.number_input("Guests", min_value=1, max_value=16, value=2)
        sources = st.multiselect("Sources", ["airbnb", "booking"], default=["airbnb", "booking"])
        submitted = st.form_submit_button("Start Scan")

    if submitted:
        if not area:
            st.error("Please enter an area.")
        else:
            st.success(f"Scan queued for **{area}** ({checkin} – {checkout}, {guests} guests). "
                       "Pipeline integration coming in a future stage.")

# ---------------------------------------------------------------------------
# Results page
# ---------------------------------------------------------------------------

elif page == "Results":
    st.title("Results")

    df: pd.DataFrame = st.session_state["listings_df"]

    if df.empty:
        st.info("No listings loaded.  Run a search or check back after a scan completes.")
        st.stop()

    # ------------------------------------------------------------------
    # Filter sidebar
    # ------------------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("Filters")

    # Source filter
    available_sources = sorted(df["source"].dropna().unique().tolist())
    selected_sources = st.sidebar.multiselect(
        "Source", available_sources, default=available_sources
    )

    # Price range filter
    price_values = df["nightly_price"].dropna()
    if not price_values.empty:
        price_min_global = float(price_values.min())
        price_max_global = float(price_values.max())
        price_range = st.sidebar.slider(
            "Nightly price (EUR)",
            min_value=price_min_global,
            max_value=price_max_global,
            value=(price_min_global, price_max_global),
            step=5.0,
            format="€%.0f",
        )
    else:
        price_range = (0.0, 9999.0)

    # Property type filter
    available_types = sorted(df["property_type"].dropna().unique().tolist())
    selected_types = st.sidebar.multiselect(
        "Property type", available_types, default=available_types
    )

    # Min rating filter
    min_rating = st.sidebar.slider("Minimum rating", 0.0, 5.0, 0.0, step=0.1)

    # ------------------------------------------------------------------
    # Apply filters
    # ------------------------------------------------------------------
    filtered = df.copy()

    if selected_sources:
        filtered = filtered[filtered["source"].isin(selected_sources)]

    filtered = filtered[
        (filtered["nightly_price"].isna())
        | (
            (filtered["nightly_price"] >= price_range[0])
            & (filtered["nightly_price"] <= price_range[1])
        )
    ]

    if selected_types:
        filtered = filtered[filtered["property_type"].isin(selected_types)]

    filtered = filtered[
        (filtered["rating"].isna()) | (filtered["rating"] >= min_rating)
    ]

    # ------------------------------------------------------------------
    # Summary metrics
    # ------------------------------------------------------------------
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Listings", f"{len(filtered):,}")

    with_price = filtered["nightly_price"].dropna()
    col2.metric(
        "Avg nightly price",
        f"€{with_price.mean():.0f}" if not with_price.empty else "—",
    )
    col3.metric(
        "Median nightly price",
        f"€{with_price.median():.0f}" if not with_price.empty else "—",
    )
    airbnb_count = int((filtered["source"] == "airbnb").sum())
    hotel_count = int((filtered["source"] == "booking").sum())
    col4.metric("Airbnb / Hotels", f"{airbnb_count} / {hotel_count}")

    st.markdown("---")

    # ------------------------------------------------------------------
    # Listings table
    # ------------------------------------------------------------------
    st.subheader("Listings")

    display_cols = [
        c for c in [
            "source", "name", "property_type", "nightly_price", "currency",
            "rating", "review_count", "bedrooms", "max_guests", "url",
        ]
        if c in filtered.columns
    ]

    st.dataframe(
        filtered[display_cols].reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
        column_config={
            "nightly_price": st.column_config.NumberColumn("Price/night", format="€%.2f"),
            "rating": st.column_config.NumberColumn("Rating", format="%.2f ⭐"),
            "url": st.column_config.LinkColumn("Link"),
        },
    )

    # CSV export
    csv_bytes = filtered[display_cols].to_csv(index=False).encode()
    st.download_button(
        label="Download CSV",
        data=csv_bytes,
        file_name="listings_export.csv",
        mime="text/csv",
    )

    st.markdown("---")

    # ------------------------------------------------------------------
    # Map view (collapsible)
    # ------------------------------------------------------------------
    with st.expander("🗺️  Map View", expanded=True):
        render_map(filtered)

    # ------------------------------------------------------------------
    # Price histogram (collapsible)
    # ------------------------------------------------------------------
    with st.expander("📊  Price Distribution", expanded=True):
        render_price_histogram(filtered)

    # ------------------------------------------------------------------
    # Source comparison chart (collapsible)
    # ------------------------------------------------------------------
    with st.expander("🏨  Airbnb vs Hotels — Price Comparison", expanded=True):
        render_source_comparison(filtered)

# ---------------------------------------------------------------------------
# Run History page (stub)
# ---------------------------------------------------------------------------

elif page == "Run History":
    st.title("Run History")
    st.info("Past search runs and extraction cost breakdowns will appear here once "
            "the database and pipeline stages are implemented.")
