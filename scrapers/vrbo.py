"""Vrbo scraper — whole-home short-stays (closest analog to Airbnb)."""

from __future__ import annotations

from urllib.parse import urlencode

from scrapers.base import SearchQuery
from scrapers.html_base import HtmlSearchScraper


class VrboScraper(HtmlSearchScraper):
    SOURCE = "vrbo"
    _BASE = "https://www.vrbo.com/search"

    def build_url(self, query: SearchQuery) -> str:
        params: dict[str, str] = {"destination": query.area}
        if query.checkin:
            params["startDate"] = str(query.checkin)
        if query.checkout:
            params["endDate"] = str(query.checkout)
        if query.guests and query.guests > 1:
            params["adults"] = str(query.guests)
        return f"{self._BASE}?{urlencode(params)}"
