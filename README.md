# Muse-Keywords-Tracker

Free keyword rank tracking — no paid services. GSC API + careful self-hosted SERP scraper, keyword suggestions, and bulk tracking.

## Free tracking engine (no paid services)

**Why this exists:** Paid rank trackers cannot track a single keyword until you
configure a scraper — and every turnkey scraper option is a paid API. This
repo now ships a **free rank-tracking engine** in `tracker/` that needs no
paid service at all:

- **Google Search Console API** (free, official) → real clicks, impressions,
  CTR and average position per query for sites you verified in GSC.
- **Careful self-hosted SERP scraper** → exact top-20 organic ranks from
  google.co.in, with jittered delays, block detection, and resume.
- **SQLite store + trend reports + alerts** → daily Markdown/JSON reports
  (movers vs yesterday/7d, page-1 changes, coverage honesty) and alerts for
  sharp drops, page-1 losses, and competitors entering the top 3.
- **Keyword suggestions** → give one seed keyword, get a ranked pick-list
  from Google Autocomplete (with alphabet expansion) + related searches;
  the owner picks numbers (`1,4,7-9`) and only picked keywords get tracked.
- **Bulk tracking** → CSV bulk import with validation/dedupe report,
  round-robin daily rotation (`--daily-budget N`) so 500 keywords rotate
  across ~N days without throttling, plus bulk pause/resume per client
  or pattern.

**Honest limits:** free Google scraping gets throttled — expect partial
coverage on big keyword sets, run small daily batches, and treat GSC as
ground truth. Blocked keywords are reported as blocked, never faked.

**Quickstart:**
```bash
pip install -r tracker/requirements.txt   # only 'requests'
cp tracker/keywords.example.csv tracker/keywords.csv   # add real keywords
# import once:
python3 -c "import sys; sys.path.insert(0,'tracker'); import store;
conn=store.connect(); store.init_db();
print(store.import_keywords_csv(conn,'tracker/keywords.csv'));
conn.commit()"
python3 tracker/run_daily.py              # GSC (if configured) -> scrape -> report -> alerts
# reports land in reports/<client>-<date>.md
```
Optional: set `GSC_CREDENTIALS=/path/to/service-account.json` and
`GSC_SITES="sc-domain:example.com"` for the free GSC data pull. Daily cron:
`0 6 * * * cd /path/to/Keywords-Tracker && python3 tracker/run_daily.py`.

**Suggest keywords:**
```bash
python3 tracker/suggest.py --keyword "goa casino" --country IN
# -> suggestions/goa-casino-<date>.md : numbered ranked ideas (autocomplete + related searches)
python3 tracker/suggest.py --keyword "goa casino" --pick --client "Casino Pride" --domain cpofficial.in
# type e.g. 1,4,7-9 — only picked keywords get tracked
python3 tracker/suggest.py --seeds-file seeds.txt   # one seed per line
```

**Bulk import + rotation:**
```bash
python3 tracker/import_bulk.py --csv tracker/keywords.csv --dry-run   # preview first
python3 tracker/import_bulk.py --csv tracker/keywords.csv
python3 tracker/run_daily.py --daily-budget 40   # round-robin rotation for large sets
python3 tracker/manage.py --deactivate-client "Old Client"   # pause tracking, keep history
```

The agent skill `skills/rank-tracker/SKILL.md` documents how to operate it.
