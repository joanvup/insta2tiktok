from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Sequence

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS reels_cache (
    shortcode TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    taken_at TEXT NOT NULL,
    duration REAL,
    thumb_path TEXT,
    thumb_url TEXT,
    video_path TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shortcode TEXT NOT NULL,
    username TEXT NOT NULL,
    caption TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'queued', 'downloading', 'uploading', 'posting',
        'verifying', 'published', 'failed', 'interrupted'
    )),
    message TEXT,
    attempts INTEGER NOT NULL DEFAULT 1,
    error_code TEXT,
    error_message TEXT,
    screenshot_path TEXT,
    post_clicked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_reel ON jobs(shortcode)
    WHERE status IN ('queued', 'downloading', 'uploading', 'posting', 'verifying');

CREATE UNIQUE INDEX IF NOT EXISTS one_published_job_per_reel ON jobs(shortcode)
    WHERE status = 'published';

CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON jobs(status, id);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    ensure_dirs()
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def fetch_one(sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()


def fetch_all(sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(conn.execute(sql, params).fetchall())


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    with get_conn() as conn:
        cursor = conn.execute(sql, params)
        return int(cursor.lastrowid or 0)


def execute_count(sql: str, params: Sequence[Any] = ()) -> int:
    with get_conn() as conn:
        cursor = conn.execute(sql, params)
        return int(cursor.rowcount)


def get_setting(key: str, default: str | None = None) -> str | None:
    row = fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def delete_setting(key: str) -> None:
    execute("DELETE FROM settings WHERE key = ?", (key,))
