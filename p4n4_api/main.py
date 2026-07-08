"""p4n4-api application factory and entrypoint."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from p4n4_api import __version__
from p4n4_api.routes import health, project, stacks


def create_app() -> FastAPI:
    app = FastAPI(
        title="p4n4-api",
        description="REST API gateway for the P4N4 platform.",
        version=__version__,
        docs_url="/swagger-ui",
    )
    app.include_router(health.router)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(project.router)
    api_v1.include_router(stacks.router)
    app.include_router(api_v1)
    return app


app = create_app()


def run() -> None:
    """Console-script entrypoint: serve the API with uvicorn."""
    import uvicorn

    from p4n4_api.config import load_settings

    settings = load_settings()
    uvicorn.run("p4n4_api.main:app", host=settings.host, port=settings.port)
