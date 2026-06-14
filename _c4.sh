#!/usr/bin/env bash
set -e
cd /home/mcurschman/projects/airbnb_scraping_tool || exit 1
git add -A
git commit -q -F - <<'MSG'
Remove legacy single-listing ListingExtraction schema

schemas/models.py defined a second `ListingExtraction` (strict single listing)
that collided by name with the page-wrapper `schemas/listing.py:ListingExtraction`
and was unused in production (only its own tests referenced it). Remove it:

- Delete the class from schemas/models.py (and the now-unused field_validator import)
- schemas/__init__ now re-exports ListingExtraction from schemas.listing (the
  wrapper), so `from schemas import ListingExtraction` resolves to the one
  canonical definition
- Remove the orphaned TestListingExtraction class from tests/test_schemas.py

One ListingExtraction definition remains. Tests: 382 passed, 1 skipped.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
MSG
git push origin main 2>&1 | tail -3
git log --oneline -1
rm -f _c4.sh
