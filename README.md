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
.venv/bin/python -m pytest        # 388 passing, 1 skipped (live network test)
```

The live Airbnb network test is skipped unless `AIRBNB_LIVE_TEST=1` is set.

## Notes / caveats

- Scraping Airbnb and Booking.com violates their ToS even though the data is
  public. Use at low volume for personal/research purposes only.
- Cost estimates use Opus 4.8 pricing ($5/$25/$0.50 per MTok input/output/cache-read).

## Known limitations / remaining cleanup

- There are still two `ListingExtraction` schemas: `schemas/listing.py`
  (the page wrapper `{listings: [...]}`, used by both extraction paths) and a
  legacy single-listing `schemas/models.py:ListingExtraction` (now unused by the
  pipeline; kept only via `schemas/__init__`). Removing the legacy one is a
  minor follow-up.
- Scrapers target live sites and will need maintenance when Airbnb/Booking
  change their markup or endpoints.

Resolved recently: the four duplicate `SearchQuery` definitions were collapsed
into one (`schemas/models.py`), and the interactive pipeline now extracts
*all* listings from a scraped page (page→many), matching the bulk
`extract_listings` / Batches path and sharing the same wrapper schema.
