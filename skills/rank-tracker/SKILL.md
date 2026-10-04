---
name: rank-tracker
description: "Free keyword rank tracking for client sites: add/manage tracked keywords, run the daily rank check (GSC + self-hosted SERP scraper, zero paid services), read trend reports and alerts. Activate when the owner asks about keyword positions, wants keywords added/tracked, or wants a daily rank check."
metadata:
  version: "1.0"
  author: consecrating
---

# rank-tracker — Free Keyword Rank Tracking

Operates the rank-tracking engine in `tracker/`. Zero paid services, ever:
positions come from the **free Google Search Console API** plus a careful
**self-hosted SERP scraper**. The owner explicitly forbade paid rank APIs.

## Hard Rules

1. **NEVER invent rank data.** If a keyword is blocked/unscraped, say so
   plainly ("Google throttled 4 of 20 keywords today — they'll retry
   tomorrow"). Zeros and guesses are defects, not data.
2. **No paid services.** Never suggest SerpApi/serper/subscriptions as the
   fix for throttling. The answer to throttling is: smaller daily batches,
   longer delays, GSC data.
3. **Credentials never in the repo.** GSC service-account JSON lives outside
   the repo; its path comes from `GSC_CREDENTIALS`. If it's missing, the
   engine still runs on scraper data — say that instead of stalling.
4. **Example data only.** `keywords.example.csv` is a template. Real client
   keywords go in `tracker/keywords.csv` (gitignored).

## Workflows

**Add keywords for a client:**
```bash
cd /path/to/Keywords-Tracker
cp tracker/keywords.example.csv tracker/keywords.csv   # first time only
# edit tracker/keywords.csv: client,keyword,domain,country,competitors
python3 -c "
import sys; sys.path.insert(0, 'tracker')
import store
conn = store.connect(); store.init_db()
print(store.import_keywords_csv(conn, 'tracker/keywords.csv'))
conn.commit(); conn.close()"
```
Verify with: `python3 -c` → `store.get_active_keywords(conn, 'Client Name')`.

**Run the daily check:**
```bash
python3 tracker/run_daily.py
# or a single stage:
python3 tracker/serp_scrape.py --limit 30
python3 tracker/report.py --client "Client Name"
python3 tracker/alerts.py
```

**Read results:** `reports/<client>-<date>.md` (human) and `.json`
(machine). Always quote the coverage line first
("X of Y keywords tracked, Z blocked") so the owner knows how complete
the numbers are.

**Suggest keywords from a seed (owner picks, never auto-track):**
```bash
python3 tracker/suggest.py --keyword "goa casino" --country IN
# -> suggestions/goa-casino-<date>.md : numbered, ranked ideas
python3 tracker/suggest.py --keyword "goa casino" --pick \
    --client "Casino Pride" --domain cpofficial.in
# prints the numbered list; owner types e.g. 1,4,7-9
# only picked keywords get tracked — suggestions are ideas, not data
python3 tracker/suggest.py --seeds-file seeds.txt --limit 50   # bulk seeds
```
Hard rule: NEVER add suggested keywords to tracking without the owner
explicitly picking them. If the owner says "track them all", confirm the
count first.

**Bulk import keywords:**
```bash
python3 tracker/import_bulk.py --csv tracker/keywords.csv --dry-run  # preview
python3 tracker/import_bulk.py --csv tracker/keywords.csv            # for real
# report: imported X, skipped Y duplicates, rejected Z invalid (with lines)
```
Same CSV format as `keywords.example.csv`. Duplicates = already actively
tracked OR repeated within the file.

**Chunked daily rotation (large sets):** scraping hundreds of keywords daily
gets throttled. The engine rotates round-robin instead:
```bash
python3 tracker/run_daily.py --daily-budget 40   # or DAILY_BUDGET=40 env
# each run scrapes the 40 least-recently-checked keywords;
# 500 keywords rotate across ~13 days. The summary prints the rotation.
```
Reports stay honest: coverage line shows "stale (>2d old, rotating)" for
keywords whose data aged out of the rotation window.

**Pause / resume tracking in bulk:**
```bash
python3 tracker/manage.py --deactivate-client "Old Client"   # pause, keep history
python3 tracker/manage.py --deactivate-pattern "%casino%"    # SQL LIKE
python3 tracker/manage.py --activate-client "Old Client"     # resume
python3 tracker/manage.py --status --client "Casino Pride"
```
Every mutating command asks for YES confirmation unless `--yes`.

**Schedule it:** `0 6 * * * cd /path/to/Keywords-Tracker && python3 tracker/run_daily.py >> tracker/data/cron.log 2>&1`

## Honest limits (tell the owner, don't hide)

- Free Google scraping is throttled aggressively: expect partial coverage on
  large keyword sets. Small daily batches (20–40 keywords) survive best.
- Scraped ranks are one daily snapshot from google.co.in, not personalized
  or hourly like paid tools.
- GSC data is the ground truth for clicks/impressions; it lags ~2 days and
  only covers sites the owner verified in Search Console.
- Blocked runs record `status='blocked'` and retry the next day — the
  report shows this honestly instead of faking continuity.
