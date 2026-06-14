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
from geocode import reverse_geocode  # noqa: E402

# Default map view (Lisbon) used until the user clicks somewhere.
_DEFAULT_CENTER = [38.7223, -9.1393]


def _render_area_picker() -> None:
    """Interactive map: clicking a point sets the search area via reverse geocode.

    Must be rendered *outside* the search form (forms suppress the per-click
    reruns that ``st_folium`` relies on). Writes the resolved name into
    ``st.session_state["area"]`` so the form's text input picks it up.
    """
    import folium
    from streamlit_folium import st_folium

    st.session_state.setdefault("area", "")
    st.session_state.setdefault("map_center", _DEFAULT_CENTER)
    st.session_state.setdefault("picked_coords", None)

    st.subheader("📍 Pick an area on the map")
    st.caption("Click anywhere to set the search area — or just type it in the box below.")

    fmap = folium.Map(location=st.session_state["map_center"], zoom_start=4, control_scale=True)
    if st.session_state["picked_coords"]:
        folium.Marker(
            st.session_state["picked_coords"],
            tooltip=st.session_state["area"] or "Selected area",
            icon=folium.Icon(color="red", icon="home"),
        ).add_to(fmap)

    map_state = st_folium(fmap, height=380, use_container_width=True, key="area_map")

    clicked = (map_state or {}).get("last_clicked")
    if clicked:
        coords = (round(clicked["lat"], 6), round(clicked["lng"], 6))
        if coords != st.session_state.get("picked_coords"):
            with st.spinner("Looking up area…"):
                name = reverse_geocode(coords[0], coords[1])
            st.session_state["picked_coords"] = coords
            st.session_state["map_center"] = [coords[0], coords[1]]
            if name:
                st.session_state["area"] = name
                st.rerun()  # redraw marker + populate the area input
            else:
                st.warning(
                    "Couldn't resolve that point to a place name — type the area manually below."
                )

    if st.session_state["area"]:
        st.success(f"Selected area: **{st.session_state['area']}**")


# Friendly label → canonical Source identifier for the source picker.
_SOURCE_LABELS: dict[str, str] = {
    "Airbnb": "airbnb",
    "Booking.com": "booking",
    "Vrbo": "vrbo",
    "Expedia / Hotels.com": "expedia",
    "Google Hotels": "google_hotels",
    "Hostelworld": "hostelworld",
}


def _run_in_thread(
    query: SearchQuery,
    progress_q: "queue.Queue[tuple[float, str]]",
    result_q: "queue.Queue[PipelineResult]",
    enrich: bool = False,
) -> None:
    """Target function for the background search thread."""

    def _callback(fraction: float, message: str) -> None:
        progress_q.put((fraction, message))

    result = run_search(query, progress_callback=_callback, enrich=enrich)
    result_q.put(result)


def render() -> None:
    st.title("🔍 Search Listings")
    st.write(
        "Pick an area on the map (or type one), set your parameters, and search. "
        "Results will be available on the **Results** page once the run completes."
    )

    # Map-based area picker (outside the form so clicks register immediately).
    _render_area_picker()

    with st.form("search_form"):
        area = st.text_input(
            "Area *",
            key="area",
            placeholder="e.g. Lisbon, Portugal",
            help="Click the map above, or type a city / region / neighbourhood.",
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
            options=list(_SOURCE_LABELS.keys()),
            default=["Airbnb"],
            help="Which platforms to search. Non-Airbnb sources are bot-protected "
            "and may rely on the paid fallback.",
        )

        enrich = st.checkbox(
            "Enrich missing fields (web research)",
            value=False,
            help="After extraction, use a web-research agent to fill gaps on "
            "listings (extra LLM + web-search cost; capped per run).",
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

    # ── Normalise sources → list of valid Source literals ─────────────────────
    sources_list: list[str] = [
        _SOURCE_LABELS[label] for label in source_options if label in _SOURCE_LABELS
    ]

    query = SearchQuery(
        area=area.strip(),
        checkin=checkin if isinstance(checkin, date) else None,
        checkout=checkout if isinstance(checkout, date) else None,
        guests=int(guests),
        sources=sources_list or ["airbnb"],  # type: ignore[arg-type]
    )

    # ── Run pipeline in background thread ─────────────────────────────────────
    progress_q: queue.Queue[tuple[float, str]] = queue.Queue()
    result_q: queue.Queue[PipelineResult] = queue.Queue()

    thread = threading.Thread(
        target=_run_in_thread,
        args=(query, progress_q, result_q, enrich),
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
