"""Expedia scraper — hotel inventory (shares a backend with Hotels.com)."""

from __future__ import annotations

from urllib.parse import urlencode

from scrapers.base import SearchQuery
from scrapers.html_base import HtmlSearchScraper


class ExpediaScraper(HtmlSearchScraper):
    SOURCE = "expedia"
    _BASE = "https://www.expedia.com/Hotel-Search"

    def build_url(self, query: SearchQuery) -> str:
        params: dict[str, str] = {"destination": query.area}
        if query.checkin:
            params["startDate"] = str(query.checkin)
        if query.checkout:
            params["endDate"] = str(query.checkout)
        if query.guests and query.guests > 1:
            params["adults"] = str(query.guests)
        return f"{self._BASE}?{urlencode(params)}"
