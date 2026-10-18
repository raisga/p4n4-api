"""The Feed tab's proxy: GET /api/v1/feed, POST /api/v1/feed/{id}/action, GET .../{id}/image."""

from __future__ import annotations

import json

import httpx
import pytest
from p4n4_lib import env as envutil
from p4n4_lib import manifest as mf

from p4n4_api.routes import feed as feed_route

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
ACTIONS = [{"id": "attended", "label": "Atender"}, {"id": "dismissed", "label": "Descartar"}]
FEED = {
    "title": "Actividad en vivo",
    "items": [
        {
            "id": "ALT-20261003-000001",
            "kind": "alert",
            "title": "Asistencia en probador",
            "status": "pending_review",
            "actions": ACTIONS,
            "meta": {"zone_id": "b-fitting"},
            "image": True,
            "link": {"label": "Revisar", "url": "https://review.example/#ALT-20261003-000001"},
        },
        {"id": "EVT-20261003-000007", "kind": "event", "title": "Se la prueba", "extra": 1},
        {
            "id": "EVT-20261003-000008",
            "kind": "event",
            "title": "Enlace raro",
            "link": {"label": "x", "url": "javascript:alert(1)"},
        },
    ],
    "counts": {"pending": 1},
    "next_before": 1791074857.9,
}


@pytest.fixture()
def feed_project(multi_project):
    """The iot+ai project with a feed whose token is in iot/.env."""
    path = multi_project / mf.MANIFEST_FILE
    data = mf.load(path)
    data["dashboard"] = {
        "tabs": ["feed"],
        "feed": {
            "url": "http://ingest.test/api/v1/feed",
            "token": {"layer": "iot", "env": "INGEST_READ_TOKEN"},
        },
    }
    mf.save(path, data)
    env = envutil.load(multi_project / "iot/.env")
    envutil.write(multi_project / "iot/.env", {**env, "INGEST_READ_TOKEN": "read-secret"})
    return multi_project


@pytest.fixture()
def upstream(monkeypatch):
    """The feed's upstream: answers FEED, records what it was asked."""

    class Fake:
        requests: list[httpx.Request] = []
        status = 200
        image_type = "image/jpeg"

        @staticmethod
        def handler(request: httpx.Request) -> httpx.Response:
            Fake.requests.append(request)
            if Fake.status != 200:
                return httpx.Response(Fake.status, json={"error": "no alert ALT-x in store s"})
            if request.url.path.endswith("/image"):
                return httpx.Response(200, content=JPEG, headers={"Content-Type": Fake.image_type})
            if request.method == "POST":
                body = json.loads(request.content)
                item = {
                    "alert_id": "ALT-20261003-000001",
                    "status": body["action"],
                    "by": body["by"],
                }
                return httpx.Response(200, json=item)
            return httpx.Response(200, json=FEED)

    Fake.requests = []
    Fake.status, Fake.image_type = 200, "image/jpeg"
    monkeypatch.setattr(feed_route, "transport", httpx.MockTransport(Fake.handler))
    return Fake


def test_reads_the_feed_with_the_projects_token(client, feed_project, upstream):
    r = client.get("/api/v1/feed", params={"limit": 20, "before": 1791074857.9})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"][:2] == [{**FEED["items"][0]}, FEED["items"][1]]
    assert body["counts"] == {"pending": 1} and body["can_act"] is True
    sent = upstream.requests[0]
    assert sent.headers["Authorization"] == "Bearer read-secret"
    assert sent.url.params["limit"] == "20" and sent.url.params["before"] == "1791074857.9"


def test_the_token_never_reaches_the_browser(client, feed_project, upstream):
    assert "read-secret" not in client.get("/api/v1/feed").text


def test_a_normie_reads_without_actions(normie, feed_project, upstream):
    body = normie.get("/api/v1/feed").json()
    assert body["can_act"] is False
    assert all("actions" not in item for item in body["items"])


def test_a_normie_cannot_act(normie, feed_project, upstream):
    r = normie.post("/api/v1/feed/ALT-20261003-000001/action", json={"action": "attended"})
    assert r.status_code == 403
    assert upstream.requests == []


def test_an_operator_acts_as_themselves(client, feed_project, upstream):
    r = client.post("/api/v1/feed/ALT-20261003-000001/action", json={"action": "attended"})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "action": "attended",
        "item": {"alert_id": "ALT-20261003-000001", "status": "attended", "by": "ops"},
    }
    sent = upstream.requests[0]
    assert sent.url.path == "/api/v1/feed/ALT-20261003-000001/action"
    assert json.loads(sent.content) == {"action": "attended", "by": "ops"}
    assert sent.headers["Authorization"] == "Bearer read-secret"


def test_an_item_id_cannot_climb_the_upstream_path(client, feed_project, upstream):
    r = client.post("/api/v1/feed/..%2F..%2Fadmin/action", json={"action": "attended"})
    assert r.status_code in (400, 404)
    assert upstream.requests == []


@pytest.mark.parametrize("item_id", ["%2E%2E", "%2E"])
def test_a_dot_segment_is_not_an_item_id(client, feed_project, upstream, item_id):
    # The HTTP client would resolve <url>/../action to the upstream's parent path
    r = client.post(f"/api/v1/feed/{item_id}/action", json={"action": "attended"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_item_id"
    assert upstream.requests == []


def test_no_feed_configured_is_a_404(client, multi_project, upstream):
    r = client.get("/api/v1/feed")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_feed"


def test_an_unknown_item_is_a_404(client, feed_project, upstream):
    upstream.status = 404
    r = client.post("/api/v1/feed/ALT-x/action", json={"action": "attended"})
    assert r.status_code == 404 and "no alert" in r.json()["error"]["message"]


def test_an_unreachable_feed_is_a_502(client, feed_project, monkeypatch):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(feed_route, "transport", httpx.MockTransport(down))
    r = client.get("/api/v1/feed")
    assert r.status_code == 502 and r.json()["error"]["code"] == "feed_unreachable"


def test_signed_out_is_refused(anon, feed_project, upstream):
    assert anon.get("/api/v1/feed").status_code == 401


def test_a_link_that_isnt_http_is_dropped(client, feed_project, upstream):
    items = client.get("/api/v1/feed").json()["items"]
    assert items[0]["link"]["url"].startswith("https://")
    assert items[2]["title"] == "Enlace raro" and "link" not in items[2]


def test_an_items_picture_with_the_projects_token(normie, feed_project, upstream):
    r = normie.get("/api/v1/feed/ALT-20261003-000001/image")
    assert r.status_code == 200, r.text
    assert r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert "private" in r.headers["cache-control"]
    sent = upstream.requests[0]
    assert sent.url.path == "/api/v1/feed/ALT-20261003-000001/image"
    assert sent.headers["Authorization"] == "Bearer read-secret"


def test_a_picture_that_isnt_an_image_is_refused(client, feed_project, upstream):
    upstream.image_type = "text/html"
    r = client.get("/api/v1/feed/ALT-20261003-000001/image")
    assert r.status_code == 502 and r.json()["error"]["code"] == "feed_failed"


def test_no_picture_is_a_404(client, feed_project, upstream):
    upstream.status = 404
    r = client.get("/api/v1/feed/ALT-20261003-000001/image")
    assert r.status_code == 404 and r.json()["error"]["code"] == "feed_item_not_found"


def test_a_picture_needs_a_good_item_id_and_a_session(client, anon, feed_project, upstream):
    assert client.get("/api/v1/feed/%2E%2E/image").status_code == 400
    assert anon.get("/api/v1/feed/ALT-20261003-000001/image").status_code == 401
    assert upstream.requests == []
