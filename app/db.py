import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

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
    review_note TEXT
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

_init_lock = threading.Lock()
_initialised = set()


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_token():
    return secrets.token_urlsafe(24)


def new_id():
    return uuid.uuid4().hex[:12]


@contextmanager
def connect(db_path):
    db_path = str(db_path)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    with _init_lock:
        if db_path not in _initialised:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
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


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value)
