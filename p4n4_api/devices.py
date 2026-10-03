"""Devices: sensors and gateways that send data, each signing in with its own API key."""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import UTC, datetime

from argon2.exceptions import InvalidHashError, VerificationError

from p4n4_api import users
from p4n4_api.users import User

# A device's token subject is "device:<id>". ':' can't appear in a username, so a device
# can never be mistaken for the user of the same name.
SUBJECT_PREFIX = "device:"
KEY_PREFIX = "p4n4_"
# Lowercase slugs like usernames: device IDs become InfluxDB tags and MQTT topic parts (M5).
_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{1,31}")
# p4n4_<key id: 12 hex>_<secret: 32 random bytes, base64url>. The key id finds the device
# without trying every hash; the prefix lets secret scanners spot leaked keys.
_KEY = re.compile(r"p4n4_([0-9a-f]{12})_[A-Za-z0-9_-]{43}")

_COLUMNS = "id, name, description, enabled, key_id, created_at, last_seen_at"


class DeviceError(ValueError):
    """Invalid input, e.g. a bad device ID."""


class DeviceExists(DeviceError):
    """The device ID is taken."""


def normalize_id(device_id: str) -> str:
    name = device_id.strip().lower()
    if not _ID.fullmatch(name):
        raise DeviceError(
            "Device ID must be 2-32 characters: lowercase letters, digits, '.', '_' or '-', "
            "starting with a letter or digit."
        )
    return name


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _new_key() -> tuple[str, str]:
    key_id = secrets.token_hex(6)
    return key_id, f"{KEY_PREFIX}{key_id}_{secrets.token_urlsafe(32)}"


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    device = dict(row)
    device["enabled"] = bool(device["enabled"])
    # Enough to tell keys apart in a list, never enough to use one.
    device["key_prefix"] = f"{KEY_PREFIX}{device.pop('key_id')}"
    return device


def get(conn: sqlite3.Connection, device_id: str) -> dict | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM devices WHERE id = ?", (device_id,)).fetchone()
    return _row(row)


def list_page(conn: sqlite3.Connection, limit: int, offset: int) -> tuple[list[dict], int]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM devices ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
    )
    total = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    return [_row(r) for r in rows], total


def create(
    conn: sqlite3.Connection, device_id: str, name: str = "", description: str = ""
) -> tuple[dict, str]:
    """Register a device. Returns it and its API key, which is never stored in plaintext."""
    device_id = normalize_id(device_id)
    key_id, key = _new_key()
    try:
        conn.execute(
            "INSERT INTO devices (id, name, description, key_id, key_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (device_id, name, description, key_id, users.hasher.hash(key), _now()),
        )
    except sqlite3.IntegrityError as exc:
        raise DeviceExists(f"Device '{device_id}' already exists.") from exc
    return get(conn, device_id), key


def update(
    conn: sqlite3.Connection,
    device_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    enabled: bool | None = None,
) -> bool:
    """Change metadata. Disabling a device ends its tokens on their next request."""
    changes = {"name": name, "description": description, "enabled": enabled}
    changes = {k: v for k, v in changes.items() if v is not None}
    if not changes:
        return get(conn, device_id) is not None
    assignments = ", ".join(f"{column} = ?" for column in changes)
    return (
        conn.execute(
            f"UPDATE devices SET {assignments} WHERE id = ?", (*changes.values(), device_id)
        ).rowcount
        > 0
    )


def delete(conn: sqlite3.Connection, device_id: str) -> bool:
    return conn.execute("DELETE FROM devices WHERE id = ?", (device_id,)).rowcount > 0


def rotate_key(conn: sqlite3.Connection, device_id: str) -> str | None:
    """A new API key. The old key and every token issued from it stop working at once."""
    key_id, key = _new_key()
    updated = conn.execute(
        "UPDATE devices SET key_id = ?, key_hash = ?, key_gen = key_gen + 1 WHERE id = ?",
        (key_id, users.hasher.hash(key), device_id),
    ).rowcount
    return key if updated else None


def principal(conn: sqlite3.Connection, device_id: str) -> User | None:
    """The device as a request's caller (role "device"), or None if removed or disabled."""
    row = conn.execute(
        "SELECT key_gen FROM devices WHERE id = ? AND enabled", (device_id,)
    ).fetchone()
    return User(f"{SUBJECT_PREFIX}{device_id}", "device", row["key_gen"]) if row else None


def authenticate(conn: sqlite3.Connection, api_key: str) -> User | None:
    """The device this key belongs to, if valid and enabled. Records when it was last seen."""
    match = _KEY.fullmatch(api_key)
    row = (
        conn.execute(
            "SELECT id, key_hash, enabled, key_gen FROM devices WHERE key_id = ?",
            (match.group(1),),
        ).fetchone()
        if match
        else None
    )
    try:
        # Unknown keys are checked against a dummy hash, so they take as long to reject.
        users.hasher.verify(row["key_hash"] if row else users._dummy_hash(), api_key)
    except (VerificationError, InvalidHashError):
        return None
    if row is None or not row["enabled"]:
        return None
    conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (_now(), row["id"]))
    return User(f"{SUBJECT_PREFIX}{row['id']}", "device", row["key_gen"])
