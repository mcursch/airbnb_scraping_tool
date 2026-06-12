"""Search launcher page.

Renders a form with area, check-in/out dates, guest count, and source
selection.  On submit the pipeline runs in a background thread; ``st.progress``
and ``st.status`` keep the UI live.  The resulting ``run_id`` lands in
``st.session_state["last_run_id"]`` so the Results page can pick it up.
"""
from __future__ import annotations

import queue
import sys
import threading
from datetime import date, timedelta

import streamlit as st

# Ensure the repo root is on the path when this page is loaded directly
# (Streamlit runs each page file as a module from its own directory).
import os

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from pipeline import PipelineResult, SearchQuery, run_search  # noqa: E402


def _run_in_thread(
    query: SearchQuery,
    progress_q: "queue.Queue[tuple[float, str]]",
    result_q: "queue.Queue[PipelineResult]",
) -> None:
    """Target function for the background search thread."""

    def _callback(fraction: float, message: str) -> None:
        progress_q.put((fraction, message))

    result = run_search(query, progress_callback=_callback)
    result_q.put(result)


def render() -> None:
    st.title("🔍 Search Listings")
    st.write(
        "Enter your search parameters below. "
        "Results will be available on the **Results** page once the run completes."
    )

    with st.form("search_form"):
        area = st.text_input(
            "Area *",
            placeholder="e.g. Lisbon, Portugal",
            help="City, region, or neighbourhood to search.",
        )

        col1, col2 = st.columns(2)
        with col1:
            checkin = st.date_input(
                "Check-in date",
                value=None,
                min_value=date.today(),
                help="Leave blank for an open-ended search.",
            )
        with col2:
            checkout = st.date_input(
                "Check-out date",
                value=None,
                min_value=date.today() + timedelta(days=1),
                help="Leave blank for an open-ended search.",
            )

        guests = st.number_input(
            "Guests",
            min_value=1,
            max_value=20,
            value=2,
            step=1,
        )

        source_options = st.multiselect(
            "Sources",
            options=["Airbnb", "Hotels"],
            default=["Airbnb", "Hotels"],
            help="Which platforms to search.",
        )

        submitted = st.form_submit_button("Search", type="primary", use_container_width=True)

    if not submitted:
        return

    # ── Validation ────────────────────────────────────────────────────────────
    errors: list[str] = []

    if not area or not area.strip():
        errors.append("**Area** is required.")

    if checkin and checkout and checkin >= checkout:
        errors.append("**Check-out** must be after **check-in**.")

    if not source_options:
        errors.append("Select at least one **source**.")

    if errors:
        for msg in errors:
            st.error(msg)
        return

    # ── Normalise sources ─────────────────────────────────────────────────────
    selected = {s.lower() for s in source_options}
    if selected == {"airbnb", "hotels"}:
        sources_str = "both"
    elif "airbnb" in selected:
        sources_str = "airbnb"
    else:
        sources_str = "hotels"

    query = SearchQuery(
        area=area.strip(),
        checkin=checkin if isinstance(checkin, date) else None,
        checkout=checkout if isinstance(checkout, date) else None,
        guests=int(guests),
        sources=sources_str,
    )

    # ── Run pipeline in background thread ─────────────────────────────────────
    progress_q: queue.Queue[tuple[float, str]] = queue.Queue()
    result_q: queue.Queue[PipelineResult] = queue.Queue()

    thread = threading.Thread(
        target=_run_in_thread,
        args=(query, progress_q, result_q),
        daemon=True,
    )
    thread.start()

    progress_bar = st.progress(0.0, text="Starting…")

    with st.status("Running pipeline…", expanded=True) as status_widget:
        while thread.is_alive() or not progress_q.empty():
            try:
                fraction, message = progress_q.get(timeout=0.1)
                progress_bar.progress(min(fraction, 1.0), text=message)
                st.write(message)
            except queue.Empty:
                pass  # keep spinning until thread finishes

        thread.join()

        result: PipelineResult = result_q.get()
        if result.status == "done":
            status_widget.update(label="Pipeline complete ✅", state="complete", expanded=False)
        else:
            status_widget.update(label="Pipeline failed ❌", state="error", expanded=True)

    progress_bar.progress(1.0, text="Done.")

    # ── Surface result ────────────────────────────────────────────────────────
    if result.status == "done":
        st.success(f"Search complete!  **Run ID: {result.run_id}**")
        st.session_state["last_run_id"] = result.run_id
        st.info("Navigate to the **Results** page to explore the listings.")
    else:
        st.error(f"Search failed: {result.error}")


render()
