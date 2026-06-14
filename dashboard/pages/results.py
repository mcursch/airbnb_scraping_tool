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
from db.repo import get_engine as _get_engine, list_search_runs  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sessionmaker  # noqa: E402


@st.cache_resource
def _get_cached_engine():
    """Return a module-level cached engine so we create it only once."""
    return _get_engine()


def _gap_columns(df) -> list[str]:
    """Enrichable display columns present in the frame (for the gap summary)."""
    candidates = [
        "neighborhood", "host_is_superhost", "cleaning_fee", "minimum_nights",
        "cancellation_policy", "checkin_time", "rating", "review_count",
    ]
    return [c for c in candidates if c in df.columns]


def _render_enrich_control(run_id: int, df) -> None:
    """Render the 'Enrich missing fields' button + a blanks summary."""
    gap_cols = _gap_columns(df)
    blanks = int(df[gap_cols].isna().sum().sum()) if gap_cols else 0

    with st.container(border=True):
        st.markdown("#### ✨ Enrich missing data")
        st.caption(
            "Fill blank fields (host, fees, policies, ratings…) with an agent that "
            "researches the web and records a source + confidence for each value. "
            "Costs extra LLM tokens + web searches (~$0.15 and ~80s per listing)."
        )
        c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.4, 1.2])
        with c1:
            n = st.number_input(
                "Listings to enrich", min_value=1, max_value=10, value=2, step=1,
                help="The gappiest listings are enriched first.",
            )
        with c2:
            fields = st.number_input(
                "Fields per listing", min_value=1, max_value=12, value=6, step=1,
                help="How many blanks to research per listing (each ≈ one web search).",
            )
        with c3:
            st.metric("Blank cells in shown fields", f"{blanks:,}")
        with c4:
            st.write("")
            go = st.button("✨ Enrich now", type="primary", use_container_width=True)

    if go:
        from enrichment.run_enrich import enrich_run

        bar = st.progress(0.0, text="Starting enrichment…")

        def _cb(frac: float, msg: str) -> None:
            bar.progress(min(max(frac, 0.0), 1.0), text=msg)

        with st.spinner("Researching the web to fill blanks…"):
            summary = enrich_run(
                run_id, max_listings=int(n), max_fields=int(fields), progress_callback=_cb
            )
        bar.progress(1.0, text="Done.")
        st.success(
            f"Enriched **{summary['enriched_count']}/{summary['selected']}** listings · "
            f"{summary['searches']} web searches · ${summary['cost_usd']:.2f}."
        )
        # Reload so the table reflects the newly-filled values.
        st.rerun()


def render() -> None:
    st.title("📋 Results")

    # Pick which run to view. Defaults to this session's last run, otherwise the
    # most recent run — so the page is useful on its own and across reloads, not
    # just immediately after a search.
    runs = list_search_runs(limit=50, engine=_get_cached_engine())
    if not runs:
        st.info("No search runs yet.  Go to **Search** and run a query first.")
        return

    run_ids = [r["id"] for r in runs]

    def _label(rid: int) -> str:
        meta = next((r for r in runs if r["id"] == rid), None)
        if not meta:
            return f"Run {rid}"
        stats = meta.get("stats") or {}
        n = stats.get("listing_count", stats.get("total_listings", 0))
        return f"Run {rid} — {meta.get('area_query', '?')} ({n} listings)"

    default_id = st.session_state.get("last_run_id")
    default_idx = run_ids.index(default_id) if default_id in run_ids else 0

    run_id = st.selectbox(
        "Search run",
        options=run_ids,
        index=default_idx,
        format_func=_label,
    )

    st.write(f"Showing results for **Run ID: {run_id}**.")

    # ── Load data ─────────────────────────────────────────────────────────────
    df = load_run_df(run_id)

    if df.empty:
        st.info("No listings found for this run.  Try running a new search.")
        return

    # ── Enrich missing fields (on-demand web research) ──────────────────────────
    _render_enrich_control(run_id, df)

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
            _Session = _sessionmaker(bind=_get_cached_engine())
            with _Session() as session:
                render_detail_panel(listing_id=listing_id, session=session)
                _render_provenance(session, listing_id)


def _render_provenance(session, listing_id: int) -> None:
    """Show where each enriched field came from (value, confidence, source URL)."""
    from db.models import Listing

    listing = session.get(Listing, listing_id)
    prov = getattr(listing, "enrichment", None) if listing else None
    if not prov:
        return

    with st.expander("✨ Enrichment provenance", expanded=True):
        st.caption("Fields filled by web research, with source and confidence.")
        for field, info in prov.items():
            conf = info.get("confidence")
            conf_str = f"{conf:.0%}" if isinstance(conf, (int, float)) else "—"
            url = info.get("source_url") or ""
            src = f"[source]({url})" if url else "—"
            st.markdown(
                f"- **{field}** = `{info.get('value')}`  ·  confidence {conf_str}  ·  {src}"
            )
            if info.get("reasoning"):
                st.caption(f"    ↳ {info['reasoning']}")


render()
