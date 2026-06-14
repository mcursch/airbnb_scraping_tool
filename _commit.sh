#!/usr/bin/env bash
set -e
cd /home/mcurschman/projects/airbnb_scraping_tool || exit 1

git add -A
echo "=== staged summary ==="
git status --short | sed -E 's/^(..) .*/\1/' | sort | uniq -c

git commit -q -F - <<'MSG'
Consolidate dual module trees into one canonical layout

The project was built by parallel agents and ended up with two overlapping
implementations (a flat `./` tree and a nested `airbnb_scraping_tool/` package)
glued together by inconsistent imports. The CLI's `scan` never actually
scraped, and config carried wrong LLM pricing.

Phase 1 — safe cleanups:
- Fix Opus 4.8 pricing in config ($15/$75/$1.50 -> $5/$25/$0.50 per MTok)
- pyproject: add missing `anthropic` dep, require Python 3.12, rename dist to
  `roomradar`, add `roomradar` console entry point, fix wheel packaging
- Deduplicate .gitignore (was repeated 8x); add README
- Remove stray `=0.23` and duplicate SPEC.md; untrack scanner.db, logs/*.jsonl,
  and 76 committed *.pyc bytecode files

Phase 2 — tree unification (flat canonical; delete nested package):
- Port engine/SessionLocal/init_db and a Repo class into flat db/
- Add extraction/provider.py (single-listing Extractor/ExtractionResult);
  keep extract_listings/process_raw_scrape bulk path for dedup tests
- Add fees/availability to ExtractedListing (Pipeline reads them)
- Repoint pipeline.py, cli.py, conftest.py, test_pipeline.py,
  test_logging_retry_cancel.py from the nested tree to flat
- Wire `cli scan` to run_search so it actually scrapes (was re-extracting
  existing rows only; previously imported nonexistent nested scraper classes)
- Delete the nested airbnb_scraping_tool/ package

Tests: 388 passed, 1 skipped (unchanged baseline).

Known follow-ups (documented in README): three SearchQuery definitions still
coexist; interactive pipeline maps one payload to one listing.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
MSG

echo "=== committed ==="
git log --oneline -1
rm -f _commit.sh
