#!/usr/bin/env python3
"""
import_bulk.py — bulk keyword import with honest reporting.

CSV columns: client,keyword,domain,country[,competitors]
(competitors: ';'-separated domains, optional — same format as
tracker/keywords.example.csv)

Validates every row, dedupes against existing ACTIVE keywords and within
the file itself, and prints a full report:

    imported X, skipped Y duplicates, rejected Z invalid
      line 12: missing domain
      line 27: duplicate of line 3 in this file

--dry-run previews the whole thing without touching the database.

Usage:
    python3 tracker/import_bulk.py --csv tracker/keywords.csv --dry-run
    python3 tracker/import_bulk.py --csv tracker/keywords.csv
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402

DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.[a-z0-9-]{1,63})+$",
                       re.IGNORECASE)


def valid_domain(d):
    return bool(DOMAIN_RE.match(d.strip()))


def load_rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = {"client", "keyword", "domain"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"CSV is missing required columns: {sorted(missing)} "
                f"(need: client,keyword,domain[,country,competitors])")
        return list(enumerate(reader, start=2))  # line 1 = header


def plan_import(conn, rows):
    """Return (to_add, duplicates, rejected).

    to_add: list of dicts {line, client, keyword, domain, country,
                             competitors}
    duplicates: list of (line, reason)
    rejected: list of (line, reason)
    """
    to_add, duplicates, rejected = [], [], []
    seen_in_file = set()
    existing = {(r["client"].lower(), r["keyword"].lower(),
                 r["domain"].lower())
                for r in conn.execute(
                    """SELECT c.name AS client, k.keyword, k.domain
                       FROM keywords k JOIN clients c ON c.id = k.client_id
                       WHERE k.active = 1""").fetchall()}

    for line, row in rows:
        client = (row.get("client") or "").strip()
        keyword = (row.get("keyword") or "").strip()
        domain = (row.get("domain") or "").strip().lower()
        country = (row.get("country") or "IN").strip().upper() or "IN"
        competitors = [d.strip().lower()
                       for d in (row.get("competitors") or "").split(";")
                       if d.strip()]

        if not client or not keyword or not domain:
            missing = [n for n, v in (("client", client),
                                      ("keyword", keyword),
                                      ("domain", domain)) if not v]
            rejected.append((line, f"missing {', '.join(missing)}"))
            continue
        if not valid_domain(domain):
            rejected.append((line, f"invalid domain {domain!r}"))
            continue
        if len(country) != 2 or not country.isalpha():
            rejected.append((line, f"invalid country {country!r} "
                                   "(use 2-letter code)"))
            continue

        key = (client.lower(), keyword.lower(), domain)
        if key in seen_in_file:
            duplicates.append((line, "duplicate of an earlier row in "
                                     "this file"))
            continue
        if key in existing:
            duplicates.append((line, "already tracked (active)"))
            continue
        seen_in_file.add(key)
        to_add.append({"line": line, "client": client, "keyword": keyword,
                       "domain": domain, "country": country,
                       "competitors": competitors})
    return to_add, duplicates, rejected


def main():
    ap = argparse.ArgumentParser(description="Bulk keyword CSV import")
    ap.add_argument("--csv", required=True, help="CSV file to import")
    ap.add_argument("--db", default=store.DEFAULT_DB)
    ap.add_argument("--dry-run", action="store_true",
                    help="validate + report, change nothing")
    args = ap.parse_args()

    store.init_db(args.db)
    conn = store.connect(args.db)
    try:
        rows = load_rows(args.csv)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    to_add, duplicates, rejected = plan_import(conn, rows)

    if args.dry_run:
        print(f"DRY RUN — {len(rows)} rows read from {args.csv}")
    for line, reason in rejected:
        print(f"  line {line}: REJECTED — {reason}")
    for line, reason in duplicates:
        print(f"  line {line}: skipped duplicate — {reason}")
    if args.dry_run:
        for r in to_add[:20]:
            print(f"  line {r['line']}: would add "
                  f"[{r['client']}] {r['keyword']!r} -> {r['domain']}")
        if len(to_add) > 20:
            print(f"  ... and {len(to_add) - 20} more")
        print(f"\nDRY RUN: would import {len(to_add)}, skip "
              f"{len(duplicates)} duplicates, reject {len(rejected)} invalid.")
        return

    added, comps = 0, 0
    for r in to_add:
        store.add_keyword(conn, r["client"], r["keyword"], r["domain"],
                          r["country"])
        added += 1
        for d in r["competitors"]:
            if valid_domain(d):
                store.add_competitor(conn, r["client"], d)
                comps += 1
    conn.commit()
    conn.close()
    print(f"Imported {added} keywords "
          f"({comps} competitor domains), skipped {len(duplicates)} "
          f"duplicates, rejected {len(rejected)} invalid.")


if __name__ == "__main__":
    main()
