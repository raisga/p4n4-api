"""How p4n4-dashboard presents this deployment to each of its views.

Views: which tabs power users and normies see (after Home) and the order every view shows
them in. Kept here rather than in each browser, so an admin's choice applies on every
device. Everyone signed in reads them (the dashboard needs its own tabs); only admins change
them. A null field means "the dashboard's default" (its brand's), not "no tabs".

Which tabs a view shows is presentation, not access control: what's behind each tab is
checked by its own endpoints.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from p4n4_lib.manifest import DASHBOARD_TABS
from pydantic import AfterValidator, BaseModel, ValidationError

from p4n4_api import audit, auth, db, store
from p4n4_api.auth import CurrentUser

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
admin_only = [Depends(auth.require_role("admin"))]

VIEWS_KEY = "dashboard.views"


def _tabs(value: list[str]) -> list[str]:
    unknown = [t for t in value if t not in DASHBOARD_TABS]
    if unknown:
        raise ValueError(f"unknown tabs {unknown}; known: {', '.join(DASHBOARD_TABS)}")
    if len(set(value)) != len(value):
        raise ValueError("tabs are listed more than once")
    return value


Tabs = Annotated[list[str], AfterValidator(_tabs)]


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
