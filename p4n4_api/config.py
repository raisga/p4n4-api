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


def load_settings() -> Settings:
    """Read settings from the environment on each call so tests can override."""
    project_dir = os.environ.get("P4N4_PROJECT_DIR")
    return Settings(
        project_dir=Path(project_dir).expanduser() if project_dir else None,
        host=os.environ.get("P4N4_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("P4N4_API_PORT", "8000")),
    )
