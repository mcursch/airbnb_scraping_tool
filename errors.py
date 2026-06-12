"""Project-wide exception hierarchy."""


class ScraperError(Exception):
    """Base class for all scraper errors."""


class ConfigurationError(ScraperError):
    """Raised when required configuration is absent or invalid."""
