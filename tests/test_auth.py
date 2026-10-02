"""Tests for auth: sign-in, tokens, roles, rate limiting and the users CLI."""

from __future__ import annotations

import io
import stat
import time
from datetime import timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from p4n4_api import auth, db, users
from p4n4_api.cli import main as cli
from tests.conftest import PASSWORD, login, make_user

PROTECTED = ("/api/v1/project", "/api/v1/stacks", "/api/v1/stacks/iot", "/api/v1/edge/metrics")
PUBLIC = ("/health", "/ready", "/api/v1/version", "/openapi.json")


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Which endpoints need auth ─────────────────────────────────────────────────


@pytest.mark.parametrize("path", PROTECTED)
def test_protected_endpoints_need_a_token(anon, path):
    r = anon.get(path)
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("path", PUBLIC)
def test_public_endpoints(anon, flat_project, path):
    assert anon.get(path).status_code == 200


def test_auth_checked_before_project_lookup(anon, tmp_path, monkeypatch):
    # No project: an anonymous caller still gets 401, not a 404 that reveals server setup.
    monkeypatch.setenv("P4N4_PROJECT_DIR", str(tmp_path))
    assert anon.get("/api/v1/project").status_code == 401


def test_admin_can_read_operator_endpoints(admin, flat_project):
    assert admin.get("/api/v1/project").status_code == 200


def test_require_role_ranks_roles(anon):
    app = FastAPI()

    @app.get("/admin-only", dependencies=[Depends(auth.require_role("admin"))])
    def admin_only() -> dict:
        return {}

    make_user("ops", "operator")
    make_user("root", "admin")
    c = TestClient(app)
    ops, root = login(anon, "ops"), login(anon, "root")
    r = c.get("/admin-only", headers=_bearer(ops["access_token"]))
    assert r.status_code == 403
    assert "admin" in r.json()["detail"]
    assert c.get("/admin-only", headers=_bearer(root["access_token"])).status_code == 200


# ── Sign-in ───────────────────────────────────────────────────────────────────


def test_login_and_me(anon):
    make_user("Alice", "operator")
    tokens = login(anon, "ALICE")  # usernames are case-insensitive
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 3600
    assert tokens["refresh_expires_in"] == 7 * 24 * 3600
    assert (tokens["username"], tokens["role"]) == ("alice", "operator")

    r = anon.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
    assert r.json() == {"username": "alice", "role": "operator", "auth": True}

    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 3600


@pytest.mark.parametrize(
    "username,password",
    [("alice", "wrong password"), ("nobody", PASSWORD), ("Bad Name!", PASSWORD)],
)
def test_login_failures_look_the_same(anon, username, password):
    make_user("alice", "operator")
    r = anon.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid username or password."


def test_login_rate_limited(anon):
    for _ in range(10):
        anon.post("/api/v1/auth/token", json={"username": "x", "password": "y"})
    r = anon.post("/api/v1/auth/token", json={"username": "x", "password": "y"})
    assert r.status_code == 429
    assert 1 <= int(r.headers["Retry-After"]) <= 7


def test_rate_limiter_refills():
    limiter = auth.RateLimiter(capacity=2, rate=1000)
    assert limiter.hit("a") == 0 and limiter.hit("a") == 0
    assert limiter.hit("b") == 0  # per key
    time.sleep(0.01)
    assert limiter.hit("a") == 0


def test_password_rehashed_when_params_change(anon, monkeypatch):
    from argon2 import PasswordHasher

    make_user("alice", "operator")
    monkeypatch.setattr(users, "hasher", PasswordHasher(time_cost=2, memory_cost=16, parallelism=1))
    login(anon, "alice")
    with db.connect() as conn:
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert "m=16,t=2" in stored
    login(anon, "alice")  # still works with the new hash


# ── Tokens ────────────────────────────────────────────────────────────────────


def test_refresh_rotates(anon):
    make_user("alice", "operator")
    first = login(anon, "alice")
    second = anon.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    second = second.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert anon.get("/api/v1/auth/me", headers=_bearer(second["access_token"])).status_code == 200


def test_refresh_reuse_revokes_the_login(anon):
    make_user("alice", "operator")
    first = login(anon, "alice")
    other_login = login(anon, "alice")
    second = anon.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    ).json()

    # The first refresh token is replayed, e.g. by someone who copied it.
    r = anon.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 401
    assert "already used" in r.json()["detail"]
    # The whole chain is revoked, including the legitimate client's newer token...
    r = anon.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert r.status_code == 401
    # ...but a separate sign-in is untouched.
    r = anon.post("/api/v1/auth/refresh", json={"refresh_token": other_login["refresh_token"]})
    assert r.status_code == 200


def test_logout_revokes_refresh(anon):
    make_user("alice", "operator")
    tokens = login(anon, "alice")
    assert (
        anon.post(
            "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 204
    )
    r = anon.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    # Signing out twice is fine.
    assert (
        anon.post(
            "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 204
    )


def test_token_types_not_interchangeable(anon):
    make_user("alice", "operator")
    tokens = login(anon, "alice")
    assert anon.get("/api/v1/auth/me", headers=_bearer(tokens["refresh_token"])).status_code == 401
    r = anon.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert r.status_code == 401


def test_expired_token(anon, monkeypatch):
    make_user("alice", "operator")
    monkeypatch.setattr(auth, "ACCESS_TTL", timedelta(seconds=-1))
    tokens = login(anon, "alice")
    r = anon.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
    assert r.status_code == 401
    assert "expired" in r.json()["detail"]


@pytest.mark.parametrize(
    "forge",
    [
        lambda c: jwt.encode(c, "not-the-secret-but-long-enough-to-pass", algorithm="HS256"),
        lambda c: jwt.encode(c, None, algorithm="none"),
        lambda c: "not.a.jwt",
    ],
    ids=["wrong-key", "alg-none", "garbage"],
)
def test_forged_tokens_rejected(anon, forge):
    make_user("alice", "admin")
    now = int(time.time())
    claims = {
        "sub": "alice",
        "type": "access",
        "role": "admin",
        "gen": 0,
        "iat": now,
        "exp": now + 60,
    }
    assert anon.get("/api/v1/auth/me", headers=_bearer(forge(claims))).status_code == 401


def test_password_change_signs_out_everywhere(anon):
    make_user("alice", "operator")
    tokens = login(anon, "alice")
    with db.connect() as conn:
        users.set_password(conn, "alice", "a brand new password")
    assert anon.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"])).status_code == 401
    r = anon.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    login(anon, "alice", "a brand new password")


def test_role_change_applies_immediately(anon):
    make_user("alice", "operator")
    token = login(anon, "alice")["access_token"]
    with db.connect() as conn:
        users.set_role(conn, "alice", "admin")
    assert anon.get("/api/v1/auth/me", headers=_bearer(token)).json()["role"] == "admin"


def test_deleted_user_rejected(anon):
    make_user("alice", "operator")
    tokens = login(anon, "alice")
    with db.connect() as conn:
        users.delete(conn, "alice")
    assert anon.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"])).status_code == 401
    r = anon.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


# ── Secret and settings ───────────────────────────────────────────────────────


def test_generated_secret_is_private_and_stable(api_state):
    secret = auth.jwt_secret()
    path = api_state / auth.JWT_SECRET_FILE
    assert len(secret) == 64
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert auth.jwt_secret() == secret
    assert [p.name for p in api_state.iterdir() if p.name.startswith(".")] == []


def test_secret_from_env(anon, api_state, monkeypatch):
    secret = "from-the-environment-" + "x" * 16
    monkeypatch.setenv("P4N4_API_JWT_SECRET", secret)
    make_user("alice", "operator")
    token = login(anon, "alice")["access_token"]
    jwt.decode(token, secret, algorithms=["HS256"])
    assert not (api_state / auth.JWT_SECRET_FILE).exists()


def test_short_secret_refused_at_startup(monkeypatch):
    from p4n4_api.main import app

    monkeypatch.setenv("P4N4_API_JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least 32"), TestClient(app):
        pass


def test_auth_off(anon, flat_project, monkeypatch):
    monkeypatch.setenv("P4N4_API_AUTH", "off")
    assert anon.get("/api/v1/project").status_code == 200
    assert anon.get("/api/v1/auth/me").json() == {
        "username": "anonymous",
        "role": "admin",
        "auth": False,
    }


def test_startup_creates_database(api_state, caplog):
    with TestClient(__import__("p4n4_api.main", fromlist=["app"]).app):
        pass
    assert stat.S_IMODE((api_state / db.DB_FILE).stat().st_mode) == 0o600
    assert users._dummy_hash.cache_info().currsize == 1
    assert "No users yet" in caplog.text


# ── Users ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "username,password,role,message",
    [
        ("a", PASSWORD, "operator", "Username"),
        ("has space", PASSWORD, "operator", "Username"),
        ("alice", "short", "operator", "at least 10"),
        ("alice", PASSWORD, "device", "Role"),
    ],
)
def test_user_validation(username, password, role, message):
    with db.connect() as conn, pytest.raises(users.UserError, match=message):
        users.create(conn, username, password, role)


def test_duplicate_user():
    make_user("alice", "operator")
    with db.connect() as conn, pytest.raises(users.UserError, match="already exists"):
        users.create(conn, "Alice", PASSWORD, "admin")


def test_cli_users(monkeypatch, capsys, anon):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert cli(["users", "add", "Admin", "--role", "admin", "--password-stdin"]) == 0
    assert login(anon, "admin")["role"] == "admin"

    assert cli(["users", "role", "admin", "operator"]) == 0
    assert cli(["users", "list"]) == 0
    assert "admin" in capsys.readouterr().out.splitlines()[-1]

    monkeypatch.setattr("sys.stdin", io.StringIO("another long password\n"))
    assert cli(["users", "passwd", "admin", "--password-stdin"]) == 0
    login(anon, "admin", "another long password")

    assert cli(["users", "remove", "admin"]) == 0
    assert cli(["users", "remove", "admin"]) == 1
    assert "No user 'admin'" in capsys.readouterr().err


def test_cli_rejects_short_password(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert cli(["users", "add", "alice", "--password-stdin"]) == 1
    assert "at least 10" in capsys.readouterr().err


def test_cli_password_prompt_mismatch(monkeypatch, capsys):
    answers = iter(["first password!", "second password"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))
    assert cli(["users", "add", "alice"]) == 1
    assert "don't match" in capsys.readouterr().err
