"""Audit log: who changed what, and when. Rows are only ever added."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from p4n4_api import db, logs


def record(
    actor: str,
    action: str,
    target: str = "",
    outcome: str = "",
    conn: sqlite3.Connection | None = None,
) -> None:
    """Add an entry. Pass `conn` to make it part of that transaction (it's dropped if the
    change rolls back); without it, the entry is written on its own connection."""
    row = (
        datetime.now(UTC).isoformat(timespec="seconds"),
        actor,
        action,
        target,
        outcome,
        logs.request_id.get(),
    )
    sql = (
        "INSERT INTO audit_log (at, actor, action, target, outcome, request_id) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    )
    if conn is not None:
        conn.execute(sql, row)
        return
    with db.connect() as own:
        own.execute(sql, row)


def page(conn: sqlite3.Connection, limit: int, offset: int) -> tuple[list[dict], int]:
    """Newest first."""
    rows = conn.execute(
        "SELECT id, at, actor, action, target, outcome, request_id FROM audit_log "
        "ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    return [dict(r) for r in rows], total
