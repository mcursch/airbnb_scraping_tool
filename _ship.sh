#!/usr/bin/env bash
# bash -l (login) so uv is on PATH
set -e
cd /home/mcurschman/projects/airbnb_scraping_tool || exit 1
echo "=== uv lock (sync lockfile with new dep) ==="
uv lock 2>&1 | tail -4
git add -A
git status --short | sed -E 's/^(..) .*/\1/' | sort | uniq -c
git commit -q -F - <<'MSG'
Fix scraper bugs surfaced by a live scan

A live acquire-only run against Lisbon exposed two real bugs:

1. Airbnb scraper falsely reported "playwright is not installed" — its import
   guard coupled playwright to the optional playwright-stealth package, and
   stealth wasn't a declared dependency. Decouple them: stealth is now optional
   (browser still runs without it, with a warning), and playwright-stealth is
   added to dependencies.

2. Airbnb navigation hard-failed on `wait_until="networkidle"` (Airbnb's SPA
   keeps long-lived connections open, so the event never fires). Switch to
   `domcontentloaded` and, on timeout, still harvest any API responses captured
   during the attempt instead of failing the whole scrape.

3. Booking stored AWS WAF challenge pages as valid results — its block
   detection only knew PerimeterX/Cloudflare/generic CAPTCHA markers. Add
   `awswaf` / `reportchallengeerror` so WAF challenges raise BlockedError and no
   garbage payload is persisted.

After the fixes, the live scan captured a real 383KB Airbnb StaysSearch JSON
payload (hundreds of listings). Booking remains AWS WAF-blocked from this IP
(now surfaced cleanly rather than stored as garbage) — it would need the paid
fallback provider or a residential IP.

Tests: 382 passed, 1 skipped.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
MSG
git push origin main 2>&1 | tail -3
git log --oneline -1
rm -f _ship.sh
