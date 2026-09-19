# Database setup and small helpers.
# Defines the SQLite schema and gives out connections that auto commit or roll back.

import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

# SQL that creates all tables and indexes used by the app, if they do not exist yet.
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname TEXT NOT NULL UNIQUE COLLATE NOCASE,
    token TEXT NOT NULL UNIQUE,
    lang TEXT NOT NULL DEFAULT 'en',
    points INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    best_streak INTEGER NOT NULL DEFAULT 0,
    last_active_day TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS badges (
    user_id INTEGER NOT NULL, code TEXT NOT NULL, earned_at TEXT NOT NULL,
    PRIMARY KEY (user_id, code)
);
CREATE TABLE IF NOT EXISTS point_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, delta INTEGER NOT NULL,
    reason TEXT NOT NULL, ref TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS features (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL, name TEXT NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL, tags TEXT NOT NULL DEFAULT '{}',
    last_verified_at TEXT, info TEXT
);
CREATE TABLE IF NOT EXISTS flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    source TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    feature_id TEXT,
    title TEXT NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL,
    urgency INTEGER NOT NULL DEFAULT 1,
    priority REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    context TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    expires_at TEXT, resolved_at TEXT,
    claimed_by INTEGER, claim_expires_at TEXT,
    reporter_id INTEGER,
    verified_count INTEGER NOT NULL DEFAULT 0,
    photo_path TEXT,
    UNIQUE (type, source, source_ref)
);
CREATE TABLE IF NOT EXISTS missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flag_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, arrived_at TEXT, submitted_at TEXT,
    lat REAL, lon REAL, accuracy REAL, distance_m REAL, manual_arrival INTEGER NOT NULL DEFAULT 0,
    photo_path TEXT, before_path TEXT, photo_hash TEXT, photo_ahash TEXT,
    verdict TEXT, points INTEGER NOT NULL DEFAULT 0, was_problem TEXT,
    review_note TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS confirmations (
    flag_id INTEGER NOT NULL, user_id INTEGER NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (flag_id, user_id)
);
CREATE TABLE IF NOT EXISTS photo_hashes (
    sha256 TEXT PRIMARY KEY, ahash TEXT NOT NULL, user_id INTEGER, flag_id INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flags_status ON flags (status);
CREATE INDEX IF NOT EXISTS idx_missions_user ON missions (user_id, status);
CREATE INDEX IF NOT EXISTS idx_missions_flag ON missions (flag_id);
CREATE INDEX IF NOT EXISTS idx_points_user ON point_events (user_id, created_at);
"""

# Lock and set used so the schema is only created once per database file.
_init_lock = threading.Lock()
_initialised = set()


# Current UTC time as an ISO string, with no microseconds.
def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Make a random, hard to guess token for user login.
def new_token():
    return secrets.token_urlsafe(24)


# Make a short random id, used for feature ids.
def new_id():
    return uuid.uuid4().hex[:12]


def _ensure_column(conn, table, column, decl):
    """Add a column to a table made by an older version of the schema."""
    if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# Open a database connection, creating the schema on first use.
# Commits on success, rolls back on error, and always closes the connection.
@contextmanager
def connect(db_path):
    db_path = str(db_path)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    with _init_lock:
        if db_path not in _initialised:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            _ensure_column(conn, "missions", "note", "TEXT")
            conn.commit()
            _initialised.add(db_path)
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def write_lock(conn):
    """Take the write lock before reading, so two people acting on the same flag at the same instant are handled one after the other."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


# Turn a cursor's rows into a list of plain dicts.
def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


# Parse an ISO date string into a datetime, or None if empty.
def parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value)
