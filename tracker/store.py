#!/usr/bin/env python3
"""
store.py — SQLite storage layer for the free rank-tracking engine.

Database lives at tracker/data/ranktracker.db (gitignored).
All table creation is idempotent: safe to call init_db() on every run.
"""
import json
import os
import sqlite3
from datetime import date

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "ranktracker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    keyword TEXT NOT NULL,
    domain TEXT NOT NULL,
    country TEXT NOT NULL DEFAULT 'IN',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (client_id, keyword, domain)
);
CREATE TABLE IF NOT EXISTS competitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    domain TEXT NOT NULL,
    UNIQUE (client_id, domain)
);
CREATE TABLE IF NOT EXISTS serp_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id),
    run_date TEXT NOT NULL,
    positions_json TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (keyword_id, run_date)
);
CREATE TABLE IF NOT EXISTS gsc_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword_id INTEGER REFERENCES keywords(id),
    site TEXT NOT NULL,
    query TEXT NOT NULL,
    country TEXT,
    row_date TEXT NOT NULL,
    clicks REAL NOT NULL DEFAULT 0,
    impressions REAL NOT NULL DEFAULT 0,
    ctr REAL NOT NULL DEFAULT 0,
    position REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gsc_kw_date ON gsc_rows (keyword_id, row_date);
CREATE INDEX IF NOT EXISTS idx_snap_kw_date ON serp_snapshots (keyword_id, run_date);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    keyword_id INTEGER REFERENCES keywords(id),
    client_id INTEGER REFERENCES clients(id),
    acknowledged INTEGER NOT NULL DEFAULT 0
);
"""


def connect(db_path=DEFAULT_DB):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=DEFAULT_DB):
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn):
    """Idempotent migrations for columns added after the initial schema."""
    cols = {r["name"] for r in
            conn.execute("PRAGMA table_info(keywords)").fetchall()}
    if "last_checked" not in cols:
        conn.execute("ALTER TABLE keywords "
                     "ADD COLUMN last_checked TEXT DEFAULT NULL")


def get_or_create_client(conn, name):
    row = conn.execute("SELECT id FROM clients WHERE name = ?",
                       (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO clients (name) VALUES (?)", (name,))
    return cur.lastrowid


def add_keyword(conn, client_name, keyword, domain, country="IN"):
    client_id = get_or_create_client(conn, client_name)
    conn.execute(
        """INSERT OR IGNORE INTO keywords
           (client_id, keyword, domain, country) VALUES (?, ?, ?, ?)""",
        (client_id, keyword.strip(), domain.strip().lower(), country))
    return conn.execute(
        """SELECT id FROM keywords
           WHERE client_id = ? AND keyword = ? AND domain = ?""",
        (client_id, keyword.strip(), domain.strip().lower())).fetchone()["id"]


def add_competitor(conn, client_name, domain):
    client_id = get_or_create_client(conn, client_name)
    conn.execute("INSERT OR IGNORE INTO competitors (client_id, domain) "
                 "VALUES (?, ?)", (client_id, domain.strip().lower()))


def get_active_keywords(conn, client_name=None):
    sql = """SELECT k.id, k.keyword, k.domain, k.country, c.name AS client
             FROM keywords k JOIN clients c ON c.id = k.client_id
             WHERE k.active = 1"""
    params = []
    if client_name:
        sql += " AND c.name = ?"
        params.append(client_name)
    sql += " ORDER BY c.name, k.keyword"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def save_snapshot(conn, keyword_id, run_date, positions, status="ok",
                  error=None):
    """Upsert one snapshot per keyword per day. positions = list of dicts."""
    conn.execute(
        """INSERT INTO serp_snapshots
           (keyword_id, run_date, positions_json, status, error)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (keyword_id, run_date) DO UPDATE SET
             positions_json = excluded.positions_json,
             status = excluded.status,
             error = excluded.error""",
        (keyword_id, run_date,
         json.dumps(positions) if positions is not None else None,
         status, error))


def completed_keyword_ids(conn, run_date):
    rows = conn.execute(
        "SELECT keyword_id FROM serp_snapshots WHERE run_date = ?",
        (run_date,)).fetchall()
    return {r["keyword_id"] for r in rows}


def get_snapshots(conn, keyword_id, limit=30):
    rows = conn.execute(
        """SELECT run_date, positions_json, status FROM serp_snapshots
           WHERE keyword_id = ? ORDER BY run_date DESC LIMIT ?""",
        (keyword_id, limit)).fetchall()
    return [dict(r) for r in rows]


def rank_of_domain(positions, domain):
    """Return 1-based rank of domain in positions list, or None."""
    if not positions:
        return None
    want = domain.lower().lstrip("www.")
    for p in positions:
        got = (p.get("domain") or "").lower().lstrip("www.")
        if got == want or got.endswith("." + want):
            return p.get("pos")
    return None


def save_gsc_rows(conn, rows):
    """rows: list of dicts with keys site, query, country, row_date, clicks,
    impressions, ctr, position, keyword_id (may be None)."""
    conn.executemany(
        """INSERT INTO gsc_rows
           (keyword_id, site, query, country, row_date, clicks,
            impressions, ctr, position)
           VALUES (:keyword_id, :site, :query, :country, :row_date, :clicks,
                   :impressions, :ctr, :position)""", rows)


def match_keyword_id(conn, client_id, query):
    row = conn.execute(
        """SELECT id FROM keywords
           WHERE client_id = ? AND lower(keyword) = lower(?) AND active = 1""",
        (client_id, query.strip())).fetchone()
    return row["id"] if row else None


def add_alert(conn, alert_type, message, keyword_id=None, client_id=None):
    conn.execute(
        """INSERT INTO alerts (type, message, keyword_id, client_id)
           VALUES (?, ?, ?, ?)""",
        (alert_type, message, keyword_id, client_id))


def get_unacknowledged_alerts(conn, client_id=None):
    sql = ("SELECT a.id, a.created_at, a.type, a.message, c.name AS client "
           "FROM alerts a LEFT JOIN clients c ON c.id = a.client_id "
           "WHERE a.acknowledged = 0")
    params = []
    if client_id:
        sql += " AND a.client_id = ?"
        params.append(client_id)
    sql += " ORDER BY a.created_at DESC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_competitors(conn, client_id):
    rows = conn.execute("SELECT domain FROM competitors WHERE client_id = ?",
                        (client_id,)).fetchall()
    return [r["domain"] for r in rows]


# --- Chunked daily rotation (Feature: bulk tracking) -----------------------

def set_last_checked(conn, keyword_id, date_str=None):
    conn.execute("UPDATE keywords SET last_checked = ? WHERE id = ?",
                 (date_str or today_str(), keyword_id))


def count_active_keywords(conn, client_name=None):
    sql = ("SELECT COUNT(*) AS n FROM keywords k "
           "JOIN clients c ON c.id = k.client_id WHERE k.active = 1")
    params = []
    if client_name:
        sql += " AND c.name = ?"
        params.append(client_name)
    return conn.execute(sql, params).fetchone()["n"]


def get_rotation_keywords(conn, budget, client_name=None):
    """Round-robin pick: the `budget` active keywords with the oldest
    last_checked (never-checked first). Fair rotation across large sets."""
    sql = """SELECT k.id, k.keyword, k.domain, k.country, c.name AS client
             FROM keywords k JOIN clients c ON c.id = k.client_id
             WHERE k.active = 1"""
    params = []
    if client_name:
        sql += " AND c.name = ?"
        params.append(client_name)
    sql += (" ORDER BY k.last_checked IS NOT NULL, k.last_checked ASC,"
            " k.id ASC LIMIT ?")
    params.append(budget)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --- Bulk activate / deactivate --------------------------------------------

def _client_id(conn, client_name):
    row = conn.execute("SELECT id FROM clients WHERE name = ?",
                       (client_name,)).fetchone()
    return row["id"] if row else None


def deactivate_client(conn, client_name):
    cid = _client_id(conn, client_name)
    if cid is None:
        return 0
    cur = conn.execute("UPDATE keywords SET active = 0 WHERE client_id = ? "
                       "AND active = 1", (cid,))
    return cur.rowcount


def activate_client(conn, client_name):
    cid = _client_id(conn, client_name)
    if cid is None:
        return 0
    cur = conn.execute("UPDATE keywords SET active = 1 WHERE client_id = ? "
                       "AND active = 0", (cid,))
    return cur.rowcount


def deactivate_pattern(conn, like_pattern):
    cur = conn.execute("UPDATE keywords SET active = 0 "
                       "WHERE keyword LIKE ? AND active = 1",
                       (like_pattern,))
    return cur.rowcount


def activate_pattern(conn, like_pattern):
    cur = conn.execute("UPDATE keywords SET active = 1 "
                       "WHERE keyword LIKE ? AND active = 0",
                       (like_pattern,))
    return cur.rowcount


def import_keywords_csv(conn, csv_path):
    """CSV columns: client,keyword,domain,country[,competitors(; separated)].
    Competitor domains in the optional 5th column are added to that client."""
    import csv
    added, comps = 0, 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("client") or not row.get("keyword") \
                    or not row.get("domain"):
                continue
            add_keyword(conn, row["client"], row["keyword"], row["domain"],
                        row.get("country") or "IN")
            added += 1
            if row.get("competitors"):
                for d in row["competitors"].split(";"):
                    if d.strip():
                        add_competitor(conn, row["client"], d)
                        comps += 1
    return added, comps


def today_str():
    return date.today().isoformat()
