"""Listing detail panel component for the Streamlit dashboard.

Usage
-----
Call :func:`render_detail_panel` with a listing ID and an open SQLAlchemy
session. The function fetches the full :class:`~db.models.Listing` record
plus its latest :class:`~db.models.ListingSnapshot` from the repository and
renders a rich detail view in a right-column layout.

    from dashboard.components.detail_panel import render_detail_panel
    render_detail_panel(listing_id=42, session=session)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from db.repo import get_listing_with_latest_snapshot

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def render_detail_panel(listing_id: int, session: "Session") -> None:
    """Render a detail panel for the listing identified by *listing_id*.

    The panel is drawn directly into the Streamlit container that is active
    when this function is called.  Typically you would call it inside the
    right column of a two-column layout produced by ``st.columns``.

    Parameters
    ----------
    listing_id:
        Primary-key ID of the :class:`~db.models.Listing` to display.
    session:
        An open SQLAlchemy :class:`~sqlalchemy.orm.Session`.  The caller is
        responsible for lifetime management (open/close/context-manager).
    """
    result = get_listing_with_latest_snapshot(session, listing_id)
    if result is None:
        st.warning(f"Listing #{listing_id} not found.")
        return

    listing, snapshot = result

    # ------------------------------------------------------------------ header
    st.subheader(listing.name or f"Listing #{listing_id}")

    if listing.property_type:
        st.caption(f"🏠 {listing.property_type}")

    # ------------------------------------------------------------------ URL
    if listing.url:
        st.markdown(
            f"[Open on {listing.source.capitalize()} ↗]({listing.url})",
            help="Opens in a new browser tab",
        )

    st.divider()

    # ------------------------------------------------------------------ core fields
    col_a, col_b = st.columns(2)

    with col_a:
        if listing.address_text:
            st.markdown(f"**📍 Address**  \n{listing.address_text}")

        if listing.host_or_brand:
            st.markdown(f"**👤 Host / Brand**  \n{listing.host_or_brand}")

        _render_capacity(listing)

    with col_b:
        _render_rating(listing)
        _render_price(snapshot)

    st.divider()

    # ------------------------------------------------------------------ amenities
    _render_amenities(listing)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _render_capacity(listing) -> None:  # type: ignore[no-untyped-def]
    """Render bedroom / bed / bath / guest counts."""
    parts: list[str] = []
    if listing.bedrooms is not None:
        parts.append(f"{listing.bedrooms} bedroom{'s' if listing.bedrooms != 1 else ''}")
    if listing.beds is not None:
        parts.append(f"{listing.beds} bed{'s' if listing.beds != 1 else ''}")
    if listing.baths is not None:
        bath_label = "bath" if listing.baths == 1 else "baths"
        parts.append(f"{listing.baths:g} {bath_label}")
    if listing.max_guests is not None:
        parts.append(f"up to {listing.max_guests} guest{'s' if listing.max_guests != 1 else ''}")

    if parts:
        st.markdown("**🛏 Capacity**  \n" + " · ".join(parts))


def _render_rating(listing) -> None:  # type: ignore[no-untyped-def]
    """Render star rating and review count."""
    if listing.rating is not None:
        stars = "⭐" * round(listing.rating)
        review_str = ""
        if listing.review_count is not None:
            review_str = f" ({listing.review_count:,} review{'s' if listing.review_count != 1 else ''})"
        st.markdown(f"**Rating**  \n{stars} {listing.rating:.1f}{review_str}")
    elif listing.review_count is not None:
        st.markdown(f"**Reviews**  \n{listing.review_count:,}")


def _render_price(snapshot) -> None:  # type: ignore[no-untyped-def]
    """Render nightly price, fees, and total from the snapshot."""
    if snapshot is None:
        st.markdown("**💰 Price**  \n*No pricing data available*")
        return

    currency = snapshot.currency or ""

    lines: list[str] = []
    if snapshot.nightly_price is not None:
        lines.append(f"Nightly: **{currency}{snapshot.nightly_price:,.2f}**")

    fees = snapshot.fees_dict
    if fees:
        for fee_name, fee_amount in fees.items():
            try:
                lines.append(f"{fee_name.replace('_', ' ').title()}: {currency}{float(fee_amount):,.2f}")
            except (TypeError, ValueError):
                lines.append(f"{fee_name.replace('_', ' ').title()}: {fee_amount}")

    if snapshot.total_price is not None:
        lines.append(f"**Total: {currency}{snapshot.total_price:,.2f}**")

    if lines:
        st.markdown("**💰 Price**  \n" + "  \n".join(lines))
    else:
        st.markdown("**💰 Price**  \n*No pricing data available*")


def _render_amenities(listing) -> None:  # type: ignore[no-untyped-def]
    """Render amenities as a readable list when the field is non-empty."""
    amenities = listing.amenities_list
    if not amenities:
        return

    st.markdown("**✅ Amenities**")
    # Split into two balanced columns for readability
    half = (len(amenities) + 1) // 2
    col_x, col_y = st.columns(2)
    with col_x:
        for item in amenities[:half]:
            st.markdown(f"- {item}")
    with col_y:
        for item in amenities[half:]:
            st.markdown(f"- {item}")
