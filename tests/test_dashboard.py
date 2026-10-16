"""Tests for the dashboard's views: which tabs each view shows, kept per deployment."""

from __future__ import annotations

import pytest

from p4n4_api import db, store
from p4n4_api.routes.dashboard import BANNER_KEY, VIEW_SETTINGS_KEY, VIEWS_KEY

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


def test_the_feed_tab_can_be_ordered(admin):
    # The dashboard sends "feed" in every order once the project serves it (p4n4-lib 0.2.1)
    order = ["feed", "services", "edge", "agent", "grafana", "video"]
    r = admin.put("/api/v1/dashboard/views", json={"tab_order": order})
    assert r.status_code == 200, r.text
    assert r.json()["tab_order"] == order


def test_a_broken_stored_value_falls_back_to_the_defaults(normie):
    with db.connect() as conn:
        store.put(conn, VIEWS_KEY, {"normie_tabs": ["nope"]}, "hand-edit")
    assert normie.get("/api/v1/dashboard/views").json() == DEFAULTS


# ── Banner ────────────────────────────────────────────────────────────────────

NO_BANNER = {"text": "", "tone": "info", "until": None}


def test_no_banner_by_default(normie):
    assert normie.get("/api/v1/dashboard/banner").json() == NO_BANNER


def test_admins_put_a_banner_above_every_view(admin, normie):
    body = {"text": "  Maintenance 3–4 pm  ", "tone": "warning", "until": "2099-01-01T16:00:00Z"}
    r = admin.put("/api/v1/dashboard/banner", json=body)
    assert r.status_code == 200
    banner = {"text": "Maintenance 3–4 pm", "tone": "warning", "until": "2099-01-01T16:00:00Z"}
    assert r.json() == banner
    assert normie.get("/api/v1/dashboard/banner").json() == banner
    entry = admin.get("/api/v1/audit").json()["items"][0]
    assert entry["action"] == "dashboard.banner"
    assert entry["outcome"] == "warning until 2099-01-01T16:00:00+00:00: Maintenance 3–4 pm"

    assert admin.put("/api/v1/dashboard/banner", json={"text": " "}).json() == NO_BANNER
    assert normie.get("/api/v1/dashboard/banner").json() == NO_BANNER
    assert admin.get("/api/v1/audit").json()["items"][0]["outcome"] == "cleared"


def test_a_banner_goes_by_itself_when_its_time_passes(admin, normie):
    with db.connect() as conn:
        stored = {"text": "Done", "tone": "info", "until": "2020-01-01T00:00:00Z"}
        store.put(conn, BANNER_KEY, stored, "root")
    assert normie.get("/api/v1/dashboard/banner").json() == NO_BANNER


@pytest.mark.parametrize(
    "body",
    [
        {"text": "Late", "until": "2020-01-01T00:00:00Z"},  # already over
        {"text": "No zone", "until": "2099-01-01T16:00:00"},
        {"text": "x" * 281},
        {"text": "Odd", "tone": "party"},
    ],
)
def test_banners_are_validated(admin, body):
    assert admin.put("/api/v1/dashboard/banner", json=body).status_code == 422


@pytest.mark.parametrize("who", ["normie", "client"])
def test_only_admins_set_the_banner(request, who):
    c = request.getfixturevalue(who)
    assert c.put("/api/v1/dashboard/banner", json={"text": "Hi"}).status_code == 403


# ── View settings ─────────────────────────────────────────────────────────────

VIEW_DEFAULTS = {
    "start_tab": None,
    "home_cards": None,
    "grafana_path": None,
    "cameras": None,
    "camera_overlay": None,
    "alerts": None,
    "alert_sound": None,
}
NO_VIEW_SETTINGS = {"power": VIEW_DEFAULTS, "normie": VIEW_DEFAULTS}


def test_unset_view_settings_are_the_defaults(normie):
    r = normie.get("/api/v1/dashboard/view-settings")
    assert r.status_code == 200
    assert r.json() == NO_VIEW_SETTINGS


def test_admins_set_each_views_settings_for_every_device(admin, normie):
    normie_view = {
        "start_tab": "agent",
        "home_cards": ["status", "page"],
        "grafana_path": " /d/overview/simple?orgId=1 ",
        "cameras": ["front-door", "cam-salon-01"],
        "camera_overlay": False,
        "alerts": "review",
        "alert_sound": False,
    }
    r = admin.put("/api/v1/dashboard/view-settings", json={"normie": normie_view})
    assert r.status_code == 200, r.text
    expected = {
        "power": VIEW_DEFAULTS,
        "normie": {**normie_view, "grafana_path": "/d/overview/simple?orgId=1"},
    }
    assert r.json() == expected
    assert normie.get("/api/v1/dashboard/view-settings").json() == expected
    entry = admin.get("/api/v1/audit").json()["items"][0]
    assert entry["action"] == "dashboard.view_settings"
    assert entry["outcome"] == (
        "power: defaults; normie: start_tab agent, home_cards status,page, "
        "grafana_path /d/overview/simple?orgId=1, cameras front-door,cam-salon-01, "
        "camera_overlay off, alerts review, alert_sound off"
    )

    # A PUT replaces both views: what's left out goes back to the defaults
    r = admin.put("/api/v1/dashboard/view-settings", json={"power": {"start_tab": "home"}})
    assert r.json() == {"power": {**VIEW_DEFAULTS, "start_tab": "home"}, "normie": VIEW_DEFAULTS}


@pytest.mark.parametrize(
    "view",
    [
        {"start_tab": "clients"},  # admins only
        {"start_tab": "nope"},
        {"home_cards": ["status", "status"]},
        {"home_cards": ["weather"]},
        {"grafana_path": "https://elsewhere.example/d/x"},
        {"grafana_path": "//elsewhere.example/d/x"},
        {"grafana_path": "/d/a b"},
        {"cameras": ["a", "a"]},
        {"cameras": [""]},
        {"alerts": "some"},
    ],
)
def test_view_settings_are_validated(admin, view):
    assert admin.put("/api/v1/dashboard/view-settings", json={"normie": view}).status_code == 422


@pytest.mark.parametrize("who", ["normie", "client"])
def test_only_admins_change_view_settings(request, who):
    c = request.getfixturevalue(who)
    r = c.put("/api/v1/dashboard/view-settings", json={"normie": {"alerts": "off"}})
    assert r.status_code == 403


def test_a_broken_stored_view_settings_falls_back_to_the_defaults(normie):
    with db.connect() as conn:
        store.put(conn, VIEW_SETTINGS_KEY, {"normie": {"start_tab": "nope"}}, "hand-edit")
    assert normie.get("/api/v1/dashboard/view-settings").json() == NO_VIEW_SETTINGS
