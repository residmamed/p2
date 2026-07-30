"""Append-only history of Amazon chart positions, on disk.

The one thing in this app that cannot be bought later. Every other signal in
Winning Products is readable from a single scrape; momentum is not, because a
rate of change needs two observations and a scrape is one. Vendors who sell
"sales velocity" are not selling a better scraper — they are selling an archive
they started years ago. This file is ours, and every day it doesn't run is a
day permanently missing from it.

Deliberately a plain SQLite file rather than a real database. The app has never
had one (the Pipeline lives in localStorage), and the write pattern here is
~100 rows per scan, appended, never updated — so the operational cost of
Postgres would buy nothing. If that changes, the schema below ports as-is.

Append-only on purpose: a snapshot is a claim about what a chart said at a
moment, and rewriting one would corrupt every delta computed from it.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "snapshots.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chart_snapshot (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at    TEXT    NOT NULL,   -- ISO-8601 UTC, one value per scan run
    category      TEXT    NOT NULL,   -- Amazon category id, e.g. "kitchen"
    chart         TEXT    NOT NULL,   -- "bestsellers" | "new_releases"
    asin          TEXT    NOT NULL,
    rank          INTEGER NOT NULL,   -- 1-based position in that chart
    title         TEXT,
    image         TEXT,
    link          TEXT,
    rating        REAL,
    ratings_total INTEGER
);
-- Reading is always "this product's history" or "this scan's rows", so index both.
CREATE INDEX IF NOT EXISTS idx_asin_time  ON chart_snapshot (asin, scanned_at);
CREATE INDEX IF NOT EXISTS idx_scan       ON chart_snapshot (category, chart, scanned_at);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def record_scan(
    rows: Iterable[dict],
    *,
    category: str,
    chart: str,
    scanned_at: Optional[str] = None,
) -> tuple[str, int]:
    """Write one chart's rows as a single scan. Returns (scanned_at, n_written).

    All rows of one scan share a timestamp so a later delta can pair them up
    without guessing which observations belonged together.
    """
    stamp = scanned_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = [
        (
            stamp,
            category,
            chart,
            r.get("asin"),
            r.get("rank") or r.get("position"),
            r.get("title"),
            r.get("image"),
            r.get("link"),
            r.get("rating"),
            r.get("ratings_total"),
        )
        for r in rows
        if r.get("asin") and (r.get("rank") or r.get("position"))
    ]
    if not payload:
        return stamp, 0
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO chart_snapshot
               (scanned_at, category, chart, asin, rank, title, image, link, rating, ratings_total)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            payload,
        )
    return stamp, len(payload)


def rank_history(asin: str, *, category: str, chart: str = "bestsellers") -> list[dict]:
    """Every recorded position for one product, oldest first.

    This list *is* the sparkline. Not a modelled curve — the actual chart
    positions we observed, which is why a product with one snapshot gets no
    line at all rather than a flat one.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT scanned_at, rank FROM chart_snapshot
               WHERE asin = ? AND category = ? AND chart = ?
               ORDER BY scanned_at ASC""",
            (asin, category, chart),
        ).fetchall()
    return [{"scanned_at": r["scanned_at"], "rank": r["rank"]} for r in rows]


def history_for_category(category: str, chart: str = "bestsellers") -> dict[str, list[dict]]:
    """rank_history() for every product in a category, in one query.

    The grid needs history for ~50 products at once; doing that as 50 separate
    reads is the obvious way to make a page that reads from local disk feel
    like one that doesn't.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT asin, scanned_at, rank FROM chart_snapshot
               WHERE category = ? AND chart = ?
               ORDER BY asin, scanned_at ASC""",
            (category, chart),
        ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["asin"], []).append(
            {"scanned_at": r["scanned_at"], "rank": r["rank"]}
        )
    return out


def scan_times(category: str, chart: str = "bestsellers") -> list[str]:
    """Distinct scan timestamps, oldest first — how much history actually exists."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT scanned_at FROM chart_snapshot
               WHERE category = ? AND chart = ? ORDER BY scanned_at ASC""",
            (category, chart),
        ).fetchall()
    return [r["scanned_at"] for r in rows]
