#!/usr/bin/env bash
set -e
cd /home/mcurschman/projects/airbnb_scraping_tool || exit 1
git add -A
git commit -q -F - <<'MSG'
Collapse the four SearchQuery definitions into one

There were four divergent SearchQuery classes (schemas/models.py,
schemas/search_query.py, schemas/search.py, scrapers/base.py) that interoperated
only by duck-typing. Unify on schemas.models.SearchQuery:

- Add `page_limit` (Booking scraper requires it) to the canonical model
- Make `guests` a non-optional `int = 1` (the Airbnb scraper does
  `query.guests > 1`, which crashed on the old `None` default)
- schemas/search_query.py, schemas/search.py, and scrapers/base.py now
  re-export the canonical class instead of defining their own
- Update two unit tests that asserted the old `guests is None` default

ISO date strings are still accepted (pydantic coerces str -> date), so existing
string-date callers/tests are unaffected.

Tests: 388 passed, 1 skipped.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
MSG
git log --oneline -1
rm -f _c2.sh
