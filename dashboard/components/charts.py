"""Chart components for the Short-Stay Market Scanner dashboard.

Provides:
- ``render_price_histogram``: distribution of nightly prices across all filtered listings.
- ``render_source_comparison``: side-by-side price distributions broken out by source
  (e.g. Airbnb vs hotel).

Both functions accept a pandas DataFrame with at minimum a ``nightly_price`` column.
Charts are built with Altair (bundled with Streamlit); a Plotly fallback is attempted
when Altair is not available.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_price_histogram(df: pd.DataFrame) -> None:
    """Render a histogram of nightly price distribution.

    Args:
        df: Filtered DataFrame.  Must contain a ``nightly_price`` column.
            Null prices are dropped before binning.
    """
    price_col = "nightly_price"
    if price_col not in df.columns:
        st.warning("Price histogram unavailable: 'nightly_price' column not found.")
        return

    prices = df[price_col].dropna()
    if prices.empty:
        st.info("No price data available for the current filters.")
        return

    currency = _infer_currency(df)

    try:
        _altair_histogram(prices, currency)
    except Exception:
        _plotly_histogram(prices, currency)


def render_source_comparison(df: pd.DataFrame) -> None:
    """Render a box / bar chart comparing nightly price distributions by source.

    Shows separate distributions for each unique value in the ``source`` column
    (e.g. 'airbnb', 'booking').  When only one source is present a note is shown
    instead of an empty or misleading chart.

    Args:
        df: Filtered DataFrame.  Must contain ``nightly_price`` and ``source``
            columns.  Rows with null prices or null sources are dropped.
    """
    for col in ("nightly_price", "source"):
        if col not in df.columns:
            st.warning(f"Source comparison unavailable: '{col}' column not found.")
            return

    plot_df = df[["nightly_price", "source"]].dropna()
    if plot_df.empty:
        st.info("No data available for source comparison with the current filters.")
        return

    sources = sorted(plot_df["source"].unique())
    if len(sources) < 2:
        st.info(
            f"Source comparison requires at least two sources; "
            f"only **{sources[0]}** is present in the current results."
        )
        return

    currency = _infer_currency(df)

    try:
        _altair_source_comparison(plot_df, currency)
    except Exception:
        _plotly_source_comparison(plot_df, currency)


# ---------------------------------------------------------------------------
# Private helpers — Altair implementations
# ---------------------------------------------------------------------------

def _altair_histogram(prices: pd.Series, currency: str) -> None:
    import altair as alt  # noqa: PLC0415

    source = pd.DataFrame({"nightly_price": prices})

    chart = (
        alt.Chart(source)
        .mark_bar(color="#FF4B4B", opacity=0.8)
        .encode(
            x=alt.X(
                "nightly_price:Q",
                bin=alt.Bin(maxbins=30),
                title=f"Nightly Price ({currency})",
            ),
            y=alt.Y("count():Q", title="Number of Listings"),
            tooltip=[
                alt.Tooltip("nightly_price:Q", bin=True, title=f"Price ({currency})"),
                alt.Tooltip("count():Q", title="Listings"),
            ],
        )
        .properties(title="Nightly Price Distribution", width="container")
        .interactive()
    )

    st.altair_chart(chart, use_container_width=True)


def _altair_source_comparison(plot_df: pd.DataFrame, currency: str) -> None:
    import altair as alt  # noqa: PLC0415

    chart = (
        alt.Chart(plot_df)
        .mark_boxplot(extent="min-max", size=40)
        .encode(
            x=alt.X("source:N", title="Source", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("nightly_price:Q", title=f"Nightly Price ({currency})"),
            color=alt.Color(
                "source:N",
                legend=alt.Legend(title="Source"),
                scale=alt.Scale(scheme="category10"),
            ),
            tooltip=[
                alt.Tooltip("source:N", title="Source"),
                alt.Tooltip("nightly_price:Q", title=f"Price ({currency})", aggregate="median", format=".0f"),
                alt.Tooltip("nightly_price:Q", title="Min", aggregate="min", format=".0f"),
                alt.Tooltip("nightly_price:Q", title="Max", aggregate="max", format=".0f"),
            ],
        )
        .properties(title="Nightly Price by Source", width="container")
    )

    st.altair_chart(chart, use_container_width=True)


# ---------------------------------------------------------------------------
# Private helpers — Plotly fallback implementations
# ---------------------------------------------------------------------------

def _plotly_histogram(prices: pd.Series, currency: str) -> None:
    import plotly.express as px  # noqa: PLC0415

    fig = px.histogram(
        prices,
        x="nightly_price",
        nbins=30,
        title="Nightly Price Distribution",
        labels={"nightly_price": f"Nightly Price ({currency})", "count": "Number of Listings"},
        color_discrete_sequence=["#FF4B4B"],
    )
    fig.update_layout(bargap=0.05, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)


def _plotly_source_comparison(plot_df: pd.DataFrame, currency: str) -> None:
    import plotly.express as px  # noqa: PLC0415

    fig = px.box(
        plot_df,
        x="source",
        y="nightly_price",
        color="source",
        title="Nightly Price by Source",
        labels={
            "source": "Source",
            "nightly_price": f"Nightly Price ({currency})",
        },
        points="outliers",
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _infer_currency(df: pd.DataFrame) -> str:
    """Return the most common currency from the DataFrame, defaulting to 'USD'."""
    if "currency" in df.columns:
        mode = df["currency"].dropna().mode()
        if not mode.empty:
            return str(mode.iloc[0])
    return "USD"
