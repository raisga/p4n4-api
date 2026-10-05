"""AI agents: Ollama models for chat and generation, Letta agents with memory.

Normies can list models and agents and chat, but only with the assistant an operator chose
(GET/PUT /agents/config) and without changing generation options. One-shot generation and
choosing the assistant are for operators.

Going through the API puts sign-in in front of both and keeps the Letta password on the
server. Ollama replies stream as Ollama's own NDJSON chunks, so a client that parses
Ollama's `/api/chat` parses these unchanged.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from p4n4_api import ai, assistant, audit, auth, db
from p4n4_api.auth import CurrentUser
from p4n4_api.context import system_status
from p4n4_api.deps import OptionalProject
from p4n4_api.errors import ApiError

router = APIRouter(prefix="/agents", tags=["agents"])
operator_only = [Depends(auth.require_role("operator"))]

# Ollama model names: `llama3.2`, `qwen2.5:0.5b`, `hf.co/org/model:Q4_K_M`
ModelName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")]
# Letta agent IDs (`agent-<uuid>`); nothing that could change the upstream path
AgentId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,100}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=100_000)]
# Ollama generation options (temperature, num_ctx, ...), scalars only
Options = Annotated[dict[str, float | int | str | bool], Field(max_length=30)]


class AssistantConfig(BaseModel):
    """Who everyone chats with: an Ollama model or a Letta agent. A null model or agent
    means the first one the service lists."""

    backend: Literal["ollama", "letta"] = "ollama"
    model: ModelName | None = None
    agent_id: AgentId | None = None


class AssistantState(AssistantConfig):
    # When and by whom it was last chosen; both null until someone chooses, which is how
    # p4n4-dashboard knows to offer its brand's default (it does so once)
    updated_at: str | None = None
    updated_by: str | None = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: Annotated[str, StringConstraints(max_length=100_000)]


class ChatRequest(BaseModel):
    model: ModelName
    messages: list[ChatMessage] = Field(min_length=1, max_length=500)
    # NDJSON chunks as they're generated (default), or one JSON reply
    stream: bool = True
    # Add the current stack status and edge metrics as a system message
    include_status: bool = False
    options: Options | None = None


class GenerateRequest(BaseModel):
    model: ModelName
    prompt: Text
    system: Annotated[str, StringConstraints(max_length=100_000)] | None = None
    stream: bool = True
    include_status: bool = False
    options: Options | None = None


class OllamaChunk(BaseModel):
    """One NDJSON line of a streamed reply (or the whole reply with `stream: false`), as
    Ollama sends it: `message.content` (chat) or `response` (generate) holds the next
    text; the last chunk has `done: true` and timings; a failure is an `error` chunk."""

    model_config = ConfigDict(extra="allow")
    model: str | None = None
    created_at: str | None = None
    message: ChatMessage | None = None
    response: str | None = None
    done: bool | None = None
    done_reason: str | None = None
    total_duration: int | None = None
    eval_count: int | None = None
    error: str | None = None


class ModelInfo(BaseModel):
    name: str
    size: int | None
    modified_at: str | None
    family: str | None
    parameter_size: str | None
    quantization: str | None


class Models(BaseModel):
    models: list[ModelInfo]


class Agent(BaseModel):
    id: str
    name: str
    description: str | None
    model: str | None


class Agents(BaseModel):
    agents: list[Agent]


class AgentChat(BaseModel):
    message: Text
    include_status: bool = False


class AgentMessage(BaseModel):
    type: str  # Letta's message_type: assistant_message, reasoning_message, ...
    text: str


class AgentReply(BaseModel):
    # The agent's answer: its assistant messages, joined
    reply: str
    # What the agent did on the way (reasoning, tool calls), for clients that show it
    messages: list[AgentMessage]


def _upstream(exc: ai.UpstreamError) -> HTTPException:
    service = exc.service.lower()
    if exc.status is None:
        return ApiError(503, f"{service}_unavailable", str(exc))
    if exc.status == 404:
        code = "model_not_found" if service == "ollama" else "agent_not_found"
        return ApiError(404, code, str(exc))
    if service == "letta" and exc.status in (401, 403):
        return ApiError(
            502,
            "upstream_auth",
            f"{exc} (check LETTA_SERVER_PASSWORD in the ai stack's .env, or "
            "P4N4_API_LETTA_PASSWORD)",
        )
    if exc.status in (400, 422):
        return ApiError(422, "upstream_rejected", str(exc))
    return ApiError(502, "upstream_error", str(exc))


def _config() -> assistant.Config:
    with db.connect() as conn:
        return assistant.load(conn)


def _restricted(message: str) -> ApiError:
    return ApiError(403, "assistant_restricted", message)


async def _check_model(user, body: ChatRequest, project) -> None:
    """Normies chat with the chosen model only, as it's set up."""
    if user.has_role("operator"):
        return
    if body.options:
        raise _restricted("Only operators can change generation options.")
    config = _config()
    if config.backend != "ollama":
        raise _restricted("The assistant is a Letta agent: chat with it instead.")
    model = config.model
    if model is None:
        try:
            listed = await ai.ollama_models(ai.config(project))
        except ai.UpstreamError as exc:
            raise _upstream(exc) from exc
        model = next((m.get("name") or m.get("model") for m in listed), None)
    if body.model != model:
        raise _restricted(f"Only operators can choose the model; the assistant uses {model!r}.")


async def _check_agent(user, agent_id: str, project) -> None:
    """Normies talk to the chosen agent only."""
    if user.has_role("operator"):
        return
    config = _config()
    if config.backend != "letta":
        raise _restricted("The assistant is an Ollama model: chat with it instead.")
    chosen = config.agent_id
    if chosen is None:
        try:
            listed = await ai.letta_agents(ai.config(project))
        except ai.UpstreamError as exc:
            raise _upstream(exc) from exc
        chosen = next((a["id"] for a in listed if isinstance(a, dict) and a.get("id")), None)
    if agent_id != chosen:
        raise _restricted("Only operators can choose the agent.")


_STREAM_RESPONSES = {
    200: {
        "model": OllamaChunk,
        "content": {"application/x-ndjson": {}},
        "description": "NDJSON chunks (`stream: true`), or one JSON reply",
    }
}


async def _ollama(
    path: str, payload: dict, stream: bool, project
) -> StreamingResponse | JSONResponse:
    try:
        reply = await ai.ollama_open(ai.config(project), path, {**payload, "stream": stream})
    except ai.UpstreamError as exc:
        raise _upstream(exc) from exc
    if not stream:
        return JSONResponse(await reply.body())
    return StreamingResponse(
        reply.lines(),
        media_type="application/x-ndjson",
        # X-Accel-Buffering: nginx (the dashboard's proxy) would otherwise hold chunks back
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _state(conn) -> AssistantState:
    when, who = assistant.changed(conn) or (None, None)
    return AssistantState(**assistant.load(conn).model_dump(), updated_at=when, updated_by=who)


@router.get("/config")
def get_config() -> AssistantState:
    """The assistant everyone chats with, and who chose it when."""
    with db.connect() as conn:
        return _state(conn)


@router.put("/config", dependencies=operator_only)
def set_config(body: AssistantConfig, actor: CurrentUser) -> AssistantState:
    """Choose the assistant: the backend, and the Ollama model or Letta agent (null for the
    first one listed). Normies chat with this one only."""
    with db.connect() as conn:
        assistant.save(conn, assistant.Config(**body.model_dump()), actor.username)
        chosen = body.model if body.backend == "ollama" else body.agent_id
        audit.record(
            actor.username, "assistant.update", body.backend, chosen or "first listed", conn
        )
        return _state(conn)


@router.get("/models")
async def models(project: OptionalProject) -> Models:
    """Models Ollama has pulled."""
    try:
        raw = await ai.ollama_models(ai.config(project))
    except ai.UpstreamError as exc:
        raise _upstream(exc) from exc
    return Models(
        models=[
            ModelInfo(
                name=m.get("name") or m.get("model") or "?",
                size=m.get("size"),
                modified_at=m.get("modified_at"),
                family=(m.get("details") or {}).get("family"),
                parameter_size=(m.get("details") or {}).get("parameter_size"),
                quantization=(m.get("details") or {}).get("quantization_level"),
            )
            for m in raw
        ]
    )


@router.post(
    "/chat", response_class=StreamingResponse, response_model=None, responses=_STREAM_RESPONSES
)
async def chat(
    body: ChatRequest, project: OptionalProject, user: CurrentUser
) -> StreamingResponse | JSONResponse:
    """Chat with an Ollama model. Ollama keeps no state: send the whole conversation.
    Normies may only use the assistant's model (GET /agents/config), without `options`."""
    await _check_model(user, body, project)
    messages = [m.model_dump() for m in body.messages]
    if body.include_status:
        messages.insert(0, {"role": "system", "content": await system_status(project)})
    payload: dict[str, Any] = {"model": body.model, "messages": messages}
    if body.options:
        payload["options"] = body.options
    return await _ollama("/api/chat", payload, body.stream, project)


@router.post(
    "/generate",
    response_class=StreamingResponse,
    response_model=None,
    responses=_STREAM_RESPONSES,
    dependencies=operator_only,
)
async def generate(
    body: GenerateRequest, project: OptionalProject
) -> StreamingResponse | JSONResponse:
    """One-shot generation with an Ollama model (text in `response` chunks)."""
    system = body.system
    if body.include_status:
        status = await system_status(project)
        system = f"{status}\n\n{system}" if system else status
    payload: dict[str, Any] = {"model": body.model, "prompt": body.prompt}
    if system:
        payload["system"] = system
    if body.options:
        payload["options"] = body.options
    return await _ollama("/api/generate", payload, body.stream, project)


@router.get("")
async def agents(project: OptionalProject) -> Agents:
    """Letta agents."""
    try:
        raw = await ai.letta_agents(ai.config(project))
    except ai.UpstreamError as exc:
        raise _upstream(exc) from exc
    return Agents(
        agents=[
            Agent(
                id=a["id"],
                name=a.get("name") or a["id"],
                description=a.get("description"),
                model=(a.get("llm_config") or {}).get("model"),
            )
            for a in raw
            if isinstance(a, dict) and a.get("id")
        ]
    )


@router.post("/{agent_id}/chat")
async def agent_chat(
    agent_id: AgentId, body: AgentChat, project: OptionalProject, user: CurrentUser
) -> AgentReply:
    """Send a message to a Letta agent. Letta keeps the conversation: send only the new
    message. Not streamed; can take a while on a Pi. Normies may only message the
    assistant's agent (GET /agents/config)."""
    await _check_agent(user, agent_id, project)
    content = body.message
    if body.include_status:
        content = f"{await system_status(project)}\n\n{content}"
    try:
        data = await ai.letta_message(ai.config(project), agent_id, content)
    except ai.UpstreamError as exc:
        raise _upstream(exc) from exc
    steps = [
        AgentMessage(
            type=m.get("message_type") or "?",
            text=ai.letta_text(m.get("content") if "content" in m else m.get("reasoning")),
        )
        for m in data.get("messages") or []
        if isinstance(m, dict)
    ]
    replies = [m.text for m in steps if m.type == "assistant_message" and m.text]
    return AgentReply(reply="\n\n".join(replies), messages=steps)
