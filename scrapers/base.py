from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from schemas.search_query import SearchQuery

# A single captured API response body, keyed by arbitrary string fields.
RawPayload = dict[str, object]


class ScrapeProvider(ABC):
    """Abstract base class for all scraping providers.

    Subclasses must implement ``search``, which is an async generator that
    yields ``(url, payload)`` pairs for each captured API response.
    """

    @abstractmethod
    async def search(
        self, query: SearchQuery
    ) -> AsyncGenerator[tuple[str, RawPayload], None]:
        """Yield ``(originating_url, payload_dict)`` for every intercepted response."""
        # Make mypy happy: this body is never executed.
        raise NotImplementedError  # pragma: no cover
        yield  # type: ignore[misc]  # marks this as an async generator in the ABC
