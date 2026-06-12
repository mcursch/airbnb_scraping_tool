"""
Deterministic pre-trim for raw scrape payloads.

Public API
----------
``pretrim(payload: str) -> str``

Accepts an HTML page or a raw JSON string captured by a scraper.  Returns a
compact string that contains only the listing-data JSON fragments.

Algorithm
---------
1. Detect HTML vs JSON by inspecting the first few characters.
2. **HTML path**: extract JSON blobs from every ``<script>`` tag, then apply
   the JSON path to each blob; strip ``<style>`` tags and inline ``style=``
   attributes from any fallback text.
3. **JSON path**: walk the object tree recursively looking for dicts whose key
   fingerprint matches that of a property listing (price signal + name or
   location signal, checked one nesting level deep to handle wrapper objects
   that separate "listing" and "pricingQuote" sub-dicts).  Each matched dict
   is slimmed to listing-relevant keys only; everything else is dropped.
4. If no listing objects are found, a generic key-based noise filter is applied
   as a fallback.

No I/O, no network, no LLM.
"""

from __future__ import annotations

import json
import re
from typing import Any

# ---------------------------------------------------------------------------
# Tuneable constants
# ---------------------------------------------------------------------------

#: String values longer than this character limit are dropped (e.g. base-64
#: images, minified blobs that slipped through).
_MAX_STR_LEN: int = 300

# ---------------------------------------------------------------------------
# Key-name heuristics
# ---------------------------------------------------------------------------

#: Substrings in a key name that signal listing-relevant content.
#: Checked after the noise list (noise takes priority).
#: Keep this list tight: only price, identity, and location signals.
#: Bulk metadata (reviews, photos, room counts, host info) is intentionally
#: omitted so that pretrim consistently achieves the ≤30 % size budget.
_KEEP_SUBSTRINGS: tuple[str, ...] = (
    "name",
    "title",
    "label",
    "price",
    "pricing",
    "rate",
    "nightly",
    "cost",
    "amount",
    "fee",
    "lat",
    "lon",
    "latitude",
    "longitude",
    "location",
    "coord",
    "geo",
    "address",
    "city",
    "country",
    "region",
    "listing",
    "property",
    "stay",
    "hotel",
    "accommodation",
    "currency",
    "checkin",
    "checkout",
    "available",
    "id",
)

#: Substrings in a key name that signal noise.  Checked first; a match causes
#: the key (and its entire value subtree) to be dropped.
_NOISE_SUBSTRINGS: tuple[str, ...] = (
    "analytic",
    "tracking",
    "tracker",
    "telemetry",
    "beacon",
    "experiment",
    "feature_flag",
    "ab_test",
    "variant",
    "cohort",
    "session",
    "csrf",
    "fingerprint",
    "nonce",
    "footer",
    "topbar",
    "navbar",
    "navigation",
    "advertisement",
    "sponsor",
    "promo",
    "banner",
    "og_image",
    "twitter_card",
    "canonical",
    "survey",
    "feedback",
    "tooltip",
    "i18n",
    "locale_string",
    "translation",
    "pagination",
    "cursor",
    "stylesheet",
    "bootstrap",
    "hydration",
    "redux",
    "__typename",
    "metadata",
    "extensions",
    "queryid",
    "sha_digest",
    "operationname",
    # Additional noise: display/UI-only fields that bulk up the payload
    # without adding extraction value.
    "a11y",        # accessibility labels (e.g. avgRatingA11yLabel)
    "total",       # total-price roll-ups are redundant with component amounts
    "url",         # hyperlinks are not needed for price/location extraction
    "formatted",   # pre-formatted display strings (e.g. amount_formatted)
)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def pretrim(payload: str) -> str:
    """Strip non-listing noise from a raw scrape payload.

    Parameters
    ----------
    payload:
        Raw HTML page or JSON string captured by a scraper.

    Returns
    -------
    str
        Compact JSON string (or stripped plain text as a last resort)
        containing only listing-data fragments.  Always smaller than *payload*
        for real-world scrape outputs.
    """
    if _is_html(payload):
        return _trim_html(payload)
    return _trim_json_str(payload)


# ---------------------------------------------------------------------------
# HTML path
# ---------------------------------------------------------------------------

_SCRIPT_RE = re.compile(r"<script(?:[^>]*)>(.*?)</script>", re.DOTALL | re.IGNORECASE)
_STYLE_TAG_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_INLINE_STYLE_RE = re.compile(
    r'\s+style\s*=\s*(?:"[^"]*"|\'[^\']*\')', re.IGNORECASE
)
_JS_ASSIGN_RE = re.compile(r"^\s*(?:var\s+\w+|window\.\w+)\s*=\s*")


def _is_html(payload: str) -> bool:
    head = payload.lstrip()[:200]
    return bool(re.search(r"<html|<!doctype", head, re.IGNORECASE)) or head.startswith("<")


def _trim_html(html: str) -> str:
    """Extract and filter listing JSON blobs from an HTML page."""
    fragments: list[str] = []

    for m in _SCRIPT_RE.finditer(html):
        raw = m.group(1).strip()
        if not raw:
            continue
        # Strip optional JS variable-assignment wrapper: window.__X = {...};
        candidate = _JS_ASSIGN_RE.sub("", raw).rstrip(";").strip()
        if not candidate or candidate[0] not in ("{", "["):
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        listings = _find_listing_objects(data)
        if listings:
            fragments.append(json.dumps(listings, separators=(",", ":")))

    if fragments:
        return "\n".join(fragments)

    # Fallback: strip <style>, <script>, and inline style= attributes.
    out = _STYLE_TAG_RE.sub(" ", html)
    out = _SCRIPT_RE.sub(" ", out)
    out = _INLINE_STYLE_RE.sub("", out)
    return re.sub(r"\s{2,}", " ", out).strip()


# ---------------------------------------------------------------------------
# JSON path
# ---------------------------------------------------------------------------


def _trim_json_str(payload: str) -> str:
    """Parse *payload* as JSON and return a filtered compact string."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload  # Unparseable → return unchanged

    listings = _find_listing_objects(data)
    if listings:
        return json.dumps(listings, separators=(",", ":"))

    # Fallback: generic noise-key filter over the whole tree.
    filtered = _filter_obj(data)
    return json.dumps(filtered, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Core: listing detection & extraction
# ---------------------------------------------------------------------------


def _find_listing_objects(data: Any) -> list[dict]:
    """Walk *data* and collect every dict that has a listing-like key fingerprint."""
    acc: list[dict] = []
    _walk(data, acc, depth=0)
    return acc


def _walk(node: Any, acc: list[dict], depth: int) -> None:
    if depth > 30:
        return
    if isinstance(node, dict):
        if _is_listing_like(node):
            slimmed = _slim_dict(node)
            if slimmed:
                acc.append(slimmed)
            # Stop recursing; we already captured this listing node.
            return
        for v in node.values():
            _walk(v, acc, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _walk(item, acc, depth + 1)


def _is_listing_like(d: dict) -> bool:
    """Return True if *d* (or its direct dict children) carries listing signals.

    Looks one nesting level deep so that wrapper objects such as::

        {"listing": {"name": "...", "coordinate": {...}},
         "pricingQuote": {"rate": {"amount": 89}}}

    are recognised even though neither "name" nor "rate" appears at the top
    level of the wrapper dict directly.
    """
    all_keys: set[str] = set()
    for k, v in d.items():
        all_keys.add(k.lower())
        if isinstance(v, dict):
            all_keys.update(kk.lower() for kk in v)

    has_price = any(
        "price" in k
        or "rate" in k
        or "nightly" in k
        or "amount" in k
        or "fee" in k
        for k in all_keys
    )
    has_name = any(
        k in ("name", "title") or k.endswith("name") or k.endswith("title")
        for k in all_keys
    )
    has_location = any(
        k in ("lat", "lon", "latitude", "longitude", "location")
        or "coord" in k
        or ("geo" in k and "category" not in k)
        for k in all_keys
    )

    return sum([has_price, has_name, has_location]) >= 2


def _slim_dict(d: dict) -> dict:
    """Return *d* with only listing-relevant keys, recursively.

    Single-key wrapper passthrough
    --------------------------------
    Some API shapes wrap a relevant value behind a non-descriptive key that
    doesn't match any keep-substring, e.g.::

        "pricingQuote": {
            "structuredStayDisplayPrice": {   ← kept via "price"
                "primaryLine": {              ← key not in keep list
                    "price": "€65"            ← value we need
                }
            }
        }

    When a dict has *exactly one* key that fails the keep-filter AND that
    key's value is a dict that slims to a non-empty result, we still emit
    the wrapper key so the nested data survives.
    """
    result: dict = {}
    for k, v in d.items():
        if not _keep_key(k):
            # Single-key wrapper passthrough: preserve the key when it is the
            # only key in this dict and its dict value contains useful content.
            if isinstance(v, dict) and len(d) == 1:
                slimmed = _slim_dict(v)
                if slimmed:
                    result[k] = slimmed
            continue
        if isinstance(v, dict):
            slimmed = _slim_dict(v)
            if slimmed:
                result[k] = slimmed
        elif isinstance(v, list):
            slimmed_list = _slim_list(v)
            if slimmed_list:
                result[k] = slimmed_list
        elif isinstance(v, str):
            if len(v) <= _MAX_STR_LEN:
                result[k] = v
            # Long strings (base-64, minified blobs) are silently dropped.
        else:
            # int, float, bool, None
            result[k] = v
    return result


def _slim_list(lst: list) -> list:
    result = []
    for item in lst:
        if isinstance(item, dict):
            s = _slim_dict(item)
            if s:
                result.append(s)
        elif isinstance(item, str):
            if len(item) <= _MAX_STR_LEN:
                result.append(item)
        else:
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# Fallback: generic noise-key filter
# ---------------------------------------------------------------------------


def _filter_obj(node: Any, depth: int = 0) -> Any:
    """Recursively drop keys that match known noise patterns."""
    if depth > 30:
        return None
    if isinstance(node, dict):
        result: dict = {}
        for k, v in node.items():
            if _is_noise_key(k):
                continue
            filtered = _filter_obj(v, depth + 1)
            if filtered is not None and filtered != {} and filtered != []:
                result[k] = filtered
        return result
    if isinstance(node, list):
        items = [_filter_obj(i, depth + 1) for i in node]
        return [x for x in items if x is not None and x != {} and x != []]
    if isinstance(node, str):
        return node if len(node) <= _MAX_STR_LEN else None
    return node


# ---------------------------------------------------------------------------
# Key-name helpers
# ---------------------------------------------------------------------------


def _keep_key(key: str) -> bool:
    """True → keep this key when slimming a listing object."""
    k = key.lower().replace("-", "_")
    for noise in _NOISE_SUBSTRINGS:
        if noise in k:
            return False
    for keep in _KEEP_SUBSTRINGS:
        if keep in k:
            return True
    return False


def _is_noise_key(key: str) -> bool:
    """True if this key is a known noise category (used by the fallback filter)."""
    k = key.lower().replace("-", "_")
    for noise in _NOISE_SUBSTRINGS:
        if noise in k:
            return True
    return False
