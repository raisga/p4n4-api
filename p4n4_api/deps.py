"""FastAPI dependencies: locate the p4n4 project this API serves."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException
from p4n4_lib import manifest as mf

from p4n4_api.config import load_settings


def get_project() -> tuple[Path, dict]:
    """Return (project_dir, manifest data), or 404 when no project is found.

    The project is located via P4N4_PROJECT_DIR (walking up to .p4n4.json),
    falling back to the server process's working directory.
    """
    manifest_path = mf.find(load_settings().project_dir)
    if manifest_path is None:
        raise HTTPException(
            status_code=404,
            detail="No .p4n4.json found. Set P4N4_PROJECT_DIR to a p4n4 project directory.",
        )
    try:
        data = mf.load(manifest_path)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f".p4n4.json is not valid JSON: {exc}") from exc
    return manifest_path.parent, data


Project = Annotated[tuple[Path, dict], Depends(get_project)]
