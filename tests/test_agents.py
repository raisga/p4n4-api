"""Tests for AI agents: Ollama chat/generate streaming and Letta agents, against fakes."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from p4n4_api import ai

TAGS = {
    "models": [
        {
            "name": "qwen2.5:0.5b",
            "model": "qwen2.5:0.5b",
            "modified_at": "2026-09-01T10:00:00Z",
            "size": 397821319,
            "details": {
                "family": "qwen2",
                "parameter_size": "494.03M",
                "quantization_level": "Q4_K_M",
            },
        }
    ]
}
CHUNKS = [
    {"model": "qwen2.5:0.5b", "message": {"role": "assistant", "content": "All "}, "done": False},
    {"model": "qwen2.5:0.5b", "message": {"role": "assistant", "content": "good."}, "done": False},
    {
        "model": "qwen2.5:0.5b",
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "eval_count": 2,
    },
]
NDJSON = "".join(json.dumps(c) + "\n" for c in CHUNKS)
LETTA_AGENTS = [
    {
        "id": "agent-1",
        "name": "ops-helper",
        "description": "Knows the plant",
        "llm_config": {"model": "qwen2.5"},
    },
    {"id": "agent-2", "name": None},
    {"name": "no id: skipped"},
]
LETTA_REPLY = {
    "messages": [
        {"message_type": "reasoning_message", "reasoning": "User asks about status."},
        {"message_type": "assistant_message", "content": "Everything is running."},
        {
            "message_type": "assistant_message",
            "content": [{"type": "text", "text": "Anything else?"}],
        },
    ],
    "usage": {"total_tokens": 42},
}


def fake_ai(ollama_chat=None, ollama_status=200, letta_status=200, letta_reply=LETTA_REPLY):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        if path in ("/api/chat", "/api/generate"):
            if ollama_status != 200:
                return httpx.Response(
                    ollama_status,
                    json={
                        "error": f'model "{json.loads(request.content)["model"]}" not found, '
                        "try pulling it first"
                    },
                )
            if json.loads(request.content)["stream"] is False:
                return httpx.Response(
                    200,
                    json=CHUNKS[-1] | {"message": {"role": "assistant", "content": "All good."}},
                )
            return httpx.Response(200, content=(ollama_chat or NDJSON).encode())
        if path == "/v1/agents/":
            return httpx.Response(
                letta_status, json=LETTA_AGENTS if letta_status == 200 else {"detail": "nope"}
            )
        if path.startswith("/v1/agents/") and path.endswith("/messages"):
            if letta_status != 200:
                return httpx.Response(
                    letta_status,
                    json={"detail": "Agent not found" if letta_status == 404 else "Unauthorized"},
                )
            return httpx.Response(200, json=letta_reply)
        return httpx.Response(404)

    return handle


def _sent(ai_stack, path: str) -> dict:
    return json.loads(next(r for r in reversed(ai_stack.requests) if r.url.path == path).content)


def _chat(c, **body):
    return c.post(
        "/api/v1/agents/chat",
        json={
            "model": "qwen2.5:0.5b",
            "messages": [{"role": "user", "content": "How is it going?"}],
            **body,
        },
    )


# ── Ollama ────────────────────────────────────────────────────────────────────


def test_models(client, ai_stack):
    ai_stack.handler = fake_ai()
    assert client.get("/api/v1/agents/models").json() == {
        "models": [
            {
                "name": "qwen2.5:0.5b",
                "size": 397821319,
                "modified_at": "2026-09-01T10:00:00Z",
                "family": "qwen2",
                "parameter_size": "494.03M",
                "quantization": "Q4_K_M",
            }
        ]
    }
    assert str(ai_stack.requests[0].url) == "http://localhost:11434/api/tags"


def test_chat_streams_ollama_chunks_unchanged(client, ai_stack):
    ai_stack.handler = fake_ai()
    r = _chat(client, options={"temperature": 0.2})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/x-ndjson"
    assert r.headers["X-Accel-Buffering"] == "no"
    assert [json.loads(line) for line in r.text.splitlines()] == CHUNKS
    assert _sent(ai_stack, "/api/chat") == {
        "model": "qwen2.5:0.5b",
        "messages": [{"role": "user", "content": "How is it going?"}],
        "options": {"temperature": 0.2},
        "stream": True,
    }


def test_chat_without_streaming(client, ai_stack):
    ai_stack.handler = fake_ai()
    r = _chat(client, stream=False)
    assert r.headers["content-type"] == "application/json"
    assert r.json()["message"]["content"] == "All good."
    assert _sent(ai_stack, "/api/chat")["stream"] is False


def test_generate(client, ai_stack):
    ai_stack.handler = fake_ai()
    r = client.post(
        "/api/v1/agents/generate",
        json={"model": "qwen2.5:0.5b", "prompt": "Hi", "system": "Be brief."},
    )
    assert r.status_code == 200
    assert _sent(ai_stack, "/api/generate") == {
        "model": "qwen2.5:0.5b",
        "prompt": "Hi",
        "system": "Be brief.",
        "stream": True,
    }


def _fake_stacks(monkeypatch):
    monkeypatch.setattr(
        "p4n4_api.routes.stacks.compose.ps",
        lambda cwd: (
            [
                {"Service": "mqtt", "State": "running", "Health": "healthy"},
                {"Service": "influxdb", "State": "exited", "ExitCode": 1},
            ]
            if cwd.name == "iot"
            else [{"Service": "ollama", "State": "running"}]
        ),
    )


def test_chat_include_status(client, ai_stack, multi_project, monkeypatch):
    _fake_stacks(monkeypatch)
    ai_stack.handler = fake_ai()
    _chat(client, include_status=True)
    system, user = _sent(ai_stack, "/api/chat")["messages"]
    assert system["role"] == "system" and user["role"] == "user"
    status = system["content"]
    assert status.startswith("Current p4n4 system status (")
    assert "- Stack iot: 1/2 services running; influxdb exited (exit 1)" in status
    assert "- Stack ai: 1/1 services running" in status
    assert "- Edge host: CPU " in status


def test_generate_include_status_keeps_system_prompt(client, ai_stack, monkeypatch):
    ai_stack.handler = fake_ai()
    monkeypatch.setattr("p4n4_api.docker.daemon_error", lambda: "cannot connect")
    client.post(
        "/api/v1/agents/generate",
        json={"model": "m", "prompt": "Hi", "system": "Be brief.", "include_status": True},
    )
    system = _sent(ai_stack, "/api/generate")["system"]
    assert system.endswith("\n\nBe brief.")
    # No project here: no stack lines, edge metrics still there.
    assert "Stack" not in system and "- Edge host:" in system


def test_status_when_docker_is_down(client, ai_stack, multi_project, monkeypatch):
    ai_stack.handler = fake_ai()
    monkeypatch.setattr("p4n4_api.docker.daemon_error", lambda: "cannot connect")
    _chat(client, include_status=True)
    status = _sent(ai_stack, "/api/chat")["messages"][0]["content"]
    assert "- Stacks: unknown (Docker unavailable: cannot connect)" in status


@pytest.mark.parametrize(
    "handler,status,code",
    [
        (fake_ai(ollama_status=404), 404, "model_not_found"),
        (fake_ai(ollama_status=400), 422, "upstream_rejected"),
        (fake_ai(ollama_status=500), 502, "upstream_error"),
        (None, 503, "ollama_unavailable"),
    ],
)
def test_ollama_errors_before_streaming(client, ai_stack, handler, status, code):
    if handler:
        ai_stack.handler = handler
    r = _chat(client)
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)
    if code == "model_not_found":
        assert "try pulling it first" in r.json()["error"]["message"]


@pytest.mark.parametrize(
    "body",
    [
        {"model": "bad model", "messages": [{"role": "user", "content": "x"}]},
        {"model": "-rf", "messages": [{"role": "user", "content": "x"}]},
        {"model": "m", "messages": []},
        {"model": "m", "messages": [{"role": "tool", "content": "x"}]},
        {"model": "m", "messages": [{"role": "user", "content": "x"}], "options": {"a": {"b": 1}}},
    ],
)
def test_chat_validation(client, ai_stack, body):
    ai_stack.handler = fake_ai()
    assert client.post("/api/v1/agents/chat", json=body).status_code == 422
    assert ai_stack.requests == []


class _BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield (json.dumps(CHUNKS[0]) + "\n").encode()
        raise httpx.ReadError("connection reset")


def test_connection_lost_mid_reply_ends_with_error_chunk(client, ai_stack):
    ai_stack.handler = lambda request: httpx.Response(200, stream=_BrokenStream())
    lines = [json.loads(line) for line in _chat(client).text.splitlines()]
    assert lines[0] == CHUNKS[0]
    assert lines[-1]["done"] is True and "connection lost" in lines[-1]["error"]


def test_leaving_early_closes_ollama_connection():
    closed = []

    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b'{"done": false}\n'
                await asyncio.sleep(0)

        async def aclose(self):
            closed.append(True)

    async def scenario():
        ai.transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Endless()))
        try:
            cfg = ai.Config("http://ollama", "http://letta", None)
            reply = await ai.ollama_open(cfg, "/api/chat", {"model": "m"})
            lines = reply.lines()
            await anext(lines)
            await lines.aclose()  # the client went away
        finally:
            ai.transport = None

    asyncio.run(scenario())
    assert closed == [True]


# ── Letta ─────────────────────────────────────────────────────────────────────


def test_agents(client, ai_stack, multi_project):
    ai_stack.handler = fake_ai()
    assert client.get("/api/v1/agents").json() == {
        "agents": [
            {
                "id": "agent-1",
                "name": "ops-helper",
                "description": "Knows the plant",
                "model": "qwen2.5",
            },
            {"id": "agent-2", "name": "agent-2", "description": None, "model": None},
        ]
    }
    # The password comes from the project's ai/.env ("x" in the fixture), server side only.
    assert ai_stack.requests[-1].headers["Authorization"] == "Bearer x"


def test_letta_password_override(client, ai_stack, multi_project, monkeypatch):
    ai_stack.handler = fake_ai()
    monkeypatch.setenv("P4N4_API_LETTA_PASSWORD", "from-env")
    monkeypatch.setenv("P4N4_API_LETTA_URL", "http://letta.example:9000/")
    client.get("/api/v1/agents")
    request = ai_stack.requests[-1]
    assert request.headers["Authorization"] == "Bearer from-env"
    assert str(request.url) == "http://letta.example:9000/v1/agents/"


def test_letta_without_password_sends_none(client, ai_stack):
    ai_stack.handler = fake_ai()
    client.get("/api/v1/agents")  # no project, no setting
    assert "Authorization" not in ai_stack.requests[-1].headers


def test_agent_chat(client, ai_stack, multi_project):
    ai_stack.handler = fake_ai()
    r = client.post("/api/v1/agents/agent-1/chat", json={"message": "Status?"})
    assert r.status_code == 200
    assert r.json() == {
        "reply": "Everything is running.\n\nAnything else?",
        "messages": [
            {"type": "reasoning_message", "text": "User asks about status."},
            {"type": "assistant_message", "text": "Everything is running."},
            {"type": "assistant_message", "text": "Anything else?"},
        ],
    }
    assert ai_stack.requests[-1].url.path == "/v1/agents/agent-1/messages"
    assert _sent(ai_stack, "/v1/agents/agent-1/messages") == {
        "messages": [{"role": "user", "content": "Status?"}]
    }


def test_agent_chat_include_status(client, ai_stack, multi_project, monkeypatch):
    _fake_stacks(monkeypatch)
    ai_stack.handler = fake_ai()
    client.post("/api/v1/agents/agent-1/chat", json={"message": "Status?", "include_status": True})
    content = _sent(ai_stack, "/v1/agents/agent-1/messages")["messages"][0]["content"]
    assert content.startswith("Current p4n4 system status (")
    assert content.endswith("\n\nStatus?")


def test_agent_chat_no_assistant_reply(client, ai_stack):
    ai_stack.handler = fake_ai(letta_reply={"messages": [{"message_type": "tool_call_message"}]})
    r = client.post("/api/v1/agents/agent-1/chat", json={"message": "x"})
    assert r.json() == {"reply": "", "messages": [{"type": "tool_call_message", "text": ""}]}


@pytest.mark.parametrize(
    "status,code", [(404, "agent_not_found"), (401, "upstream_auth"), (500, "upstream_error")]
)
def test_letta_errors(client, ai_stack, status, code):
    ai_stack.handler = fake_ai(letta_status=status)
    r = client.post("/api/v1/agents/agent-1/chat", json={"message": "x"})
    assert (r.status_code, r.json()["error"]["code"]) == (404 if status == 404 else 502, code)
    if code == "upstream_auth":
        assert "LETTA_SERVER_PASSWORD" in r.json()["error"]["message"]


def test_letta_down(client):
    r = client.get("/api/v1/agents")
    assert (r.status_code, r.json()["error"]["code"]) == (503, "letta_unavailable")


@pytest.mark.parametrize("agent_id", ["a.b", "a%2Fb", "x" * 101])
def test_agent_id_checked(client, ai_stack, agent_id):
    ai_stack.handler = fake_ai()
    r = client.post(f"/api/v1/agents/{agent_id}/chat", json={"message": "x"})
    assert r.status_code in (404, 422)
    assert ai_stack.requests == []


# ── Access and readiness ──────────────────────────────────────────────────────


def test_agents_need_operator(anon, admin, ai_stack):
    ai_stack.handler = fake_ai()
    assert anon.get("/api/v1/agents/models").status_code == 401
    key = admin.post("/api/v1/devices", json={"id": "dev-1"}).json()["api_key"]
    token = anon.post("/api/v1/auth/token", json={"api_key": key}).json()["access_token"]
    assert (
        anon.get("/api/v1/agents", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    )


def test_normie_chats_but_cannot_generate(normie, ai_stack, multi_project):
    ai_stack.handler = fake_ai()
    assert normie.get("/api/v1/agents").status_code == 200
    assert _chat(normie, stream=False).status_code == 200
    assert normie.post("/api/v1/agents/agent-1/chat", json={"message": "Hi"}).status_code == 200
    r = normie.post("/api/v1/agents/generate", json={"model": "m", "prompt": "hi", "stream": False})
    assert r.status_code == 403


def test_ready_reports_ai_services(client, ai_stack, multi_project, influxdb):
    influxdb.handler = lambda request: httpx.Response(200)

    def handle(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.12.0"})
        return httpx.Response(503)

    ai_stack.handler = handle
    r = client.get("/ready")
    assert r.status_code == 200
    checks = r.json()["checks"]
    assert checks["ollama"] == {"ok": True, "required": False}
    assert checks["letta"] == {"ok": False, "detail": "/v1/health/ returned 503", "required": False}
