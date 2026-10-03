"""Tests for the device registry and device sign-in with API keys."""

from __future__ import annotations

import re

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from p4n4_api import auth, db, devices

KEY = re.compile(r"p4n4_[0-9a-f]{12}_[A-Za-z0-9_-]{43}")


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(admin: TestClient, device_id: str = "greenhouse-01", **fields) -> dict:
    r = admin.post("/api/v1/devices", json={"id": device_id, **fields})
    assert r.status_code == 201, r.text
    return r.json()


def _device_token(c: TestClient, api_key: str) -> dict:
    r = c.post("/api/v1/auth/token", json={"api_key": api_key})
    assert r.status_code == 200, r.text
    return r.json()


def _me(c: TestClient, token: str) -> int:
    return c.get("/api/v1/auth/me", headers=_bearer(token)).status_code


# ── Registry ──────────────────────────────────────────────────────────────────


def test_register_shows_key_once(admin, client):
    body = _register(admin, "Greenhouse-01", name="Greenhouse", description="Tomatoes")
    assert KEY.fullmatch(body["api_key"])
    assert body["key_prefix"] == body["api_key"][:17]
    assert (body["id"], body["name"], body["enabled"]) == ("greenhouse-01", "Greenhouse", True)
    assert body["last_seen_at"] is None

    # Readable afterwards, but never the key again.
    for c in (admin, client):
        got = c.get("/api/v1/devices/greenhouse-01").json()
        assert "api_key" not in got
        assert got["key_prefix"] == body["key_prefix"]
    with db.connect() as conn:
        stored = conn.execute("SELECT key_hash FROM devices").fetchone()[0]
    assert body["api_key"] not in stored and stored.startswith("$argon2id$")


@pytest.mark.parametrize(
    "device_id,status,code",
    [("greenhouse-01", 409, "device_exists"), ("x", 422, "validation_error")],
)
def test_register_rejects(admin, device_id, status, code):
    _register(admin)
    r = admin.post("/api/v1/devices", json={"id": device_id})
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)


def test_list_is_paginated(admin, client):
    for i in range(5):
        _register(admin, f"dev-{i}")
    page = client.get("/api/v1/devices", params={"limit": 2, "offset": 2}).json()
    assert [d["id"] for d in page["items"]] == ["dev-2", "dev-3"]
    assert (page["total"], page["limit"], page["offset"]) == (5, 2, 2)
    assert client.get("/api/v1/devices", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/devices", params={"limit": 201}).status_code == 422


def test_update(admin):
    _register(admin, name="old")
    r = admin.patch("/api/v1/devices/greenhouse-01", json={"name": "new"})
    assert (r.json()["name"], r.json()["description"]) == ("new", "")
    assert admin.patch("/api/v1/devices/greenhouse-01", json={}).status_code == 200


def test_unknown_device_404(admin):
    for name in ("nope", "Bad Name!"):
        assert admin.get(f"/api/v1/devices/{name}").status_code == 404
        assert admin.patch(f"/api/v1/devices/{name}", json={}).status_code == 404
        assert admin.delete(f"/api/v1/devices/{name}").status_code == 404
        assert admin.post(f"/api/v1/devices/{name}/key").status_code == 404


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/devices"),
        ("PATCH", "/api/v1/devices/greenhouse-01"),
        ("DELETE", "/api/v1/devices/greenhouse-01"),
        ("POST", "/api/v1/devices/greenhouse-01/key"),
    ],
)
def test_writes_need_admin(admin, client, anon, method, path):
    _register(admin)
    assert client.request(method, path, json={"id": "other"}).status_code == 403
    assert anon.request(method, path, json={"id": "other"}).status_code == 401


# ── Device sign-in ────────────────────────────────────────────────────────────


def test_device_signs_in_with_key(admin, anon):
    key = _register(admin)["api_key"]
    tokens = _device_token(anon, key)
    assert (tokens["device_id"], tokens["role"], tokens["expires_in"]) == (
        "greenhouse-01",
        "device",
        3600,
    )
    assert "refresh_token" not in tokens
    me = anon.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"])).json()
    assert me == {"username": "device:greenhouse-01", "role": "device", "auth": True}
    assert admin.get("/api/v1/devices/greenhouse-01").json()["last_seen_at"] is not None


@pytest.mark.parametrize(
    "key",
    [
        "p4n4_000000000000_" + "A" * 43,  # well-formed, unknown key id
        "not a key",
        "",
    ],
)
def test_bad_keys_rejected(admin, anon, key):
    real = _register(admin)["api_key"]
    r = anon.post("/api/v1/auth/token", json={"api_key": key})
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "Invalid API key."
    # Right key id, wrong secret.
    forged = real[:18] + ("B" if real[18] != "B" else "C") + real[19:]
    assert anon.post("/api/v1/auth/token", json={"api_key": forged}).status_code == 401


def test_token_body_must_be_one_kind(anon):
    r = anon.post("/api/v1/auth/token", json={"username": "a", "password": "b", "api_key": "c"})
    assert r.status_code == 422


def test_device_cannot_do_what_people_do(admin, anon, flat_project):
    token = _device_token(anon, _register(admin)["api_key"])["access_token"]
    for path in ("/api/v1/project", "/api/v1/devices", "/api/v1/users", "/api/v1/edge/metrics"):
        assert anon.get(path, headers=_bearer(token)).status_code == 403, path
    r = anon.post(
        "/api/v1/auth/password",
        json={"current_password": "x", "new_password": "y" * 12},
        headers=_bearer(token),
    )
    assert r.status_code == 403


def test_device_role_is_its_own(admin, anon):
    # Only devices pass require_role("device") (telemetry ingest, M5); people don't.
    app = FastAPI()

    @app.get("/ingest", dependencies=[Depends(auth.require_role("device"))])
    def ingest() -> dict:
        return {}

    c = TestClient(app)
    device = _device_token(anon, _register(admin)["api_key"])["access_token"]
    assert c.get("/ingest", headers=_bearer(device)).status_code == 200
    assert c.get("/ingest", headers=admin.headers).status_code == 403


def test_device_named_like_a_user_is_not_that_user(admin, anon):
    # The admin is "root"; a device called "root" must not get its rights.
    token = _device_token(anon, _register(admin, "root")["api_key"])["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == "device:root"
    assert anon.get("/api/v1/users", headers=_bearer(token)).status_code == 403


# ── Revocation ────────────────────────────────────────────────────────────────


def test_rotation_ends_old_key_and_tokens(admin, anon):
    old = _register(admin)["api_key"]
    old_token = _device_token(anon, old)["access_token"]

    r = admin.post("/api/v1/devices/greenhouse-01/key")
    assert r.status_code == 200
    new = r.json()["api_key"]
    assert KEY.fullmatch(new) and new != old
    assert r.json()["key_prefix"] == admin.get("/api/v1/devices/greenhouse-01").json()["key_prefix"]

    assert anon.post("/api/v1/auth/token", json={"api_key": old}).status_code == 401
    assert _me(anon, old_token) == 401
    assert _me(anon, _device_token(anon, new)["access_token"]) == 200


def test_disable_and_enable(admin, anon):
    key = _register(admin)["api_key"]
    token = _device_token(anon, key)["access_token"]
    assert admin.patch("/api/v1/devices/greenhouse-01", json={"enabled": False}).status_code == 200
    assert _me(anon, token) == 401
    assert anon.post("/api/v1/auth/token", json={"api_key": key}).status_code == 401

    admin.patch("/api/v1/devices/greenhouse-01", json={"enabled": True})
    assert _me(anon, token) == 200  # the same key generation, so the token is good again
    _device_token(anon, key)


def test_delete_ends_tokens(admin, anon):
    key = _register(admin)["api_key"]
    token = _device_token(anon, key)["access_token"]
    assert admin.delete("/api/v1/devices/greenhouse-01").status_code == 204
    assert _me(anon, token) == 401
    assert anon.post("/api/v1/auth/token", json={"api_key": key}).status_code == 401


def test_key_exchange_is_rate_limited(anon):
    for _ in range(10):
        anon.post("/api/v1/auth/token", json={"api_key": "guess"})
    assert anon.post("/api/v1/auth/token", json={"api_key": "guess"}).status_code == 429


def test_migration_adds_devices_to_existing_database(api_state):
    # A database from before M3 (schema version 1) gets the devices table on next open.
    import sqlite3

    path = api_state / db.DB_FILE
    conn = sqlite3.connect(path)
    conn.executescript(f"BEGIN; {db.MIGRATIONS[0]}; PRAGMA user_version = 1; COMMIT;")
    conn.close()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
        device, _ = devices.create(conn, "after-upgrade")
    assert device["id"] == "after-upgrade"
