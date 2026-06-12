"""Short-Stay Market Scanner — Streamlit entry point.

Run with:
    streamlit run dashboard/app.py

Three pages are registered via ``st.navigation()``:
    • Search   — launch a new listing search
    • Results  — browse the listings from the latest (or selected) run
    • History  — table of all past search runs
"""
from __future__ import annotations

import sys
import os

# Make the repo root importable regardless of the working directory from which
# Streamlit is invoked (e.g. ``streamlit run dashboard/app.py`` from the repo
# root puts the repo root in sys.path, but ``streamlit run app.py`` from inside
# the dashboard/ dir would not).
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import streamlit as st

st.set_page_config(
    page_title="Short-Stay Market Scanner",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

_pages_dir = os.path.join(os.path.dirname(__file__), "pages")

pg = st.navigation(
    [
        st.Page(
            os.path.join(_pages_dir, "search.py"),
            title="Search",
            icon="🔍",
            default=True,
        ),
        st.Page(
            os.path.join(_pages_dir, "results.py"),
            title="Results",
            icon="📋",
        ),
        st.Page(
            os.path.join(_pages_dir, "history.py"),
            title="History",
            icon="🕐",
        ),
    ]
)

pg.run()
