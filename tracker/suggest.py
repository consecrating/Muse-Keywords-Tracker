#!/usr/bin/env python3
"""
suggest.py — FREE keyword suggestions from one seed keyword. No paid APIs.

Sources (all free, no keys):
  1. Google Autocomplete (suggestqueries.google.com) with alphabet expansion:
     "<seed> a" .. "<seed> z", "<seed> 0" .. "<seed> 9" — this is how you
     mine the long tail Google itself suggests.
  2. "Related searches" extracted from one real SERP fetch of the seed
     (reuses serp_scrape's fetcher style and block detection).

Output: ranked, deduped list -> suggestions/<seed-slug>-<date>.md (numbered,
human-pickable) and .json (with per-keyword sources).

Pick mode (--pick): prints the numbered list, you type numbers like
"1,4,7-9", and the chosen keywords are appended to tracker/keywords.csv's
live store (deduped against existing). Only USER-PICKED keywords ever get
tracked — suggestions are ideas, not data.

Usage:
    python3 tracker/suggest.py --keyword "goa casino" --country IN
    python3 tracker/suggest.py --keyword "goa casino" --pick --client "Casino Pride" --domain cpofficial.in
    python3 tracker/suggest.py --seeds-file seeds.txt --limit 50
    python3 tracker/suggest.py --keyword "goa casino" --no-expand   # faster, shallower
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402
import serp_scrape  # noqa: E402

AC_URL = "https://suggestqueries.google.com/complete/search"
NAV_JUNK = {"images", "videos", "news", "maps", "shopping", "more", "all",
            "search", "web"}


# --- Autocomplete ------------------------------------------------------------

def parse_autocomplete_payload(data):
    """Pure parser (testable): Google AC JSON -> list of suggestion strings.
    Handles both client=firefox (["q", ["s1", ...]]) and client=chrome
    (["q", ["s1", ...], [], {...}]) shapes."""
    if not isinstance(data, list) or len(data) < 2:
        return []
    cands = data[1]
    if not isinstance(cands, list):
        return []
    out = []
    for c in cands:
        if isinstance(c, str) and c.strip():
            out.append(c.strip())
        elif isinstance(c, list) and c and isinstance(c[0], str):
            out.append(c[0].strip())
    return out


def fetch_autocomplete(query, session, gl="in"):
    """One autocomplete call. Tries client=firefox, falls back to chrome.
    Returns (suggestions|None, blocked: bool)."""
    for client in ("firefox", "chrome"):
        try:
            resp = session.get(
                AC_URL,
                params={"client": client, "q": query, "gl": gl, "hl": "en"},
                headers={"User-Agent": random.choice(serp_scrape.USER_AGENTS)},
                timeout=20)
        except requests.RequestException:
            return None, False
        if resp.status_code == 429:
            return None, True
        if resp.status_code != 200:
            continue
        try:
            return parse_autocomplete_payload(resp.json()), False
        except (ValueError, KeyError):
            continue
    return None, False


def expand_queries(seed):
    """Seed plus alphabet/digit expansion for deep autocomplete mining."""
    out = [seed]
    out += [f"{seed} {c}" for c in "abcdefghijklmnopqrstuvwxyz"]
    out += [f"{seed} {d}" for d in "0123456789"]
    return out


# --- Related searches from one SERP fetch ------------------------------------

class RelatedParser(HTMLParser):
    """Collect anchor texts of /search?q=... links (Google's related
    searches block). Skips nav junk and the seed query itself."""

    def __init__(self, seed):
        super().__init__(convert_charrefs=True)
        self.seed = seed.strip().lower()
        self._in_a = False
        self._a_text = []
        self.related = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        if not href.startswith("/search?"):
            return
        q = parse_qs(urlparse(href).query).get("q", [None])[0]
        if q:
            self._in_a = True
            self._a_text = []
            self._a_q = unquote(q).strip().lower()

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self._in_a = False
            text = "".join(self._a_text).strip()
            q = getattr(self, "_a_q", "")
            if (text and text.lower() not in NAV_JUNK
                    and q and q != self.seed and len(text) < 120):
                self.related.append(text)

    def handle_data(self, data):
        if self._in_a:
            self._a_text.append(data)


def fetch_related(seed, session, gl="in"):
    """One polite SERP fetch; extract related searches. Returns list (may be
    empty on block — never raises for HTTP-level issues)."""
    time.sleep(random.uniform(2, 5))
    headers = {
        "User-Agent": random.choice(serp_scrape.USER_AGENTS),
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.google.co.in/",
    }
    url = (f"{serp_scrape.BASE_URL}?q={quote_plus(seed)}&num=20"
           f"&hl=en&gl={gl}&pws=0")
    try:
        resp = session.get(url, headers=headers, timeout=30)
    except requests.RequestException:
        return []
    if serp_scrape.is_blocked(resp.status_code, resp.text):
        print("  related-searches fetch blocked — continuing with "
              "autocomplete only", flush=True)
        return []
    if resp.status_code != 200:
        return []
    parser = RelatedParser(seed)
    parser.feed(resp.text)
    # Dedupe preserving order.
    seen, out = set(), []
    for r in parser.related:
        k = r.lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


# --- Ranking -----------------------------------------------------------------

def rank_suggestions(seed, ac_hits, related):
    """Dedupe + rank. Score: (#sources) then (total mentions) then alpha.
    Sources are 'autocomplete' and 'related'. Seed itself is excluded."""
    seed_l = seed.strip().lower()
    agg = {}  # lower -> {"text": first-seen, "sources": set, "hits": int}
    for s in ac_hits:
        k = s.lower()
        if k == seed_l or not k:
            continue
        e = agg.setdefault(k, {"text": s, "sources": set(), "hits": 0})
        e["sources"].add("autocomplete")
        e["hits"] += 1
    for s in related:
        k = s.lower()
        if k == seed_l or not k:
            continue
        e = agg.setdefault(k, {"text": s, "sources": set(), "hits": 0})
        e["sources"].add("related")
        e["hits"] += 1
    ranked = sorted(agg.values(),
                    key=lambda e: (-len(e["sources"]), -e["hits"],
                                   e["text"].lower()))
    return [{"keyword": e["text"],
             "sources": sorted(e["sources"]),
             "mentions": e["hits"]} for e in ranked]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-",
                  s.lower()).strip("-") or "seed"


def write_outputs(seed, ranked, out_dir, run_date):
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"{slug(seed)}-{run_date}")
    md_path = base + ".md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Keyword suggestions for {seed!r} ({run_date})\n\n")
        f.write(f"{len(ranked)} ideas from Google Autocomplete + related "
                f"searches. Pick by number with `--pick`.\n\n")
        for i, r in enumerate(ranked, 1):
            src = ", ".join(r["sources"])
            f.write(f"{i}. {r['keyword']}  _(via {src})_\n")
    json_path = base + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"seed": seed, "date": run_date,
                   "suggestions": ranked}, f, indent=2)
    return md_path, json_path


# --- Pick mode ----------------------------------------------------------------

def parse_selection(spec, n):
    """'1,4,7-9' -> sorted list of 0-based indices. Raises ValueError."""
    picked = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            if lo < 1 or hi > n or lo > hi:
                raise ValueError(f"bad range {part!r}")
            picked.update(range(lo - 1, hi))
        else:
            i = int(part)
            if i < 1 or i > n:
                raise ValueError(f"bad number {part!r}")
            picked.add(i - 1)
    return sorted(picked)


def pick_interactive(ranked, db_path, client, domain, country, select=None):
    n = len(ranked)
    for i, r in enumerate(ranked, 1):
        print(f"{i:3}. {r['keyword']}  ({', '.join(r['sources'])})")
    if select is None:
        try:
            select = input("\nPick numbers (e.g. 1,4,7-9), or Enter to skip: "
                           ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSkipped.")
            return 0, 0
    if not select:
        print("Nothing picked.")
        return 0, 0
    try:
        idxs = parse_selection(select, n)
    except ValueError as e:
        print(f"Invalid selection: {e} — nothing added.")
        return 0, 0
    if not idxs:
        return 0, 0
    if not client:
        client = input("Client name: ").strip()
    if not domain:
        domain = input("Domain to track (e.g. example.com): ").strip().lower()
    if not client or not domain:
        print("Client and domain are required — nothing added.")
        return 0, 0
    store.init_db(db_path)
    conn = store.connect(db_path)
    cid = store.get_or_create_client(conn, client)
    added, skipped = 0, 0
    for i in idxs:
        kw = ranked[i]["keyword"]
        exists = conn.execute(
            """SELECT id FROM keywords WHERE client_id = ? AND keyword = ?
               AND domain = ?""",
            (cid, kw, domain)).fetchone()
        if exists:
            skipped += 1
            continue
        store.add_keyword(conn, client, kw, domain, country)
        added += 1
    conn.commit()
    conn.close()
    print(f"Added {added}, skipped {skipped} already tracked.")
    return added, skipped


# --- Main ---------------------------------------------------------------------

def suggest_one(seed, session, out_dir, run_date, gl="in", limit=None,
                no_expand=False):
    print(f"\n=== suggestions for {seed!r} ===", flush=True)
    queries = [seed] if no_expand else expand_queries(seed)
    ac_hits = []
    blocked = False
    for qi, q in enumerate(queries, 1):
        if qi > 1:
            time.sleep(random.uniform(1, 2))  # polite
        suggs, was_blocked = fetch_autocomplete(q, session, gl)
        if was_blocked:
            print("  autocomplete blocked — stopping expansion",
                  flush=True)
            blocked = True
            break
        if suggs:
            ac_hits.extend(suggs)
        if qi == 1 or qi % 10 == 0:
            print(f"  autocomplete: {qi}/{len(queries)} queries, "
                  f"{len(ac_hits)} raw hits", flush=True)
    related = fetch_related(seed, session, gl)
    print(f"  related searches: {len(related)}", flush=True)
    ranked = rank_suggestions(seed, ac_hits, related)
    if limit:
        ranked = ranked[:limit]
    md_path, json_path = write_outputs(seed, ranked, out_dir, run_date)
    print(f"  -> {len(ranked)} ideas written to {md_path}", flush=True)
    if blocked:
        print("  NOTE: autocomplete was throttled partway — list may be "
              "shorter than usual.", flush=True)
    return ranked, md_path, json_path


def main():
    ap = argparse.ArgumentParser(
        description="Free keyword suggestions from a seed keyword")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--keyword", help="single seed keyword")
    src.add_argument("--seeds-file",
                     help="file with one seed keyword per line")
    ap.add_argument("--country", default="IN",
                    help="gl= param for Google (default IN)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap suggestions per seed")
    ap.add_argument("--no-expand", action="store_true",
                    help="skip alphabet expansion (faster, shallower)")
    ap.add_argument("--pick", action="store_true",
                    help="interactive pick mode: choose numbers to start "
                         "tracking")
    ap.add_argument("--select",
                    help="non-interactive pick, e.g. --select '1,4,7-9' "
                         "(implies --pick)")
    ap.add_argument("--client", default=None)
    ap.add_argument("--domain", default=None)
    ap.add_argument("--db", default=store.DEFAULT_DB)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    gl = args.country.lower()
    run_date = date.today().isoformat()
    out_dir = args.out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "suggestions")

    if args.seeds_file:
        with open(args.seeds_file, encoding="utf-8") as f:
            seeds = [l.strip() for l in f if l.strip()]
    else:
        seeds = [args.keyword]

    if args.pick and len(seeds) > 1:
        print("ERROR: --pick works with a single --keyword, not "
              "--seeds-file.", file=sys.stderr)
        sys.exit(2)

    session = requests.Session()
    total_added, total_skipped = 0, 0
    for seed in seeds:
        ranked, _, _ = suggest_one(seed, session, out_dir, run_date, gl,
                                   args.limit, args.no_expand)
        if args.pick or args.select:
            a, s = pick_interactive(ranked, args.db, args.client,
                                    args.domain, args.country,
                                    select=args.select)
            total_added += a
            total_skipped += s
    if args.pick or args.select:
        print(f"\nTotal: {total_added} keywords now tracked, "
              f"{total_skipped} already tracked.")


if __name__ == "__main__":
    main()
