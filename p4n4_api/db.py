"""The API's own SQLite database: users, refresh tokens, devices and the audit log."""

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
    """
    CREATE TABLE devices (
        id           TEXT PRIMARY KEY,
        name         TEXT NOT NULL DEFAULT '',
        description  TEXT NOT NULL DEFAULT '',
        enabled      INTEGER NOT NULL DEFAULT 1,
        -- Public part of the API key, to find the device; the key itself is only hashed
        key_id       TEXT NOT NULL UNIQUE,
        key_hash     TEXT NOT NULL,
        -- Bumped on key rotation; device tokens carry it, so old ones stop working
        key_gen      INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL,
        -- Last API key exchange (POST /auth/token)
        last_seen_at TEXT
    );
    """,
    """
    -- Append-only: who changed what, and when
    CREATE TABLE audit_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        at         TEXT NOT NULL,
        actor      TEXT NOT NULL,
        action     TEXT NOT NULL,
        target     TEXT NOT NULL DEFAULT '',
        outcome    TEXT NOT NULL DEFAULT '',
        request_id TEXT NOT NULL DEFAULT '-'
    );
    """,
    # Adds the normie role. SQLite can't change a CHECK constraint, so the table is rebuilt
    # (foreign keys are off while migrating, so refresh tokens survive the drop).
    """
    CREATE TABLE users_new (
        username      TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL CHECK (role IN ('normie', 'operator', 'admin')),
        token_gen     INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL
    );
    INSERT INTO users_new SELECT username, password_hash, role, token_gen, created_at FROM users;
    DROP TABLE users;
    ALTER TABLE users_new RENAME TO users;
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
        # Migrations run first, with foreign keys off: a table rebuild drops the old table,
        # which would otherwise cascade into the rows that reference it.
        _migrate(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn
    finally:
        conn.close()
