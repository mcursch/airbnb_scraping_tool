"""Google Hotels scraper — aggregator across many providers.

Heavily bot-protected (reCAPTCHA / "unusual traffic"); will frequently fall
through to the Bright Data fallback.
"""

from __future__ import annotations

from urllib.parse import urlencode

from scrapers.base import SearchQuery
from scrapers.html_base import HtmlSearchScraper


class GoogleHotelsScraper(HtmlSearchScraper):
    SOURCE = "google_hotels"
    _BASE = "https://www.google.com/travel/search"

    def build_url(self, query: SearchQuery) -> str:
        params: dict[str, str] = {"q": f"hotels in {query.area}", "hl": "en"}
        if query.checkin and query.checkout:
            # Google encodes dates in the q string; appending keeps it simple.
            params["q"] = (
                f"hotels in {query.area} from {query.checkin} to {query.checkout}"
            )
        return f"{self._BASE}?{urlencode(params)}"
