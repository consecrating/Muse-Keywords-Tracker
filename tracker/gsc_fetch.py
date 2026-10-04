#!/usr/bin/env python3
"""
gsc_fetch.py — pull free, official rank data from the Google Search Console
Search Analytics API.

Auth: service-account JSON file whose path comes from the GSC_CREDENTIALS
environment variable. NEVER commit the JSON file.
Sites: comma-separated list in GSC_SITES env var, e.g.
    GSC_SITES="sc-domain:example.com,https://www.example.com/"
If GSC_SITES is unset, sites are derived from the domains of tracked keywords
(as https://<domain>/).

No credentials -> prints a clear message and exits 0. The engine still works
on scraper data alone.

Requires (only for this module): pip install google-api-python-client google-auth

Usage:
    GSC_CREDENTIALS=/path/sa.json GSC_SITES="sc-domain:example.com" \
        python3 gsc_fetch.py --db tracker/data/ranktracker.db
"""
import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
DAYS = 28


def get_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        print("google-api-python-client / google-auth not installed. "
              "Run: pip install google-api-python-client google-auth",
              file=sys.stderr)
        return None
    cred_path = os.environ.get("GSC_CREDENTIALS")
    if not cred_path or not os.path.exists(cred_path):
        return None
    creds = service_account.Credentials.from_service_account_file(
        cred_path, scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds,
                 cache_discovery=False)


def site_list(conn):
    env = os.environ.get("GSC_SITES", "").strip()
    if env:
        return [s.strip() for s in env.split(",") if s.strip()]
    domains = {k["domain"] for k in store.get_active_keywords(conn)}
    return [f"https://{d}/" for d in sorted(domains)]


def fetch_site(service, site_url, start, end):
    rows = []
    start_row = 0
    while True:
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query", "country"],
            "rowLimit": 25000,
            "startRow": start_row,
        }
        resp = service.searchanalytics().query(
            siteUrl=site_url, body=body).execute()
        batch = resp.get("rows", [])
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 25000:
            break
        start_row += len(batch)
    return rows


def run(db_path):
    store.init_db(db_path)
    service = get_service()
    if service is None:
        print("GSC: no credentials (GSC_CREDENTIALS unset or libraries "
              "missing) — skipping. Scraper data still works.")
        return {"skipped": True}

    conn = store.connect(db_path)
    end = date.today() - timedelta(days=2)   # GSC data lags ~2 days
    start = end - timedelta(days=DAYS - 1)
    total = 0
    for site in site_list(conn):
        client_id = None
        print(f"GSC: pulling {site} ({start}..{end}) ...", flush=True)
        try:
            rows = fetch_site(service, site, start, end)
        except Exception as e:  # noqa: BLE001 - report, don't crash the run
            print(f"GSC: {site} failed: {e}", flush=True)
            continue
        if "sc-domain:" in site:
            domain = site.split("sc-domain:")[1]
        else:
            domain = site.replace("https://", "").replace("http://", "") \
                         .strip("/").lstrip("www.")
        # find a client that tracks this domain to match queries
        crow = conn.execute(
            "SELECT client_id FROM keywords WHERE domain = ? LIMIT 1",
            (domain,)).fetchone()
        client_id = crow["client_id"] if crow else None
        to_save = []
        for r in rows:
            query, country = r["keys"][0], r["keys"][1]
            kw_id = (store.match_keyword_id(conn, client_id, query)
                     if client_id else None)
            to_save.append({
                "keyword_id": kw_id, "site": site, "query": query,
                "country": country, "row_date": end.isoformat(),
                "clicks": r.get("clicks", 0),
                "impressions": r.get("impressions", 0),
                "ctr": r.get("ctr", 0), "position": r.get("position", 0),
            })
        # replace this window's rows for the site to keep the table fresh
        conn.execute("DELETE FROM gsc_rows WHERE site = ?", (site,))
        store.save_gsc_rows(conn, to_save)
        conn.commit()
        total += len(to_save)
        print(f"GSC: {site}: {len(to_save)} rows stored.", flush=True)
    conn.close()
    print(f"GSC: done, {total} rows total.")
    return {"skipped": False, "rows": total}


def main():
    ap = argparse.ArgumentParser(description="GSC Search Analytics pull")
    ap.add_argument("--db", default=store.DEFAULT_DB)
    args = ap.parse_args()
    run(args.db)


if __name__ == "__main__":
    main()
