import json
import os
import sqlite3
from datetime import UTC, datetime


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or os.getenv("TRIAGE_DB", "triage.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            subject TEXT NOT NULL, body TEXT NOT NULL, customer TEXT NOT NULL,
            suggestion TEXT NOT NULL,           -- JSON: triage + articles + draft
            status TEXT NOT NULL DEFAULT 'pending_review',
            system TEXT, external_id TEXT,      -- set when the ticket came from a ticket system
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
            ai_reply TEXT
        );
        """
    )
    return conn


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def row_to_ticket(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["suggestion"] = json.loads(d["suggestion"])
    return d
