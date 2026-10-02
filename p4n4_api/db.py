"""The API's own SQLite database (users and refresh tokens; devices in M3)."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from p4n4_api.config import load_settings

DB_FILE = "api.db"

# One entry per schema version, applied in order. Never edit a released entry: append.
MIGRATIONS = (
    """
    CREATE TABLE users (
        username      TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL CHECK (role IN ('operator', 'admin')),
        -- Bumped on password change; access tokens carry it, so old ones stop working
        token_gen     INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL
    );
    CREATE TABLE refresh_tokens (
        jti        TEXT PRIMARY KEY,
        username   TEXT NOT NULL REFERENCES users (username) ON DELETE CASCADE,
        -- All tokens from one login share a family, so reuse can revoke the whole chain
        family     TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        used       INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX refresh_tokens_family ON refresh_tokens (family);
    """,
)


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {version}; COMMIT;")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open the database (creating and migrating it if needed); commit on success."""
    data_dir = load_settings().data_dir
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = data_dir / DB_FILE
    if not path.exists():
        # Holds password hashes: create it owner-only before SQLite opens it.
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _migrate(conn)
        with conn:
            yield conn
    finally:
        conn.close()
