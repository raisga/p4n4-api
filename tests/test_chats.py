"""The admins' chats proxy: GET /api/v1/chats, POST …/codes and DELETE …/{id}."""

from __future__ import annotations

import json

import httpx
import pytest
from p4n4_lib import env as envutil
from p4n4_lib import manifest as mf

from p4n4_api.routes import chats as chats_route

CHATS = {
    "channels": [
        {
            "id": "telegram",
            "name": "Telegram",
            "state": "live",
            "mode": "polling",
            "bot": "@store_bot",
            "url": "https://t.me/store_bot",
        }
    ],
    "chats": [
        {
            "id": "telegram:-1001",
            "channel": "telegram",
            "name": "Floor staff",
            "lang": "es",
            "linked_at": "2026-10-15T12:00:00+00:00",
            "linked_by": "root",
            "muted": ["A2"],
        },
        {"name": "no id: dropped"},
    ],
}
CODE = {
    "code": "K7QF3MXP",
    "expires_at": "2026-10-15T12:15:00+00:00",
    "ttl_s": 900,
    "command": "/start K7QF3MXP",
    "links": [{"channel": "telegram", "url": "https://t.me/store_bot?start=K7QF3MXP"}],
}


@pytest.fixture()
def chats_project(multi_project):
    """The iot+ai project with chats whose token is in ai/.env."""
    path = multi_project / mf.MANIFEST_FILE
    data = mf.load(path)
    data["dashboard"] = {
        "chats": {
            "url": "http://channels.test/api/v1/chats",
            "token": {"layer": "ai", "env": "CHANNELS_ADMIN_TOKEN"},
        },
    }
    mf.save(path, data)
    env = envutil.load(multi_project / "ai/.env")
    envutil.write(multi_project / "ai/.env", {**env, "CHANNELS_ADMIN_TOKEN": "admin-secret"})
    return multi_project


@pytest.fixture()
def upstream(monkeypatch):
    """The chats service: answers CHATS and CODE, records what it was asked."""

    class Fake:
        requests: list[httpx.Request] = []
        status = 200
        body: object = None

        @staticmethod
        def handler(request: httpx.Request) -> httpx.Response:
            Fake.requests.append(request)
            if Fake.status != 200:
                return httpx.Response(Fake.status, json=Fake.body or {"error": "nope"})
            if request.method == "POST":
                return httpx.Response(200, json=CODE)
            if request.method == "DELETE":
                return httpx.Response(200, json={"id": "telegram:-1001", "unlinked": True})
            return httpx.Response(200, json=CHATS)

    Fake.requests, Fake.status, Fake.body = [], 200, None
    monkeypatch.setattr(chats_route, "transport", httpx.MockTransport(Fake.handler))
    return Fake


def test_an_admin_lists_the_chats_with_the_projects_token(admin, chats_project, upstream):
    r = admin.get("/api/v1/chats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channels"] == CHATS["channels"]
    assert body["chats"] == [CHATS["chats"][0]]
    sent = upstream.requests[0]
    assert sent.headers["Authorization"] == "Bearer admin-secret"
    assert str(sent.url) == "http://channels.test/api/v1/chats"


def test_the_token_never_reaches_the_browser(admin, chats_project, upstream):
    assert "admin-secret" not in admin.get("/api/v1/chats").text
    assert "admin-secret" not in admin.post("/api/v1/chats/codes").text


@pytest.mark.parametrize("who", ["client", "normie"])
def test_only_admins(request, who, chats_project, upstream):
    c = request.getfixturevalue(who)
    assert c.get("/api/v1/chats").status_code == 403
    assert c.post("/api/v1/chats/codes").status_code == 403
    assert c.delete("/api/v1/chats/telegram:-1001").status_code == 403
    assert upstream.requests == []


def test_signed_out_is_refused(anon, chats_project, upstream):
    assert anon.get("/api/v1/chats").status_code == 401


def test_a_code_is_made_by_the_admin(admin, chats_project, upstream):
    r = admin.post("/api/v1/chats/codes")
    assert r.status_code == 200, r.text
    assert r.json() == CODE
    sent = upstream.requests[0]
    assert sent.url.path == "/api/v1/chats/codes"
    assert json.loads(sent.content) == {"by": "root"}


def test_an_admin_unlinks_a_chat(admin, chats_project, upstream):
    r = admin.delete("/api/v1/chats/telegram:-1001")
    assert r.status_code == 200, r.text
    assert r.json() == {"id": "telegram:-1001", "unlinked": True}
    sent = upstream.requests[0]
    assert sent.method == "DELETE" and sent.url.path == "/api/v1/chats/telegram:-1001"


@pytest.mark.parametrize("chat_id", ["%2E%2E", "%2E", "a%20b"])
def test_a_chat_id_cannot_climb_the_upstream_path(admin, chats_project, upstream, chat_id):
    r = admin.delete(f"/api/v1/chats/{chat_id}")
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_chat_id"
    assert upstream.requests == []


def test_an_unknown_chat_is_a_404(admin, chats_project, upstream):
    upstream.status, upstream.body = 404, {"error": "no linked chat telegram:9"}
    r = admin.delete("/api/v1/chats/telegram:9")
    assert r.status_code == 404 and "no linked chat" in r.json()["error"]["message"]


def test_no_chats_configured_is_a_404(admin, multi_project, upstream):
    r = admin.get("/api/v1/chats")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_chats"


def test_a_service_without_its_token_says_so(admin, chats_project, upstream):
    upstream.status = 503
    upstream.body = {"error": "CHANNELS_ADMIN_TOKEN is not set: chats can't be managed"}
    r = admin.get("/api/v1/chats")
    assert r.status_code == 503
    error = r.json()["error"]
    assert error["code"] == "chats_unavailable"
    assert error["message"] == "CHANNELS_ADMIN_TOKEN is not set: chats can't be managed"


def test_a_refused_token_is_a_502(admin, chats_project, upstream):
    upstream.status = 401
    r = admin.get("/api/v1/chats")
    assert r.status_code == 502 and r.json()["error"]["code"] == "chats_refused"


def test_unreachable_chats_are_a_502(admin, chats_project, monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(chats_route, "transport", httpx.MockTransport(down))
    r = admin.get("/api/v1/chats")
    assert r.status_code == 502 and r.json()["error"]["code"] == "chats_unreachable"
