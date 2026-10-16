"""How p4n4-dashboard presents this deployment to each of its views.

Views: which tabs power users and normies see (after Home) and the order every view shows
them in. Kept here rather than in each browser, so an admin's choice applies on every
device. Everyone signed in reads them (the dashboard needs its own tabs); only admins change
them. A null field means "the dashboard's default" (its brand's), not "no tabs".

View settings: what else each of those two views shows: the tab it opens on, Home's cards,
its Grafana page, its cameras and their overlay, and which alerts pop up. Also presentation:
p4n4-api still decides what each role may read.

Banner: a message an admin puts above every view (planned maintenance, a known outage),
until they clear it or its end time passes.

Which tabs a view shows is presentation, not access control: what's behind each tab is
checked by its own endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from p4n4_lib.manifest import DASHBOARD_TABS
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, ValidationError

from p4n4_api import audit, auth, db, store
from p4n4_api.auth import CurrentUser
from p4n4_api.errors import ApiError

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
admin_only = [Depends(auth.require_role("admin"))]

VIEWS_KEY = "dashboard.views"
VIEW_SETTINGS_KEY = "dashboard.view_settings"
BANNER_KEY = "dashboard.banner"


def _tabs(value: list[str]) -> list[str]:
    unknown = [t for t in value if t not in DASHBOARD_TABS]
    if unknown:
        raise ValueError(f"unknown tabs {unknown}; known: {', '.join(DASHBOARD_TABS)}")
    if len(set(value)) != len(value):
        raise ValueError("tabs are listed more than once")
    return value


Tabs = Annotated[list[str], AfterValidator(_tabs)]

# Home's cards: the "is everything OK" summary, the device's readings, the project's HOME.md
HOME_CARDS = ("status", "device", "page")


def _start_tab(value: str) -> str:
    if value != "home" and value not in DASHBOARD_TABS:
        raise ValueError(f"unknown tab {value!r}; known: home, {', '.join(DASHBOARD_TABS)}")
    return value


def _home_cards(value: list[str]) -> list[str]:
    unknown = [c for c in value if c not in HOME_CARDS]
    if unknown:
        raise ValueError(f"unknown cards {unknown}; known: {', '.join(HOME_CARDS)}")
    if len(set(value)) != len(value):
        raise ValueError("cards are listed more than once")
    return value


def _grafana_path(value: str) -> str:
    value = value.strip()
    # A page on the deployment's Grafana, never another site
    if not value.startswith("/") or value.startswith("//") or any(c.isspace() for c in value):
        raise ValueError("must be a path on Grafana starting with '/', e.g. /d/abc/overview")
    return value


def _cameras(value: list[str]) -> list[str]:
    if any(not c or len(c) > 64 or not c.isprintable() for c in value):
        raise ValueError("camera ids are 1-64 printable characters")
    if len(set(value)) != len(value):
        raise ValueError("cameras are listed more than once")
    return value


class ViewSettings(BaseModel):
    """One view's settings. Null means the dashboard's default."""

    # The tab it opens on: "home" or a dashboard tab
    start_tab: Annotated[str, AfterValidator(_start_tab)] | None = None
    # Home's cards it shows (all by default)
    home_cards: Annotated[list[str], AfterValidator(_home_cards)] | None = None
    # Its Grafana page, instead of the deployment's
    grafana_path: Annotated[str, Field(max_length=512), AfterValidator(_grafana_path)] | None = None
    # The deployment's cameras it shows, by id (all by default)
    cameras: Annotated[list[str], Field(max_length=64), AfterValidator(_cameras)] | None = None
    # Whether the vision overlay is drawn over its cameras (on by default)
    camera_overlay: bool | None = None
    # Which alerts pop up: all of them, only those that ask for review, or none
    alerts: Literal["all", "review", "off"] | None = None
    # Whether an alert that asks for review chimes (on by default)
    alert_sound: bool | None = None


class ViewSettingsAll(BaseModel):
    """Admins always get the defaults, and every tab."""

    power: ViewSettings = Field(default_factory=ViewSettings)
    normie: ViewSettings = Field(default_factory=ViewSettings)


class Views(BaseModel):
    # Tabs after Home, in the order every view shows them; tabs left out follow in the
    # dashboard's default order
    tab_order: Tabs | None = None
    # Tabs each view shows (admins always see every tab)
    power_tabs: Tabs | None = None
    normie_tabs: Tabs | None = None


@router.get("/views")
def get_views() -> Views:
    with db.connect() as conn:
        try:
            return Views.model_validate(store.get(conn, VIEWS_KEY) or {})
        except ValidationError:
            return Views()  # hand-edited or from a newer version: the dashboard's defaults


@router.put("/views", dependencies=admin_only)
def set_views(body: Views, actor: CurrentUser) -> Views:
    """Replace the views (admins only). Omitted or null fields go back to the defaults."""
    with db.connect() as conn:
        store.put(conn, VIEWS_KEY, body.model_dump(), actor.username)
        changes = [
            f"{name} {','.join(value) if value is not None else 'default'}"
            for name, value in body.model_dump().items()
        ]
        audit.record(actor.username, "dashboard.views", "", "; ".join(changes), conn)
    return body


@router.get("/view-settings")
def get_view_settings() -> ViewSettingsAll:
    with db.connect() as conn:
        try:
            return ViewSettingsAll.model_validate(store.get(conn, VIEW_SETTINGS_KEY) or {})
        except ValidationError:
            return ViewSettingsAll()  # hand-edited or from a newer version: the defaults


def _describe(settings: ViewSettings) -> str:
    def value(v: object) -> str:
        if isinstance(v, bool):
            return "on" if v else "off"
        return ",".join(v) if isinstance(v, list) else str(v)

    fields = [f"{k} {value(v)}" for k, v in settings.model_dump().items() if v is not None]
    return ", ".join(fields) or "defaults"


@router.put("/view-settings", dependencies=admin_only)
def set_view_settings(body: ViewSettingsAll, actor: CurrentUser) -> ViewSettingsAll:
    """Replace both views' settings (admins only). Omitted or null fields are the defaults."""
    with db.connect() as conn:
        store.put(conn, VIEW_SETTINGS_KEY, body.model_dump(), actor.username)
        outcome = f"power: {_describe(body.power)}; normie: {_describe(body.normie)}"
        audit.record(actor.username, "dashboard.view_settings", "", outcome, conn)
    return body


class Banner(BaseModel):
    """Shown above every view. Empty text means no banner."""

    text: Annotated[str, AfterValidator(str.strip)] = Field("", max_length=280)
    tone: Literal["info", "warning"] = "info"
    # When it stops showing by itself; null keeps it until an admin clears it
    until: AwareDatetime | None = None

    def expired(self) -> bool:
        return self.until is not None and self.until <= datetime.now(UTC)


@router.get("/banner")
def get_banner() -> Banner:
    with db.connect() as conn:
        try:
            banner = Banner.model_validate(store.get(conn, BANNER_KEY) or {})
        except ValidationError:
            return Banner()
    return Banner() if banner.expired() else banner


@router.put("/banner", dependencies=admin_only)
def set_banner(body: Banner, actor: CurrentUser) -> Banner:
    """Show a banner (admins only), or clear it with empty text."""
    if body.text and body.expired():
        raise ApiError(422, "validation_error", "The banner's end time has already passed.")
    banner = body if body.text else Banner()
    with db.connect() as conn:
        store.put(conn, BANNER_KEY, banner.model_dump(mode="json"), actor.username)
        until = f" until {banner.until.isoformat()}" if banner.until else ""
        outcome = f"{banner.tone}{until}: {banner.text}" if banner.text else "cleared"
        audit.record(actor.username, "dashboard.banner", "", outcome, conn)
    return banner
