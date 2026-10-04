#!/usr/bin/env python3
"""
run_daily.py — daily orchestrator: GSC pull -> SERP scrape -> reports -> alerts.

Designed for cron:
    0 6 * * * cd /path/to/Keywords-Tracker && python3 tracker/run_daily.py >> tracker/data/cron.log 2>&1

Env knobs:
    DAILY_BUDGET   max keywords to scrape per run (default 40). Large sets
                   rotate round-robin: the N least-recently-checked keywords
                   go first, so 500 keywords rotate across ~12 days.
    MAX_KEYWORDS   legacy alias for DAILY_BUDGET (still honoured)
    GSC_CREDENTIALS / GSC_SITES  (see gsc_fetch.py; optional)
    TRACKER_DB     override DB path

Prints a one-screen summary at the end.
"""
import argparse
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402
import gsc_fetch  # noqa: E402
import serp_scrape  # noqa: E402
import report  # noqa: E402
import alerts  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Daily rank-tracker run")
    ap.add_argument("--daily-budget", type=int, default=None,
                    help="max keywords to scrape this run (default: "
                         "DAILY_BUDGET env or 40)")
    ap.add_argument("--client", default=None,
                    help="limit this run to one client")
    args = ap.parse_args()

    db = os.environ.get("TRACKER_DB", store.DEFAULT_DB)
    budget = args.daily_budget
    if budget is None:
        budget = os.environ.get("DAILY_BUDGET",
                                os.environ.get("MAX_KEYWORDS"))
        budget = int(budget) if budget else 40

    started = time.time()
    print(f"=== rank-tracker daily run {store.today_str()} "
          f"(budget {budget}) ===")
    store.init_db(db)
    total_active = store.count_active_keywords(store.connect(db),
                                               args.client)

    summary = {}

    # 1. GSC pull (free, official). Skips cleanly without credentials.
    try:
        summary["gsc"] = gsc_fetch.run(db)
    except Exception:  # noqa: BLE001 - one stage must not kill the run
        print("GSC stage failed (continuing):")
        traceback.print_exc()
        summary["gsc"] = {"error": True}

    # 2. SERP scrape (free, careful, honest about blocks).
    try:
        summary["scrape"] = serp_scrape.run(db, client=args.client,
                                            budget=budget)
    except Exception:
        print("Scrape stage failed (continuing):")
        traceback.print_exc()
        summary["scrape"] = {"error": True}

    # 3. Reports.
    try:
        made = report.run(db, out_dir=os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "reports"))
        summary["reports"] = [m[1] for m in made]
    except Exception:
        print("Report stage failed (continuing):")
        traceback.print_exc()
        summary["reports"] = []

    # 4. Alerts.
    try:
        fired = alerts.run(db)
        summary["alerts"] = len(fired)
    except Exception:
        print("Alerts stage failed:")
        traceback.print_exc()
        summary["alerts"] = 0

    mins = (time.time() - started) / 60
    rotation_days = max(1, -(-total_active // budget))  # ceil div
    print(f"=== done in {mins:.1f} min ===")
    print(f"GSC: {summary['gsc']} | scrape: {summary['scrape']} | "
          f"reports: {len(summary['reports'])} | alerts: {summary['alerts']}")
    print(f"Coverage: {total_active} active keywords, budget {budget}/day "
          f"-> full rotation every ~{rotation_days} day(s).")


if __name__ == "__main__":
    main()
