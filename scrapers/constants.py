"""Airbnb-specific constants for the scraper layer.

All endpoint URL patterns, network-intercept identifiers, request-header
defaults, and CSS selectors live here so that maintenance changes (when
Airbnb rotates an endpoint) are a single-file fix.
"""

import re

# ---------------------------------------------------------------------------
# Airbnb API endpoint URL patterns
# ---------------------------------------------------------------------------

# Internal search endpoint used by Airbnb's web app (network intercept target).
# Matches both the v2 and v3 path variants:
#   https://www.airbnb.com/api/v2/explore_tabs?...
#   https://www.airbnb.com/api/v3/ExploreSearch?...
STAYS_SEARCH_URL_PATTERN: re.Pattern[str] = re.compile(
    r"https://www\.airbnb\.[a-z.]+/api/v[23]/"
    r"(?:StaysSearch|explore_tabs|ExploreSearch)",
    re.IGNORECASE,
)

# Alternate pattern used by the newer GraphQL-style StaysSearch endpoint.
# Matches: https://www.airbnb.com/api/v3/StaysSearch/<hash>
STAYS_SEARCH_GRAPHQL_URL_PATTERN: re.Pattern[str] = re.compile(
    r"https://www\.airbnb\.[a-z.]+/api/v3/StaysSearch",
    re.IGNORECASE,
)

# Older explore-tabs endpoint (still observed on some locale variants).
EXPLORE_TABS_URL_PATTERN: re.Pattern[str] = re.compile(
    r"https://www\.airbnb\.[a-z.]+/api/v2/explore_tabs",
    re.IGNORECASE,
)

# Combined pattern: matches any Airbnb listing-search API call.
ANY_LISTING_SEARCH_URL_PATTERN: re.Pattern[str] = re.compile(
    r"https://www\.airbnb\.[a-z.]+/api/v[23]/"
    r"(?:StaysSearch|explore_tabs|ExploreSearch)",
    re.IGNORECASE,
)

# Base URL for Airbnb (used for constructing listing deep-links).
AIRBNB_BASE_URL: str = "https://www.airbnb.com"

# Search path template — substitute ``{query}`` before navigating.
AIRBNB_SEARCH_URL_TEMPLATE: str = (
    "https://www.airbnb.com/s/{query}/homes"
)

# ---------------------------------------------------------------------------
# Request-header defaults (mimic a real browser session)
# ---------------------------------------------------------------------------

DEFAULT_HEADERS: dict[str, str] = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "x-airbnb-api-key": "",  # populated at runtime from page JS or env
    "x-airbnb-graphql-platform": "web",
    "x-airbnb-graphql-platform-client": "minimalist-niobe",
    "x-csrf-token": "",  # populated at runtime from page cookies
    "user-agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# ---------------------------------------------------------------------------
# CSS / DOM selectors (for fallback HTML-parsing path)
# ---------------------------------------------------------------------------

# Container for each listing card on the search-results page.
LISTING_CARD_SELECTOR: str = '[data-testid="listing-card-container"]'

# Link element within a listing card that holds the listing URL and title.
LISTING_CARD_LINK_SELECTOR: str = '[data-testid="listing-card-title"]'

# Price element inside a listing card.
LISTING_PRICE_SELECTOR: str = '[data-testid="price-availability-row"]'

# ---------------------------------------------------------------------------
# Network-intercept identifiers (for Playwright route matching)
# ---------------------------------------------------------------------------

# Glob patterns passed to ``page.route()`` / ``page.wait_for_response()``.
STAYS_SEARCH_ROUTE_GLOB: str = "**/api/v*/StaysSearch**"
EXPLORE_TABS_ROUTE_GLOB: str = "**/api/v*/explore_tabs**"

# Response header key Airbnb sets on its API responses (used for detection).
AIRBNB_API_RESPONSE_HEADER: str = "x-airbnb-request-id"

# ---------------------------------------------------------------------------
# Pagination / rate-limiting defaults
# ---------------------------------------------------------------------------

# Maximum number of search-results pages to fetch per run (override via config).
DEFAULT_MAX_PAGES: int = 5

# Approximate inter-request delay range in seconds (randomised in scraper).
REQUEST_DELAY_MIN_S: float = 1.5
REQUEST_DELAY_MAX_S: float = 4.0
