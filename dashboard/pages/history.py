"""History page — lists past SearchRun rows from the database."""
from __future__ import annotations

import sys
import os

import streamlit as st

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from db.repo import list_search_runs  # noqa: E402


def render() -> None:
    st.title("🕐 Search History")

    runs = list_search_runs(limit=50)

    if not runs:
        st.info("No searches have been run yet.  Head to **Search** to get started.")
        return

    st.write(f"Showing the {len(runs)} most recent search runs.")

    # Flatten for display
    display_rows = [
        {
            "Run ID": r["id"],
            "Area": r["area_query"],
            "Check-in": r["checkin"] or "—",
            "Check-out": r["checkout"] or "—",
            "Guests": r["guests"],
            "Sources": r["sources"],
            "Status": r["status"],
            "Started": r["started_at"].strftime("%Y-%m-%d %H:%M") if r["started_at"] else "—",
            "Finished": r["finished_at"].strftime("%Y-%m-%d %H:%M") if r["finished_at"] else "—",
        }
        for r in runs
    ]

    st.dataframe(display_rows, use_container_width=True)

    # Allow the user to jump to results for a specific run
    run_ids = [r["id"] for r in runs]
    selected_id = st.selectbox("Load results for run:", options=run_ids, index=0)
    if st.button("View Results", use_container_width=True):
        st.session_state["last_run_id"] = selected_id
        st.success(f"Run {selected_id} selected — navigate to **Results** to view.")


render()
