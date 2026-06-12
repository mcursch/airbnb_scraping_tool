"""Streamlit entry point for the Short-Stay Market Scanner dashboard."""

import streamlit as st

from db.repo import init_db

# Ensure the database and all tables exist before any page renders.
init_db()

st.set_page_config(
    page_title="Short-Stay Market Scanner",
    page_icon="🏠",
    layout="wide",
)

st.title("🏠 Short-Stay Market Scanner")
st.markdown(
    "Use the sidebar to navigate between pages:\n"
    "- **History** — past search runs with extraction cost rollup\n"
    "- **Results** — listings for a selected run\n"
)
