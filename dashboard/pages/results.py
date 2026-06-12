"""Results page — placeholder until Stage 4/5 persistence is wired up."""
from __future__ import annotations

import streamlit as st


def render() -> None:
    st.title("📋 Results")

    run_id = st.session_state.get("last_run_id")
    if run_id is None:
        st.info("No search run selected yet.  Go to **Search** and run a query first.")
        return

    st.write(f"Showing results for **Run ID: {run_id}**.")
    st.warning(
        "Full listing display is implemented in Stage 5.  "
        "Check back once the persistence and extraction stages are complete."
    )


render()
