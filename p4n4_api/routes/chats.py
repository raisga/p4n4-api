"""The project's messaging chats: which ones get its alerts and may talk to it (admins).

A project names its chats service in .p4n4.json (``dashboard.chats``: an upstream ``url``
and, optionally, the layer ``.env`` variable holding its token), e.g. a Telegram bot that
answers only the chats an admin linked. This API calls it on the dashboard's behalf, so
the upstream's credential stays server-side and only admins get here.

The upstream speaks p4n4-dashboard's chats contract (dashboard/README.md#chats-contract):

    GET    <url>          {"channels": [...], "chats": [...]}
    POST   <url>/codes    {"by"}  → a one-time code that links a chat, and its links
    DELETE <url>/<id>     unlinks a chat
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from p4n4_api import audit
from p4n4_api.auth import CurrentUser
from p4n4_api.deps import OptionalProject, layer_env
from p4n4_api.errors import ApiError

router = APIRouter(prefix="/chats", tags=["chats"])

TIMEOUT_S = 10
# Swapped in by tests (httpx.MockTransport); None means real HTTP.
transport: httpx.AsyncBaseTransport | None = None
# What a chat id may be: it goes into the upstream's URL. Never "." or ".."
CHAT_ID = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._:-]{1,128}$")


class ChatChannel(BaseModel):
    """A messaging channel the project has, e.g. its Telegram bot."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str | None = None
    # "live", "mock" (messages are logged, not sent) or "off"
    state: str | None = None
    # The bot's handle and address, e.g. "@store_bot" and https://t.me/store_bot
    bot: str | None = None
    url: str | None = None


class LinkedChat(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    channel: str
    # The group's title or the person's name
    name: str | None = None
    lang: str | None = None
    linked_at: str | None = None
    # Who made the code it was linked with
    linked_by: str | None = None
    # Alert kinds the chat has muted (e.g. "A2")
    muted: list[str] = []


class Chats(BaseModel):
    channels: list[ChatChannel] = []
    chats: list[LinkedChat] = []


class ChatLink(BaseModel):
    channel: str
    # Opens the chat with the code already in it (e.g. https://t.me/store_bot?start=CODE)
    url: str


class LinkCode(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    expires_at: str | None = None
    ttl_s: int | None = None
    # What to send the bot to link a chat by hand, e.g. "/start CODE"
    command: str | None = None
    links: list[ChatLink] = []


class Unlinked(BaseModel):
    id: str
    unlinked: bool


def _upstream(project) -> tuple[str, dict[str, str]]:
    """(the chats service's URL, headers for it), or 404 when the project has none."""
    chats = ((project or (None, {}))[1].get("dashboard") or {}).get("chats")
    if not isinstance(chats, dict) or not isinstance(chats.get("url"), str):
        raise ApiError(404, "no_chats", "This project has no chats (.p4n4.json dashboard.chats).")
    headers = {}
    token = chats.get("token")
    if isinstance(token, dict):
        value = layer_env(project, str(token.get("layer"))).get(str(token.get("env")), "")
        if value:
            headers["Authorization"] = f"Bearer {value}"
    return chats["url"].rstrip("/"), headers


async def _call(method: str, url: str, headers: dict, **kwargs) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise ApiError(
            502, "chats_unreachable", f"The chats are unreachable at {url}: {exc}"
        ) from exc
    if response.status_code == 404:
        raise ApiError(404, "chat_not_found", _message(response) or "Not found.")
    if response.status_code in (401, 403):
        raise ApiError(502, "chats_refused", "The chats service refused this API's token.")
    if response.status_code == 503:
        # The upstream says why it can't (e.g. it has no admin token set)
        raise ApiError(503, "chats_unavailable", _message(response) or "The chats are unavailable.")
    if response.status_code != 200:
        raise ApiError(502, "chats_failed", f"The chats service answered {response.status_code}.")
    try:
        body = response.json()
    except ValueError as exc:
        raise ApiError(502, "chats_failed", "The chats service did not answer with JSON.") from exc
    if not isinstance(body, dict):
        raise ApiError(502, "chats_failed", "The chats service did not answer with an object.")
    return body


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return str(body.get("error") or body.get("detail") or "") if isinstance(body, dict) else ""


@router.get("")
async def list_chats(project: OptionalProject) -> Chats:
    """The project's channels and the chats linked to them."""
    url, headers = _upstream(project)
    body = await _call("GET", url, headers)
    return Chats(
        channels=[c for c in body.get("channels") or [] if isinstance(c, dict) and "id" in c],
        chats=[
            c
            for c in body.get("chats") or []
            if isinstance(c, dict) and "id" in c and "channel" in c
        ],
    )


@router.post("/codes")
async def new_code(user: CurrentUser, project: OptionalProject) -> LinkCode:
    """A one-time code that links a chat: sent to the bot, or opened as one of its links."""
    url, headers = _upstream(project)
    body = await _call("POST", f"{url}/codes", headers, json={"by": user.username})
    if not isinstance(body.get("code"), str):
        raise ApiError(502, "chats_failed", "The chats service answered without a code.")
    audit.record(user.username, "chats.code", "", "")
    return LinkCode.model_validate(body)


@router.delete("/{chat_id}")
async def unlink(chat_id: str, user: CurrentUser, project: OptionalProject) -> Unlinked:
    """Unlinks a chat: it gets no more alerts, and the bot stops answering it."""
    if not CHAT_ID.match(chat_id):
        raise ApiError(
            400, "bad_chat_id", "Chat ids are 1-128 letters, digits, '.', '_', ':', '-'."
        )
    url, headers = _upstream(project)
    await _call("DELETE", f"{url}/{chat_id}", headers)
    audit.record(user.username, "chats.unlink", chat_id, "")
    return Unlinked(id=chat_id, unlinked=True)
