"""The AI stack's services over HTTP: Ollama (local models) and Letta (agents with memory).

Ollama is stateless: a chat sends the whole conversation, and replies stream as NDJSON
chunks (`{"message": {"content": ...}, "done": false}`), which the API passes through
unchanged so clients parse them as they would Ollama's own. Letta keeps each agent's
conversation itself: a chat sends only the new message. Its password stays on the server.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from p4n4_api.config import load_settings
from p4n4_api.deps import layer_env

# Generating on a Pi is slow, and loading a model before the first token can take minutes:
# wait long for data, briefly for a connection.
STREAM_TIMEOUT = httpx.Timeout(connect=5, read=600, write=30, pool=5)
LETTA_TIMEOUT = httpx.Timeout(connect=5, read=300, write=30, pool=5)
LIST_TIMEOUT = httpx.Timeout(10, connect=5)
# Swapped in by tests (httpx.MockTransport); None means real HTTP.
transport: httpx.AsyncBaseTransport | None = None


class UpstreamError(Exception):
    """Ollama or Letta is unreachable (`status` None) or answered with an error."""

    def __init__(self, service: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service
        self.status = status


@dataclass(frozen=True)
class Config:
    ollama_url: str
    letta_url: str
    letta_password: str | None


def config(project: tuple | None) -> Config:
    settings = load_settings()
    ai_env = layer_env(project, "ai")
    return Config(
        ollama_url=settings.ollama_url.rstrip("/"),
        letta_url=settings.letta_url.rstrip("/"),
        letta_password=settings.letta_password or ai_env.get("LETTA_SERVER_PASSWORD") or None,
    )


def _error_message(response: httpx.Response, body: bytes) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body.decode(errors="replace").strip() or response.reason_phrase
    if isinstance(data, dict):
        detail = data.get("error") or data.get("detail") or data.get("message")
        if detail:
            return detail if isinstance(detail, str) else json.dumps(detail)
    return response.reason_phrase


# ── Ollama ────────────────────────────────────────────────────────────────────


async def ollama_models(cfg: Config) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=LIST_TIMEOUT, transport=transport) as client:
            response = await client.get(f"{cfg.ollama_url}/api/tags")
    except httpx.HTTPError as exc:
        raise UpstreamError("Ollama", f"unreachable at {cfg.ollama_url}: {exc}") from exc
    if response.status_code != 200:
        raise UpstreamError(
            "Ollama", _error_message(response, response.content), response.status_code
        )
    return response.json().get("models") or []


class OllamaStream:
    """An Ollama response opened before the API answers, so errors that come before the
    first chunk (unknown model, Ollama down) become HTTP errors rather than a broken
    stream. Iterate `lines()` once; it closes the connection when done or abandoned."""

    def __init__(self, client: httpx.AsyncClient, response: httpx.Response) -> None:
        self._client = client
        self._response = response

    async def lines(self) -> AsyncIterator[bytes]:
        try:
            async for line in self._response.aiter_lines():
                if line.strip():
                    yield line.encode() + b"\n"
        except httpx.HTTPError as exc:
            # The connection broke mid-reply: end with an error chunk, as Ollama would.
            yield json.dumps({"error": f"Ollama: connection lost: {exc}", "done": True}).encode()
            yield b"\n"
        finally:
            await self.close()

    async def body(self) -> dict:
        """The whole (non-streamed) reply."""
        try:
            return json.loads(await self._response.aread())
        finally:
            await self.close()

    async def close(self) -> None:
        await self._response.aclose()
        await self._client.aclose()


async def ollama_open(cfg: Config, path: str, payload: dict) -> OllamaStream:
    """POST to Ollama (`/api/chat` or `/api/generate`) and return the open response."""
    client = httpx.AsyncClient(timeout=STREAM_TIMEOUT, transport=transport)
    try:
        request = client.build_request("POST", f"{cfg.ollama_url}{path}", json=payload)
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise UpstreamError("Ollama", f"unreachable at {cfg.ollama_url}: {exc}") from exc
    if response.status_code != 200:
        body = await response.aread()
        await response.aclose()
        await client.aclose()
        raise UpstreamError("Ollama", _error_message(response, body), response.status_code)
    return OllamaStream(client, response)


# ── Letta ─────────────────────────────────────────────────────────────────────


def _letta_client(cfg: Config, timeout: httpx.Timeout) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {cfg.letta_password}"} if cfg.letta_password else {}
    return httpx.AsyncClient(
        base_url=cfg.letta_url, headers=headers, timeout=timeout, transport=transport
    )


async def _letta(cfg: Config, method: str, path: str, timeout: httpx.Timeout, **kwargs) -> object:
    try:
        async with _letta_client(cfg, timeout) as client:
            response = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise UpstreamError("Letta", f"unreachable at {cfg.letta_url}: {exc}") from exc
    if response.status_code >= 400:
        raise UpstreamError(
            "Letta", _error_message(response, response.content), response.status_code
        )
    return response.json()


async def letta_agents(cfg: Config) -> list[dict]:
    data = await _letta(cfg, "GET", "/v1/agents/", LIST_TIMEOUT)
    return data if isinstance(data, list) else []


async def letta_message(cfg: Config, agent_id: str, content: str) -> dict:
    data = await _letta(
        cfg,
        "POST",
        f"/v1/agents/{agent_id}/messages",
        LETTA_TIMEOUT,
        json={"messages": [{"role": "user", "content": content}]},
    )
    return data if isinstance(data, dict) else {}


def letta_text(content: object) -> str:
    """Text of a Letta message's content: a string, or a list of parts with `text`."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return ""


async def ping(url: str, path: str) -> str | None:
    """None when the service answers `path`, else why not (for /ready)."""
    try:
        async with httpx.AsyncClient(timeout=3, transport=transport) as client:
            response = await client.get(f"{url}{path}")
    except httpx.HTTPError as exc:
        return f"unreachable at {url}: {exc}"
    return None if response.status_code < 400 else f"{path} returned {response.status_code}"
