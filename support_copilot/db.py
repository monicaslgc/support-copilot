import json
import os
import sqlite3
from datetime import UTC, datetime

from .clusters import normalize_customer


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open (and create or upgrade) the database. Without a path: TRIAGE_DB, else ./triage.db."""
    return init(sqlite3.connect(path or os.environ.get("TRIAGE_DB") or "triage.db", check_same_thread=False))


def init(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Create or upgrade the tables on a connection you opened yourself."""
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            subject TEXT NOT NULL, body TEXT NOT NULL, customer TEXT NOT NULL,
            customer_key TEXT,                  -- clusters.normalize_customer(customer): what is compared
            suggestion TEXT NOT NULL,           -- JSON: triage + articles + draft
            status TEXT NOT NULL DEFAULT 'pending_review',
            system TEXT, external_id TEXT,      -- set when the ticket came from a ticket system
            category TEXT, priority TEXT,       -- the AI's triage, as columns so the queue can be sorted in SQL
            topic TEXT,                         -- best matching KB article id
            cluster_id INTEGER,                 -- tickets about the same issue share this (parent ticket id)
            duplicate_of INTEGER,               -- same customer, same category, within the window
            armed INTEGER NOT NULL DEFAULT 1,   -- 0: arrived carrying a decision tag, which is not a decision
            UNIQUE (system, external_id)
        );
        CREATE TABLE IF NOT EXISTS outbox (     -- every call we make to a ticket system, for audit/debug
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            system TEXT NOT NULL, external_id TEXT NOT NULL,
            kind TEXT NOT NULL,                 -- triage_note | public_reply | rejection_note
            mode TEXT NOT NULL,                 -- sent | dry_run | error
            payload TEXT NOT NULL, error TEXT
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL REFERENCES tickets(id),
            created_at TEXT NOT NULL,
            reviewer TEXT NOT NULL, action TEXT NOT NULL,
            final_category TEXT, final_priority TEXT, final_reply TEXT,
            ai_category TEXT, ai_priority TEXT,  -- kept to measure AI accuracy
            ai_reply TEXT,
            reasons TEXT,                        -- JSON list of why the human corrected or rejected the AI
            note TEXT
        );
        """
    )
    _add_missing_columns(conn)
    return conn


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Upgrade a database created by an older version (CREATE TABLE IF NOT EXISTS does not add columns)."""
    wanted = {"tickets": {"topic": "TEXT", "cluster_id": "INTEGER", "duplicate_of": "INTEGER",
                          "category": "TEXT", "priority": "TEXT", "customer_key": "TEXT",
                          "armed": "INTEGER NOT NULL DEFAULT 1"},
              "reviews": {"ai_reply": "TEXT", "reasons": "TEXT", "note": "TEXT"}}
    for table, cols in wanted.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in cols.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    for row in conn.execute("SELECT id, customer FROM tickets WHERE customer_key IS NULL").fetchall():  # backfill
        conn.execute("UPDATE tickets SET customer_key = ? WHERE id = ?",
                     (normalize_customer(row["customer"]), row["id"]))
    for row in conn.execute("SELECT id, suggestion FROM tickets WHERE category IS NULL").fetchall():  # backfill
        try:
            triage = json.loads(row["suggestion"]).get("triage", {})
        except ValueError:
            triage = {}
        conn.execute("UPDATE tickets SET category = ?, priority = ? WHERE id = ?",
                     (triage.get("category", "other"), triage.get("priority", "P3"), row["id"]))
    for index, columns in (("cluster", "cluster_id"), ("created", "created_at"),
                           ("topic", "topic, category, created_at"),
                           ("customer_key", "customer_key, category, created_at"),
                           ("queue", "status, priority, created_at, id")):
        conn.execute(f"CREATE INDEX IF NOT EXISTS ix_tickets_{index} ON tickets({columns})")
    conn.execute("DROP INDEX IF EXISTS ix_tickets_customer")  # the old one was on lower(customer): ASCII-only
    conn.commit()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def row_to_ticket(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["suggestion"] = json.loads(d["suggestion"])
    return d
