"""JWT issuing and checking, role dependencies, and login rate limiting."""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from p4n4_api import db, users
from p4n4_api.config import load_settings
from p4n4_api.users import User

ALGORITHM = "HS256"
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=7)
JWT_SECRET_FILE = "jwt_secret"
# RFC 7518 §3.2: an HS256 key must be at least as long as the hash output (32 bytes).
MIN_SECRET_LENGTH = 32

# Stands in for a real user when P4N4_API_AUTH=off.
ANONYMOUS_ADMIN = User("anonymous", "admin")


class TokenError(Exception):
    """A token that is malformed, forged, expired, revoked or of the wrong type."""


def jwt_secret() -> str:
    """P4N4_API_JWT_SECRET, or a random key generated once and kept in the data dir."""
    settings = load_settings()
    if settings.jwt_secret:
        if len(settings.jwt_secret) < MIN_SECRET_LENGTH:
            # A short key can be brute-forced offline from any token, then used to forge admins.
            raise RuntimeError(
                f"P4N4_API_JWT_SECRET must be at least {MIN_SECRET_LENGTH} characters "
                "(e.g. `openssl rand -hex 32`), or unset to generate one."
            )
        return settings.jwt_secret
    path = settings.data_dir / JWT_SECRET_FILE
    if not path.exists():
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Write a temp file, then link it into place: a concurrent starter either wins
        # or reads the complete file, never an empty one.
        tmp = path.with_name(f".{JWT_SECRET_FILE}.{uuid.uuid4().hex}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        finally:
            tmp.unlink()
    return path.read_text().strip()


def _encode(claims: dict, ttl: timedelta) -> str:
    now = int(time.time())
    return jwt.encode(
        {**claims, "iat": now, "exp": now + int(ttl.total_seconds())}, jwt_secret(), ALGORITHM
    )


def decode(token: str, token_type: str) -> dict:
    try:
        claims = jwt.decode(
            token,
            jwt_secret(),
            algorithms=[ALGORITHM],
            options={"require": ["exp", "iat", "sub", "type"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims["type"] != token_type:
        raise TokenError(f"Expected a {token_type} token.")
    return claims


def issue_tokens(conn: sqlite3.Connection, user: User, family: str | None = None) -> dict:
    """A new access/refresh pair. The refresh token is recorded so it can be used once."""
    jti = uuid.uuid4().hex
    family = family or uuid.uuid4().hex
    refresh = _encode({"sub": user.username, "type": "refresh", "jti": jti}, REFRESH_TTL)
    conn.execute(
        "INSERT INTO refresh_tokens (jti, username, family, expires_at) VALUES (?, ?, ?, ?)",
        (jti, user.username, family, int(time.time() + REFRESH_TTL.total_seconds())),
    )
    # Prune here so the table never needs a background job.
    conn.execute("DELETE FROM refresh_tokens WHERE expires_at < ?", (int(time.time()),))
    access = _encode(
        {"sub": user.username, "type": "access", "role": user.role, "gen": user.token_gen},
        ACCESS_TTL,
    )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_expires_in": int(REFRESH_TTL.total_seconds()),
        "username": user.username,
        "role": user.role,
    }


def _refresh_row(conn: sqlite3.Connection, refresh_token: str) -> sqlite3.Row:
    claims = decode(refresh_token, "refresh")
    row = conn.execute(
        "SELECT * FROM refresh_tokens WHERE jti = ?", (claims.get("jti"),)
    ).fetchone()
    if row is None:
        raise TokenError("Refresh token has been revoked.")
    return row


def rotate(conn: sqlite3.Connection, refresh_token: str) -> dict:
    """Swap a refresh token for a new pair. Each refresh token works once.

    Reusing one means it was copied, so the whole family (every token from that login)
    is revoked, signing out both the legitimate client and whoever copied it.
    """
    row = _refresh_row(conn, refresh_token)
    if row["used"]:
        conn.execute("DELETE FROM refresh_tokens WHERE family = ?", (row["family"],))
        # Commit the revocation even though the request fails.
        conn.commit()
        raise TokenError("Refresh token was already used; this login has been revoked.")
    conn.execute("UPDATE refresh_tokens SET used = 1 WHERE jti = ?", (row["jti"],))
    user = users.get(conn, row["username"])
    if user is None:
        raise TokenError("User no longer exists.")
    return issue_tokens(conn, user, family=row["family"])


def revoke(conn: sqlite3.Connection, refresh_token: str) -> None:
    """Sign out: revoke every refresh token from the same login."""
    row = _refresh_row(conn, refresh_token)
    conn.execute("DELETE FROM refresh_tokens WHERE family = ?", (row["family"],))


# ── Dependencies ──────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /api/v1/auth/token")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """The signed-in user. Role and token generation come from the database, not the
    token, so role changes, password changes and deletions apply immediately."""
    if not load_settings().auth_enabled:
        return ANONYMOUS_ADMIN
    if credentials is None:
        raise _unauthorized("Not authenticated.")
    try:
        claims = decode(credentials.credentials, "access")
    except TokenError as exc:
        raise _unauthorized(f"Invalid token: {exc}") from exc
    with db.connect() as conn:
        user = users.get(conn, claims["sub"])
    if user is None or claims.get("gen") != user.token_gen:
        raise _unauthorized("Invalid token: signed out. Sign in again.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(role: str):
    """Dependency factory: 403 unless the user has `role` or a higher one."""

    def check(user: CurrentUser) -> User:
        if not user.has_role(role):
            raise HTTPException(status_code=403, detail=f"Requires the {role} role.")
        return user

    return check


# ── Rate limiting ─────────────────────────────────────────────────────────────


class RateLimiter:
    """Per-key token bucket, in memory: `capacity` requests, refilled at `rate` per second."""

    def __init__(self, capacity: int, rate: float) -> None:
        self.capacity = capacity
        self.rate = rate
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float:
        """Take one token. Returns 0 if allowed, else seconds until the next token."""
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > 10_000:
                self._prune(now)
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return (1 - tokens) / self.rate
            self._buckets[key] = (tokens - 1, now)
            return 0.0

    def _prune(self, now: float) -> None:
        full = self.capacity / self.rate
        self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < full}

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# 10 attempts in a burst, then one every 6 s, per client address.
login_limiter = RateLimiter(capacity=10, rate=1 / 6)


def rate_limit_login(request: Request) -> None:
    wait = login_limiter.hit(request.client.host if request.client else "unknown")
    if wait:
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Try again shortly.",
            headers={"Retry-After": str(int(wait) + 1)},
        )
