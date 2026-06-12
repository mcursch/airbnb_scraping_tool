"""History page — lists all past SearchRun rows with extraction cost rollup."""

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from db.repo import get_all_runs_with_cost, get_session

st.set_page_config(page_title="Run History", layout="wide")

st.title("📋 Run History")
st.caption("All past search runs, ordered newest first, with estimated Claude extraction cost.")

session: Session = get_session()
try:
    runs = get_all_runs_with_cost(session)
finally:
    session.close()

if not runs:
    st.info("No search runs found yet. Start a search from the main page.")
    st.stop()

# ── Summary dataframe ────────────────────────────────────────────────────────
# Build a display-friendly DataFrame with the five required columns.
df = pd.DataFrame(runs)

display_df = df[
    ["area_query", "started_at", "status", "listing_count", "estimated_cost_usd"]
].copy()

display_df["estimated_cost_usd"] = display_df["estimated_cost_usd"].map(
    lambda v: f"${v:.2f}"
)

display_df.columns = ["Area Query", "Started At", "Status", "Listings", "Est. Cost (USD)"]

st.dataframe(display_df, use_container_width=True, hide_index=True)

# ── Per-row Load Results buttons ─────────────────────────────────────────────
st.subheader("Load a past run")
st.caption("Click **Load Results** to view the listings for that run.")

for run in runs:
    col_area, col_time, col_status, col_listings, col_cost, col_btn = st.columns(
        [3, 2, 1, 1, 1, 1]
    )
    col_area.write(run["area_query"])
    col_time.write(
        run["started_at"].strftime("%Y-%m-%d %H:%M") if run["started_at"] else "—"
    )
    col_status.write(run["status"])
    col_listings.write(str(run["listing_count"]))
    col_cost.write(f"${run['estimated_cost_usd']:.2f}")

    if col_btn.button("Load Results", key=f"load_{run['id']}"):
        st.session_state["run_id"] = run["id"]
        st.switch_page("pages/results.py")
