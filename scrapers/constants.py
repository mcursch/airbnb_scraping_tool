# ---------------------------------------------------------------------------
# Airbnb scraper constants
#
# All URL patterns, base URLs, and tunable defaults live here so that
# breakage caused by Airbnb endpoint changes requires only a one-file fix.
# ---------------------------------------------------------------------------

# URL fragments matched against every intercepted response URL.
# A response is captured when *any* of these substrings appears in its URL.
AIRBNB_ENDPOINT_PATTERNS: tuple[str, ...] = (
    "StaysSearch",      # primary v3 search API  (POST /api/v3/StaysSearch)
    "explore_tabs",     # legacy v2 search API   (GET  /api/v2/explore_tabs)
    "ExploreSearch",    # alternate v3 explore   (POST /api/v3/ExploreSearch)
    "StaysPdpSections", # listing-detail sections (sometimes emitted on search)
)

# Base URL and path template used to construct search URLs.
AIRBNB_BASE_URL = "https://www.airbnb.com"
AIRBNB_SEARCH_PATH = "/s/{area}/homes"

# ---------------------------------------------------------------------------
# Browser / timing defaults
# ---------------------------------------------------------------------------

# Run Chromium without a visible window.
DEFAULT_HEADLESS: bool = True

# Hard timeout (ms) for page.goto(); keeps the scraper from hanging forever.
DEFAULT_PAGE_TIMEOUT_MS: int = 30_000

# Randomised extra wait (seconds) after network-idle, to catch late responses.
DEFAULT_EXTRA_WAIT_MIN: float = 1.0
DEFAULT_EXTRA_WAIT_MAX: float = 2.5

# User-agent string presented to Airbnb (desktop Chrome on macOS).
DEFAULT_USER_AGENT: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
