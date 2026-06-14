"""Per-payload extractor used by the interactive ``Pipeline`` / ``run_search``.

Like :func:`extraction.extractor.extract_listings` (the bulk/Batches path),
this turns one scraped page into *many* listings — both paths use the same
``ListingExtraction`` wrapper schema, ``client.messages.parse()``, and prompt
caching on the frozen system prompt.  The difference is only the I/O shape:
``extract_listings`` operates on persisted ``RawScrape`` rows and writes
``ExtractionLog`` rows itself; this ``Extractor`` operates on raw strings and
returns an :class:`ExtractionResult` for the caller (``Pipeline``) to persist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config import settings
from extraction.pretrim import pretrim
from schemas.listing import ExtractedListing, ListingExtraction

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """\
You are a structured data extraction assistant for a short-stay accommodation scanner.
Given raw scraped content from a listing page, extract the listing details into the
provided JSON schema.  Be precise; use null for any field you cannot determine.
Extract only what is present in the content — do not invent values.
"""


@dataclass
class ExtractionResult:
    """Outcome of extracting one raw payload into zero or more listings."""

    listings: list[ExtractedListing] = field(default_factory=list)  # empty on failure
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    status: str = "ok"  # "ok" | "failed"
    error: str | None = None

    @property
    def listing(self) -> ExtractedListing | None:
        """Convenience accessor for the first listing (or None)."""
        return self.listings[0] if self.listings else None

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
        """Extract a payload into all the listings it contains.

        On any exception the result has ``status='failed'`` and an empty
        ``listings`` list so the pipeline can record the failure and continue.
        """
        if self._client is None:
            raise RuntimeError(
                "No Anthropic client configured. Pass client= or set ANTHROPIC_API_KEY."
            )

        trimmed = pretrim(payload)

        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=4096,
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
                output_format=ListingExtraction,
            )
        except Exception as exc:  # noqa: BLE001
            return ExtractionResult(listings=[], status="failed", error=str(exc))

        usage = response.usage
        parsed: ListingExtraction = response.parsed
        return ExtractionResult(
            listings=list(parsed.listings),
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) if usage else 0,
            status="ok",
        )
