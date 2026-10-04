#!/usr/bin/env python3
"""
manage.py — bulk activate / deactivate tracked keywords.

Deactivating is how you pause tracking (a finished campaign, a client on
hold) without deleting history: snapshots stay, the keyword just leaves
the daily rotation.

Usage:
    python3 tracker/manage.py --deactivate-client "Old Client"
    python3 tracker/manage.py --deactivate-pattern "%casino%"   # SQL LIKE
    python3 tracker/manage.py --activate-client "Old Client"
    python3 tracker/manage.py --activate-pattern "%goa%"
    python3 tracker/manage.py --status --client "Casino Pride"
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description="Bulk activate/deactivate tracked keywords")
    ap.add_argument("--db", default=store.DEFAULT_DB)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--deactivate-client", metavar="NAME")
    g.add_argument("--activate-client", metavar="NAME")
    g.add_argument("--deactivate-pattern", metavar="LIKE",
                   help='SQL LIKE pattern, e.g. "%%casino%%"')
    g.add_argument("--activate-pattern", metavar="LIKE")
    g.add_argument("--status", action="store_true",
                   help="show active/inactive counts (use with --client)")
    ap.add_argument("--client", default=None,
                    help="with --status: limit to this client")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt")
    args = ap.parse_args()

    store.init_db(args.db)
    conn = store.connect(args.db)

    if args.status:
        total = store.count_active_keywords(conn, args.client)
        scope = f" for {args.client!r}" if args.client else ""
        print(f"Active keywords{scope}: {total}")
        conn.close()
        return

    if args.deactivate_client:
        desc = f"deactivate ALL keywords for client {args.deactivate_client!r}"
        action = lambda: store.deactivate_client(conn,  # noqa: E731
                                                args.deactivate_client)
    elif args.activate_client:
        desc = f"activate ALL keywords for client {args.activate_client!r}"
        action = lambda: store.activate_client(conn,  # noqa: E731
                                              args.activate_client)
    elif args.deactivate_pattern:
        desc = (f"deactivate keywords matching LIKE "
                f"{args.deactivate_pattern!r}")
        action = lambda: store.deactivate_pattern(  # noqa: E731
            conn, args.deactivate_pattern)
    else:
        desc = (f"activate keywords matching LIKE {args.activate_pattern!r}")
        action = lambda: store.activate_pattern(  # noqa: E731
            conn, args.activate_pattern)

    if not args.yes:
        try:
            ans = input(f"{desc}. Type YES to confirm: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return
        if ans != "YES":
            print("Aborted.")
            return

    n = action()
    conn.commit()
    conn.close()
    print(f"Done: {n} keyword(s) changed.")


if __name__ == "__main__":
    main()
