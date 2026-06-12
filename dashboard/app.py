"""Streamlit dashboard entry point.

Run with:
    streamlit run dashboard/app.py
"""

import streamlit as st

st.set_page_config(
    page_title="Short-Stay Market Scanner",
    page_icon="🏠",
    layout="wide",
)

st.title("Short-Stay Market Scanner")
st.markdown(
    """
    Welcome to the Short-Stay Market Scanner dashboard.

    Use the sidebar to navigate between pages:

    * **Results** — browse, filter, and export scraped listings.
    """
)

st.info("Select a page from the sidebar to get started.")
