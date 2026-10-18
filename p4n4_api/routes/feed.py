"""The Feed tab's items: a project's live events and alerts, through this API.

A project names its feed in .p4n4.json (``dashboard.feed``: an upstream ``url`` and,
optionally, the layer ``.env`` variable holding its token). This API reads it and posts
actions to it on the dashboard's behalf, so the upstream's credential stays server-side
and the role check happens here: everyone signed in reads the feed; acting on an item
(e.g. marking an alert attended) takes an operator.

The upstream speaks p4n4-dashboard's feed contract (dashboard/README.md#feed-contract):

    GET  <url>?limit=&before=     {"title", "items": [...], "counts", "next_before"}
    POST <url>/<id>/action        {"action", "by"}  → the item as it is now
    GET  <url>/<id>/image         the item's picture (an item with "image": true)

Items are passed through as they come (fields beyond the contract's included), except
that a normie's have no ``actions``, and a ``link`` that isn't http(s) is dropped.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from p4n4_api import audit, auth
from p4n4_api.auth import CurrentUser
from p4n4_api.deps import OptionalProject, layer_env
from p4n4_api.errors import ApiError

router = APIRouter(prefix="/feed", tags=["feed"])
operator_only = [Depends(auth.require_role("operator"))]

TIMEOUT_S = 10
# Swapped in by tests (httpx.MockTransport); None means real HTTP.
transport: httpx.AsyncBaseTransport | None = None
# What an item id may be: it goes into the upstream's URL. Never "." or "..", which the
# HTTP client resolves as path segments, sending the action (and token) elsewhere
ITEM_ID = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._:-]{1,128}$")
# What an item's picture may be, and how big
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class FeedAction(BaseModel):
    id: str
    label: str


class FeedLink(BaseModel):
    """A page about the item, opened in the browser (e.g. the project's own review page)."""

    label: str
    url: str


class FeedItem(BaseModel):
    """One item of the feed contract. Fields the upstream adds pass through untouched."""

    model_config = ConfigDict(extra="allow")

    id: str
    time: str | None = None
    # "alert" (something for a person to review) or "event" (something that happened)
    kind: str | None = None
    title: str
    subtitle: str | None = None
    # What a person should do about it, when there is something to do
    detail: str | None = None
    badge: str | None = None
    # "info", "review" or "neutral": how the dashboard colours it
    tone: str | None = None
    # An alert's review status, e.g. pending_review, attended, dismissed
    status: str | None = None
    actions: list[FeedAction] | None = None
    meta: dict[str, Any] | None = None
    synthetic: bool | None = None
    # A picture of it (the evidence), at GET /api/v1/feed/<id>/image
    image: bool | None = None
    link: FeedLink | None = None


class Feed(BaseModel):
    title: str | None = None
    items: list[FeedItem] = []
    counts: dict[str, Any] = {}
    # Pass as `before` for the next (older) page; null on the last one
    next_before: float | str | None = None
    # Whether this user may act on items (operators and admins)
    can_act: bool = False


class Action(BaseModel):
    action: str = Field(min_length=1, max_length=64)


class ActionResult(BaseModel):
    action: str
    # The item as the upstream reports it after the action
    item: dict[str, Any]


def _upstream(project) -> tuple[str, dict[str, str]]:
    """(the feed's URL, headers for it), or 404 when the project has no feed."""
    feed = ((project or (None, {}))[1].get("dashboard") or {}).get("feed")
    if not isinstance(feed, dict) or not isinstance(feed.get("url"), str):
        raise ApiError(404, "no_feed", "This project has no feed (.p4n4.json dashboard.feed).")
    headers = {}
    token = feed.get("token")
    if isinstance(token, dict):
        value = layer_env(project, str(token.get("layer"))).get(str(token.get("env")), "")
        if value:
            headers["Authorization"] = f"Bearer {value}"
    return feed["url"].rstrip("/"), headers


async def _call(method: str, url: str, headers: dict, **kwargs) -> dict:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise ApiError(502, "feed_unreachable", f"The feed is unreachable at {url}: {exc}") from exc
    if response.status_code == 404:
        raise ApiError(404, "feed_item_not_found", _message(response) or "Not found.")
    if response.status_code in (400, 409, 422):
        raise ApiError(400, "feed_refused", _message(response) or "The feed refused the request.")
    if response.status_code != 200:
        raise ApiError(502, "feed_failed", f"The feed answered {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise ApiError(502, "feed_failed", "The feed did not answer with JSON.") from exc


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return str(body.get("error") or body.get("detail") or "") if isinstance(body, dict) else ""


# Unset fields stay out, as the upstream left them out (a normie's items have no "actions")
@router.get("", response_model_exclude_none=True)
async def get_feed(
    user: CurrentUser,
    project: OptionalProject,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[float | None, Query()] = None,
) -> Feed:
    url, headers = _upstream(project)
    params = {"limit": limit, **({"before": before} if before is not None else {})}
    body = await _call("GET", url, headers, params=params)
    can_act = user.has_role("operator")
    items = [
        _safe_link(i)
        for i in body.get("items") or []
        if isinstance(i, dict) and "id" in i and "title" in i
    ]
    if not can_act:
        items = [{k: v for k, v in i.items() if k != "actions"} for i in items]
    return Feed(
        title=body.get("title"),
        items=items,
        counts=body.get("counts") or {},
        next_before=body.get("next_before"),
        can_act=can_act,
    )


def _safe_link(item: dict) -> dict:
    """The item without a link the dashboard shouldn't open (anything but an http(s) URL)."""
    link = item.get("link")
    if link is None:
        return item
    ok = (
        isinstance(link, dict)
        and isinstance(link.get("label"), str)
        and isinstance(link.get("url"), str)
        and re.match(r"^https?://", link["url"], re.IGNORECASE)
    )
    return item if ok else {k: v for k, v in item.items() if k != "link"}


def _item_id(item_id: str) -> str:
    if not ITEM_ID.match(item_id):
        raise ApiError(
            400, "bad_item_id", "Item ids are 1-128 letters, digits, '.', '_', ':', '-'."
        )
    return item_id


@router.get(
    "/{item_id}/image",
    response_class=Response,
    responses={200: {"content": {t: {} for t in IMAGE_TYPES}, "description": "The picture"}},
)
async def image(item_id: str, user: CurrentUser, project: OptionalProject) -> Response:
    """An item's picture (its evidence), for anyone signed in, with the feed's token."""
    url, headers = _upstream(project)
    target = f"{url}/{_item_id(item_id)}/image"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport) as client:
            response = await client.get(target, headers=headers)
    except httpx.HTTPError as exc:
        raise ApiError(502, "feed_unreachable", f"The feed is unreachable at {url}: {exc}") from exc
    if response.status_code == 404:
        raise ApiError(404, "feed_item_not_found", _message(response) or "No picture.")
    if response.status_code != 200:
        raise ApiError(502, "feed_failed", f"The feed answered {response.status_code}.")
    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind not in IMAGE_TYPES:
        raise ApiError(502, "feed_failed", f"The feed's picture is {kind or 'untyped'}, not one.")
    if len(response.content) > MAX_IMAGE_BYTES:
        raise ApiError(502, "feed_failed", "The feed's picture is over 8 MB.")
    return Response(
        response.content, media_type=kind, headers={"Cache-Control": "private, max-age=300"}
    )


@router.post("/{item_id}/action", dependencies=operator_only)
async def act(
    item_id: str, body: Action, user: CurrentUser, project: OptionalProject
) -> ActionResult:
    """Act on an item (operators): the upstream decides what the action means."""
    _item_id(item_id)
    url, headers = _upstream(project)
    result = await _call(
        "POST",
        f"{url}/{item_id}/action",
        headers,
        json={"action": body.action, "by": user.username},
    )
    audit.record(user.username, "feed.action", item_id, body.action)
    return ActionResult(action=body.action, item=result)
