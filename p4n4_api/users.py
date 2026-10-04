"""Normie, operator and admin accounts: people who sign in to the dashboard or the API."""

from __future__ import annotations

import functools
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# People's roles, lowest first. Devices have the role "device", outside this ranking: they
# can't do what normies can, and only devices pass require_role("device").
# Normies read status and chat with agents; operators also act (publish, infer, generate).
ROLES = ("normie", "operator", "admin")
DEVICE_ROLE = "device"
MIN_PASSWORD_LENGTH = 10
_USERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{1,31}")

# Development accounts, one per dashboard view, all with the same well-known password. Only
# created on request (P4N4_API_DEV_USERS=true or `p4n4-api users dev`): never in production.
# The password is deliberately shorter than MIN_PASSWORD_LENGTH; seeding skips that check.
DEV_PASSWORD = "p4n4"
DEV_USERS = (("admin", "admin"), ("power", "operator"), ("normie", "normie"))

# argon2id with the library's defaults (RFC 9106 low-memory profile: 64 MiB, 3 passes).
hasher = PasswordHasher()


@dataclass(frozen=True)
class User:
    username: str
    role: str
    token_gen: int = 0

    def has_role(self, role: str) -> bool:
        """Roles are ranked: an admin can do everything an operator can, and so on down."""
        if DEVICE_ROLE in (role, self.role):
            return role == self.role
        return self.role in ROLES and ROLES.index(self.role) >= ROLES.index(role)


class UserError(ValueError):
    """Invalid input, e.g. a bad username, short password or unknown role."""


class UserExists(UserError):
    """The username is taken."""


class LastAdminError(UserError):
    """The change would leave no admin to manage users."""


# Appended to a statement's WHERE clause: the row isn't an admin, or another admin remains.
# Checked inside the write itself, so two admins demoting each other can't both succeed.
_NOT_LAST_ADMIN = "(role != 'admin' OR (SELECT COUNT(*) FROM users WHERE role = 'admin') > 1)"


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


def info(conn: sqlite3.Connection, username: str) -> dict | None:
    """A user's public fields (no password hash), or None."""
    row = conn.execute(
        "SELECT username, role, created_at FROM users WHERE username = ?", (username,)
    ).fetchone()
    return dict(row) if row else None


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
        raise UserExists(f"User '{name}' already exists.") from exc
    return User(name, role)


def _last_admin(username: str) -> LastAdminError:
    return LastAdminError(
        f"'{username}' is the last admin. Make another user an admin first "
        "(or use `p4n4-api users` on the server)."
    )


def bootstrap_admin(conn: sqlite3.Connection, username: str = "admin") -> str | None:
    """Create the first admin with a generated password, only while there are no users.

    Returns the password (for the caller to show once), or None if users already exist.
    Safe to run on every install or container start.
    """
    name = normalize_username(username)
    password = secrets.token_urlsafe(18)  # 24 characters, 144 bits
    # One statement, so two concurrent bootstraps can't both see an empty table.
    created = conn.execute(
        "INSERT INTO users (username, password_hash, role, created_at) "
        "SELECT ?, ?, 'admin', ? WHERE NOT EXISTS (SELECT 1 FROM users)",
        (name, hasher.hash(password), datetime.now(UTC).isoformat(timespec="seconds")),
    ).rowcount
    return password if created else None


def seed_dev_users(conn: sqlite3.Connection) -> list[str]:
    """Create the missing DEV_USERS with DEV_PASSWORD; returns the names created.

    Existing accounts are left alone (their role and password may have been changed on
    purpose), so it's safe on every start.
    """
    created = []
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for name, role in DEV_USERS:
        if conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, hasher.hash(DEV_PASSWORD), role, now),
        ).rowcount:
            created.append(name)
    return created


def delete(conn: sqlite3.Connection, username: str, *, keep_admin: bool = False) -> bool:
    """Remove a user; their refresh tokens go with them (ON DELETE CASCADE).

    With `keep_admin`, refuse to remove the last admin (the CLI can, as a recovery path).
    """
    sql = "DELETE FROM users WHERE username = ?"
    if keep_admin:
        sql += f" AND {_NOT_LAST_ADMIN}"
    if conn.execute(sql, (username,)).rowcount:
        return True
    if keep_admin and get(conn, username):
        raise _last_admin(username)
    return False


def set_password(conn: sqlite3.Connection, username: str, password: str) -> bool:
    """Change a password and sign the user out everywhere."""
    _check_password(password)
    updated = conn.execute(
        "UPDATE users SET password_hash = ?, token_gen = token_gen + 1 WHERE username = ?",
        (hasher.hash(password), username),
    ).rowcount
    conn.execute("DELETE FROM refresh_tokens WHERE username = ?", (username,))
    return updated > 0


def set_role(
    conn: sqlite3.Connection, username: str, role: str, *, keep_admin: bool = False
) -> bool:
    """Change a role. It applies on the next request: the role is read per request.

    With `keep_admin`, refuse to demote the last admin.
    """
    _check_role(role)
    sql = "UPDATE users SET role = ? WHERE username = ?"
    if keep_admin and role != "admin":
        sql += f" AND {_NOT_LAST_ADMIN}"
    if conn.execute(sql, (role, username)).rowcount:
        return True
    if keep_admin and get(conn, username):
        raise _last_admin(username)
    return False


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
