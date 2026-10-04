#!/usr/bin/env python3
"""
alerts.py — rank alert rules. Data-only: writes to the alerts table and
prints. Sending (email/Slack/WhatsApp) is a hook point, not implemented —
wire your notifier where marked below.

Rules:
  1. drop >= 5 positions day-over-day (both ranks known)
  2. fell from page 1 (<=10) to page 2+ (>10 or gone from top 20)
  3. a tracked competitor domain entered the top 3

Usage:
    python3 alerts.py --db tracker/data/ranktracker.db [--client NAME]
                      [--drop-threshold 5]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402
from report import latest_run, nearest_run_before, rank_on  # noqa: E402


def check_client(conn, client_name, drop_threshold=5):
    client_id = store.get_or_create_client(conn, client_name)
    competitors = store.get_competitors(conn, client_id)
    fired = []
    for kw in store.get_active_keywords(conn, client_name):
        cur_date = latest_run(conn, kw["id"])
        if not cur_date:
            continue
        rank, status = rank_on(conn, kw["id"], cur_date)
        if status != "ok":
            continue
        prev_date = nearest_run_before(conn, kw["id"], cur_date, 2)
        prev_rank, _ = rank_on(conn, kw["id"], prev_date) if prev_date \
            else (None, "nodata")

        # Rule 1: sharp drop
        if rank is not None and prev_rank is not None:
            drop = prev_rank - rank
            if drop <= -drop_threshold:
                fired.append(("rank_drop",
                              f"{kw['keyword']!r}: {kw['domain']} fell "
                              f"#{prev_rank} -> #{rank} ({drop})", kw["id"]))
        # Rule 2: fell off page 1
        if prev_rank is not None and prev_rank <= 10 and \
                (rank is None or rank > 10):
            now = f"#{rank}" if rank else "out of top 20"
            fired.append(("page1_lost",
                          f"{kw['keyword']!r}: {kw['domain']} fell off page "
                          f"1 (#{prev_rank} -> {now})", kw["id"]))
        # Rule 3: competitor in top 3
        if competitors:
            row = conn.execute(
                """SELECT positions_json FROM serp_snapshots
                   WHERE keyword_id = ? AND run_date = ?""",
                (kw["id"], cur_date)).fetchone()
            positions = json.loads(row["positions_json"] or "[]")
            for p in positions:
                if p.get("pos", 99) > 3:
                    continue
                dom = (p.get("domain") or "").lower().lstrip("www.")
                for comp in competitors:
                    c = comp.lower().lstrip("www.")
                    if dom == c or dom.endswith("." + c):
                        fired.append((
                            "competitor_top3",
                            f"{kw['keyword']!r}: competitor {comp} is "
                            f"#{p['pos']} ({p.get('title', '')[:60]})",
                            kw["id"]))
                        break
    for atype, msg, kw_id in fired:
        store.add_alert(conn, atype, msg, keyword_id=kw_id,
                        client_id=client_id)
    conn.commit()
    return fired


def run(db_path, client=None, drop_threshold=5):
    store.init_db(db_path)
    conn = store.connect(db_path)
    clients = [client] if client else \
        [r["name"] for r in conn.execute(
            "SELECT name FROM clients ORDER BY name").fetchall()]
    all_fired = []
    for c in clients:
        fired = check_client(conn, c, drop_threshold)
        all_fired.extend([(c,) + f for f in fired])
    conn.close()
    for c, atype, msg, _ in all_fired:
        print(f"[{c}] {atype}: {msg}")
    # === NOTIFIER HOOK POINT ===
    # Wire email/Slack/WhatsApp here: iterate all_fired and send.
    # Keep it out of this repo's core so no credentials ever live here.
    if not all_fired:
        print("No alerts.")
    return all_fired


def main():
    ap = argparse.ArgumentParser(description="Rank alert rules")
    ap.add_argument("--db", default=store.DEFAULT_DB)
    ap.add_argument("--client", default=None)
    ap.add_argument("--drop-threshold", type=int, default=5)
    args = ap.parse_args()
    run(args.db, args.client, args.drop_threshold)


if __name__ == "__main__":
    main()
