"""LLM extraction pipeline.

Uses the Anthropic ``client.messages.parse()`` endpoint with a Pydantic
``ListingExtraction`` schema to convert raw scraped content into structured
listing objects.

Prompt caching: the system prompt + schema instructions are sent with
``cache_control={"type": "ephemeral"}`` so repeated calls reuse the KV cache
and only pay cache-read pricing for the frozen prefix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from airbnb_scraping_tool.extraction.pretrim import pretrim
from airbnb_scraping_tool.schemas import ListingExtraction

# Token-cost constants for claude-opus-4-8 (USD per million tokens)
_INPUT_COST_PER_MTOK = 5.00
_OUTPUT_COST_PER_MTOK = 25.00
_CACHE_READ_COST_PER_MTOK = 0.50

_SYSTEM_PROMPT = """\
You are a structured data extraction assistant for a short-stay accommodation scanner.
Given raw scraped content from a listing page, extract the listing details into the
provided JSON schema.  Be precise; use null for any field you cannot determine.
Extract only what is present in the content — do not invent values.
"""


@dataclass
class ExtractionResult:
    """The outcome of extracting one raw scrape payload."""

    listing: ListingExtraction | None  # None on failure
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    status: str = "ok"   # "ok" | "failed"
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """Approximate cost for this single extraction call."""
        return (
            self.input_tokens / 1_000_000 * _INPUT_COST_PER_MTOK
            + self.output_tokens / 1_000_000 * _OUTPUT_COST_PER_MTOK
            + self.cache_read_tokens / 1_000_000 * _CACHE_READ_COST_PER_MTOK
        )


class Extractor:
    """Drives the LLM extraction for a single raw payload.

    In production this calls the Anthropic API.  In tests, pass a
    ``mock_client`` that has a compatible ``.messages.parse()`` interface.
    """

    def __init__(self, client: Any | None = None, model: str = "claude-opus-4-8") -> None:
        self._client = client
        self._model = model

    def extract(self, source: str, url: str, payload: str) -> ExtractionResult:
        """Extract a single raw payload into a ``ListingExtraction``.

        Steps:
        1. Pre-trim the payload to reduce token count.
        2. Call the LLM with the trimmed payload.
        3. Return an ``ExtractionResult`` with the parsed listing and token usage.

        On any exception the result has ``status='failed'`` and the listing is
        ``None``; the pipeline continues with the next payload.
        """
        if self._client is None:
            raise RuntimeError(
                "No Anthropic client configured. Pass a client= argument or set ANTHROPIC_API_KEY."
            )

        trimmed = pretrim(payload)

        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Source: {source}\nURL: {url}\n\n"
                            f"Raw content:\n{trimmed}"
                        ),
                    }
                ],
                response_format=ListingExtraction,
            )
        except Exception as exc:  # noqa: BLE001
            return ExtractionResult(listing=None, status="failed", error=str(exc))

        usage = response.usage
        return ExtractionResult(
            listing=response.parsed,
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
            status="ok",
        )
