"""Tests for the dashboard's views: which tabs each view shows, kept per deployment."""

from __future__ import annotations

import pytest

from p4n4_api import db, store
from p4n4_api.routes.dashboard import VIEWS_KEY

DEFAULTS = {"tab_order": None, "power_tabs": None, "normie_tabs": None}
VIEWS = {
    "tab_order": ["edge", "agent", "grafana"],
    "power_tabs": ["services", "edge", "agent"],
    "normie_tabs": ["agent", "grafana"],
}


def test_unset_views_mean_the_dashboard_defaults(normie):
    r = normie.get("/api/v1/dashboard/views")
    assert r.status_code == 200
    assert r.json() == DEFAULTS


def test_admins_set_views_for_every_device(admin, normie, client):
    r = admin.put("/api/v1/dashboard/views", json=VIEWS)
    assert r.status_code == 200
    assert r.json() == VIEWS
    # Read back by everyone signed in, on any device
    assert normie.get("/api/v1/dashboard/views").json() == VIEWS
    assert client.get("/api/v1/dashboard/views").json() == VIEWS
    entry = admin.get("/api/v1/audit").json()["items"][0]
    assert (entry["actor"], entry["action"]) == ("root", "dashboard.views")
    assert entry["outcome"] == (
        "tab_order edge,agent,grafana; power_tabs services,edge,agent; normie_tabs agent,grafana"
    )


def test_omitted_fields_go_back_to_the_defaults(admin):
    admin.put("/api/v1/dashboard/views", json=VIEWS)
    r = admin.put("/api/v1/dashboard/views", json={"normie_tabs": []})
    assert r.json() == {**DEFAULTS, "normie_tabs": []}, "an empty list hides every tab but Home"


@pytest.mark.parametrize("who", ["normie", "client"])
def test_only_admins_change_views(request, who):
    c = request.getfixturevalue(who)
    r = c.put("/api/v1/dashboard/views", json=VIEWS)
    assert r.status_code == 403
    assert c.get("/api/v1/dashboard/views").json() == DEFAULTS


def test_views_need_sign_in(anon):
    assert anon.get("/api/v1/dashboard/views").status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"normie_tabs": ["clients"]},  # admin-only, never configurable
        {"tab_order": ["edge", "edge"]},
        {"power_tabs": "services"},
    ],
)
def test_views_are_validated(admin, body):
    assert admin.put("/api/v1/dashboard/views", json=body).status_code == 422


def test_a_broken_stored_value_falls_back_to_the_defaults(normie):
    with db.connect() as conn:
        store.put(conn, VIEWS_KEY, {"normie_tabs": ["nope"]}, "hand-edit")
    assert normie.get("/api/v1/dashboard/views").json() == DEFAULTS
