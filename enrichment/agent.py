"""Reason-and-act enrichment agent.

When a scraped listing comes back with important fields missing, this agent
reasons about how each gap might be obtained and then *acts* on it — using
Claude's server-side ``web_search`` and ``web_fetch`` tools to look the
information up — and returns each filled value with a confidence score and the
source URL it came from.

Design
------
* One Anthropic ``messages.create`` agentic loop per listing.
* Tools: server-side ``web_search_20260209`` + ``web_fetch_20260209`` (Claude
  runs these on Anthropic's infrastructure; dynamic filtering is built in) plus
  a single strict client tool ``submit_enrichment`` that the model calls to hand
  back its findings as validated JSON.
* The loop handles ``stop_reason == "pause_turn"`` (server-tool iteration limit)
  by re-sending, and terminates when the model calls ``submit_enrichment`` or
  ends its turn.
* Nothing is invented: the prompt instructs the model to omit any field it
  cannot find a real source for, and every returned value carries a
  ``source_url`` for provenance.

Cost
----
Each run bills LLM tokens plus one charge per ``web_search`` request. Enrichment
is therefore gated and capped by the caller (see ``pipeline``/``config``) — it is
off by default.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

from config import settings
from schemas.listing import ExtractedListing

logger = logging.getLogger(__name__)

# Server-tool identifier. We use web_search only: its results already include
# filtered page content (dynamic filtering), and unlike web_fetch it does not
# run inside a code-execution container (which would require threading a
# container_id across pause_turn continuations). ``max_uses`` bounds how many
# searches the model runs per request so one call can't run for minutes (a
# listing can have 20+ gaps; uncapped, the model searches each one serially).
_WEB_SEARCH_MAX_USES = 6
_WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": _WEB_SEARCH_MAX_USES,
}

# Fields worth the cost of enrichment — the expanded categories plus a few
# commonly-missing core attributes. Identity/price fields that the scraper
# reliably provides are intentionally excluded.
IMPORTANT_FIELDS: tuple[str, ...] = (
    # core, often missing on search-results pages
    "property_type",
    "rating",
    "review_count",
    "host_or_brand",
    # host & trust signals
    "host_is_superhost",
    "host_response_rate",
    "host_response_time",
    "years_hosting",
    "rating_cleanliness",
    "rating_location",
    "rating_value",
    "license_number",
    # pricing breakdown
    "cleaning_fee",
    "service_fee",
    "taxes",
    "deposit",
    "minimum_nights",
    # location precision
    "neighborhood",
    "distance_to_center_km",
    # policies & rules
    "cancellation_policy",
    "checkin_time",
    "checkout_time",
    "instant_book",
    "pets_allowed",
    "smoking_allowed",
    "events_allowed",
)

# Fields that live on ListingSnapshot rather than Listing (per-stay pricing).
SNAPSHOT_FIELDS: frozenset[str] = frozenset(
    {"cleaning_fee", "service_fee", "taxes", "deposit", "minimum_nights"}
)

SYSTEM_PROMPT = (
    "You are a research assistant for a short-stay accommodation market scanner. "
    "You are given one listing whose record has gaps. Your job is to fill ONLY the "
    "requested missing fields by researching the web.\n\n"
    "Process for each missing field:\n"
    "1. Reason about the best way to obtain it (the listing's own page, the host/brand "
    "site, a licensing registry, a maps/geocoding source, review aggregators, etc.).\n"
    "2. Use web_search to actually find it (search result content is provided "
    "inline). Prefer the listing's own platform page and other authoritative sources.\n"
    "3. Only report a field if you find a credible source for it. Never guess or "
    "fabricate. If you cannot find a field, omit it.\n\n"
    "When done, call submit_enrichment with one entry per field you could fill, each "
    "with the value (as a string), a confidence in [0,1] reflecting source quality and "
    "match certainty, the source_url you got it from, and one sentence of reasoning. "
    "Match values to the requested types (numbers as plain numerals, booleans as "
    "'true'/'false')."
)


def _python_type(field_name: str) -> type:
    """Return the underlying scalar type of an ExtractedListing field."""
    ann = ExtractedListing.model_fields[field_name].annotation
    # Unwrap Optional[...] / X | None.
    if get_origin(ann) is not None:
        args = [a for a in get_args(ann) if a is not type(None)]
        if args:
            ann = args[0]
    return ann if isinstance(ann, type) else str


def _coerce(field_name: str, raw: str) -> Any:
    """Coerce a string value returned by the model to the field's Python type."""
    t = _python_type(field_name)
    s = str(raw).strip()
    if s == "":
        return None
    try:
        if t is bool:
            return s.lower() in ("true", "1", "yes", "y")
        if t is int:
            # Tolerate "95%", "2 nights", "1,234".
            digits = "".join(ch for ch in s if ch.isdigit() or ch == "-")
            return int(digits) if digits else None
        if t is float:
            cleaned = "".join(ch for ch in s if ch.isdigit() or ch in ".-")
            return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None
    return s


def _submit_tool() -> dict[str, Any]:
    """The strict client tool the model calls to return its findings."""
    return {
        "name": "submit_enrichment",
        "description": (
            "Submit the fields you were able to fill by research. Include only "
            "fields you found a credible source for; omit the rest."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "enum": list(IMPORTANT_FIELDS)},
                            "value": {"type": "string"},
                            "confidence": {"type": "number"},
                            "source_url": {"type": "string"},
                            "reasoning": {"type": "string"},
                        },
                        "required": [
                            "field",
                            "value",
                            "confidence",
                            "source_url",
                            "reasoning",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["fields"],
            "additionalProperties": False,
        },
    }


def missing_important_fields(listing: ExtractedListing) -> list[str]:
    """Return the IMPORTANT_FIELDS that are None/empty on *listing*."""
    gaps: list[str] = []
    for name in IMPORTANT_FIELDS:
        val = getattr(listing, name, None)
        if val is None or (isinstance(val, str) and not val.strip()):
            gaps.append(name)
    return gaps


@dataclass
class EnrichmentResult:
    """Outcome of enriching one listing."""

    # field -> coerced value
    filled: dict[str, Any] = field(default_factory=dict)
    # field -> {value, confidence, source_url, reasoning}
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    web_search_count: int = 0
    status: str = "enriched"  # "enriched" | "no_gaps" | "failed"
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """LLM token cost plus web-search request cost (≈ $10 / 1k searches)."""
        token_cost = (
            self.input_tokens / 1_000_000 * settings.CLAUDE_OPUS_4_8_INPUT_PRICE_PER_MTOK
            + self.output_tokens / 1_000_000 * settings.CLAUDE_OPUS_4_8_OUTPUT_PRICE_PER_MTOK
            + self.cache_read_tokens
            / 1_000_000
            * settings.CLAUDE_OPUS_4_8_CACHE_READ_PRICE_PER_MTOK
        )
        return token_cost + self.web_search_count * settings.WEB_SEARCH_PRICE_PER_REQUEST


class EnrichmentAgent:
    """Fills missing listing fields via a web-research agentic loop."""

    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        *,
        max_loops: int = 2,
        max_fields: int | None = None,
        max_seconds: float = 100.0,
    ) -> None:
        self._client = client
        self._model = model or settings.enrich_model
        self._max_loops = max_loops
        # Hard wall-clock budget per listing so a hard-to-research listing (e.g.
        # an anonymous apartment the model can't find) can't loop indefinitely
        # across pause_turn continuations and hang a UI spinner.
        self._max_seconds = max_seconds
        # Cap how many gaps we research per listing. Each field costs ~1 web
        # search, and a request that searches 20+ fields serially runs for
        # minutes; bounding the field count keeps each call fast and cheap.
        self._max_fields = max_fields or settings.enrich_max_fields

    @property
    def model(self) -> str:
        return self._model

    def _known_summary(self, listing: ExtractedListing) -> str:
        known = {
            k: v
            for k, v in listing.model_dump().items()
            if v not in (None, [], "") and k not in IMPORTANT_FIELDS
        }
        return json.dumps(known, separators=(",", ":"), default=str)

    def enrich(self, listing: ExtractedListing, *, source: str) -> EnrichmentResult:
        """Research and fill the missing important fields of *listing*."""
        gaps = missing_important_fields(listing)
        if not gaps:
            return EnrichmentResult(status="no_gaps")
        if self._client is None:
            raise RuntimeError("No Anthropic client configured for enrichment.")

        # Research only the highest-priority gaps (IMPORTANT_FIELDS order) so a
        # single call stays bounded in time and cost.
        gaps = gaps[: self._max_fields]

        gap_lines = "\n".join(
            f"- {name}: {ExtractedListing.model_fields[name].description}" for name in gaps
        )
        user_prompt = (
            f"Source platform: {source}\n"
            f"Listing URL: {listing.url or '(unknown)'}\n"
            f"Known fields: {self._known_summary(listing)}\n\n"
            f"Research and fill these MISSING fields where you can find a credible source:\n"
            f"{gap_lines}\n\n"
            "Call submit_enrichment when finished."
        )

        tools = [_WEB_SEARCH_TOOL, _submit_tool()]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
        result = EnrichmentResult()
        container: Any = None
        started = time.monotonic()

        try:
            for _ in range(self._max_loops):
                if time.monotonic() - started > self._max_seconds:
                    logger.info("Enrichment time budget hit for %s", listing.url)
                    break
                kwargs: dict[str, Any] = dict(
                    model=self._model,
                    max_tokens=4096,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    tools=tools,
                    messages=messages,
                )
                # Carry the server-tool container across pause_turn continuations
                # if the API ever returns one (required for container-backed tools).
                if container is not None:
                    kwargs["container"] = container
                resp = self._client.messages.create(**kwargs)
                self._accumulate_usage(result, resp.usage)
                resp_container = getattr(resp, "container", None)
                if resp_container is not None:
                    container = getattr(resp_container, "id", resp_container)

                submit = next(
                    (
                        b
                        for b in resp.content
                        if getattr(b, "type", None) == "tool_use"
                        and getattr(b, "name", None) == "submit_enrichment"
                    ),
                    None,
                )
                if submit is not None:
                    self._apply_submission(result, submit.input)
                    return result

                if getattr(resp, "stop_reason", None) == "pause_turn":
                    # Server tool hit its iteration limit; continue the turn.
                    messages.append({"role": "assistant", "content": resp.content})
                    continue

                # end_turn (or any other terminal reason) without a submission.
                result.status = "enriched" if result.filled else "no_gaps"
                return result

            result.status = "enriched" if result.filled else "no_gaps"
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("Enrichment failed for %s: %s", listing.url, exc)
            result.status = "failed"
            result.error = str(exc)
            return result

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _accumulate_usage(result: EnrichmentResult, usage: Any) -> None:
        if not usage:
            return
        result.input_tokens += getattr(usage, "input_tokens", 0) or 0
        result.output_tokens += getattr(usage, "output_tokens", 0) or 0
        result.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        server = getattr(usage, "server_tool_use", None)
        if server is not None:
            result.web_search_count += getattr(server, "web_search_requests", 0) or 0

    @staticmethod
    def _apply_submission(result: EnrichmentResult, payload: Any) -> None:
        entries = (payload or {}).get("fields", []) if isinstance(payload, dict) else []
        for entry in entries:
            name = entry.get("field")
            if name not in IMPORTANT_FIELDS:
                continue
            coerced = _coerce(name, entry.get("value", ""))
            if coerced is None:
                continue
            result.filled[name] = coerced
            result.provenance[name] = {
                "value": coerced,
                "confidence": entry.get("confidence"),
                "source_url": entry.get("source_url"),
                "reasoning": entry.get("reasoning"),
            }
