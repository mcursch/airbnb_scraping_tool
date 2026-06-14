"""Backwards-compatible re-export.

``SearchQuery`` is defined once in :mod:`schemas.models`; this module re-exports
it so existing imports (``from schemas.search_query import SearchQuery``) keep
working.
"""

from schemas.models import SearchQuery

__all__ = ["SearchQuery"]
