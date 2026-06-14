"""Per-payload extractor used by the interactive ``Pipeline`` / ``run_search``.

This is the single-listing extraction path: one raw payload (typically a
listing detail page) → one :class:`~schemas.listing.ExtractedListing`.  It
complements :func:`extraction.extractor.extract_listings`, which is the
bulk/Batches path that turns one search-results page into *many* listings.

Both paths use ``client.messages.parse()`` with a Pydantic schema and prompt
caching on the frozen system prompt.

NOTE (known limitation, see PLAN.md): the interactive Pipeline currently maps
one scraped payload to one listing.  For search-results pages that contain many
listings, use the bulk ``extract_listings`` / Batches path instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import settings
from extraction.pretrim import pretrim
from schemas.listing import ExtractedListing

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """\
You are a structured data extraction assistant for a short-stay accommodation scanner.
Given raw scraped content from a listing page, extract the listing details into the
provided JSON schema.  Be precise; use null for any field you cannot determine.
Extract only what is present in the content — do not invent values.
"""


@dataclass
class ExtractionResult:
    """Outcome of extracting one raw payload into a single listing."""

    listing: ExtractedListing | None  # None on failure
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    status: str = "ok"  # "ok" | "failed"
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """Approximate USD cost for this single extraction call."""
        return (
            self.input_tokens / 1_000_000 * settings.CLAUDE_OPUS_4_8_INPUT_PRICE_PER_MTOK
            + self.output_tokens / 1_000_000 * settings.CLAUDE_OPUS_4_8_OUTPUT_PRICE_PER_MTOK
            + self.cache_read_tokens
            / 1_000_000
            * settings.CLAUDE_OPUS_4_8_CACHE_READ_PRICE_PER_MTOK
        )


class Extractor:
    """Drives LLM extraction for a single raw payload.

    In production this calls the Anthropic API.  In tests, pass a mock client
    with a compatible ``.messages.parse()`` interface, or subclass and override
    :meth:`extract` to avoid the network entirely.
    """

    def __init__(self, client: Any | None = None, model: str = MODEL) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def extract(self, source: str, url: str, payload: str) -> ExtractionResult:
        """Extract a single payload into an :class:`ExtractedListing`.

        On any exception the result has ``status='failed'`` and ``listing=None``
        so the pipeline can record the failure and continue.
        """
        if self._client is None:
            raise RuntimeError(
                "No Anthropic client configured. Pass client= or set ANTHROPIC_API_KEY."
            )

        trimmed = pretrim(payload)

        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": f"Source: {source}\nURL: {url}\n\nRaw content:\n{trimmed}",
                    }
                ],
                output_format=ExtractedListing,
            )
        except Exception as exc:  # noqa: BLE001
            return ExtractionResult(listing=None, status="failed", error=str(exc))

        usage = response.usage
        return ExtractionResult(
            listing=response.parsed,
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) if usage else 0,
            status="ok",
        )
