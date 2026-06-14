"""Hostelworld scraper — budget / hostel segment.

(The fourth requested source was "Agoda / Hostelworld"; Hostelworld is the more
scrapable of the pair. An Agoda scraper would follow the identical pattern —
subclass HtmlSearchScraper with an Agoda build_url.)
"""

from __future__ import annotations

from urllib.parse import urlencode

from scrapers.base import SearchQuery
from scrapers.html_base import HtmlSearchScraper


class HostelworldScraper(HtmlSearchScraper):
    SOURCE = "hostelworld"
    _BASE = "https://www.hostelworld.com/search"

    def build_url(self, query: SearchQuery) -> str:
        params: dict[str, str] = {"search_keywords": query.area}
        if query.checkin:
            params["date_from"] = str(query.checkin)
        if query.checkout:
            params["date_to"] = str(query.checkout)
        if query.guests and query.guests > 1:
            params["number_of_guests"] = str(query.guests)
        return f"{self._BASE}?{urlencode(params)}"
