"""Tests for admin user management and self-service password change."""

from __future__ import annotations

import pytest

from p4n4_api import db, users
from tests.conftest import PASSWORD, login, make_user

NEW_PASSWORD = "a brand new password"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Who can manage users ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/users"),
        ("POST", "/api/v1/users"),
        ("GET", "/api/v1/users/ops"),
        ("PATCH", "/api/v1/users/ops"),
        ("DELETE", "/api/v1/users/ops"),
    ],
)
def test_users_endpoints_need_admin(anon, client, method, path):
    assert anon.request(method, path).status_code == 401
    assert client.request(method, path, json={}).status_code == 403


def test_auth_off_can_manage_users(anon, monkeypatch):
    monkeypatch.setenv("P4N4_API_AUTH", "off")
    r = anon.post("/api/v1/users", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 201


# ── CRUD ──────────────────────────────────────────────────────────────────────


def test_create_list_get(admin, anon):
    r = admin.post("/api/v1/users", json={"username": "Client-1", "password": PASSWORD})
    assert r.status_code == 201
    body = r.json()
    assert (body["username"], body["role"]) == ("client-1", "operator")
    assert body["created_at"]
    assert "password_hash" not in body

    assert [u["username"] for u in admin.get("/api/v1/users").json()] == ["client-1", "root"]
    assert admin.get("/api/v1/users/CLIENT-1").json() == body
    assert login(anon, "client-1")["role"] == "operator"


@pytest.mark.parametrize(
    "payload,status,message",
    [
        ({"username": "root", "password": PASSWORD}, 409, "already exists"),
        ({"username": "x", "password": PASSWORD}, 422, "Username"),
        ({"username": "alice", "password": "short"}, 422, "at least 10"),
    ],
)
def test_create_rejects(admin, payload, status, message):
    r = admin.post("/api/v1/users", json=payload)
    assert r.status_code == status
    assert message in r.json()["error"]["message"]


def test_create_rejects_unknown_role(admin):
    r = admin.post("/api/v1/users", json={"username": "a1", "password": PASSWORD, "role": "device"})
    assert r.status_code == 422


@pytest.mark.parametrize("name", ["nobody", "Bad Name!"])
def test_unknown_user_404(admin, name):
    assert admin.get(f"/api/v1/users/{name}").status_code == 404
    assert admin.patch(f"/api/v1/users/{name}", json={"role": "admin"}).status_code == 404
    assert admin.delete(f"/api/v1/users/{name}").status_code == 404


def test_patch_role(admin, anon):
    make_user("alice", "operator")
    token = login(anon, "alice")["access_token"]
    r = admin.patch("/api/v1/users/alice", json={"role": "admin"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    assert anon.get("/api/v1/auth/me", headers=_bearer(token)).json()["role"] == "admin"


def test_patch_password_signs_user_out(admin, anon):
    make_user("alice", "operator")
    tokens = login(anon, "alice")
    assert admin.patch("/api/v1/users/alice", json={"password": NEW_PASSWORD}).status_code == 200
    assert anon.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"])).status_code == 401
    r = anon.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    login(anon, "alice", NEW_PASSWORD)


def test_patch_is_all_or_nothing(admin, anon):
    # The role change is refused (last admin), so the password change is rolled back too.
    r = admin.patch("/api/v1/users/root", json={"password": NEW_PASSWORD, "role": "operator"})
    assert r.status_code == 409
    login(anon, "root")


def test_patch_rejects_short_password(admin):
    make_user("alice", "operator")
    r = admin.patch("/api/v1/users/alice", json={"password": "short"})
    assert r.status_code == 422


def test_delete(admin, anon):
    make_user("alice", "operator")
    tokens = login(anon, "alice")
    assert admin.delete("/api/v1/users/alice").status_code == 204
    assert admin.get("/api/v1/users/alice").status_code == 404
    assert anon.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"])).status_code == 401


# ── Last admin ────────────────────────────────────────────────────────────────


def test_last_admin_cannot_be_demoted_or_deleted(admin):
    r = admin.patch("/api/v1/users/root", json={"role": "operator"})
    assert r.status_code == 409
    assert "last admin" in r.json()["error"]["message"]
    assert admin.delete("/api/v1/users/root").status_code == 409
    assert admin.get("/api/v1/users/root").json()["role"] == "admin"


def test_admin_can_step_down_when_another_remains(admin):
    make_user("second", "admin")
    assert admin.patch("/api/v1/users/root", json={"role": "operator"}).status_code == 200
    # root is an operator now, so its own token can no longer manage users.
    assert admin.get("/api/v1/users").status_code == 403


def test_last_admin_guard_in_users_module():
    make_user("root", "admin")
    make_user("ops", "operator")
    with db.connect() as conn:
        with pytest.raises(users.LastAdminError):
            users.set_role(conn, "root", "operator", keep_admin=True)
        with pytest.raises(users.LastAdminError):
            users.delete(conn, "root", keep_admin=True)
        assert users.set_role(conn, "root", "admin", keep_admin=True)  # no-op stays allowed
        assert users.delete(conn, "ops", keep_admin=True)
        assert not users.delete(conn, "nobody", keep_admin=True)
        # Without the guard (the CLI's recovery path) it's allowed.
        assert users.set_role(conn, "root", "operator")


# ── Self-service password change ──────────────────────────────────────────────


def _change(c, token, current=PASSWORD, new=NEW_PASSWORD):
    return c.post(
        "/api/v1/auth/password",
        json={"current_password": current, "new_password": new},
        headers=_bearer(token),
    )


def test_change_own_password(anon):
    make_user("alice", "operator")
    this_device, other_device = login(anon, "alice"), login(anon, "alice")

    r = _change(anon, this_device["access_token"])
    assert r.status_code == 200
    fresh = r.json()
    assert (fresh["username"], fresh["role"]) == ("alice", "operator")

    # The new pair works; every earlier token, on this device or another, doesn't.
    assert anon.get("/api/v1/auth/me", headers=_bearer(fresh["access_token"])).status_code == 200
    for old in (this_device, other_device):
        assert anon.get("/api/v1/auth/me", headers=_bearer(old["access_token"])).status_code == 401
        r = anon.post("/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]})
        assert r.status_code == 401
    r = anon.post("/api/v1/auth/refresh", json={"refresh_token": fresh["refresh_token"]})
    assert r.status_code == 200
    login(anon, "alice", NEW_PASSWORD)


def test_change_password_wrong_current(anon):
    make_user("alice", "operator")
    token = login(anon, "alice")["access_token"]
    r = _change(anon, token, current="not my password")
    assert r.status_code == 403
    assert anon.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 200
    login(anon, "alice")


def test_change_password_too_short(anon):
    make_user("alice", "operator")
    token = login(anon, "alice")["access_token"]
    r = _change(anon, token, new="short")
    assert r.status_code == 422
    login(anon, "alice")


def test_change_password_needs_sign_in(anon, monkeypatch):
    r = anon.post(
        "/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert r.status_code == 401
    monkeypatch.setenv("P4N4_API_AUTH", "off")
    r = anon.post(
        "/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert r.status_code == 400


def test_change_password_rate_limited(anon):
    make_user("alice", "operator")
    token = login(anon, "alice")["access_token"]  # uses 1 of the 10
    for _ in range(9):
        assert _change(anon, token, current="guess guess guess").status_code == 403
    assert _change(anon, token, current="guess guess guess").status_code == 429
