"""Settings read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # Project root (or any directory inside it); None means walk up from cwd
    project_dir: Path | None
    host: str
    port: int
    # Browser origins allowed to call the API; empty disables CORS
    cors_origins: tuple[str, ...]
    # Where the API keeps its own state (SQLite database, generated JWT secret)
    data_dir: Path
    # False only when P4N4_API_AUTH=off: every request is treated as an admin
    auth_enabled: bool
    # HS256 signing key; None means generate one and keep it in data_dir
    jwt_secret: str | None


def _default_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "p4n4-api"


def load_settings() -> Settings:
    """Read settings from the environment on each call so tests can override."""
    project_dir = os.environ.get("P4N4_PROJECT_DIR")
    return Settings(
        project_dir=Path(project_dir).expanduser() if project_dir else None,
        host=os.environ.get("P4N4_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("P4N4_API_PORT", "8000")),
        cors_origins=tuple(
            o.strip().rstrip("/")
            for o in os.environ.get("P4N4_API_CORS_ORIGINS", "").split(",")
            if o.strip()
        ),
        data_dir=Path(os.environ["P4N4_API_DATA_DIR"]).expanduser()
        if os.environ.get("P4N4_API_DATA_DIR")
        else _default_data_dir(),
        auth_enabled=os.environ.get("P4N4_API_AUTH", "on").lower() not in ("off", "false", "0"),
        jwt_secret=os.environ.get("P4N4_API_JWT_SECRET") or None,
    )
