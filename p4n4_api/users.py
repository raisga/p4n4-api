"""Operator and admin accounts: people who sign in to the dashboard or the API."""

from __future__ import annotations

import functools
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Lowest first. "device" joins with the device registry (M3), outside this ranking.
ROLES = ("operator", "admin")
MIN_PASSWORD_LENGTH = 10
_USERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{1,31}")

# argon2id with the library's defaults (RFC 9106 low-memory profile: 64 MiB, 3 passes).
hasher = PasswordHasher()


@dataclass(frozen=True)
class User:
    username: str
    role: str
    token_gen: int = 0

    def has_role(self, role: str) -> bool:
        """Roles are ranked: an admin can do everything an operator can."""
        return self.role in ROLES and ROLES.index(self.role) >= ROLES.index(role)


class UserError(ValueError):
    """Invalid input, e.g. a bad username, short password or unknown role."""


def normalize_username(username: str) -> str:
    name = username.strip().lower()
    if not _USERNAME.fullmatch(name):
        raise UserError(
            "Username must be 2-32 characters: lowercase letters, digits, '.', '_' or '-', "
            "starting with a letter or digit."
        )
    return name


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise UserError(f"Role must be one of: {', '.join(ROLES)}.")


def _row_to_user(row: sqlite3.Row | None) -> User | None:
    return User(row["username"], row["role"], row["token_gen"]) if row else None


def get(conn: sqlite3.Connection, username: str) -> User | None:
    row = conn.execute(
        "SELECT username, role, token_gen FROM users WHERE username = ?", (username,)
    ).fetchone()
    return _row_to_user(row)


def list_all(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT username, role, created_at FROM users ORDER BY username")
    return [dict(row) for row in rows]


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create(conn: sqlite3.Connection, username: str, password: str, role: str) -> User:
    name = normalize_username(username)
    _check_password(password)
    _check_role(role)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (name, hasher.hash(password), role, datetime.now(UTC).isoformat(timespec="seconds")),
        )
    except sqlite3.IntegrityError as exc:
        raise UserError(f"User '{name}' already exists.") from exc
    return User(name, role)


def delete(conn: sqlite3.Connection, username: str) -> bool:
    """Remove a user; their refresh tokens go with them (ON DELETE CASCADE)."""
    return conn.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount > 0


def set_password(conn: sqlite3.Connection, username: str, password: str) -> bool:
    """Change a password and sign the user out everywhere."""
    _check_password(password)
    updated = conn.execute(
        "UPDATE users SET password_hash = ?, token_gen = token_gen + 1 WHERE username = ?",
        (hasher.hash(password), username),
    ).rowcount
    conn.execute("DELETE FROM refresh_tokens WHERE username = ?", (username,))
    return updated > 0


def set_role(conn: sqlite3.Connection, username: str, role: str) -> bool:
    """Change a role. It applies on the next request: the role is read per request."""
    _check_role(role)
    return (
        conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username)).rowcount > 0
    )


@functools.cache
def _dummy_hash() -> str:
    """Verified against for unknown users. Warmed at startup (`warm_up`), or the first
    unknown-user sign-in would take two hashes and stand out."""
    return hasher.hash("p4n4-api timing equalizer")


def warm_up() -> None:
    _dummy_hash()


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> User | None:
    """The user if the password matches, else None (unknown users take as long to reject)."""
    try:
        name = normalize_username(username)
    except UserError:
        name = None
    row = (
        conn.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone() if name else None
    )
    try:
        hasher.verify(row["password_hash"] if row else _dummy_hash(), password)
    except (VerificationError, InvalidHashError):
        return None
    if row is None:
        return None
    if hasher.check_needs_rehash(row["password_hash"]):
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hasher.hash(password), row["username"]),
        )
    return _row_to_user(row)
