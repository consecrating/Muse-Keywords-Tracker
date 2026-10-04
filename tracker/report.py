#!/usr/bin/env python3
"""
report.py — rank trend reports: movers vs previous run and vs 7 days ago,
page-1 changes, coverage honesty. Writes Markdown + JSON.

Usage:
    python3 report.py --db tracker/data/ranktracker.db [--client NAME]
                      [--out reports]
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402


def rank_on(conn, keyword_id, run_date):
    row = conn.execute(
        """SELECT positions_json, status FROM serp_snapshots
           WHERE keyword_id = ? AND run_date = ?""",
        (keyword_id, run_date)).fetchone()
    if not row:
        return None, "nodata"
    if row["status"] != "ok":
        return None, row["status"]
    kw = conn.execute("SELECT domain FROM keywords WHERE id = ?",
                      (keyword_id,)).fetchone()
    positions = json.loads(row["positions_json"] or "[]")
    return store.rank_of_domain(positions, kw["domain"]), "ok"


def nearest_run_before(conn, keyword_id, run_date, max_days_back):
    """Closest run_date strictly before run_date, within max_days_back."""
    cutoff = (date.fromisoformat(run_date)
              - timedelta(days=max_days_back)).isoformat()
    row = conn.execute(
        """SELECT run_date FROM serp_snapshots
           WHERE keyword_id = ? AND run_date < ? AND run_date >= ?
           ORDER BY run_date DESC LIMIT 1""",
        (keyword_id, run_date, cutoff)).fetchone()
    return row["run_date"] if row else None


def latest_run(conn, keyword_id):
    row = conn.execute(
        """SELECT run_date FROM serp_snapshots
           WHERE keyword_id = ? ORDER BY run_date DESC LIMIT 1""",
        (keyword_id,)).fetchone()
    return row["run_date"] if row else None


def build_client_report(conn, client_name):
    kws = store.get_active_keywords(conn, client_name)
    today = store.today_str()
    rows = []
    counts = {"tracked": 0, "blocked": 0, "nodata": 0, "stale": 0}
    for kw in kws:
        cur_date = latest_run(conn, kw["id"])
        if not cur_date:
            counts["nodata"] += 1
            rows.append({"keyword": kw["keyword"], "domain": kw["domain"],
                         "rank": None, "prev_rank": None, "week_rank": None,
                         "change_1d": None, "change_7d": None,
                         "status": "nodata", "run_date": None})
            continue
        # Rotation honesty: data older than 2 days is stale, not fresh.
        stale = (date.fromisoformat(today) - date.fromisoformat(cur_date)) \
            .days > 2
        rank, status = rank_on(conn, kw["id"], cur_date)
        if status != "ok":
            counts["blocked" if status == "blocked" else "nodata"] += 1
        else:
            counts["tracked"] += 1
            if stale:
                counts["stale"] += 1
        prev_date = nearest_run_before(conn, kw["id"], cur_date, 2)
        week_date = nearest_run_before(conn, kw["id"], cur_date, 8)
        prev_rank, _ = rank_on(conn, kw["id"], prev_date) if prev_date \
            else (None, "nodata")
        week_rank, _ = rank_on(conn, kw["id"], week_date) if week_date \
            else (None, "nodata")

        def delta(new, old):
            if new is None or old is None:
                return None
            return old - new  # positive = moved up

        rows.append({
            "keyword": kw["keyword"], "domain": kw["domain"],
            "rank": rank, "prev_rank": prev_rank, "week_rank": week_rank,
            "change_1d": delta(rank, prev_rank),
            "change_7d": delta(rank, week_rank),
            "status": status, "run_date": cur_date,
        })
    return rows, counts


def fmt_rank(r):
    return f"#{r}" if r else "—"


def fmt_delta(d):
    if d is None:
        return "—"
    return f"+{d}▲" if d > 0 else (f"{d}▼" if d < 0 else "0")


def markdown(client, rows, counts, run_date):
    total = len(rows)
    lines = [f"# Rank report — {client} ({run_date})", "",
             f"**Coverage:** {counts['tracked']} of {total} keywords tracked"
             + (f", {counts['blocked']} blocked" if counts["blocked"] else "")
             + (f", {counts['nodata']} no data yet" if counts["nodata"]
                else "")
             + (f", {counts['stale']} stale (>2d old, rotating)"
                if counts["stale"] else ""),
             ""]
    ok = [r for r in rows if r["status"] == "ok"]
    gainers = sorted([r for r in ok if (r["change_1d"] or 0) > 0],
                     key=lambda r: -r["change_1d"])[:5]
    losers = sorted([r for r in ok if (r["change_1d"] or 0) < 0],
                    key=lambda r: r["change_1d"])[:5]
    entered = [r for r in ok if r["rank"] and r["rank"] <= 10
               and (r["prev_rank"] is None or r["prev_rank"] > 10)]
    left = [r for r in ok if (r["rank"] is None or r["rank"] > 10)
            and r["prev_rank"] and r["prev_rank"] <= 10]

    if gainers:
        lines += ["## Biggest gainers (vs previous run)"] + \
            [f"- {r['keyword']}: {fmt_rank(r['prev_rank'])} → "
             f"{fmt_rank(r['rank'])} ({fmt_delta(r['change_1d'])})"
             for r in gainers] + [""]
    if losers:
        lines += ["## Biggest losers (vs previous run)"] + \
            [f"- {r['keyword']}: {fmt_rank(r['prev_rank'])} → "
             f"{fmt_rank(r['rank'])} ({fmt_delta(r['change_1d'])})"
             for r in losers] + [""]
    if entered:
        lines += ["## Entered page 1"] + \
            [f"- {r['keyword']}: now {fmt_rank(r['rank'])}" for r in entered] \
            + [""]
    if left:
        lines += ["## Fell off page 1"] + \
            [f"- {r['keyword']}: was {fmt_rank(r['prev_rank'])}, now "
             f"{fmt_rank(r['rank'])}" for r in left] + [""]

    lines += ["## All keywords",
              "| Keyword | Rank | Δ 1d | Δ 7d | Status |",
              "|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["rank"] is None, r["rank"] or 99)):
        lines.append(f"| {r['keyword']} | {fmt_rank(r['rank'])} | "
                     f"{fmt_delta(r['change_1d'])} | "
                     f"{fmt_delta(r['change_7d'])} | {r['status']} |")
    blocked = [r for r in rows if r["status"] == "blocked"]
    if blocked:
        lines += ["", "## Blocked (not tracked — no fake data)",
                  "Google throttled these; they will retry on the next run:"] + \
                 [f"- {r['keyword']}" for r in blocked]
    return "\n".join(lines) + "\n"


def run(db_path, client=None, out_dir="reports"):
    store.init_db(db_path)
    conn = store.connect(db_path)
    clients = [client] if client else \
        [r["name"] for r in conn.execute(
            "SELECT name FROM clients ORDER BY name").fetchall()]
    os.makedirs(out_dir, exist_ok=True)
    today = store.today_str()
    made = []
    for c in clients:
        rows, counts = build_client_report(conn, c)
        md = markdown(c, rows, counts, today)
        base = os.path.join(out_dir,
                            f"{c.lower().replace(' ', '-')}-{today}")
        with open(base + ".md", "w", encoding="utf-8") as f:
            f.write(md)
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump({"client": c, "date": today, "coverage": counts,
                       "keywords": rows}, f, indent=2)
        made.append((c, base + ".md", counts, len(rows)))
        print(f"{c}: {counts['tracked']}/{len(rows)} tracked -> {base}.md")
    conn.close()
    return made


def main():
    ap = argparse.ArgumentParser(description="Rank trend reports")
    ap.add_argument("--db", default=store.DEFAULT_DB)
    ap.add_argument("--client", default=None)
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()
    run(args.db, args.client, args.out)


if __name__ == "__main__":
    main()
