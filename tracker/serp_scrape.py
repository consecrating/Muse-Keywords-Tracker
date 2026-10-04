#!/usr/bin/env python3
"""
serp_scrape.py — careful, low-volume, FREE Google SERP scraper.

Hard truth, stated up front: Google throttles unauthenticated scraping
aggressively. This module is engineered to survive as long as possible
(jittered delays, real user agents, daily cadence, small keyword sets) and
to FAIL HONESTLY when blocked: snapshots record status='blocked', never
zeros, never invented ranks. The report shows coverage truthfully.

Usage:
    python3 serp_scrape.py --db tracker/data/ranktracker.db [--client NAME]
                           [--limit N] [--delay-min 20] [--delay-max 40]
"""
import argparse
import json
import random
import re
import sys
import time
from html.parser import HTMLParser
from urllib.parse import quote_plus, unquote, urlparse

import requests

sys.path.insert(0, __import__("os").path.dirname(
    __import__("os").path.abspath(__file__)))
import store  # noqa: E402

# Rotating pool of real desktop Chrome user agents.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 "
    "Firefox/127.0",
]

BASE_URL = "https://www.google.co.in/search"

# Subtrees that are ads / shopping / non-organic — skipped during parsing.
AD_MARKERS = ("tads", "bottomads", "uEierd", "ads-ad", "commercial-unit",
              "mnr-c", "pla-unit", "shopping")

BLOCK_MARKERS = ("unusual traffic", "/sorry/", "recaptcha",
                 "detected unusual traffic", "our systems have detected")


class OrganicParser(HTMLParser):
    """Collect organic results in document order.

    Strategy: track a stack of (tag, attrs). Skip any subtree rooted at an
    ad/shopping container. Inside organic land, every <a href="/url?q=...">
    whose nearest heading text we can capture becomes a result.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._skip_depth = 0
        self._stack_classes = []
        self._in_a = False
        self._a_href = None
        self._a_text = []
        self._in_h3 = False
        self._h3_text = []
        self._last_h3 = ""

    def _is_ad_root(self, attrs):
        blob = " ".join(str(v) for _, v in attrs).lower()
        return any(m in blob for m in AD_MARKERS)

    def handle_starttag(self, tag, attrs):
        if self._skip_depth:
            self._skip_depth += 1
            return
        if tag in ("div", "section", "aside") and self._is_ad_root(attrs):
            self._skip_depth = 1
            return
        if tag == "h3":
            self._in_h3 = True
            self._h3_text = []
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href.startswith("/url?q="):
                self._in_a = True
                self._a_href = href
                self._a_text = []

    def handle_endtag(self, tag):
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "h3":
            self._in_h3 = False
            self._last_h3 = "".join(self._h3_text).strip()
        if tag == "a" and self._in_a:
            self._in_a = False
            self._register_result()

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_h3:
            self._h3_text.append(data)
        if self._in_a:
            self._a_text.append(data)

    def _register_result(self):
        m = re.match(r"/url\?q=([^&]+)", self._a_href or "")
        if not m:
            return
        url = unquote(m.group(1))
        parsed = urlparse(url)
        if not parsed.netloc or "google." in parsed.netloc:
            return  # internal / non-organic link
        title = "".join(self._a_text).strip() or self._last_h3
        if any(r["url"] == url for r in self.results):
            return
        self.results.append({
            "url": url,
            "domain": parsed.netloc.lower(),
            "title": title[:200],
        })


def parse_serp(html):
    """Parse raw Google SERP HTML -> list of organic results (max 20)."""
    parser = OrganicParser()
    parser.feed(html)
    out = []
    for i, r in enumerate(parser.results[:20], start=1):
        out.append({"pos": i, "url": r["url"],
                    "domain": r["domain"], "title": r["title"]})
    return out


def is_blocked(status_code, html):
    if status_code == 429:
        return True
    low = html.lower()
    return any(m in low for m in BLOCK_MARKERS)


def fetch_serp(keyword, session, delay_min, delay_max):
    """Fetch one SERP. Returns (status, positions|None, error|None)."""
    time.sleep(random.uniform(delay_min, delay_max))
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
        "Referer": "https://www.google.co.in/",
    }
    url = (f"{BASE_URL}?q={quote_plus(keyword)}&num=20&hl=en&gl=in"
           f"&pws=0&filter=0")
    try:
        resp = session.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        return "error", None, f"request failed: {e}"
    if is_blocked(resp.status_code, resp.text):
        return "blocked", None, f"HTTP {resp.status_code} / block markers"
    if resp.status_code != 200:
        return "error", None, f"HTTP {resp.status_code}"
    positions = parse_serp(resp.text)
    if not positions:
        # Empty parse on a 200 usually means markup changed or soft block.
        return "blocked", None, "no organic results parsed (markup/block?)"
    return "ok", positions, None


def run(db_path, client=None, limit=None, delay_min=20, delay_max=40,
        budget=None):
    """Scrape due keywords. With budget=N: round-robin — the N active
    keywords with the oldest last_checked (fair rotation for large sets).
    Without budget: all active keywords not yet done today (legacy path)."""
    store.init_db(db_path)
    conn = store.connect(db_path)
    run_date = store.today_str()
    done = store.completed_keyword_ids(conn, run_date)

    if budget is not None:
        keywords = [k for k in
                    store.get_rotation_keywords(conn, budget, client)
                    if k["id"] not in done]
        total_active = store.count_active_keywords(conn, client)
    else:
        keywords = [k for k in store.get_active_keywords(conn, client)
                    if k["id"] not in done]
        total_active = len(store.get_active_keywords(conn, client))
    if limit:
        keywords = keywords[:limit]

    session = requests.Session()
    consecutive_blocks = 0
    counts = {"ok": 0, "blocked": 0, "error": 0, "skipped": len(done)}

    for i, kw in enumerate(keywords, start=1):
        print(f"[{i}/{len(keywords)}] {kw['keyword']!r} ...", flush=True)
        status, positions, error = fetch_serp(kw["keyword"], session,
                                              delay_min, delay_max)
        if status == "blocked":
            consecutive_blocks += 1
        else:
            consecutive_blocks = 0
        store.save_snapshot(conn, kw["id"], run_date, positions,
                            status=status, error=error)
        # Rotation bookkeeping: this keyword was attempted today, whatever
        # the outcome — it goes to the back of the queue.
        store.set_last_checked(conn, kw["id"], run_date)
        conn.commit()
        counts[status] += 1
        print(f"    -> {status}" + (f": {error}" if error else ""), flush=True)
        if status == "blocked":
            rank = None
        else:
            rank = store.rank_of_domain(positions, kw["domain"])
        if rank:
            print(f"    rank for {kw['domain']}: #{rank}", flush=True)
        if consecutive_blocks >= 3:
            print("STOPPING: 3 consecutive blocks — Google is throttling. "
                  "Remaining keywords left for tomorrow's run.", flush=True)
            break

    conn.close()
    summary = (f"Done: {counts['ok']} ok, {counts['blocked']} blocked, "
               f"{counts['error']} errors, {counts['skipped']} already done.")
    if budget is not None:
        summary += (f" | rotation: {len(keywords)} of {total_active} "
                    f"active keywords due today")
    print(summary, flush=True)
    return counts


def main():
    ap = argparse.ArgumentParser(description="Free Google SERP scraper")
    ap.add_argument("--db", default=store.DEFAULT_DB)
    ap.add_argument("--client", default=None)
    ap.add_argument("--limit", type=int, default=None,
                    help="max keywords this run")
    ap.add_argument("--delay-min", type=float, default=20)
    ap.add_argument("--delay-max", type=float, default=40)
    ap.add_argument("--budget", type=int, default=None,
                    help="round-robin: scrape only the N least-recently "
                         "checked keywords (for large sets)")
    args = ap.parse_args()
    run(args.db, args.client, args.limit, args.delay_min, args.delay_max,
        args.budget)


if __name__ == "__main__":
    main()
