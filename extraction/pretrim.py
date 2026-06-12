"""
Deterministic pre-trim step.

Reduces the raw scraped payload to the smallest representation that still
contains all listing data before sending it to the LLM.  Cutting noise here
dramatically lowers token costs (and latency) without requiring the model to
filter irrelevant markup.

Strategy
--------
1. Reject obviously unusable payloads early (empty / too short).
2. If the payload is valid JSON: compact it (remove whitespace) and return.
3. Otherwise assume HTML: strip <script>, <style>, and all other tags, then
   collapse whitespace.  This leaves the visible-text content which usually
   contains the listing data in a format Claude handles well.
"""
from __future__ import annotations

import json
import re

# Minimum number of non-whitespace characters required to attempt extraction.
_MIN_PAYLOAD_CHARS = 20


def pretrim(payload: str) -> str:
    """Return a trimmed version of *payload* suitable for LLM extraction.

    Raises
    ------
    ValueError
        If the payload is empty, blank, or too short to plausibly contain
        listing data.  The extractor catches this and records a failed log.
    """
    if not payload or not payload.strip():
        raise ValueError("Payload is empty or blank")

    stripped = payload.strip()
    if len(stripped.replace(" ", "")) < _MIN_PAYLOAD_CHARS:
        raise ValueError(
            f"Payload is too short ({len(stripped)} chars) to contain listing data"
        )

    # --- JSON path ---
    if stripped[0] in ("{", "["):
        try:
            parsed = json.loads(stripped)
            # Compact JSON removes ~30-50% of characters on typical payloads.
            return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
        except json.JSONDecodeError:
            pass  # Fall through to HTML path

    # --- HTML path ---
    # Remove script and style blocks entirely (they never contain listing data).
    result = re.sub(
        r"<(script|style)[^>]*>.*?</(script|style)>",
        " ",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Drop all remaining HTML tags.
    result = re.sub(r"<[^>]+>", " ", result)
    # Decode common HTML entities.
    result = (
        result.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    # Collapse runs of whitespace to a single space.
    result = re.sub(r"\s+", " ", result).strip()

    if not result:
        raise ValueError("Payload contained only markup — no text content found")

    return result
