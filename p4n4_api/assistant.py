"""The deployment's assistant: which backend (Ollama or Letta) and which model or agent
everyone chats with.

Operators and admins choose it; normies chat with whatever is chosen and nothing else, so
someone who only reads the dashboard can't load a bigger model onto the device or reach an
agent meant for someone else. Until an operator chooses, the first model (or agent) the
service lists is the one.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel, ValidationError

from p4n4_api import store

KEY = "assistant"


class Config(BaseModel):
    backend: Literal["ollama", "letta"] = "ollama"
    # Ollama model; None means the first one Ollama lists
    model: str | None = None
    # Letta agent ID; None means the first agent Letta lists
    agent_id: str | None = None


def load(conn: sqlite3.Connection) -> Config:
    try:
        return Config.model_validate(store.get(conn, KEY) or {})
    except ValidationError:
        return Config()  # written by a newer version, or hand-edited: fall back to defaults


def changed(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """When an operator last chose the assistant and who, or None if nobody has yet."""
    return store.changed(conn, KEY)


def save(conn: sqlite3.Connection, config: Config, actor: str) -> None:
    store.put(conn, KEY, config.model_dump(), actor)
