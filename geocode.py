"""Reverse geocoding via OpenStreetMap Nominatim (free, no API key).

Used by the dashboard so a user can click a point on the map and have it
resolved to a human-readable area string (e.g. "Lisbon, Portugal") that the
scrapers accept as ``SearchQuery.area``.

Network or parse failures return ``None`` rather than raising, so the UI can
fall back to manual text entry.
"""

from __future__ import annotations

from typing import Any

import httpx

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
# Nominatim's usage policy requires a descriptive User-Agent identifying the app.
_USER_AGENT = "roomradar/0.1 (short-stay market scanner)"


def reverse_geocode(
    lat: float,
    lon: float,
    *,
    http_client: httpx.Client | None = None,
    zoom: int = 8,
    lang: str = "en",
) -> str | None:
    """Resolve ``(lat, lon)`` to a concise "City, Country" area name, or ``None``.

    Parameters
    ----------
    lat, lon:
        Coordinates of the clicked point.
    http_client:
        Optional pre-built :class:`httpx.Client` (inject a mock in tests). A
        short-lived client is created and closed when omitted.
    zoom:
        Nominatim address-detail level. Default 8 ≈ city (e.g. clicking central
        Tokyo gives "Tokyo, Japan" rather than the "Suginami" ward you get at
        10). Lower = broader, higher = more granular.
    lang:
        Preferred language for place names (Nominatim ``accept-language``).
        Defaults to English so areas come back as e.g. "Tokyo, Japan" rather
        than the local-script name — more usable and matched more reliably by
        the scrapers.
    """
    client = http_client or httpx.Client(timeout=10.0)
    try:
        resp = client.get(
            _NOMINATIM_URL,
            params={
                "lat": lat,
                "lon": lon,
                "format": "jsonv2",
                "zoom": zoom,
                "addressdetails": 1,
                "accept-language": lang,
            },
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — any failure → fall back to manual entry
        return None
    finally:
        if http_client is None:
            client.close()

    return _format_area(data)


def _format_area(data: Any) -> str | None:
    """Build a "Locality, Country" string from a Nominatim response."""
    if not isinstance(data, dict):
        return None

    addr = data.get("address") or {}
    locality = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or addr.get("county")
        or addr.get("state")
    )
    country = addr.get("country")

    if locality and country:
        return f"{locality}, {country}"
    if locality:
        return locality

    # Fall back to a trimmed display_name ("first, ..., country").
    display_name = data.get("display_name")
    if isinstance(display_name, str) and display_name.strip():
        parts = [p.strip() for p in display_name.split(",") if p.strip()]
        if len(parts) >= 2:
            return f"{parts[0]}, {parts[-1]}"
        return display_name.strip()

    return None
