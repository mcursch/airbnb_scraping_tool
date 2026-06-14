#!/usr/bin/env bash
set -e
cd /home/mcurschman/projects/airbnb_scraping_tool || exit 1
git add -A
git commit -q -F - <<'MSG'
Unify extraction: interactive pipeline now extracts all listings per page

The interactive path (Extractor.extract via Pipeline/run_search/dashboard)
previously mapped one scraped payload to a single listing, silently dropping
the rest of a search-results page. Make it page->many like the bulk
extract_listings/Batches path, and share the same ListingExtraction wrapper
schema between both:

- extraction/provider.py: ExtractionResult.listing -> listings: list[...]
  (with a back-compat `listing` property); Extractor.extract uses
  output_format=ListingExtraction (wrapper) and returns all listings
- pipeline.py: Pipeline.run loops over result.listings, upserting each with its
  own snapshot; token usage counted once per extraction call
- tests: update fakes to listings=[...]; add test_many_listings_from_one_page
  proving 3 listings + 3 snapshots from one page

Tests: 389 passed, 1 skipped.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
MSG
echo "=== push ==="
git push origin main 2>&1 | tail -3
git log --oneline -3
rm -f _c3.sh
