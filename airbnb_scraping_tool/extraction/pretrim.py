"""Deterministic pre-trim step to reduce token cost before sending to the LLM.

Strips script tags, style tags, and HTML comments from raw HTML payloads.
If the payload looks like JSON already, it is returned as-is.
"""

from __future__ import annotations

import json
import re


def pretrim(payload: str) -> str:
    """Reduce a raw payload to just the listing-relevant content.

    Strategy:
    1. If the payload is valid JSON, return it directly (no markup to strip).
    2. Otherwise treat it as HTML: remove <script>, <style> blocks and HTML
       comments, collapse whitespace runs.
    """
    stripped = payload.strip()

    # Fast path: already JSON
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            return stripped
        except json.JSONDecodeError:
            pass

    # Strip script/style blocks
    result = re.sub(r"<script[^>]*>.*?</script>", "", stripped, flags=re.IGNORECASE | re.DOTALL)
    result = re.sub(r"<style[^>]*>.*?</style>", "", result, flags=re.IGNORECASE | re.DOTALL)
    # Strip HTML comments
    result = re.sub(r"<!--.*?-->", "", result, flags=re.DOTALL)
    # Collapse whitespace
    result = re.sub(r"\s{3,}", " ", result)
    return result.strip()
