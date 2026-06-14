# Roomradar

Scrape Airbnb and hotel listings for a given area, extract the messy scraped
payloads into clean structured records with the Claude API, store them in
SQLite, and explore them in a Streamlit dashboard.

See [PLAN.md](PLAN.md) for the full architecture and stage breakdown.

## Status

Functional. The codebase was originally built by parallel agents as two
overlapping module trees; these have been unified into one canonical flat
layout (`scrapers/`, `extraction/`, `db/`, `schemas/`, `dashboard/` + root
`cli.py`/`pipeline.py`/`config.py`). Full suite: 388 passing, 1 skipped.

## Requirements

- Python 3.12+
- An Anthropic API key (`ANTHROPIC_API_KEY`)
- Chromium for Playwright (`playwright install chromium`)

## Setup

```bash
uv sync                       # or: pip install -e ".[dev]"
uv run playwright install chromium
cp .env.example .env          # then fill in ANTHROPIC_API_KEY
```

## Configuration

Settings are read from environment variables or `.env` (see `config.py`):

| Variable | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (required for extraction) | — |
| `LLM_MODEL` | Extraction model | `claude-opus-4-8` |
| `DB_PATH` | SQLite file | `scanner.db` |
| `MAX_PAGES` | Pages to scrape per source | `3` |
| `SCRAPER_API_KEY` | Optional paid scraping-API fallback key | — |
| `FALLBACK_PROVIDER` | `scraperapi` or `apify` | `scraperapi` |
| `BATCH_THRESHOLD` | Use the Batches API above this many scrapes | `10` |

## Usage

```bash
# Run a scan (acquire -> extract -> store)
python cli.py scan "Lisbon, Portugal" --checkin 2026-08-01 --checkout 2026-08-07 --guests 2

# Acquire only (no LLM), or collect-but-don't-write
python cli.py scan "Lisbon, Portugal" --no-extract
python cli.py scan "Lisbon, Portugal" --dry-run

# Maintenance
python cli.py purge-run <RUN_ID>
python cli.py vacuum

# Dashboard
streamlit run dashboard/app.py
```

## Tests

```bash
.venv/bin/python -m pytest        # 382 passing, 1 skipped (live network test)
```

The live Airbnb network test is skipped unless `AIRBNB_LIVE_TEST=1` is set.

## Notes / caveats

- Scraping Airbnb and Booking.com violates their ToS even though the data is
  public. Use at low volume for personal/research purposes only.
- Cost estimates use Opus 4.8 pricing ($5/$25/$0.50 per MTok input/output/cache-read).

## Known limitations / remaining cleanup

- **Scraping is intermittent from datacenter/WSL IPs.** Airbnb often serves no
  listing API response (challenge/login wall) and Booking returns an AWS WAF
  challenge — both are detected and surfaced cleanly (no garbage stored), but
  reliable data needs residential proxies or the paid fallback provider
  (`SCRAPER_API_KEY`). Scrapers also need maintenance when the sites change.
- Extraction sends the trimmed payload to Claude in JSON mode (the listing
  schema is too rich for grammar-constrained structured output). One LLM call
  per scraped page; cost ≈ $0.13 for a ~20-listing Lisbon page.

Resolved: the four duplicate `SearchQuery` definitions and the two
`ListingExtraction` schemas were each collapsed to one; the interactive pipeline
extracts *all* listings per page (page→many) and writes `ExtractionLog` rows so
the history page reports real counts/cost; `pretrim` preserves
price/rating/beds; and both extraction paths use JSON mode end-to-end (verified
live: a Lisbon scan stores 26 listings with price + rating).
