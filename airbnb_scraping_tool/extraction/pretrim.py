"""Deterministic pre-trim step to reduce token cost before sending to the LLM."""

from __future__ import annotations

import json
import re


def pretrim(payload: str) -> str:
    """Reduce a raw payload to just the listing-relevant content."""
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
    result = re.sub(r"<!--.*?-->", "", result, flags=re.DOTALL)
    result = re.sub(r"\s{3,}", " ", result)
    return result.strip()
