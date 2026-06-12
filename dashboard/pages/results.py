"""Results page — shows listings for a run selected from the History page."""

import streamlit as st

st.set_page_config(page_title="Results", layout="wide")

st.title("🔍 Results")

run_id = st.session_state.get("run_id")

if run_id is None:
    st.info(
        "No run selected. Go to the **History** page and click "
        "**Load Results** for a past run."
    )
    st.stop()

st.success(f"Showing results for run **#{run_id}**")

# TODO (Stage 5): query Listing / ListingSnapshot rows for this run_id and
# render the full results table, map view, and export controls.
st.caption("Full results view will be implemented in Stage 5.")
