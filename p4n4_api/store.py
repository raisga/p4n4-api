"""Deployment-wide settings changed through the API (the `settings` table): one JSON object
per key, with who changed it last."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime


def get(conn: sqlite3.Connection, key: str) -> dict | None:
    """The stored object, or None when it was never set (or isn't an object)."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row["value"])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def changed(conn: sqlite3.Connection, key: str) -> tuple[str, str] | None:
    """When the value was last set and by whom, or None when it never was."""
    row = conn.execute(
        "SELECT updated_at, updated_by FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return None if row is None else (row["updated_at"], row["updated_by"])


def put(conn: sqlite3.Connection, key: str, value: dict, actor: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value), datetime.now(UTC).isoformat(timespec="seconds"), actor),
    )
