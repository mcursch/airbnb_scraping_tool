# Roomradar — Development Plan

Python package `roomradar`, CLI command `roomradar`, dashboard title "Roomradar".

A tool that, given an area (and optionally dates/guests), scrapes Airbnb listings and hotel
listings, normalizes the messy scraped payloads into clean structured records via the Claude API,
stores them in a database, and renders them in a Streamlit dashboard.

## Locked decisions

| Decision | Choice |
|---|---|
| Scraping strategy | Hybrid: self-hosted Playwright/httpx first, pluggable paid scraping-API fallback |
| Stack | Python 3.12+, Playwright, httpx, Pydantic v2, SQLAlchemy |
| Usage mode | On-demand searches (no scheduler; schema still snapshot-friendly) |
| Database | SQLite via SQLAlchemy (swap to Postgres later without code changes) |
| LLM | Claude API, `claude-opus-4-8`, structured outputs via `client.messages.parse()` + Pydantic |
| Dashboard | Streamlit |

## End-to-end flow

```
User enters: area, check-in/out, guests, sources (airbnb / hotels / both)
        │
        ▼
[1] ACQUIRE  (scrapers/)
    - Airbnb: Playwright with stealth; capture the embedded JSON / internal
      StaysSearch API responses rather than parsing rendered HTML
    - Hotels (Booking.com first): httpx/Playwright; on block/CAPTCHA, fall back
      to the configured scraping-API provider (same ScrapeProvider interface)
    - Output: RawScrape records (source, query, url, payload, content_hash,
      fetched_at, status='pending') persisted immediately — scraping and
      extraction are decoupled so a crash never loses fetched data
        │
        ▼
[2] EXTRACT  (extraction/)
    - Deterministic pre-trim: strip scripts/markup, isolate the JSON fragments
      that contain listing data (cuts token cost ~10-50x)
    - Claude claude-opus-4-8 via client.messages.parse() with a Pydantic
      Listing schema → guaranteed-valid structured output
    - Prompt caching: frozen system prompt + schema instructions cached;
      per-listing payloads after the cache breakpoint
    - Optional bulk path: Message Batches API (50% price, <1h typical) when a
      search yields many pages
    - Output: validated Listing objects + extraction metadata (model, tokens,
      confidence notes); failures recorded per-record, retryable
        │
        ▼
[3] STORE  (db/)
    - Upsert listings keyed by (source, source_listing_id)
    - Each search run recorded as a SearchRun; listings linked to runs through
      ListingSnapshot (price/availability at fetch time) — keeps the door open
      for monitoring later without a schema migration
        │
        ▼
[4] PRESENT  (dashboard/)
    - Streamlit: search launcher, results table with filters (price, rating,
      type, source), map view (st.map/pydeck), price-distribution charts,
      Airbnb-vs-hotel comparison, CSV export
```

## Data model (Pydantic + SQLAlchemy mirror)

```
SearchRun:        id, area_query, checkin, checkout, guests, sources, started_at,
                  finished_at, status, stats(json)
RawScrape:        id, run_id, source, url, payload(text), content_hash(unique),
                  fetched_at, status('pending'|'extracted'|'failed'), error
Listing:          id, source('airbnb'|'booking'|...), source_listing_id,
                  name, property_type, lat, lon, address_text,
                  bedrooms, beds, baths, max_guests,
                  rating, review_count, amenities(json), images(json), url,
                  host_or_brand, first_seen_at, last_seen_at
                  UNIQUE(source, source_listing_id)
ListingSnapshot:  id, listing_id, run_id, nightly_price, currency, total_price,
                  fees(json), availability, captured_at
ExtractionLog:    id, raw_scrape_id, model, input_tokens, output_tokens,
                  cache_read_tokens, status, error
```

## Repository layout

```
airbnb_scraping_tool/
├── pyproject.toml
├── .env.example                # ANTHROPIC_API_KEY, SCRAPER_API_KEY (optional)
├── config.py                   # pydantic-settings
├── schemas/                    # Pydantic models (Listing, SearchQuery, ...)
├── scrapers/
│   ├── base.py                 # ScrapeProvider ABC: search(query) -> list[RawPayload]
│   ├── airbnb.py
│   ├── booking.py
│   └── fallback_api.py         # paid scraping-API adapter (ScraperAPI/Apify)
├── extraction/
│   ├── pretrim.py              # HTML/JSON reduction before LLM
│   ├── extractor.py            # messages.parse() pipeline + caching
│   └── batch.py                # Batches API path for bulk runs
├── db/
│   ├── models.py               # SQLAlchemy
│   └── repo.py                 # upserts, queries
├── pipeline.py                 # orchestrates acquire -> extract -> store
├── cli.py                      # `scan "Lisbon, Portugal" --checkin ... --checkout ...`
├── dashboard/
│   └── app.py                  # Streamlit
└── tests/
    ├── fixtures/               # saved real payloads (recorded once, replayed in CI)
    └── ...
```

---

## Stages (each is a self-contained agent task with exit criteria)

### Stage 0 — Scaffolding & contracts
**Scope:** pyproject (uv-managed), config via pydantic-settings, all Pydantic schemas,
SQLAlchemy models + engine setup, `ScrapeProvider` ABC, empty pipeline skeleton, pytest wiring.
**Exit criteria:** `uv run pytest` green on schema round-trip tests; DB file created with all
tables; `cli.py scan --help` runs.
**Why first:** every later stage codes against these interfaces, so parallel stages don't drift.

### Stage 1 — Airbnb scraper
**Scope:** Playwright (with playwright-stealth) drives an Airbnb search for the query area/dates;
intercept network responses to the StaysSearch/ExploreTabs endpoints and capture raw JSON;
pagination up to a configurable page limit; polite rate limiting (randomized delays); persist
RawScrape rows. Save 2–3 real payloads as test fixtures.
**Exit criteria:** `scan "<area>" --sources airbnb --no-extract` produces ≥1 page of RawScrape
rows for a real query; fixture-based unit test for the response-capture logic passes offline.
**Risk note:** this is the highest-breakage component. Keep all selectors/endpoint patterns in
one constants module so fixes are one-file changes.

### Stage 2 — Hotel scraper + fallback provider
**Scope:** Booking.com scraper implementing the same `ScrapeProvider` interface (httpx first,
Playwright if needed); block/CAPTCHA detection; `fallback_api.py` adapter that routes the same
search through a paid scraping API when the direct path fails (provider chosen via config, off
by default); fixtures saved.
**Exit criteria:** hotel RawScrape rows for a real query; forced-failure test proves fallback
engages; with no fallback key configured it degrades to a clear error, not a crash.

### Stage 3 — LLM extraction pipeline
**Scope:** pre-trim module; extraction with `client.messages.parse(model="claude-opus-4-8",
output_format=ListingExtraction)`; system prompt frozen and cache-marked
(`cache_control: {"type": "ephemeral"}`) with volatile payload after the breakpoint; SDK retry
config; per-record failure handling (mark RawScrape failed, continue); ExtractionLog rows with
token usage; `batch.py` using the Message Batches API for runs over a configurable threshold.
**Exit criteria:** running extraction over the Stage 1/2 fixtures yields valid Listing objects;
cache hits verified via `usage.cache_read_input_tokens > 0` on second call; a corrupted-payload
test produces a logged failure without aborting the batch.

### Stage 4 — Persistence & dedup
**Scope:** repo layer upserts (insert-or-update on `(source, source_listing_id)`), snapshot
insertion per run, content-hash dedup so re-scraped identical payloads skip the LLM entirely,
run statistics rollup, simple `purge`/`vacuum` CLI helpers.
**Exit criteria:** running the same search twice produces no duplicate listings, zero extraction
calls on unchanged payloads (hash short-circuit verified by token counts), and two snapshots.

### Stage 5 — Streamlit dashboard
**Scope:** search launcher form (kicks off pipeline with progress display), results table with
sort/filter, map view, price histogram + source comparison chart, listing detail panel, CSV
export, run-history page showing past searches and extraction costs.
**Exit criteria:** `streamlit run dashboard/app.py` shows seeded data; a search started from the
UI lands in the table without restart; filters and export verified.

### Stage 6 — Orchestrator polish & end-to-end hardening
**Scope:** wire pipeline stages with structured logging (per-run log file), retries/timeouts
budget, graceful Ctrl-C (already-fetched data kept), `--dry-run` and `--no-extract` flags,
README with setup instructions, one full end-to-end smoke test against a small real query.
**Exit criteria:** fresh clone → `uv sync` → `playwright install chromium` → `.env` →
`uv run python cli.py scan "Lisbon" ...` → dashboard shows results, documented and reproducible.

---

## Cross-cutting notes

- **Anti-bot reality:** Airbnb/Booking actively block scrapers. The design treats blocks as
  expected runtime events (detect → backoff → fallback provider → surface clearly in run stats),
  not exceptions. Keep request volume low: this is an on-demand tool, not a crawler.
- **Terms of service:** scraping these sites violates their ToS even when the data is public.
  Acceptable for personal/research use at low volume; don't redistribute the data commercially.
- **Cost control:** pre-trim before the LLM, prompt caching, hash-based skip of unchanged
  payloads, Batches API (50% off) for big runs. `claude-opus-4-8` is $5/$25 per MTok; a typical
  trimmed listing-page extraction is a few thousand input tokens.
- **Testing without the network:** every scraper records fixtures; extraction tests run against
  fixtures; only the e2e smoke test touches live sites. CI never needs to scrape.
- **Future (out of scope now, schema-ready):** scheduler for recurring monitoring, price-history
  charts over snapshots, more hotel sources (Hotels.com, Expedia), email alerts.
