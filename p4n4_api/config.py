"""Settings read from environment variables."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from p4n4_api import topics


def _default_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "p4n4-api"


# A comma-separated list (`a, b`), not the JSON pydantic-settings expects for collections.
CommaList = Annotated[tuple[str, ...], NoDecode]


class Settings(BaseSettings):
    """Each field is read from `P4N4_API_<NAME>` unless an alias says otherwise. Empty
    variables count as unset. Bad values (e.g. a non-numeric port) fail at startup."""

    model_config = SettingsConfigDict(env_prefix="P4N4_API_", env_ignore_empty=True, frozen=True)

    # Project root (or any directory inside it); None means walk up from cwd
    project_dir: Path | None = Field(None, validation_alias=AliasChoices("P4N4_PROJECT_DIR"))
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    # Browser origins allowed to call the API; empty disables CORS
    cors_origins: CommaList = ()
    # Where the API keeps its own state (SQLite database, generated JWT secret)
    data_dir: Path = Field(default_factory=_default_data_dir)
    # False only when P4N4_API_AUTH=off (or false/0/no): every request is treated as an admin
    auth_enabled: bool = Field(True, validation_alias=AliasChoices("P4N4_API_AUTH"))
    # Development only: create admin, power and normie (one per dashboard view,
    # password "p4n4") on startup if they don't exist
    dev_users: bool = False
    # HS256 signing key; None means generate one and keep it in data_dir
    jwt_secret: str | None = None
    # Reverse proxies (IPs or CIDR networks) whose X-Forwarded-For is believed; empty trusts none
    trusted_proxies: CommaList = ()
    # False when the API has no Docker access (e.g. a container without the socket mounted):
    # stack status and control return 503 and /ready doesn't require Docker
    docker_enabled: bool = Field(True, validation_alias=AliasChoices("P4N4_API_DOCKER"))
    # Filesystem whose usage /edge/metrics reports as disk_percent
    disk_path: Path = Path("/")
    # InfluxDB (telemetry). Org, bucket and token default to the project's iot/.env, the
    # file the IoT stack itself uses, so a host-run API needs no extra setup.
    influxdb_url: str = "http://localhost:8086"
    influxdb_token: str | None = None
    influxdb_org: str | None = None
    influxdb_bucket: str | None = None
    # Where the edge runner writes inference results; default: INFLUXDB_BUCKET_AI_EVENTS
    # from the project's edge/.env (or iot/.env), then "ai_events"
    influxdb_ai_events_bucket: str | None = None
    # The edge stack's inference runner (Edge Impulse .eim, ONNX or mock backend)
    edge_runner_url: str = "http://localhost:8080"
    # The AI stack: Ollama (models) and Letta (agents). The Letta password defaults to
    # LETTA_SERVER_PASSWORD from the project's ai/.env, and never leaves the server.
    ollama_url: str = "http://localhost:11434"
    letta_url: str = "http://localhost:8283"
    letta_password: str | None = None
    # MQTT broker: publishes ingested readings, and feeds the live telemetry stream
    mqtt_enabled: bool = True
    mqtt_host: str = "localhost"
    mqtt_port: int = Field(1883, ge=1, le=65535)
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    # Topics POST /mqtt/publish may use (MQTT filters). Denied wins. The default deny keeps
    # people from publishing as devices: Node-RED stores sensors/ and inference/ as data.
    # "none" empties a list (an empty variable counts as unset).
    mqtt_publish_allow: CommaList = ("#",)
    mqtt_publish_deny: CommaList = ("sensors/#", "inference/#")
    # `p4n4-api serve` logging: text for people, json (one object per line) for collectors
    log_format: Literal["text", "json"] = "text"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    @field_validator("log_format", "log_level", mode="before")
    @classmethod
    def _lowercase(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @field_validator("mqtt_publish_allow", "mqtt_publish_deny", mode="before")
    @classmethod
    def _topic_filters(cls, value: object) -> object:
        if isinstance(value, str):
            if value.strip().lower() == "none":
                return ()
            value = tuple(v.strip() for v in value.split(",") if v.strip())
        for topic_filter in value:
            topics.check_filter(topic_filter)
        return value

    @field_validator("cors_origins", "trusted_proxies", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        return (
            tuple(v.strip() for v in value.split(",") if v.strip())
            if isinstance(value, str)
            else value
        )

    @field_validator("cors_origins")
    @classmethod
    def _no_trailing_slash(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # Browsers send Origin without one, so `https://x/` would never match.
        return tuple(o.rstrip("/") for o in value)

    @field_validator("project_dir", "data_dir", "disk_path")
    @classmethod
    def _expand_user(cls, value: Path | None) -> Path | None:
        return value.expanduser() if value else value


def load_settings() -> Settings:
    """Read settings from the environment on each call so tests can override."""
    return Settings()
